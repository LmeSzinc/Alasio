# =============================================================================
# PROCESS BOUNDARY -- module-level imports must stay stdlib-only.
#
# This module is the worker spawn target: the backend process imports it at
# startup (worker.manager holds the target here) and the worker process imports
# it to deserialize the pickled target. Every alasio import here must be a
# local import (inside mod_entry), or the backend would load the worker runtime
# (bridge / msgspec) just to be able to spawn a worker.
# =============================================================================

# Worker process entry point.
#
# The spawn target lives in its own module so the backend keeps a light
# reference to it: alasio.backend.worker.bridge is the worker runtime, which
# the backend has no use for until a mod referencing BackendBridge is loaded
# at runtime.

import importlib
import os
import sys


def mod_entry(mod_name, config_name, child_conn, project_root='', mod_root='', path_main=''):
    """
    Run mod scheduler infinitely

    Args:
        mod_name (str): Name of the mod to run, a "WorkerTest*" name runs the
            test mod of tests/backend/worker/worker_mods.py
        config_name (str): Name of the config the scheduler runs
        child_conn (Connection): Pipe connection to the backend process
        project_root (str): Project root, set as env.PROJECT_ROOT in the worker
        mod_root (str): Mod folder, becomes the worker cwd and sys.path[0]
        path_main (str): Import path of the mod entry file, which defines
            class Scheduler
    """
    # local import, see PROCESS BOUNDARY above: this is the worker runtime
    from alasio.backend.worker.bridge import BackendBridge

    # Test mods are defined in tests/backend/worker/worker_mods.py to keep
    # test code out of runtime, import lazily on demand.
    # A test worker is silenced BEFORE the bridge starts: the bridge recv
    # thread logs the command handling, and the parent can send a command as
    # soon as the worker reported "running" (the end of init), so a mute after
    # init would race with the first log line and leave a log file behind.
    is_test_mod = mod_name.startswith('WorkerTest')
    if is_test_mod:
        from tests.backend.worker.worker_mods import WORKER_TEST_MODS, mute_test_worker_logging

        # a test worker must not open a log file in the real log directory
        mute_test_worker_logging()

    BackendBridge().init(mod_name, config_name, child_conn)

    if is_test_mod:
        try:
            worker = WORKER_TEST_MODS[mod_name]
        except KeyError:
            raise KeyError(f'No such mod to run {mod_name}') from None
        worker()
        return

    # if project_root, mod_root, path_main all provided, consider as real mod
    if project_root and mod_root and path_main:
        # local imports, see PROCESS BOUNDARY above: a test mod never needs
        # the path helpers
        from alasio.ext import env
        from alasio.ext.path.calc import to_python_import

        try:
            # This process runs the game script of one mod, so the models are
            # loaded as packages of the mod (see env.PROCESS_ROLE). The worker
            # is spawned, so the default already holds; set it anyway, the
            # role must not depend on the start method of multiprocessing.
            env.PROCESS_ROLE = 'worker'

            # set mod root path
            os.chdir(mod_root)
            sys.path[0] = mod_root

            # set project root path
            env.set_project_root(project_root)

            # import Scheduler
            entry = to_python_import(path_main)
            module = importlib.import_module(entry)
            try:
                cls = module.Scheduler
            except AttributeError:
                raise AttributeError('Module entry file did not define class Scheduler')

            # run mod scheduler
            scheduler = cls(config_name)
            scheduler.run()

        except KeyboardInterrupt:
            pass

    else:
        raise KeyError(f'No such mod to run {mod_name}')
