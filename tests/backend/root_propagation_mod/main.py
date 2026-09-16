"""
Throwaway worker mod of the root propagation test.

tests/backend/root_propagation.py spawns this file as the scheduler of a REAL
mod worker (bridge.mod_entry real-mod branch: chdir to mod_root,
set_project_root(project_root), import path_main): mod_root is the folder this
file lives in and path_main is 'main.py' -- the shape of a real mod entry,
which is resolved relative to its mod root.

The scheduler prints the worker cwd and PROJECT_ROOT, then returns
immediately: the worker process exits as soon as the two markers are out. The
mod_root and path_main are fixed files, nothing is written at test time.
"""
import os

from alasio.ext import env


class Scheduler:
    """
    Prints the worker cwd and PROJECT_ROOT, then finishes.
    """

    def __init__(self, config_name):
        print(f'WORKER_CWD={os.getcwd()}', flush=True)
        print(f'WORKER_ROOT={env.PROJECT_ROOT}', flush=True)

    def run(self):
        pass
