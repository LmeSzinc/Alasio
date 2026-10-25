"""
Fixtures for the assets_dev tests.

AssetsExtractor.patch_const() changes the global Const for the whole process,
and AssetImage keeps the current project root and language root as class
attributes, so both are restored around every test and the test files can use
any extractor in any order.
"""
import pytest

from alasio.assets_dev.parse import AssetImage
from alasio.config.const import Const
from alasio.ext import env
from tests.assets_dev.helpers import RESOLUTION

# Const attributes changed by AssetsExtractor.patch_const()
CONST_ATTRIBUTES = (
    'ASSETS_PATH',
    'ASSETS_MODULE',
    'ASSETS_LANG',
    'ASSETS_EXT',
    'ASSETS_ATTR',
    'ASSETS_RESOLUTION',
    'ASSETS_TEMPLATE_PREFIX',
    'ASSETS_SEARCH_OUTSET',
)


@pytest.fixture(autouse=True)
def restore_const():
    """Restore Const after the test, patch_const() changes it for the whole process"""
    saved = {name: getattr(Const, name) for name in CONST_ATTRIBUTES}
    yield
    for name, value in saved.items():
        setattr(Const, name, value)


@pytest.fixture(autouse=True)
def restore_asset_image_root():
    """Restore the repository root and the language root of AssetImage"""
    saved = AssetImage.REPO_ROOT, AssetImage.LANG_ROOT
    yield
    AssetImage.REPO_ROOT, AssetImage.LANG_ROOT = saved


@pytest.fixture(autouse=True)
def restore_project_root():
    """
    Restore env.PROJECT_ROOT after the test

    Importing alasio.assets_dev.extract points the project root to this
    repository at import time, the code generator scans the project root to sort
    imports by stdlib / third party / local project.
    """
    saved = env.PROJECT_ROOT
    yield
    env.PROJECT_ROOT = saved


@pytest.fixture(autouse=True)
def reset_library_scanner():
    """
    Clear the library scan cache around every test of this folder

    Code generation in these tests runs on the fake filesystem, the scanner
    would cache the stdlib and site-packages paths of the fake root, and the
    code generation of a later test file would sort imports by that cache.
    """
    from alasio.codegen.python.libscan import EnvLibraryScanner

    EnvLibraryScanner.singleton_clear()
    yield
    EnvLibraryScanner.singleton_clear()


@pytest.fixture
def src_languages():
    """Languages of the StarRailCopilot assets"""
    Const.ASSETS_LANG = dict.fromkeys(['share', 'cn', 'en'])
    return Const.ASSETS_LANG


@pytest.fixture
def small_resolution():
    """Use the resolution of the test images instead of 1280x720"""
    Const.ASSETS_RESOLUTION = RESOLUTION
    return Const.ASSETS_RESOLUTION
