import msgspec
import trio

from alasio.backend.worker.manager import WORKER_STATUS, WorkerManager
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.entry.loader import MOD_LOADER
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


class Worker(BaseTopic):
    @classmethod
    def _get_state_info(self):
        manager = BackendWorkerManager()
        return manager.get_state_info()

    async def getdata(self):
        """
        Returns:
            dict[str, WorkerStateInfo]:
        """
        return trio.to_thread.run_sync(self._get_state_info)


if __name__ == '__main__':
    self = BackendWorkerManager()
    mod = MOD_LOADER.dict_mod['example_mod']
    self.worker_start(mod, 'alas')
    import time
    time.sleep(10)
