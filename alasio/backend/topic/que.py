from typing import List, TypedDict

import trio
from msgspec import NODEFAULT

from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.reactive.source import ConfigSource, DiskCache, DiskCachePush, KeyedSource, ResidentCache
from alasio.backend.topic.scan import ConfigScanSource
from alasio.backend.topic.state import ConnState
from alasio.backend.ws.context import GLOBAL_CONTEXT
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.entry.loader import MOD_LOADER
from alasio.config.entry.model import TaskItem
from alasio.ext.deep import deep_iter_depth1
from alasio.logger import logger


class TaskQueueData(TypedDict):
    pending: List[TaskItem]
    waiting: List[TaskItem]


class TaskQueueSource(ConfigSource, DiskCachePush):
    """
    Task queue cache source: disk cache + event push (config-keyed).

    Data = {pending, waiting} loaded from the disk schedule table
    (reinit) and kept up to date by worker TaskQueue events. Event
    protocol: ConfigEvent(t='TaskQueue') with v a dict of any subset
    of {pending, waiting} -- merged key by key. Increments are keyed set
    patches of the changed keys (the client merges them into the task
    table); full events carry the whole data at the root.

    Lifecycle: DiskCachePush -- worker events / config-event refreshes
    refresh the data TTL; the data-expiry GC recycles the instance only
    when the TTL expired AND no subscriber is attached (the push channel
    is bound to the instance).
    """
    TOPIC_NAME = 'TaskQueue'
    TTL = 5
    data: TaskQueueData

    def __init__(self, config_name):
        super().__init__(config_name)

    def on_init(self):
        """
        [线程] Build the task table from the ConfigScan cache and the mod
        schedule. Runs in a worker thread (on_init_async). Never touches
        self.data (cross-thread).

        Returns:
            dict: {'pending': list, 'waiting': list} -- empty lists when
                the config / mod is gone.
        """
        # access cache directly, no rescan
        configs = ConfigScanSource().data
        try:
            info = configs[self.config_name]
        except KeyError:
            return {'pending': [], 'waiting': []}
        try:
            mod = MOD_LOADER.dict_mod[info.mod]
        except KeyError:
            return {'pending': [], 'waiting': []}

        pending_task, waiting_task = mod.get_task_schedule(self.config_name)
        return {'pending': pending_task, 'waiting': waiting_task}

    def _apply(self, event):
        """
        [锁内] Simple merge: each present key of {pending, waiting}
        replaces the corresponding key of data.

        Returns:
            bool: If data changed
        """
        value = event.v
        modified = False
        for key in ['pending', 'waiting']:
            if key not in value:
                continue
            before = self.data.get(key, NODEFAULT)
            after = value[key]
            # stop broadcast if not modified
            if before != after:
                self.data[key] = after
                modified = True
        return modified

    def _convert(self, event):
        """
        [锁内] Keyed set patches of the event's changed keys, referencing
        the applied values.

        The worker event v is a dict of any subset of {pending, waiting};
        each key is forwarded as a separate keyed set so the client merges
        the patch instead of replacing the whole task table. Values
        reference the event payload: they are bound into data by
        replacement and never mutated in place afterwards, so encoding
        later (outside the lock) is safe. A redundant key (value equal to
        the current one, untouched by _apply) is harmless: the client
        merge is idempotent.

        Returns:
            ResponseEvent | list[ResponseEvent]:
        """
        value = event.v
        return [
            ResponseEvent(t=self.TOPIC_NAME, o='set', k=(key,), v=after)
            for key, after in value.items()
        ]

    # ---------------- config-event linkage ----------------

    @staticmethod
    def _need_reinit(responses):
        """
        Does the config-save payload touch Scheduler.Enable / NextRun?
        Handles ConfigSetEvent and dict payloads, single or list.

        Args:
            responses (ConfigSetEvent | list[ConfigSetEvent] | dict |
                list[dict]):

        Returns:
            bool:
        """
        if not isinstance(responses, list):
            responses = [responses]
        for resp in responses:
            if resp is None:
                continue
            # worker payloads are dicts (decoded from bytes)
            if type(resp) is dict:
                try:
                    group = resp['group']
                    arg = resp['arg']
                except KeyError:
                    continue
            else:
                group = resp.group
                arg = resp.arg
            if group == 'Scheduler' and (arg == 'Enable' or arg == 'NextRun'):
                return True
        return False

    def on_config_event(self, event):
        """
        [任意线程] Config-save linkage: recompute the task table when the
        scheduler settings (Scheduler.Enable / NextRun) changed.

        - subscribers present: force a refresh (queued on the Trio thread;
          concurrent hits serialize on _fetch_lock and identical reads do
          not broadcast -- no debounce needed);
        - no subscriber: mark the data dirty. The refresh happens on the
          next subscribe (fetch_init bypasses TTL while dirty); nothing is
          broadcast anyway without subscribers, and the data is kept as a
          fallback (never cleared: a failed read still has the old table).
        """
        if not self._need_reinit(event):
            return
        # snapshot under the lock, branch outside: mark_dirty takes the
        # lock itself (threading.Lock is not reentrant)
        with self._lock:
            has_subscribers = bool(self._subscribers)
        if has_subscribers:
            # queue a forced refresh on the Trio thread
            try:
                GLOBAL_CONTEXT.trio_token.run_sync_soon(self._spawn_refresh)
            except trio.RunFinishedError:
                pass
        else:
            self.mark_dirty()

    def _spawn_refresh(self):
        """
        [Trio 线程, run_sync_soon callback] Launch the linkage coroutine.
        """
        try:
            GLOBAL_CONTEXT.global_nursery.start_soon(self._refresh)
        except RuntimeError:
            pass  # nursery already ended (shutdown race)

    async def _refresh(self):
        """
        [Trio task] Forced refresh; errors must not crash the nursery.
        """
        try:
            await self.reinit(force=True)
        except Exception:
            logger.exception(f'{self} config-event refresh failed')


class TaskQueue(BaseTopic):
    TOPIC_NAME = 'TaskQueue'

    async def get_source(self):
        """
        Data preparation: reinit (TTL / dirty no-op when fresh; the first
        subscription of a config without worker events fills the task
        table through on_init).
        """
        state = ConnState(self.conn_id, self.server)
        config_name = await state.config_name
        if not config_name:
            return None
        source = TaskQueueSource(config_name)
        await source.reinit()
        return source


class TaskRunningSource(ConfigSource, ResidentCache):
    """
    Current running task of a config, resident (event stream only).

    Event protocol: ConfigEvent(t='TaskRunning') with
    v = task_name | None -- sent by the worker scheduler on every task
    switch. data is the task name itself (None = no task running). There
    is no full-data source: every change flows through events, so reinit
    is never called (the get_source of the topic does not call it).
    Runtime-produced state is kept resident: a later front-end visit
    finds the current task without events having to be replayed.
    """
    TOPIC_NAME = 'TaskRunning'

    def __init__(self, config_name):
        super().__init__(config_name)
        # no running task until the first scheduler event
        self.data = None

    def _apply(self, event):
        """
        [锁内] Replace the running task.

        Args:
            event: ConfigEvent with v = task name | None

        Returns:
            bool: If data changed
        """
        value = event.v
        if self.data == value:
            return False
        self.data = value
        return True

    def _convert(self, event):
        """
        [锁内] Event -> root set of the running task.
        """
        return ResponseEvent(t=self.TOPIC_NAME, o='set', v=self.data)


class TaskRunning(BaseTopic):
    TOPIC_NAME = 'TaskRunning'

    async def get_source(self):
        """
        Pure event-stream source: no reinit (there is no full-data read;
        the full is the in-memory data snapshot of subscribe).
        """
        state = ConnState(self.conn_id, self.server)
        config_name = await state.config_name
        if not config_name:
            return None
        return TaskRunningSource(config_name)


class TaskQueueI18nSource(KeyedSource, DiskCache):
    """
    One-shot disk-cache source of TaskQueueI18n: key (mod_name, lang).

    Static i18n content read from disk; no push after the full snapshot.
    Recycled by the data-expiry GC when the data TTL expired, regardless
    of subscribers (DiskCache semantics).
    """
    TOPIC_NAME = 'TaskQueueI18n'
    TTL = 8

    def __init__(self, mod_name, lang):
        super().__init__()
        self.mod_name = mod_name
        self.lang = lang

    def on_init(self):
        """
        Returns:
            dict[str, str]:
                key: {task_name}
                value: i18n translation
        """
        data = MOD_LOADER.get_queue_i18n(self.mod_name)
        # {task_name}.{lang}=i18n -> {task_name}=i18n
        i18n_dict = {}
        for task, i18n in deep_iter_depth1(data):
            value = i18n.get(self.lang, task)
            i18n_dict[task] = value
        return i18n_dict


class TaskQueueI18n(BaseTopic):
    TOPIC_NAME = 'TaskQueueI18n'

    async def get_source(self):
        state = ConnState(self.conn_id, self.server)
        mod_name = await state.mod_name
        lang = await state.lang
        if not mod_name or not lang:
            return None
        source = TaskQueueI18nSource.get(mod_name, lang)
        await source.reinit()
        return source
