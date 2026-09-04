import time
from typing import List, Optional, TypedDict

import trio
from msgspec import NODEFAULT

from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.reactive.source import ConfigEventSource, KeyedEventSource
from alasio.backend.topic.scan import ConfigScanSource
from alasio.backend.topic.state import ConnState
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.entry.loader import MOD_LOADER
from alasio.config.entry.model import TaskItem
from alasio.ext.deep import deep_get, deep_iter_depth1


class TaskQueueData(TypedDict):
    running: Optional[str]
    pending: List[TaskItem]
    waiting: List[TaskItem]


class TaskQueueSource(ConfigEventSource):
    """
    Task queue cache source, resident (event stream).

    Event protocol: ConfigEvent(t='TaskQueue') with v a dict of any subset
    of {running, pending, waiting} -- merged key by key. Increments are
    keyed set patches of the changed keys (the client merges them into the
    task table); full events carry the whole data at the root.
    """
    TOPIC_NAME = 'TaskQueue'
    # Freshness window of fetch_init (running trust overrides it)
    TTL = 5
    data: TaskQueueData

    def __init__(self, config_name):
        super().__init__(config_name)
        # debounce flag of the config_event linkage (see config_event.py):
        # consecutive scheduler changes merge into one forced reinit
        self._reinit_pending = False

    def on_init(self, running) -> TaskQueueData:
        """
        Build the task table from the ConfigScan cache and the mod schedule.
        Runs in a worker thread (on_init_async).

        Args:
            running (str | None): Preserved running state, read under the
                lock by fetch_init (never read self.data in the thread).

        Returns:
            TaskQueueData:
        """
        # access cache directly, no rescan
        configs = ConfigScanSource().data
        try:
            info = configs[self.config_name]
        except KeyError:
            return {'running': running, 'pending': [], 'waiting': []}
        try:
            mod = MOD_LOADER.dict_mod[info.mod]
        except KeyError:
            return {'running': running, 'pending': [], 'waiting': []}

        pending_task, waiting_task = mod.get_task_schedule(self.config_name)
        return {'running': running, 'pending': pending_task, 'waiting': waiting_task}

    async def fetch_init(self, force=False):
        """
        Read the task table when needed:
        1. running trust: the worker is alive and its events already cover
           every change (data is up to date), skip the read;
        2. TTL 5s freshness;
        3. read the task table in a thread (pending / waiting), the running
           state is preserved from data -- read under the lock first, to
           keep it safe against concurrent recv-thread apply.

        Args:
            force (bool): Ignore the running trust and the TTL window.

        Returns:
            TaskQueueData | None: The new data, or None when fresh.
        """
        if not force:
            with self._lock:
                if self._running:
                    return None
                if time.monotonic() - self._lastrun < self.TTL:
                    return None
        # preserve the current running state from the worker: read under the
        # lock, never from the worker thread
        with self._lock:
            running = deep_get(self.data, keys='running', default=None)
        new = await trio.to_thread.run_sync(self.on_init, running)
        with self._lock:
            self._lastrun = time.monotonic()
        return new

    def _apply(self, event):
        """
        [锁内] Simple merge: each present key of {running, pending, waiting}
        replaces the corresponding key of data.

        Returns:
            bool: If data changed
        """
        value = event.v
        modified = False
        for key in ['running', 'pending', 'waiting']:
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

        The worker event v is a dict of any subset of {running, pending,
        waiting}; each key is forwarded as a separate keyed set so the
        client merges the patch instead of replacing the whole task table.
        Values reference the event payload: they are bound into data by
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

    def _reinit_mark(self):
        """
        [任意线程] Mark a forced-reinit request (config_event linkage).

        Returns:
            bool: True when a reinit should be scheduled; False when one is
                already pending / running (the request is merged into it).
        """
        with self._lock:
            if self._reinit_pending:
                return False
            self._reinit_pending = True
            return True

    def _reinit_clear(self):
        """
        [Trio] Clear the pending flag after a forced reinit finished.
        """
        with self._lock:
            self._reinit_pending = False


class TaskQueue(BaseTopic):
    TOPIC_NAME = 'TaskQueue'

    async def get_source(self):
        """
        Data preparation: reinit (running trust / TTL no-op when fresh; the
        first subscription of a config without worker events fills the task
        table through on_init).
        """
        state = ConnState(self.conn_id, self.server)
        config_name = await state.config_name
        if not config_name:
            return None
        source = TaskQueueSource(config_name)
        await source.reinit()
        return source


class TaskQueueI18nSource(KeyedEventSource):
    """
    One-shot keyed cache source of TaskQueueI18n: key (mod_name, lang).
    """
    TOPIC_NAME = 'TaskQueueI18n'
    TTL = 8
    IDLE_TTL = 8

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
