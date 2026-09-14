import trio

from alasio.backend.reactive.base_rpc import rpc
from alasio.backend.reactive.event import RpcValueError
from alasio.backend.topic._worker import BACKEND_WORKER_MANAGER, WorkerSource
from alasio.backend.topic.scan import ConfigScanSource
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.entry.loader import MOD_LOADER


def get_mod(config: str):
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
    TOPIC_NAME = 'Worker'

    async def get_source(self):
        """
        Worker states flow entirely through events
        (WorkerManager.on_worker_state -> WorkerSource): no reinit needed.
        """
        return WorkerSource()

    @rpc
    async def start(self, config: str):
        """
        Start running a config
        """
        mod = get_mod(config)

        success, msg = await trio.to_thread.run_sync(BACKEND_WORKER_MANAGER.worker_start, mod, config)
        if not success:
            raise RpcValueError(msg)

    @rpc
    async def scheduler_stop(self, config: str, restart_resume: bool = False):
        """
        Request to stop scheduler loop

        Args:
            config (str): Config name
            restart_resume (bool): Only meaningful for a worker already parked
                by a graceful restart: True keeps the pending resume, False
                (default) cancels it
        """
        # Validate config/mod existence
        get_mod(config)

        success, msg = await trio.to_thread.run_sync(
            BACKEND_WORKER_MANAGER.worker_scheduler_stop, config, restart_resume)
        if not success:
            raise RpcValueError(msg)

    @rpc
    async def scheduler_continue(self, config: str):
        """
        Request to continue scheduler loop, to cancel previous "scheduler-stopping"
        """
        # Validate config/mod existence
        get_mod(config)

        success, msg = await trio.to_thread.run_sync(BACKEND_WORKER_MANAGER.worker_scheduler_continue, config)
        if not success:
            raise RpcValueError(msg)

    @rpc
    async def kill(self, config: str, restart_resume: bool = False):
        """
        Request to kill worker

        Args:
            config (str): Config name
            restart_resume (bool): During a graceful restart wait, True stops
                the worker but keeps the pending resume (it still resumes after
                the backend restart), False (default) stops it for good
        """
        # Validate config/mod existence
        get_mod(config)

        success, msg = await trio.to_thread.run_sync(
            BACKEND_WORKER_MANAGER.worker_kill, config, restart_resume)
        if not success:
            raise RpcValueError(msg)

    @rpc
    async def force_kill(self, config: str, restart_resume: bool = False):
        """
        Request to force kill worker

        Args:
            config (str): Config name
            restart_resume (bool): See kill()
        """
        # Validate config/mod existence
        get_mod(config)

        success, msg = await trio.to_thread.run_sync(
            BACKEND_WORKER_MANAGER.worker_force_kill, config, restart_resume)
        if not success:
            raise RpcValueError(msg)
