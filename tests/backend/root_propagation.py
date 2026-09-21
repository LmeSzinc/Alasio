"""
Supervisor-side script for root propagation tests.

Started by tests/backend/test_root_propagation.py from a foreign cwd (the
tests/backend folder) with --root <project root>. The backend entry calls the
REAL create_config (production code path: env.set_project_root + os.chdir),
prints its cwd, PROJECT_ROOT and the root the mod loader got bound to, then
spawns a real mod worker which prints its own cwd and PROJECT_ROOT. All prints
go to the process stdout, which the test collects (spawn children inherit the
stdio chain).

The module-level code stays light and is re-executed by every spawn child
(backend / worker) as __mp_main__, which is what makes the log mute below
reach all of them.
"""
import builtins
import multiprocessing
import os

from alasio.backend.supervisor import Supervisor
from alasio.backend.worker.bridge import mod_entry
from alasio.logger import logger

# Mod the worker runs: the fixed fixture folder next to this script with its
# entry file, so the test writes nothing at run time. path_main is relative to
# mod_root, like a real mod entry.
MOD_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'root_propagation_mod')
PATH_MAIN = 'main.py'

# Every process of this chain is test-only, mute the log file target at the
# module level, which each spawn child (backend / worker) re-executes: the
# backend logs its 'Start' banner (create_config) BEFORE it sets PROJECT_ROOT,
# and at that moment the log folder comes from the cwd, which is the test
# directory (the chain is started from a foreign cwd on purpose). Unmuted, the
# chain would leave a log folder next to the test. Same as the other test-only
# processes, see supervisor/backends.py and worker_mods.mute_test_worker_logging.
logger.mute(fd=True)


class RootPropagationSupervisor(Supervisor):
    @staticmethod
    def backend_entry(args):
        # production code: the server layer parses --root, sets PROJECT_ROOT
        # and chdirs. The app chain may only be imported after that: the
        # loader (MOD_LOADER = ModLoader(env.PROJECT_ROOT)) binds PROJECT_ROOT
        # at import time and an earlier import would freeze an empty root.
        from alasio.backend.app.asgi import create_config
        from alasio.ext import env

        create_config(args)
        print(f'BACKEND_CWD={os.getcwd()}', flush=True)
        print(f'BACKEND_ROOT={env.PROJECT_ROOT}', flush=True)

        from alasio.config.entry.loader import MOD_LOADER
        print(f'LOADER_ROOT={MOD_LOADER.root}', flush=True)

        # spawn a real mod worker (mod_entry real-mod branch: chdir to
        # mod_root, set_project_root(project_root), import entry, run)
        print(f'MOD_ROOT={MOD_ROOT}', flush=True)

        ctx = multiprocessing.get_context('spawn')
        parent_conn, child_conn = ctx.Pipe()
        proc = ctx.Process(
            target=mod_entry,
            args=('RootTestMod', 'root_test', child_conn, str(env.PROJECT_ROOT), MOD_ROOT, PATH_MAIN),
            name='root-test-worker',
            daemon=True,
        )
        proc.start()
        proc.join(timeout=10)
        child_conn.close()
        parent_conn.close()

        # ask the supervisor to shut down cleanly instead of restarting
        builtins.__mpipe_conn__.send_bytes(b'command:stop')


if __name__ == '__main__':
    supervisor = RootPropagationSupervisor()
    supervisor.run()
