"""
Fixtures for the code generation tests.
"""
import pytest


@pytest.fixture(autouse=True)
def reset_library_scanner():
    """
    Clear the library scan cache around every test of this folder

    EnvLibraryScanner is a named singleton and caches the stdlib and
    site-packages paths it derives from the real filesystem. The tests here
    generate code under the fake filesystem, where that scan records paths that
    do not exist on the real one, and every code generation of a later test file
    then sorts the stdlib imports as third-party. The scanner is only used by
    these tests, so it is reset here and not by the fake filesystem fixture.
    """
    from alasio.codegen.python.libscan import EnvLibraryScanner

    EnvLibraryScanner.singleton_clear()
    yield
    EnvLibraryScanner.singleton_clear()
