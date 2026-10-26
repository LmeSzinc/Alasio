"""
Fixtures shared by the whole test suite.
"""
import pytest


@pytest.fixture(scope='session', autouse=True)
def stdlib_module_names_warmed():
    """
    Read the standard library names of the interpreter before any test runs

    The code generator sorts the imports by the category of their module, and
    python 3.8 / 3.9 read the standard library names from the disk on the first
    call. The fake filesystem of the tests serves os.listdir() from memory, so
    a scan started inside a test finds nothing but the built-in modules, and
    every stdlib import would then be sorted as third party.

    The session scope matters: a session fixture is set up before the function
    scoped `fs` fixture of the first test, so the scan always sees the real
    disk. The warm up also keeps the scan out of a test that disables
    sys.stdlib_module_names to exercise the scan itself.
    """
    from alasio.codegen.python.libscan import StdlibModuleNames

    StdlibModuleNames().STDLIB_MODULE_NAMES
