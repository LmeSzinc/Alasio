"""
Isolation of the process wide caches used by the config generator.

CacheAlasio holds the generator of the alasio entry, its data is read from the
filesystem that is active on the first access. The tests run on the in-memory
filesystem, so a cache built by one test must not be served to another one.
"""
import pytest

from alasio.config_dev.parse.cache_alasio import CacheAlasio
from alasio.config_dev.parse.load_alas_i18n import LoadAlasI18n


@pytest.fixture(autouse=True)
def reset_generator_caches():
    """
    Start every test with an empty CacheAlasio and LoadAlasI18n.
    """
    CacheAlasio.singleton_clear()
    LoadAlasI18n.singleton_clear()
    yield
    CacheAlasio.singleton_clear()
    LoadAlasI18n.singleton_clear()
