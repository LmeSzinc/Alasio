import pytest

from alasio.config.base import AlasioConfigBase
from alasio.config.entry.mod import Mod
from alasio.config.entry.mod_entry import ModEntry
from alasio.db.conn import SQLITE_POOL
from alasio.ext import env
from alasio.logger import logger

# The mod the config tests of this folder run on
entry = ModEntry(env.ALASIO_ROOT.joinpath('ExampleMod'))
# Importing the mod set the project root to the mod, the tests rely on the
# value, so it is set here without importing the mod
if not env.PROJECT_ROOT:
    env.set_project_root(entry.root)

env.ALASIO_ROOT.chdir_here()


# Module-level fixtures shared across all test classes
@pytest.fixture(scope='module')
def example_mod():
    """Get the example mod from MOD_LOADER"""
    mod = Mod(entry)
    return mod


@pytest.fixture(scope='module')
def config_cls(example_mod):
    """Create a dynamic config class for testing"""

    class MyConfig(AlasioConfigBase):
        entry = example_mod.entry
        # Annotation mapping group name to "nav.Class"
        # Scheduler: "scheduler.Scheduler"
        Campaign: "main.Campaign"

    return MyConfig


@pytest.fixture(autouse=True)
def cleanup_memory_db():
    """Clear memory database after each test"""
    with logger.mock_capture_writer():
        yield
        # delete_file(':memory:') will release the pool and clear the database
        SQLITE_POOL.delete_file(':memory:')
