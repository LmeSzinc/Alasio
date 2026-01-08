import trio

from alasio.backend.reactive.base_msgbus import on_msgbus_global_event
from alasio.backend.reactive.base_rpc import rpc
from alasio.backend.reactive.event import ResponseEvent, RpcValueError
from alasio.backend.topic.log import LogCache
from alasio.backend.topic.scan import ConfigScanSource
from alasio.backend.worker.event import ConfigEvent
from alasio.backend.worker.manager import WORKER_STATUS, WorkerManager
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.entry.loader import MOD_LOADER
from alasio.config.entry.mod import Mod
from alasio.ext import env


class BackendWorkerManager(WorkerManager):
    def __init__(self):
        super().__init__()
        self.trio_token = None

    def worker_start(self, mod: Mod, config: str) -> "tuple[bool, str]":
        project_root = env.PROJECT_ROOT
        mod_root = mod.root
        path_main = mod.entry.path_main
        return super().worker_start(
            mod=mod.name, config=config,
            project_root=project_root, mod_root=mod_root, path_main=path_main
        )

    def on_worker_status(self, config: str, status: WORKER_STATUS):
        # Broadcast worker status to msgbus
        trio.from_thread.run(
            BaseTopic.msgbus_global_asend, 'Worker', (config, status),
            trio_token=self.trio_token
        )

    def on_config_event(self, event: ConfigEvent):
        if event.t == 'Log':
            # cache and broadcast log
            cache = LogCache(event.c)
            cache.on_event(event)
        else:
            # broadcast other config events to msgbus
            trio.from_thread.run(
                BaseTopic.msgbus_config_asend, event,
                trio_token=self.trio_token
            )


async def get_worker_manager():
    """
    Get worker manager and inject trio token
    """
    manager = BackendWorkerManager()
    if manager.trio_token is None:
        manager.trio_token = trio.lowlevel.current_trio_token()
    return manager


async def get_mod(config: str):
    """
    Get Mod object from config
    """
    # access cache directly, no rescan
    configs = ConfigScanSource().data
    try:
        info = configs[config]
    except KeyError:
        raise RpcValueError(f'No such config "{config}"')
    try:
        mod = MOD_LOADER.dict_mod[info.mod]
    except KeyError:
        raise RpcValueError(f'No such mod "{info.mod}" from config "{config}"')

    return mod


class Worker(BaseTopic):
    async def getdata(self):
        """
        Returns:
            dict[str, WorkerStateInfo]:
        """
        manager = await get_worker_manager()
        return await trio.to_thread.run_sync(manager.get_state_info)

    @on_msgbus_global_event('Worker')
    async def on_worker_status(self, value):
        # Broadcast worker status to websocket connection
        config, status = value
        if status == 'idle':
            # remove worker state
            event = ResponseEvent(t='Worker', o='del', k=(config,))
        else:
            event = ResponseEvent(t='Worker', o='set', k=(config,), v=status)
        await self.server.send(event)

    @rpc
    async def start(self, config: str):
        """
        Start running a config
        """
        manager = await get_worker_manager()
        mod = await get_mod(config)

        success, msg = await trio.to_thread.run_sync(manager.worker_start, mod, config)
        if not success:
            raise RpcValueError(msg)

    @rpc
    async def scheduler_stop(self, config: str):
        """
        Request to stop scheduler loop
        """
        manager = await get_worker_manager()
        # Validate config/mod existence
        await get_mod(config)

        success, msg = await trio.to_thread.run_sync(manager.worker_scheduler_stop, config)
        if not success:
            raise RpcValueError(msg)

    @rpc
    async def scheduler_continue(self, config: str):
        """
        Request to continue scheduler loop, to cancel previous "scheduler-stopping"
        """
        manager = await get_worker_manager()
        # Validate config/mod existence
        await get_mod(config)

        success, msg = await trio.to_thread.run_sync(manager.worker_scheduler_continue, config)
        if not success:
            raise RpcValueError(msg)

    @rpc
    async def kill(self, config: str):
        """
        Request to kill worker
        """
        manager = await get_worker_manager()
        # Validate config/mod existence
        await get_mod(config)

        success, msg = await trio.to_thread.run_sync(manager.worker_kill, config)
        if not success:
            raise RpcValueError(msg)

    @rpc
    async def force_kill(self, config: str):
        """
        Request to force kill worker
        """
        manager = await get_worker_manager()
        # Validate config/mod existence
        await get_mod(config)

        success, msg = await trio.to_thread.run_sync(manager.worker_force_kill, config)
        if not success:
            raise RpcValueError(msg)
