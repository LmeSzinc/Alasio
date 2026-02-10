import trio

from alasio.backend.topic.log import LogCache
from alasio.backend.topic.preview import PreviewTask
from alasio.backend.topic.que import TaskQueueSource
from alasio.backend.worker.event import ConfigEvent
from alasio.backend.worker.manager import WORKER_STATUS, WorkerManager
from alasio.backend.ws.context import GLOBAL_CONTEXT
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.entry.mod import Mod
from alasio.ext import env


class BackendWorkerManager(WorkerManager):
    def worker_start(self, mod: Mod, config: str) -> "tuple[bool, str]":
        project_root = env.PROJECT_ROOT
        mod_root = mod.root
        path_main = mod.entry.path_main
        return super().worker_start(
            mod=mod.name, config=config,
            project_root=project_root, mod_root=mod_root, path_main=path_main
        )

    async def _on_worker_status(self, config: str, status: WORKER_STATUS):
        # Broadcast worker status to msgbus
        await BaseTopic.msgbus_global_asend('Worker', (config, status))
        # notify preview
        cache = PreviewTask(config)
        cache.on_worker_status(status)

    def on_worker_status(self, config: str, status: WORKER_STATUS):
        try:
            trio.from_thread.run(
                self._on_worker_status, config, status,
                trio_token=GLOBAL_CONTEXT.trio_token
            )
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
            cache.on_event(event, GLOBAL_CONTEXT.trio_token)
        else:
            # broadcast other config events to msgbus
            try:
                trio.from_thread.run(
                    BaseTopic.msgbus_config_asend, event,
                    trio_token=GLOBAL_CONTEXT.trio_token
                )
            except trio.RunFinishedError:
                pass


BACKEND_WORKER_MANAGER = BackendWorkerManager()
