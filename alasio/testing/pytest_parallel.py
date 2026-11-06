"""
Run the marked test files in parallel, keep everything else serial.

A test module declares itself independent of the other tests with a module level
mark: assigning the `parallel` mark of the project to `pytestmark`. The mark may
sit on a class or on a single test function as well, the nearest marked level
decides, so a slow test of an otherwise serial module is moved out of the module
group on its own.

Everything unmarked shares one group, and a group is the dispatch unit of
`--dist loadgroup`, so the unmarked tests are always held by one worker and
executed in collection order, exactly like a plain serial run. Each marked file
gets its own group, so its items stay in one worker as well, and the marked
files run beside each other.

The pool is a fixed number of workers (`MAX_WORKERS`), whatever the command line
says about `-n`: the size is a property of the project, because the smallest
machine that runs the suite is a 4 core github runner, and the number of cores is
not a thing the caller of pytest knows better.

The pool is built only for a run of a folder. A run of one file, one class or
one test has nothing to spread over the pool, and it would pay the report hop of
xdist for every test, so it stays in process. A run that wants no pool at all
leaves this plugin out, which is plain serial pytest:

    pytest -p no:alasio.testing.pytest_parallel

Whether a file is marked is a decision of its author, so nothing is scanned or
guessed here: the marks of the collected items are the whole input of the
dispatch.
"""
import dataclasses
import os
import re

import pytest

# Workers of the pool, see the module docstring
MAX_WORKERS = 4
# The mark declared in pyproject.toml
MARK_NAME = 'parallel'
# Group of every unmarked test, all of them are executed in one worker
SERIAL_GROUP = 'serial'
# Prefix of the group of a marked file, the rest of the name is the file path
PARALLEL_PREFIX = 'par:'
# Options of xdist that carry the pool size or the scheduler, removed from the
# command line of the workers, see normalize_args()
POOL_OPTIONS = ('-n', '--numprocesses', '--maxprocesses', '--dist')
# Shortcut of xdist for `--dist load`, a switch without a value
POOL_SWITCHES = ('-d',)
# Attached form of the short option of the worker count, e.g. `-n4` and `-nauto`
ATTACHED_COUNT = re.compile(r'-n(?:\d+|auto|logical)$')


def group_of(item):
    """
    Dispatch group of a test item: one group per marked file, one for the rest.

    Args:
        item (pytest.Item): Collected test item

    Returns:
        str: Name of the xdist group of the item
    """
    if item.get_closest_marker(MARK_NAME) is None:
        return SERIAL_GROUP
    return PARALLEL_PREFIX + str(item.path)


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """
    Assign every item to the group of its file.

    xdist reads the group marks in its own collection hook, which is registered
    after this plugin but called earlier, so the marks have to be added with
    `tryfirst=True`.
    """
    if not config.pluginmanager.hasplugin('xdist'):
        return
    for item in items:
        item.add_marker(pytest.mark.xdist_group(name=group_of(item)))


@pytest.hookimpl(tryfirst=True)
def pytest_collection_finish(session):
    """
    Make the group of xdist readable on pytest 9, which no longer reads `_nodeid`.

    xdist appends the group to `item._nodeid` on the worker. pytest 8 renders the
    nodeid from that attribute, pytest 9 renders it from `item._id` (a frozen
    `NodeId` dataclass), so on pytest 9 the suffix is written and never read, and
    `--dist loadgroup` degrades to a plain load: the serial group gets split
    across workers. Rebuilding `_id` here puts the suffix back in the only place
    pytest 9 reads, without patching xdist.

    This hook runs after every `pytest_collection_modifyitems`, and with
    `tryfirst=True` it runs before the `pytest_collection_finish` of xdist, which
    is the hook that reports the node ids to the controller.
    """
    config = session.config
    if not config.pluginmanager.hasplugin('xdist'):
        return
    for item in session.items:
        suffixed = getattr(item, '_nodeid', None)
        if suffixed is None or suffixed == item.nodeid:
            # pytest 8: xdist wrote the suffix where nodeid already reads it
            continue
        node_id = getattr(item, '_id', None)
        if node_id is None:
            continue
        # pytest 9: rebuild the structured node id, which keeps the path part
        item._id = type(node_id).parse(suffixed)


def selects_folders(config):
    """
    Check whether the run is a whole tree instead of a single test or module.

    A run of one file, one module or one node id has nothing to parallelize:
    its items all belong to the same file, so the pool would only add the report
    hop of xdist to every test. Those runs stay in process.

    The positional arguments are used as they are: pytest resolves a run with no
    argument to the invocation folder before the plugins are configured, so a
    bare `pytest` is a folder run as well.

    Args:
        config (pytest.Config): Pytest config

    Returns:
        bool: True when every positional argument is an existing folder
    """
    if not config.args:
        return False
    for arg in config.args:
        path, sep, _ = arg.partition('::')
        if sep:
            # A node id picks a single test or class
            return False
        if not os.path.isdir(path):
            # A file picks a single module
            return False
    return True


def normalize_args(args):
    """
    Remove the pool options from a command line and pin the scheduler.

    A worker re-parses the command line on its own, and it decides from its own
    `--dist` whether it writes the group name into the nodeid (see the
    collection hook of xdist). A worker launched with `--dist loadfile` would
    drop the group, and the controller would then split the serial group across
    workers, which is exactly what the groups exist to prevent. So the options
    must not reach the workers at all.

    Args:
        args (Sequence[str]): Command line arguments of the controller

    Returns:
        list: Arguments without the pool options, ending with --dist=loadgroup
    """
    output = []
    args = list(args)
    index = 0
    while index < len(args):
        arg = args[index]
        index += 1
        name, equal, _ = arg.partition('=')
        if name in POOL_OPTIONS:
            # The value of the option is the next argument when it is separated
            if not equal and index < len(args) and not args[index].startswith('-'):
                index += 1
            continue
        if name in POOL_SWITCHES:
            continue
        # Attached form of the short option, e.g. `-n4`
        if ATTACHED_COUNT.match(arg):
            continue
        output.append(arg)
    output.append('--dist=loadgroup')
    return output


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    """
    Own the pool size and the scheduler of the run.

    xdist resolves the command line in `pytest_cmdline_main`, but it builds its
    session in `pytest_configure` with `trylast=True`, so what is written here
    is what the session reads, whatever the command line asked for.
    """
    if not config.pluginmanager.hasplugin('xdist'):
        return
    if hasattr(config, 'workerinput') or os.environ.get('PYTEST_XDIST_WORKER'):
        # Already inside a pool, a worker must not build another one
        return
    if config.getoption('usepdb', False):
        # xdist turns the pool off under --pdb, a debugger needs one process
        return

    # A collection run executes no test, and a run of one file or one node id
    # has nothing to spread over a pool
    if config.getoption('collectonly', False) or not selects_folders(config):
        workers = 0
    else:
        workers = MAX_WORKERS
    config.option.numprocesses = workers
    config.option.dist = 'loadgroup' if workers else 'no'
    config.option.tx = ['popen'] * workers

    args = normalize_args(config.invocation_params.args)
    if tuple(args) != tuple(config.invocation_params.args):
        config.invocation_params = dataclasses.replace(config.invocation_params, args=tuple(args))
