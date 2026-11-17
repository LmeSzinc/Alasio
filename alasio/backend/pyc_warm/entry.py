# =============================================================================
# PROCESS BOUNDARY -- module-level imports must stay stdlib-only.
#
# This module is the warmup spawn target: the backend process imports it at
# startup (pyc_warm.manager holds the target here) and the warmup process
# imports it to deserialize the pickled target. Every alasio import here must
# be a local import (inside pyc_warm_entry), or the backend would load the
# warmup runtime just to be able to spawn it.
# =============================================================================

# Warmup process entry point.
#
# The target lives in its own module so the backend keeps a light reference
# to it: alasio.deploy.pyc_warm is the warmup runtime, which the backend has
# no use for until it spawns one.

import sys


def pyc_warm_entry(mod_name, config_name, child_conn, project_root='', mod_root='', path_main=''):
    """
    Warm the bytecode cache of a release and report it to the backend.

    The arguments are the ones of every child the manager spawns, see
    WorkerManager._worker_start_process: a warmup is not a mod of a config,
    so it takes the two names of the run and the project root, and leaves
    the mod folder and the entry file empty.

    The interpreter is the one of the backend, and a spawn child keeps the
    optimization level of its parent, see multiprocessing.spawn: the pyc
    files written here are named for the interpreter the workers run, which
    is the whole point of running the warmup inside this chain.

    A failure is reported, never raised: the warmup is an optimization, an
    environment that can not be warmed runs exactly like an environment that
    was never warmed.

    Args:
        mod_name (str): Name of the entry, the name of the warmup run
        config_name (str): Unused, the warmup has no config
        child_conn (Connection): Pipe connection to the backend process
        project_root (str): Project root, set as env.PROJECT_ROOT, where the
            mods of the release declare themselves
        mod_root (str): Unused, the warmup scans every mod of the root
        path_main (str): Unused, the warmup runs no mod entry file
    """
    from alasio.ext import env

    if project_root:
        env.set_project_root(project_root)

    if sys.dont_write_bytecode:
        # py_compile writes the files whatever this flag says, it is the
        # modules the run imports itself that would be compiled again
        sys.dont_write_bytecode = False

    from alasio.logger import logger

    # The warmup is a background optimization and writes no line of its own.
    # It must above all not open the log file of the release: a spawn child
    # inherits sys.argv from the supervisor, so the file it would derive is
    # the one gui.py is writing at this very moment
    logger.mute(all=True)

    import msgspec

    from alasio.deploy.pyc_warm.runner import warm
    from alasio.deploy.pyc_warm.scope import Scope
    from alasio.logger.utils import stringify_event

    try:
        report = warm(Scope()).to_dict()
    except Exception as e:
        # Reported rather than raised: the parent logs it and moves on, the
        # release runs the way it ran before the warmup existed
        report = {'error': stringify_event(e)}

    try:
        child_conn.send_bytes(msgspec.msgpack.encode(report))
    except (EOFError, OSError):
        # The backend died while the warmup ran: the compilation it did still
        # serves the next start, there is only nobody left to read the report
        pass
    child_conn.close()
