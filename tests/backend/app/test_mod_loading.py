"""
Mod loading tests.

MOD_LOADER binds env.PROJECT_ROOT when it is imported
(MOD_LOADER = ModLoader(env.PROJECT_ROOT)), so the backend entry
(entry.backend_entry -> asgi.run) may only import the app chain after
create_config() set the project root (see alasio/backend/app/asgi.py).

These tests run the entry startup order in a fresh interpreter
(tests/backend/app/mod_loading.py, started from a foreign cwd with
--root <project root>) and assert that the repo mod ExampleMod is loaded from
the resolved root, in both project layouts:

- repo_root: the repo is the project root, ExampleMod is the mod registered
  under it (DICT_MOD_ENTRY, root="ExampleMod");
- example_mod_root: ExampleMod is the project root, so it declares itself the
  mod (<root>/module/config/const.py, loaded by ModLoader.self_mod) -- the
  layout a real mod repo has, and the layout the empty mod root bug was
  reported with: the mod root came out '', worker_start() was handed an empty
  mod_root and the worker died with KeyError('No such mod to run ...').

The probe also builds the app (create_app()): ExampleMod has no frontend/build
of its own, so the SPA mount logs and skips in the example_mod_root case (the
frontend build belongs to the project root); the mod assets mount is asserted
in both.
"""
import json
import os
import subprocess
import sys

import pytest

from alasio.ext.env import ALASIO_ROOT

# the mod under test: <repo>/ExampleMod, registered as DICT_MOD_ENTRY "example_mod"
MOD_NAME = 'example_mod'
MOD_ROOT = ALASIO_ROOT.joinpath('ExampleMod')
# the test folder: a cwd that is not the project root, so the probe must
# resolve everything through --root (no temp folder is created per run)
APP_TEST_DIR = ALASIO_ROOT.joinpath('tests/backend/app')
SCRIPT = APP_TEST_DIR.joinpath('mod_loading.py')

# project layouts under test, see the module docstring
CASE_ROOTS = {
    'repo_root': ALASIO_ROOT,
    'example_mod_root': MOD_ROOT,
}

# generous window: the probe imports starlette / trio / hypercorn and builds the app
PROBE_TIMEOUT = 60

# content of ExampleMod/module/config/_index (tracked fixture files)
MOD_NAV_NAMES = ['alas', 'gems', 'general', 'main', 'opsi']
MOD_CONFIG_NAMES = ['alas', 'dashboard', 'gems', 'general', 'main', 'opsi']
MOD_QUEUE_TASKS = ['GemsFarming', 'Main', 'OpsiAshAssist', 'OpsiDaily', 'OpsiExplore', 'RestartDevice', 'RestartGame']
MOD_TASK_COUNT = 13


def _norm(path):
    """
    Normalize a path for comparison: paths reported by the probe are PathStr
    (forward slashes) while the test builds them with os.path (platform
    separator).

    Args:
        path (str | None):

    Returns:
        str | None:
    """
    if path is None:
        return None
    return os.path.normcase(os.path.normpath(path))


def _probe(root):
    """
    Run the mod loading probe in a fresh interpreter and return its report.

    Args:
        root: Project root to pass as --root

    Returns:
        dict: Parsed MOD_REPORT of the probe
    """
    repo = os.path.normpath(ALASIO_ROOT)
    env = os.environ.copy()
    existing = env.get('PYTHONPATH')
    # the probe must import the repo copy of alasio, not an installed one
    env['PYTHONPATH'] = repo + (os.pathsep + existing if existing else '')
    # foreign cwd: the mod must resolve through PROJECT_ROOT, not through the cwd
    workdir = APP_TEST_DIR

    proc = subprocess.run(
        [sys.executable, os.path.normpath(SCRIPT), '--root', os.path.normpath(root)],
        cwd=workdir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        timeout=PROBE_TIMEOUT,
        # the report line is ASCII, logger noise may contain non-ascii
        encoding='utf-8',
        errors='replace',
    )
    output = proc.stdout

    report = None
    for line in output.splitlines():
        _, sep, payload = line.partition('MOD_REPORT=')
        if sep:
            report = json.loads(payload)

    assert report is not None, f'no MOD_REPORT in probe output (exit code {proc.returncode}):\n{output}'
    assert proc.returncode == 0, output
    return report


@pytest.fixture(scope='module', params=sorted(CASE_ROOTS))
def case_root(request):
    """
    Project root of the layout under test.
    """
    return CASE_ROOTS[request.param]


@pytest.fixture(scope='module')
def report(case_root):
    """
    Probe report of the layout under test.

    A fresh interpreter per layout (~1s), shared by the assertions of that
    layout instead of one run per test.
    """
    return _probe(case_root)


class TestModLoading:
    """
    ExampleMod must be loaded from the resolved project root, see the module
    docstring.
    """

    def test_loader_binds_project_root(self, report, case_root):
        """
        The loader is bound to the root the backend set: not to '' (what an
        app import before create_config freezes) and not to the cwd (the probe
        runs from a foreign cwd).
        """
        assert _norm(report['root']) == _norm(case_root)
        assert _norm(report['project_root']) == _norm(case_root)
        assert _norm(report['loader_root']) == _norm(case_root)
        assert report['loader_imported_before_root'] is False

    def test_example_mod_loaded(self, report):
        """
        The mod is registered with its root and its config folder resolved
        under it.
        """
        mod = report['mods'][MOD_NAME]
        assert _norm(mod['root']) == _norm(MOD_ROOT)
        assert _norm(mod['path_config']) == _norm(MOD_ROOT.joinpath('module/config'))
        assert _norm(mod['path_assets']) == _norm(MOD_ROOT.joinpath('assets'))
        assert mod['exist'] is True

    def test_self_mod_declared_by_project_root(self, report, case_root):
        """
        ExampleMod as the project root declares itself the mod in
        <root>/module/config/const.py (ModLoader.self_mod): the mod entry is
        loaded from that file and resolves its own root from it.
        """
        if _norm(case_root) == _norm(MOD_ROOT):
            assert report['self_mod'] is not None
            assert report['self_mod']['name'] == MOD_NAME
            assert _norm(report['self_mod']['root']) == _norm(MOD_ROOT)
        else:
            # the repo root has no module/config/const.py: the mod is only
            # registered in DICT_MOD_ENTRY
            assert report['self_mod'] is None

    def test_example_mod_index_data(self, report):
        """
        The mod config index is readable under the resolved root: nav cards,
        config names, queue i18n and the task index of the fixture.
        """
        mod = report['mods'][MOD_NAME]
        assert mod['nav_names'] == MOD_NAV_NAMES
        assert mod['config_names'] == MOD_CONFIG_NAMES
        assert mod['queue_tasks'] == MOD_QUEUE_TASKS
        assert mod['task_count'] == MOD_TASK_COUNT

    def test_app_mounts_mod_assets(self, report):
        """
        create_app() mounts the mod asset folder under the resolved mod root.
        """
        path = f'/api/dev_assets/{MOD_NAME}/assets'
        mounts = dict(report['mounts'])
        assert path in mounts, mounts
        assert _norm(mounts[path]) == _norm(MOD_ROOT.joinpath('assets'))

    def test_worker_start_args_complete(self, report):
        """
        worker_start() is handed project_root / mod_root / path_main: a falsy
        one makes the manager spawn the "test mod" branch and the worker dies
        with KeyError('No such mod to run ...').
        """
        mod = report['mods'][MOD_NAME]
        args = mod['worker_start_args']
        assert args['project_root']
        assert args['mod_root']
        assert args['path_main']
        assert _norm(args['project_root']) == _norm(report['project_root'])
        assert _norm(args['mod_root']) == _norm(MOD_ROOT)
        # the mod entry the worker would import
        assert mod['path_main_exists'] is True
