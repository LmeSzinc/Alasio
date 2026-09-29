"""
Tests for the thread pool the pack module compresses on.

The pool is dedicated to the pack module -- the shared THREAD_POOL runs the
blocking calls of the whole process, a pack build would starve them -- and it is
as wide as the physical cores of the machine, see pack_pool.py and
doc/2026-09-27_update-pack-from-repo.md section 7.29.
"""
from alasio.deploy_dev.pack.pack_pool import PACK_POOL
from alasio.ext.concurrent.processpool import get_max_worker
from alasio.ext.concurrent.threadpool import THREAD_POOL


class TestPackPool:
    """The pool the encoders of the pack module compress on."""

    def test_the_pool_is_dedicated_to_the_pack_module(self):
        """The pack encoders do not queue on the THREAD_POOL of the process."""
        assert PACK_POOL is not THREAD_POOL

    def test_the_pool_is_as_wide_as_the_physical_cores(self):
        """The batch is as wide as the physical cores of the machine."""
        assert PACK_POOL.pool_size == (get_max_worker() or 1)
