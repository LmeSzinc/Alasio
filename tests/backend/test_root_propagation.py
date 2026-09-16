"""
Root propagation tests.

The project root set by the supervisor (gui.py --root, computed with stdlib
from the entry file) must reach every process of the chain:

- backend: create_config sets env.PROJECT_ROOT and os.chdir() to the root,
  so both PROJECT_ROOT and cwd equal the supervisor-provided root even when
  the chain was started from a foreign cwd
- loader: MOD_LOADER is bound to env.PROJECT_ROOT at import time
  (MOD_LOADER = ModLoader(env.PROJECT_ROOT)), so the import chain that
  reaches config/entry/loader must only run after create_config(). An
  earlier import freezes the root to '', the mods that resolve their own
  root through it get an empty one and workers are spawned with an empty
  mod_root (see asgi.py)
- worker: PROJECT_ROOT propagates through the spawn args (mod_entry calls
  env.set_project_root(project_root)); the worker cwd is the mod_root by
  design (mod_entry chdirs to the mod folder as the mod's relative-path
  base), while the worker's initial cwd inherits the backend cwd (= root)

The test launches tests/backend/root_propagation.py from a temporary
foreign cwd and asserts on the stdout markers printed by the backend and
the worker processes.
"""
import os
import subprocess
import sys
import tempfile
import time

import pytest

from alasio.ext.env import ALASIO_ROOT

# Project root, the value passed as --root (also the repo layout)
ROOT = ALASIO_ROOT
SCRIPT = ALASIO_ROOT.joinpath('tests/backend/root_propagation.py')

# generous window: the chain imports starlette / trio / hypercorn
START_TIMEOUT = 30
# backend prints markers, spawns a worker, then asks the supervisor to stop
EXIT_TIMEOUT = 15


def _run_chain():
    """
    Start the supervisor script from a foreign cwd with --root <ROOT> and
    collect its stdout until exit.

    Returns:
        str: Full stdout of the whole process chain
    """
    foreign_cwd = tempfile.mkdtemp(prefix='alasio_root_test_cwd_')
    env = os.environ.copy()
    existing = env.get('PYTHONPATH')
    # the launcher computes the root with stdlib, i.e. with the platform separator
    root = os.path.normpath(ROOT)
    env['PYTHONPATH'] = root + (os.pathsep + existing if existing else '')

    proc = subprocess.Popen(
        [sys.executable, SCRIPT, '--root', root],
        cwd=foreign_cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True,
        bufsize=1,
    )

    output = ''
    deadline = time.time() + START_TIMEOUT
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        output += line
        if 'WORKER_ROOT=' in output:
            # markers complete, the backend asks the supervisor to stop now
            break

    try:
        proc.wait(timeout=EXIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    return output


def _marker(output, name):
    """
    Extract the value of a `NAME=value` marker from the output.

    Args:
        output (str): Collected chain stdout
        name (str): Marker name

    Returns:
        str | None: The marker value, or None when missing
    """
    for line in output.splitlines():
        if line.startswith(f'{name}='):
            return line.partition('=')[2]
    return None


def _norm(value):
    """
    Normalize path separators for comparison: PROJECT_ROOT is a PathStr
    using forward slashes while os.getcwd() uses the platform separator.

    Args:
        value (str | None):

    Returns:
        str | None:
    """
    if value is None:
        return None
    return os.path.normpath(value)


@pytest.fixture(scope='module')
def chain_output():
    """
    Output of the whole chain, started once for the three tests on it

    The chain is a real supervisor -> backend -> worker startup (~1.4s) and
    every test asserts on a different marker of the same run: running it once
    per test only pays the startup cost three times.
    """
    return _run_chain()


class TestRootPropagation:
    """
    --root set by the supervisor must reach backend, loader and worker, and
    the backend cwd must follow the root (chdir) even from a foreign cwd.
    """

    def test_backend_cwd_and_project_root_equal_root(self, chain_output):
        assert _norm(_marker(chain_output, 'BACKEND_CWD')) == _norm(ROOT), chain_output
        assert _norm(_marker(chain_output, 'BACKEND_ROOT')) == _norm(ROOT), chain_output

    def test_loader_root_equal_root(self, chain_output):
        """
        The mod loader binds env.PROJECT_ROOT when it is imported: importing
        it before create_config() freezes the root to '' (and every mod that
        resolves its own root through it), which makes worker_start() spawn
        workers with an empty mod_root.
        """
        assert _norm(_marker(chain_output, 'LOADER_ROOT')) == _norm(ROOT), chain_output

    def test_worker_project_root_propagates(self, chain_output):
        assert _norm(_marker(chain_output, 'WORKER_ROOT')) == _norm(ROOT), chain_output

    def test_worker_cwd_is_mod_root(self, chain_output):
        """
        The worker chdirs to the mod folder (mod_entry, the mod's
        relative-path base); the mod_root marker must match the worker cwd.
        """
        mod_root = _marker(chain_output, 'MOD_ROOT')
        assert mod_root is not None, chain_output
        assert _marker(chain_output, 'WORKER_CWD') == mod_root, chain_output
