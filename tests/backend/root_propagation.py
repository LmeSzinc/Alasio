"""
Supervisor-side script for root propagation tests.

Started by tests/backend/test_root_propagation.py from a foreign cwd with
--root <project root>. The backend entry calls the REAL create_config
(production code path: env.set_project_root + os.chdir), prints its cwd,
PROJECT_ROOT and the root the mod loader got bound to, then spawns a real
mod worker which prints its own cwd and PROJECT_ROOT. All prints go to the
process stdout, which the test collects (spawn children inherit the stdio
chain).

The module-level code stays light: it is re-executed by every spawn child
(backend / worker) as __mp_main__.
"""
import builtins
import multiprocessing
import os
import tempfile

from alasio.backend.supervisor import Supervisor
from alasio.backend.worker.bridge import mod_entry

# Entry module of the throwaway worker mod: prints the worker's cwd and
# PROJECT_ROOT, then returns immediately so the worker process exits.
ENTRY_CODE = '''\
import os

from alasio.ext import env


class Scheduler:
    def __init__(self, config_name):
        print(f"WORKER_CWD={os.getcwd()}", flush=True)
        print(f"WORKER_ROOT={env.PROJECT_ROOT}", flush=True)

    def run(self):
        pass
'''


class RootPropagationSupervisor(Supervisor):
    @staticmethod
    def backend_entry(args):
        # production code: the server layer parses --root, sets PROJECT_ROOT
        # and chdirs. The app chain may only be imported after that: the
        # loader (MOD_LOADER = ModLoader(env.PROJECT_ROOT)) binds PROJECT_ROOT
        # at import time and an earlier import would freeze an empty root.
        from alasio.backend.asgi import create_config
        from alasio.ext import env

        create_config(args)
        print(f'BACKEND_CWD={os.getcwd()}', flush=True)
        print(f'BACKEND_ROOT={env.PROJECT_ROOT}', flush=True)

        from alasio.config.entry.loader import MOD_LOADER
        print(f'LOADER_ROOT={MOD_LOADER.root}', flush=True)

        # spawn a real mod worker (mod_entry real-mod branch: chdir to
        # mod_root, set_project_root(project_root), import entry, run)
        # path_main is relative to mod_root, like real mod entries
        mod_root = tempfile.mkdtemp(prefix='alasio_root_test_mod_')
        path_main = 'main.py'
        with open(os.path.join(mod_root, path_main), 'w', encoding='utf-8') as f:
            f.write(ENTRY_CODE)
        print(f'MOD_ROOT={mod_root}', flush=True)

        ctx = multiprocessing.get_context('spawn')
        parent_conn, child_conn = ctx.Pipe()
        proc = ctx.Process(
            target=mod_entry,
            args=('RootTestMod', 'root_test', child_conn, str(env.PROJECT_ROOT), mod_root, path_main),
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
