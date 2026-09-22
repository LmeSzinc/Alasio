"""
Fixtures of the asar tests.

The module asks the platform two things: whether the file system can hold a
symbolic link (``CAN_SYMLINK``) and whether it stores the executable bit of a
file (``CAN_EXECUTABLE``). The tests run on the in-memory filesystem of
``alasio.testing.filesystem``, which serves every platform, so the branch that
matches the platform the tests run on is the default and the other one is the
fixture of the test that needs it: no test is skipped for the platform it runs
on, and both branches of the module are checked everywhere.
"""
import pytest

from alasio.codegen.asar import archive as archive_module


@pytest.fixture
def posix_links(monkeypatch):
    """
    Run the symbolic link branch of ``create_link()`` on every platform.

    The in-memory filesystem implements symlink() and resolves the links it
    holds, so the branch and the tree it builds are checked wherever the tests
    run (creating a symbolic link needs elevation on Windows, on the real
    filesystem).

    Args:
        monkeypatch (MonkeyPatch): Patch helper of pytest
    """
    monkeypatch.setattr(archive_module, 'CAN_SYMLINK', True)


@pytest.fixture
def windows_links(monkeypatch):
    """
    Run the branch of a platform that can not create a symbolic link.

    The extraction materializes a link as a copy of its target there, which is
    what the real file system of Windows does without elevation.

    Args:
        monkeypatch (MonkeyPatch): Patch helper of pytest
    """
    monkeypatch.setattr(archive_module, 'CAN_SYMLINK', False)


@pytest.fixture
def posix_executable(monkeypatch):
    """
    Run the branch of a platform that stores the executable bit of a file.

    The in-memory filesystem stores the mode of a file wherever the tests run,
    so an entry that is extracted with the bit set is checked everywhere
    (Windows has no executable bit, on the real filesystem).

    Args:
        monkeypatch (MonkeyPatch): Patch helper of pytest
    """
    monkeypatch.setattr(archive_module, 'CAN_EXECUTABLE', True)
