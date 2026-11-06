"""
End to end tests of the parallel plugin: a real pytest run of a small suite.

The suite is written under `temp/`, its test files are named `check_*.py` and the
`pytest.ini` of the suite sets `python_files` accordingly, so a collection of the
repository never picks them up.

These tests start a pytest subprocess for every case, and the subprocess starts a
pool of its own, so they cost more than the whole unit test file of the plugin and
are only meaningful where the pool actually runs: the CI matrix. Locally they are
skipped, the unit tests of the plugin already cover the logic they exercise. Set
`CI=1` to run them on demand:

    CI=1 pytest tests/testing/test_pytest_parallel_e2e.py
"""
import os
import shutil
import subprocess
import sys

import pytest

from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.testing import pytest_parallel

pytestmark = [
    pytest.mark.skipif(not env.CI, reason='the nested pytest runs are covered by the CI matrix'),
    pytest.mark.parallel,
]

SUITE = env.ALASIO_TEMP.joinpath('test_pytest_parallel_suite')
# The mark as it is written in a test file, composed from the name of the mark so
# this file never declares itself parallel
MARK = 'pytest.mark.' + pytest_parallel.MARK_NAME

RECORD_SOURCE = """\
import os


def record(name):
    folder = os.environ['ALASIO_PARALLEL_LOG']
    worker = os.environ.get('PYTEST_XDIST_WORKER', 'master')
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, worker + '.log'), 'a', encoding='utf-8') as f:
        print(name, file=f)
"""

INI_SOURCE = f"""\
[pytest]
python_files = check_*.py
markers =
    {pytest_parallel.MARK_NAME}: the file is safe to run in parallel with other marked files
"""

SERIAL_SOURCE = """\
from record import record


def test_one():
    record('{name}:one')


def test_two():
    record('{name}:two')
"""

MODULE_SOURCE = """\
import pytest

from record import record

pytestmark = {mark}


def test_one():
    record('{name}:one')


def test_two():
    record('{name}:two')
"""

CLASS_SOURCE = """\
import pytest

from record import record


@{mark}
class TestMarked:

    def test_one(self):
        record('{name}:one')

    def test_two(self):
        record('{name}:two')


def test_unmarked():
    record('{name}:unmarked')
"""

FUNCTION_SOURCE = """\
import pytest

from record import record


@{mark}
def test_marked():
    record('{name}:marked')


def test_unmarked():
    record('{name}:unmarked')
"""

# The tests of the suite, by dispatch group. The serial group holds the unmarked
# tests, including the unmarked tests of the two files that are partly marked.
SERIAL_ORDER = [
    'serial_one:one',
    'serial_one:two',
    'serial_two:one',
    'serial_two:two',
]
GROUPS = {
    'serial': SERIAL_ORDER + ['par_class:unmarked', 'par_function:unmarked'],
    'par_module': ['par_module:one', 'par_module:two'],
    'par_class': ['par_class:one', 'par_class:two'],
    'par_function': ['par_function:marked'],
}
# Collection order of the whole suite, which is the file order of the folder
ALL_TESTS = (
    ['par_class:one', 'par_class:two', 'par_class:unmarked']
    + ['par_function:marked', 'par_function:unmarked']
    + ['par_module:one', 'par_module:two']
    + SERIAL_ORDER
)


def write(root, name, source):
    """
    Write a file of the suite

    Args:
        root (PathStr): Folder of the suite
        name (str): Name of the file
        source (str): Content of the file
    """
    path = root.joinpath(name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(source)


@pytest.fixture(scope='module')
def suite():
    """
    Build the suite of the end to end tests, removed after the module

    Yields:
        PathStr: Folder of the suite
    """
    root = PathStr.new(str(SUITE))
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    write(root, 'pytest.ini', INI_SOURCE)
    write(root, 'record.py', RECORD_SOURCE)
    write(root, 'check_serial_one.py', SERIAL_SOURCE.format(name='serial_one'))
    write(root, 'check_serial_two.py', SERIAL_SOURCE.format(name='serial_two'))
    write(root, 'check_par_module.py', MODULE_SOURCE.format(mark=MARK, name='par_module'))
    write(root, 'check_par_class.py', CLASS_SOURCE.format(mark=MARK, name='par_class'))
    write(root, 'check_par_function.py', FUNCTION_SOURCE.format(mark=MARK, name='par_function'))
    yield root
    shutil.rmtree(root, ignore_errors=True)


def read_logs(folder):
    """
    Read the tests recorded by every worker of the last run

    Args:
        folder (PathStr): Folder of the logs, one file per worker

    Returns:
        dict: Worker name to the list of tests it recorded, in order
    """
    logs = {}
    for name in sorted(os.listdir(folder)):
        with open(folder.joinpath(name), encoding='utf-8') as f:
            logs[name[:-len('.log')]] = [line.strip() for line in f if line.strip()]
    return logs


def run_suite(suite, args=(), selection=('.',)):
    """
    Run pytest on the suite and return the tests recorded by every worker

    Args:
        suite (PathStr): Folder of the suite
        args (Sequence[str]): Extra arguments of the run, after the selection
        selection (Sequence[str]): Positional arguments of the run

    Returns:
        dict: Worker name to the list of tests it recorded, in order
    """
    logs = suite.joinpath('logs')
    shutil.rmtree(logs, ignore_errors=True)
    os.makedirs(logs)
    environment = os.environ.copy()
    environment['PYTHONPATH'] = str(env.ALASIO_ROOT) + os.pathsep + environment.get('PYTHONPATH', '')
    environment['ALASIO_PARALLEL_LOG'] = str(logs)
    for name in (
        'PYTEST_ADDOPTS',
        'PYTEST_CURRENT_TEST',
        'PYTEST_XDIST_WORKER',
        'PYTEST_XDIST_WORKER_COUNT',
        'PYTEST_XDIST_TESTRUNUID',
    ):
        environment.pop(name, None)
    command = [
        sys.executable,
        '-m',
        'pytest',
        *selection,
        '-q',
        '-p',
        'no:cacheprovider',
        '-p',
        'alasio.testing.pytest_parallel',
        *args,
    ]
    completed = subprocess.run(
        command,
        cwd=str(suite),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=300,
    )
    assert completed.returncode == 0, completed.stdout
    return read_logs(logs)


def placement(logs):
    """
    Map every recorded test to the worker that ran it

    Args:
        logs (dict): Worker name to the list of tests it recorded

    Returns:
        dict: Test name to worker name
    """
    found = {}
    for worker, names in logs.items():
        for name in names:
            assert name not in found, f'{name} ran in both {found[name]} and {worker}'
            found[name] = worker
    return found


def assert_grouped(logs):
    """
    Assert the tests ran in the groups of the suite, on the whole pool

    Args:
        logs (dict): Worker name to the list of tests it recorded
    """
    assert sorted(logs) == ['gw0', 'gw1', 'gw2', 'gw3'], f'expected 4 workers, got {sorted(logs)}'
    found = placement(logs)
    assert set(found) == {name for names in GROUPS.values() for name in names}, f'unexpected tests: {sorted(found)}'

    workers = {}
    for group, names in GROUPS.items():
        workers[group] = {found[name] for name in names}
        assert len(workers[group]) == 1, f'{group} was split across {workers[group]}'
    assert len({worker for found_workers in workers.values() for worker in found_workers}) == 4


class TestFixedPool:
    """The pool size is a property of the project, not of the command line."""

    @pytest.mark.parametrize(
        'args',
        [
            # The plugin is loaded with no argument at all
            (),
            # A scheduler of the caller that would otherwise break the groups
            ('-n', '1', '--dist', 'loadfile'),
            ('--dist', 'load'),
            ('-d',),
            # Every way of turning the pool off in xdist
            ('-n', '0'),
            # A pool size of the caller
            ('-n', '12'),
            ('-n', 'auto'),
            ('--maxprocesses', '1'),
        ],
    )
    def test_the_command_line_does_not_change_the_pool(self, suite, args):
        """A run of the marked suite always lands on the same 4 workers and groups."""
        assert_grouped(run_suite(suite, args))

    def test_the_pool_does_not_depend_on_marks(self, suite):
        """A folder run gets the pool even when every remaining test is unmarked."""
        logs = run_suite(suite, args=('-k', 'serial'))
        # Every remaining test shares one group, so exactly one worker executes
        # the run, but which worker of the pool gets it is xdist's decision: the
        # scheduler hands the group to the first worker that reports ready, and
        # the spawn order of the workers is not fixed (a loaded Windows runner
        # can bring up gw1 before gw0). Only "a pool worker, not an in process
        # run" is asserted here.
        assert len(logs) == 1, f'expected a single worker, got {sorted(logs)}'
        worker, names = next(iter(logs.items()))
        assert worker != 'master', 'expected the pool to exist, got an in process run'
        # The order inside a group is the collection order of the running pytest,
        # which differs between pytest versions, so only the set is asserted here
        assert sorted(names) == sorted(SERIAL_ORDER)


class TestWithoutThePlugin:
    """The plugin is the only source of the pool, leaving it out is plain serial pytest."""

    def test_no_plugin_is_serial(self, suite):
        """A run without the plugin executes every test in process."""
        logs = run_suite(suite, args=('-p', 'no:alasio.testing.pytest_parallel'))
        assert sorted(logs) == ['master'], f'expected an in process run, got {sorted(logs)}'
        assert sorted(logs['master']) == sorted(ALL_TESTS)


class TestNarrowSelection:
    """A run of one file, one class or one test does not pay for a pool."""

    @pytest.mark.parametrize(
        'selection',
        [
            # One module
            ('check_par_module.py',),
            # One class of a module
            ('check_par_class.py::TestMarked',),
            # One test of a module
            ('check_par_function.py::test_marked',),
            # A narrow argument beside a folder keeps the whole run in process
            ('.', 'check_par_module.py'),
        ],
    )
    def test_a_narrow_selection_runs_in_process(self, suite, selection):
        """The tests run in the main process, even when the file is marked."""
        logs = run_suite(suite, selection=selection)
        assert sorted(logs) == ['master'], f'expected an in process run, got {sorted(logs)}'

    @pytest.mark.parametrize('selection', [('check_par_module.py',), ('check_par_class.py::TestMarked',)])
    def test_a_narrow_selection_ignores_the_pool_options(self, suite, selection):
        """The -n of the caller does not bring the pool back."""
        logs = run_suite(suite, args=('-n', '12', '--dist', 'loadgroup'), selection=selection)
        assert sorted(logs) == ['master'], f'expected an in process run, got {sorted(logs)}'

    def test_several_files_stay_in_process(self, suite):
        """Only folders are a tree run, a list of files is not."""
        logs = run_suite(suite, selection=('check_par_module.py', 'check_par_function.py'))
        assert sorted(logs) == ['master'], f'expected an in process run, got {sorted(logs)}'

    def test_the_whole_folder_on_the_pool(self, suite):
        """The suite folder is a tree run, and it gets the pool."""
        assert_grouped(run_suite(suite))


class TestGuards:
    """Runs that must not build a pool of their own."""

    def test_collect_only(self, suite):
        """A collection executes no test, so there is nothing to parallelize."""
        assert run_suite(suite, args=('--collect-only',)) == {}

    def test_pdb(self, suite):
        """xdist turns the pool off under --pdb, a debugger needs one process."""
        logs = run_suite(suite, args=('--pdb',))
        assert sorted(logs) == ['master'], f'expected an in process run, got {sorted(logs)}'

    def test_without_xdist(self, suite):
        """Without xdist there is no pool to size, and nothing to break."""
        logs = run_suite(suite, args=('-p', 'no:xdist'))
        assert sorted(logs) == ['master'], f'expected an in process run, got {sorted(logs)}'
