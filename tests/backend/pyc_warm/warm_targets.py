"""
Spawn targets of the tests of the warmup manager.

They live in their own module because multiprocessing spawn pickles a target
by its qualified name: the child re-imports the module, so the target has to
be importable from a module of the project, not a closure of a test.

Every target takes the six arguments the manager hands to every child, see
WorkerManager._spawn_process.
"""
import time

# What a finished warmup answers: two units that compiled, one that could not,
# and one the scope asked for and did not find
REPORT = {
    'results': [
        {
            'tier': 'pack', 'target': 'trio', 'sources': 5, 'stale': 2,
            'compiled': 2, 'failed': 0, 'seconds': 0.01, 'errors': [],
        },
        {
            'tier': 'mod', 'target': 'alas', 'sources': 10, 'stale': 10,
            'compiled': 9, 'failed': 1, 'seconds': 0.4,
            'errors': ['module/main.py: OSError: file is locked'],
        },
        {
            'tier': 'common', 'target': 'scipy', 'sources': 0, 'stale': 0,
            'compiled': 0, 'failed': 0, 'seconds': 0.0, 'errors': [],
        },
    ],
    'unresolved': ['scipy'],
    'compiled': 11,
    'failed': 1,
    'stale': 12,
    'seconds': 0.5,
}

# What a warmup that raised answers
ERROR_REPORT = {'error': 'RuntimeError: the scope could not be read'}


def report_target(mod_name, config_name, child_conn, project_root='', mod_root='', path_main=''):
    """
    Send a report and exit, the shape of a warmup that finished.

    Args:
        mod_name (str): Name of the entry
        config_name (str): Name of the entry
        child_conn (Connection): Pipe connection to the parent
        project_root (str): Project root the manager passed
        mod_root (str): Unused
        path_main (str): Unused
    """
    import msgspec

    child_conn.send_bytes(msgspec.msgpack.encode(REPORT))
    child_conn.close()


def error_target(mod_name, config_name, child_conn, project_root='', mod_root='', path_main=''):
    """
    Answer the report of a run that raised before it could do anything.

    Args:
        mod_name (str): Unused
        config_name (str): Unused
        child_conn (Connection): Pipe connection to the parent
        project_root (str): Unused
        mod_root (str): Unused
        path_main (str): Unused
    """
    import msgspec

    child_conn.send_bytes(msgspec.msgpack.encode(ERROR_REPORT))
    child_conn.close()


def garbage_target(mod_name, config_name, child_conn, project_root='', mod_root='', path_main=''):
    """
    Answer bytes that are not a msgpack report.

    Args:
        mod_name (str): Unused
        config_name (str): Unused
        child_conn (Connection): Pipe connection to the parent
        project_root (str): Unused
        mod_root (str): Unused
        path_main (str): Unused
    """
    child_conn.send_bytes(b'not a report')
    child_conn.close()


def silent_target(mod_name, config_name, child_conn, project_root='', mod_root='', path_main=''):
    """
    Say nothing and stay alive, the shape of a warmup that is still running.

    Args:
        mod_name (str): Unused
        config_name (str): Unused
        child_conn (Connection): Pipe connection to the parent
        project_root (str): Unused
        mod_root (str): Unused
        path_main (str): Unused
    """
    try:
        time.sleep(60)
    finally:
        child_conn.close()


def failing_target(mod_name, config_name, child_conn, project_root='', mod_root='', path_main=''):
    """
    Send a report and leave with a non-zero code, a warmup that crashed.

    Args:
        mod_name (str): Unused
        config_name (str): Unused
        child_conn (Connection): Pipe connection to the parent
        project_root (str): Unused
        mod_root (str): Unused
        path_main (str): Unused
    """
    import os

    import msgspec

    child_conn.send_bytes(msgspec.msgpack.encode(REPORT))
    child_conn.close()
    os._exit(1)
