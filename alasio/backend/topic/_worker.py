import trio

from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.reactive.source import GlobalEventSource
from alasio.backend.topic import config_event
from alasio.backend.topic.log import LogCache
from alasio.backend.topic.preview import PreviewTask
from alasio.backend.topic.que import TaskQueueSource
from alasio.backend.worker.event import ConfigEvent
from alasio.backend.worker.manager import WORKER_STATE, WorkerManager
from alasio.backend.ws.context import GLOBAL_CONTEXT
from alasio.config.entry.mod import Mod
from alasio.ext import env
from alasio.logger import logger


class WorkerSource(GlobalEventSource):
    """
    Worker states of every config: data = dict[config, WORKER_STATE].

    Event protocol: on_event((config, state)); 'idle' deletes the config
    (absent workers default to idle), any other state is set. There is no
    full-data source: every state transition goes through
    WorkerManager.on_worker_state -> WorkerSource().on_event, so the data is
    always up to date, also without subscribers.
    """
    TOPIC_NAME = 'Worker'

    def _apply(self, event):
        config, state = event
        if state == 'idle':
            if config not in self.data:
                return False
            del self.data[config]
        else:
            if self.data.get(config, None) == state:
                return False
            self.data[config] = state
        return True

    def _make_response(self, event):
        config, state = event
        if state == 'idle':
            return ResponseEvent(t=self.TOPIC_NAME, o='del', k=(config,))
        return ResponseEvent(t=self.TOPIC_NAME, o='set', k=(config,), v=state)


class BackendWorkerManager(WorkerManager):
    def worker_start(self, mod: Mod, config: str) -> "tuple[bool, str]":
        project_root = env.PROJECT_ROOT
        mod_root = mod.root
        path_main = mod.entry.path_main
        return super().worker_start(
            mod=mod.name, config=config,
            project_root=project_root, mod_root=mod_root, path_main=path_main
        )

    def on_worker_state(self, config: str, state: WORKER_STATE):
        # 1. Worker state source: locked forwarding, safe from any thread
        #    (worker recv thread / trio RPC thread / to_thread).
        WorkerSource().on_event((config, state))
        # 2. Preview notification: PreviewTask uses trio APIs (nursery /
        #    send_lossy), so it must run on the Trio thread. No
        #    from_thread.run blocking round trip.
        cache = PreviewTask(config)
        try:
            GLOBAL_CONTEXT.trio_token.run_sync_soon(cache.on_worker_state, state)
        except trio.RunFinishedError:
            pass

    def on_config_event(self, event: ConfigEvent):
        topic = event.t
        if topic == 'Log':
            # cache and broadcast log
            cache = LogCache(event.c)
            cache.on_event(event)
        elif topic == 'Preview':
            cache = PreviewTask(event.c)
            try:
                GLOBAL_CONTEXT.trio_token.run_sync_soon(cache.on_preview, event.v)
            except trio.RunFinishedError:
                pass
        elif topic == 'TaskQueue':
            cache = TaskQueueSource(event.c)
            cache.on_event(event)
        elif topic == 'Worker':
            self.on_worker_state(config=event.c, state=event.v)
        elif topic == 'ConfigArg':
            # unified entry of config-save events (viewport dispatch +
            # TaskQueue linkage), replaces the old msgbus broadcast
            config_event.on_config_event(event.c, event.v)
        else:
            logger.warning(f'Unknown topic event: {topic}')


BACKEND_WORKER_MANAGER = BackendWorkerManager()
