import trio

from alasio.backend.reactive.base_msgbus import on_msgbus_global_event
from alasio.backend.reactive.base_rpc import rpc
from alasio.backend.reactive.event import ResponseEvent, RpcValueError
from alasio.backend.topic._worker import BACKEND_WORKER_MANAGER
from alasio.backend.topic.scan import ConfigScanSource
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.entry.loader import MOD_LOADER


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
            dict[str, WORKER_STATE]: key: config name, value: worker state
        """
        return await trio.to_thread.run_sync(BACKEND_WORKER_MANAGER.get_state_info)

    @on_msgbus_global_event('Worker')
    async def on_worker_state(self, value):
        # Broadcast worker state to websocket connection
        config, state = value
        if state == 'idle':
            # remove worker state
            event = ResponseEvent(t='Worker', o='del', k=(config,))
        else:
            event = ResponseEvent(t='Worker', o='set', k=(config,), v=state)
        await self.server.send(event)

    @rpc
    async def start(self, config: str):
        """
        Start running a config
        """
        mod = await get_mod(config)

        success, msg = await trio.to_thread.run_sync(BACKEND_WORKER_MANAGER.worker_start, mod, config)
        if not success:
            raise RpcValueError(msg)

    @rpc
    async def scheduler_stop(self, config: str):
        """
        Request to stop scheduler loop
        """
        # Validate config/mod existence
        await get_mod(config)

        success, msg = await trio.to_thread.run_sync(BACKEND_WORKER_MANAGER.worker_scheduler_stop, config)
        if not success:
            raise RpcValueError(msg)

    @rpc
    async def scheduler_continue(self, config: str):
        """
        Request to continue scheduler loop, to cancel previous "scheduler-stopping"
        """
        # Validate config/mod existence
        await get_mod(config)

        success, msg = await trio.to_thread.run_sync(BACKEND_WORKER_MANAGER.worker_scheduler_continue, config)
        if not success:
            raise RpcValueError(msg)

    @rpc
    async def kill(self, config: str):
        """
        Request to kill worker
        """
        # Validate config/mod existence
        await get_mod(config)

        success, msg = await trio.to_thread.run_sync(BACKEND_WORKER_MANAGER.worker_kill, config)
        if not success:
            raise RpcValueError(msg)

    @rpc
    async def force_kill(self, config: str):
        """
        Request to force kill worker
        """
        # Validate config/mod existence
        await get_mod(config)

        success, msg = await trio.to_thread.run_sync(BACKEND_WORKER_MANAGER.worker_force_kill, config)
        if not success:
            raise RpcValueError(msg)
