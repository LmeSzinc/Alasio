"""
Isolation of the process wide caches used by the config generator.

CacheAlasio holds the generator of the alasio entry, its data is read from the
filesystem that is active on the first access. The tests run on the in-memory
filesystem, so a cache built by one test must not be served to another one, and
the alasio entry has to be created on that filesystem: a nav of a mod may
inherit the groups of alasio, see ParseGroups.external_groups(), and the
generator loads them while parsing the nav.
"""
import pytest

from alasio.config_dev.parse.cache_alasio import CacheAlasio
from alasio.config_dev.parse.load_alas_i18n import LoadAlasI18n
from alasio.ext import env
from alasio.testing.filesystem import fs  # noqa: F401

# The real dashboard base groups, read at import time, before the in-memory
# filesystem of a test hides the project. They are the groups a nav of a mod
# inherits from, and a change in the store config is exposed by these tests
# instead of being hidden behind a duplicated copy.
STORE_YAML = env.ALASIO_ROOT.joinpath('alasio/config/alasio/store.args.yaml').atomic_read_text()


@pytest.fixture(autouse=True)
def reset_generator_caches(fs):
    """
    Start every test with an empty CacheAlasio and LoadAlasI18n.

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture
    """
    CacheAlasio.singleton_clear()
    LoadAlasI18n.singleton_clear()
    fs.create_file(env.ALASIO_ROOT.joinpath('alasio/config/alasio/store.args.yaml'), contents=STORE_YAML)
    yield
    CacheAlasio.singleton_clear()
    LoadAlasI18n.singleton_clear()
