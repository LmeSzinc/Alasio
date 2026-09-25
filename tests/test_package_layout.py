"""
Layout of the tests package tree.

The tests folder is a package: every folder that holds modules has an
``__init__.py``, so pytest imports each module under its qualified name
(``tests.backend.app.test_app``) instead of its bare file name. Without them
two files sharing a name collide, pytest fails to collect the second one with
"import file mismatch" (``test_cli``, ``test_model`` and ``test_gate`` had
that before those folders became packages), see
:func:`_pytest.pathlib.import_path`.

The exception is a folder whose name shadows an importable top level name,
see SHADOWING_FOLDERS: it must stay without ``__init__.py``. A plain folder is
only a namespace portion of the import system, the real module found behind it
on sys.path wins, while an ``__init__.py`` turns it into a regular package
that wins over the stdlib or environment module for every process whose
sys.path holds the parent folder: the process chain spawned by
tests/backend/root_propagation.py (multiprocessing spawn children inherit
sys.path, the tests/backend folder is on it), or pytest started inside the
folder (``import locale`` inside gettext fails when tests/backend/locale is a
package).
"""
import os

import pytest

from alasio.ext.env import ALASIO_ROOT

TESTS = ALASIO_ROOT.joinpath('tests')

# Folders whose name shadows an importable top level name, they must stay
# plain folders (no __init__.py), the module behind them on sys.path is the
# one every process has to get.
SHADOWING_FOLDERS = [
    # stdlib locale, imported by gettext and argparse
    'backend/locale',
    # the alasio package itself
    'config/alasio',
    # stdlib concurrent, imported for concurrent.futures
    'ext/concurrent',
    # attr of the attrs package
    'git/attr',
]

# folders that never hold a module of the tree
SKIP_FOLDERS = {'__pycache__', '.pytest_cache'}


def iter_modules():
    """
    Every module pytest imports by name: test files and conftest.

    Yields:
        str: Absolute file path
    """
    for root, dirs, files in os.walk(TESTS):
        dirs[:] = [dir for dir in dirs if dir not in SKIP_FOLDERS]
        for name in files:
            if not name.endswith('.py'):
                continue
            if name.startswith('test_') or name.endswith('_test.py') or name == 'conftest.py':
                yield os.path.join(root, name)


def import_name(file):
    """
    The module name pytest imports a file under.

    The prepend import mode (the pytest default) names a module after the
    chain of folders that hold an ``__init__.py``, plus the file name, and
    after the file name alone when that chain is empty.

    Args:
        file (str): Absolute path of a python file

    Returns:
        str: Import name, for example 'tests.backend.app.test_app'
    """
    parts = []
    folder = os.path.dirname(file)
    while os.path.exists(os.path.join(folder, '__init__.py')):
        parts.append(os.path.basename(folder))
        parent = os.path.dirname(folder)
        if parent == folder:
            break
        folder = parent
    return '.'.join(parts[::-1] + [os.path.basename(file)[:-3]])


class TestPackageTree:
    """The tests folder is a package tree, see the module docstring."""

    def test_import_names_are_unique(self):
        """Two modules must never share an import name: pytest imports the
        first one and fails to collect the second one."""
        names = {}
        for file in iter_modules():
            names.setdefault(import_name(file), []).append(file)
        duplicated = {name: files for name, files in names.items() if len(files) > 1}
        assert not duplicated, f'Duplicated test module names: {duplicated}'

    def test_folders_are_packages(self):
        """Every folder that holds a module has an ``__init__.py``, the
        shadowing folders are the only exception."""
        shadowing = {os.path.normpath(TESTS.joinpath(path)) for path in SHADOWING_FOLDERS}
        missing = []
        for file in iter_modules():
            folder = os.path.normpath(os.path.dirname(file))
            while True:
                relative = os.path.relpath(folder, TESTS)
                if relative.startswith('..'):
                    # left the tests tree
                    break
                if folder in shadowing:
                    break
                if not os.path.exists(os.path.join(folder, '__init__.py')):
                    missing.append(relative)
                folder = os.path.dirname(folder)
        assert not missing, f'Folders without __init__.py: {sorted(set(missing))}'

    @pytest.mark.parametrize('path', SHADOWING_FOLDERS)
    def test_shadowing_folders_have_no_package(self, path):
        """A folder that shadows an importable name must stay a plain folder:
        an ``__init__.py`` would shadow the real module on sys.path."""
        folder = TESTS.joinpath(path)
        assert os.path.isdir(folder), f'Shadowing folder does not exist: {path}'
        assert not os.path.exists(os.path.join(folder, '__init__.py')), (
            f'{path} shadows an importable module and must not be a package'
        )
