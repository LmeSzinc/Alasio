import gc
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from alasio.ext.file.filelock import SQLiteFileLock, Timeout

CHILD_HOLD = (
    "import os, sys, time\n"
    "from alasio.ext.file.filelock import SQLiteFileLock\n"
    "lock = SQLiteFileLock(sys.argv[1], timeout=0)\n"
    "lock.acquire()\n"
    "print('HELD', flush=True)\n"
    "flag = sys.argv[2]\n"
    "for _ in range(500):\n"
    "    if os.path.exists(flag):\n"
    "        break\n"
    "    time.sleep(0.02)\n"
    "lock.release()\n"
)


class TestTimeout:
    """Test cases for the Timeout exception"""

    def test_exception_message_and_lock_file(self, tmp_path):
        """Timeout should carry the lock file path in its message and attribute"""
        lock_file = tmp_path / "a.lock"
        with pytest.raises(Timeout, match="Timeout occurred trying to acquire lock") as excinfo:
            raise Timeout(str(lock_file))
        assert excinfo.value.lock_file == str(lock_file.resolve())


class TestProperties:
    """Test cases for the constructor and properties"""

    def test_lock_file_is_absolute(self, tmp_path):
        """lock_file should be the resolved absolute path"""
        lock = SQLiteFileLock(tmp_path / "sub" / "a.lock")
        assert lock.lock_file == str((tmp_path / "sub" / "a.lock").resolve())

    def test_default_timeout_get_and_set(self, tmp_path):
        """timeout should store the constructor value and be mutable"""
        lock = SQLiteFileLock(tmp_path / "a.lock", timeout=0.5)
        assert lock.timeout == 0.5
        lock.timeout = 1.5
        assert lock.timeout == 1.5

    @pytest.mark.parametrize("lock_file", ["a.lock", Path("a.lock")])
    def test_accepts_str_and_path(self, tmp_path, lock_file):
        """Constructor should accept both str and Path lock files"""
        lock = SQLiteFileLock(tmp_path / lock_file, timeout=0)
        assert lock.lock_file == str((tmp_path / "a.lock").resolve())

    def test_initial_state(self, tmp_path):
        """A new lock should not be locked"""
        lock = SQLiteFileLock(tmp_path / "a.lock", timeout=0)
        assert not lock.is_locked


class TestAcquireRelease:
    """Test cases for acquire() and release()"""

    def test_acquire_returns_self(self, tmp_path):
        """acquire() should return self, matching the filelock interface"""
        lock = SQLiteFileLock(tmp_path / "a.lock", timeout=0)
        assert lock.acquire() is lock
        lock.release()

    def test_acquire_and_release(self, tmp_path):
        """is_locked should be True after acquire and False after release"""
        lock = SQLiteFileLock(tmp_path / "a.lock", timeout=0)
        lock.acquire()
        assert lock.is_locked
        lock.release()
        assert not lock.is_locked

    def test_acquire_creates_parent_directory(self, tmp_path):
        """Parent directories of the lock file should be created automatically"""
        lock = SQLiteFileLock(tmp_path / "nested" / "dir" / "a.lock", timeout=0)
        lock.acquire()
        assert lock.is_locked
        lock.release()

    def test_release_without_acquire_is_noop(self, tmp_path):
        """release() should do nothing when the lock is not held"""
        lock = SQLiteFileLock(tmp_path / "a.lock", timeout=0)
        lock.release()
        assert not lock.is_locked
        lock.release(force=True)
        assert not lock.is_locked

    def test_acquire_accepts_poll_interval(self, tmp_path):
        """poll_interval should be accepted for filelock interface compatibility"""
        lock = SQLiteFileLock(tmp_path / "a.lock", timeout=0)
        assert lock.acquire(poll_interval=0.1) is lock
        lock.release()


class TestReentrant:
    """Test cases for reentrant locking on the same instance"""

    def test_multiple_acquire_counts(self, tmp_path):
        """Each acquire() should increase the counter and each release() decrease it"""
        lock = SQLiteFileLock(tmp_path / "a.lock", timeout=0)
        lock.acquire()
        lock.acquire()
        lock.acquire()
        assert lock.is_locked
        lock.release()
        assert lock.is_locked
        lock.release()
        assert lock.is_locked
        lock.release()
        assert not lock.is_locked

    def test_reentrant_acquire_does_not_block(self, tmp_path):
        """A second acquire() on the same instance should succeed even with timeout=0"""
        lock = SQLiteFileLock(tmp_path / "a.lock", timeout=0)
        lock.acquire()
        assert lock.acquire() is lock
        lock.release()
        lock.release()

    def test_force_release(self, tmp_path):
        """release(force=True) should release the lock immediately, ignoring the counter"""
        lock = SQLiteFileLock(tmp_path / "a.lock", timeout=0)
        lock.acquire()
        lock.acquire()
        lock.release(force=True)
        assert not lock.is_locked


class TestExclusion:
    """Test cases for mutual exclusion between instances"""

    def test_timeout_zero_is_non_blocking(self, tmp_path):
        """acquire() with timeout=0 should fail fast with Timeout when the lock is busy"""
        lock_file = tmp_path / "a.lock"
        lock1 = SQLiteFileLock(lock_file, timeout=0)
        lock2 = SQLiteFileLock(lock_file, timeout=0)
        lock1.acquire()
        try:
            start = time.monotonic()
            with pytest.raises(Timeout) as excinfo:
                lock2.acquire()
            elapsed = time.monotonic() - start
            assert excinfo.value.lock_file == lock1.lock_file
            assert elapsed < 0.5
        finally:
            lock1.release()

    def test_positive_timeout_waits_then_raises(self, tmp_path):
        """acquire() with a positive timeout should wait and then raise Timeout"""
        lock_file = tmp_path / "a.lock"
        lock1 = SQLiteFileLock(lock_file, timeout=0)
        lock2 = SQLiteFileLock(lock_file, timeout=0.2)
        lock1.acquire()
        try:
            start = time.monotonic()
            with pytest.raises(Timeout):
                lock2.acquire()
            elapsed = time.monotonic() - start
            assert elapsed >= 0.15
        finally:
            lock1.release()

    def test_acquire_after_release(self, tmp_path):
        """Another instance should acquire the lock once the holder releases it"""
        lock_file = tmp_path / "a.lock"
        lock1 = SQLiteFileLock(lock_file, timeout=0)
        lock2 = SQLiteFileLock(lock_file, timeout=0)
        lock1.acquire()
        lock1.release()
        lock2.acquire()
        assert lock2.is_locked
        lock2.release()

    def test_different_files_do_not_conflict(self, tmp_path):
        """Locks on different files should not block each other"""
        lock1 = SQLiteFileLock(tmp_path / "a.lock", timeout=0)
        lock2 = SQLiteFileLock(tmp_path / "b.lock", timeout=0)
        lock1.acquire()
        lock2.acquire()
        assert lock1.is_locked
        assert lock2.is_locked
        lock1.release()
        lock2.release()

    def test_unrelated_operational_error_is_reraisied(self, tmp_path):
        """OperationalError not related to locking should be propagated as is"""
        dir_path = tmp_path / "adir"
        dir_path.mkdir()
        lock = SQLiteFileLock(dir_path, timeout=0)
        with pytest.raises(sqlite3.OperationalError):
            lock.acquire()


class TestDefaultTimeout:
    """Test cases for the default timeout"""

    def test_constructor_timeout_is_used(self, tmp_path):
        """acquire() without a timeout should use the constructor timeout"""
        lock_file = tmp_path / "a.lock"
        lock1 = SQLiteFileLock(lock_file, timeout=0)
        lock2 = SQLiteFileLock(lock_file, timeout=0.2)
        lock1.acquire()
        try:
            start = time.monotonic()
            with pytest.raises(Timeout):
                lock2.acquire()
            assert time.monotonic() - start >= 0.15
        finally:
            lock1.release()

    def test_acquire_timeout_overrides_constructor(self, tmp_path):
        """acquire(timeout=...) should override the constructor timeout"""
        lock_file = tmp_path / "a.lock"
        lock1 = SQLiteFileLock(lock_file, timeout=0)
        # Constructor timeout is 0, but the override should make acquire() wait
        lock2 = SQLiteFileLock(lock_file, timeout=0)
        lock1.acquire()
        try:
            start = time.monotonic()
            with pytest.raises(Timeout):
                lock2.acquire(timeout=0.2)
            assert time.monotonic() - start >= 0.15
        finally:
            lock1.release()


class TestContextManager:
    """Test cases for the context manager protocol"""

    def test_context_manager(self, tmp_path):
        """The lock should be acquired on enter and released on exit"""
        lock = SQLiteFileLock(tmp_path / "a.lock", timeout=0)
        with lock as acquired:
            assert acquired is lock
            assert lock.is_locked
        assert not lock.is_locked

    def test_release_on_exception(self, tmp_path):
        """The lock should be released even when the block raises"""
        lock_file = tmp_path / "a.lock"
        with pytest.raises(RuntimeError):
            with SQLiteFileLock(lock_file, timeout=0):
                raise RuntimeError("boom")
        lock2 = SQLiteFileLock(lock_file, timeout=0)
        lock2.acquire()
        lock2.release()


class TestThreads:
    """Test cases for locking across threads"""

    def test_exclusion_between_threads(self, tmp_path):
        """A thread should not acquire a lock held by another thread"""
        lock_file = tmp_path / "a.lock"
        holder = SQLiteFileLock(lock_file, timeout=0)
        contender = SQLiteFileLock(lock_file, timeout=0)
        holder.acquire()
        results = []

        def try_acquire():
            try:
                contender.acquire()
            except Timeout:
                results.append("timeout")
            else:
                results.append("acquired")

        thread = threading.Thread(target=try_acquire)
        thread.start()
        thread.join(timeout=10)
        holder.release()
        assert not thread.is_alive()
        assert results == ["timeout"]

    def test_thread_waits_until_release(self, tmp_path):
        """A thread should acquire the lock once the holder releases it"""
        lock_file = tmp_path / "a.lock"
        holder = SQLiteFileLock(lock_file, timeout=0)
        contender = SQLiteFileLock(lock_file, timeout=5)
        holder.acquire()
        results = []

        def try_acquire():
            with contender:
                results.append("acquired")

        thread = threading.Thread(target=try_acquire)
        thread.start()
        time.sleep(0.1)
        holder.release()
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert results == ["acquired"]

    def test_concurrent_acquire_on_same_instance(self, tmp_path):
        """Concurrent acquires on the same instance must race on the SQLite lock,
        each thread failing on its own timeout budget without waiting for others"""
        lock_file = tmp_path / "a.lock"
        holder = SQLiteFileLock(lock_file, timeout=0)
        shared = SQLiteFileLock(lock_file, timeout=0)
        holder.acquire()
        results = []
        c_elapsed = []

        def thread_b():
            # Waits 0.5s on the SQLite lock
            try:
                shared.acquire(timeout=0.5)
            except Timeout:
                results.append("b:timeout")

        def thread_c():
            # Non-blocking, must fail fast even though thread B is also waiting
            start = time.monotonic()
            try:
                shared.acquire(timeout=0)
            except Timeout:
                c_elapsed.append(time.monotonic() - start)
                results.append("c:timeout")

        b = threading.Thread(target=thread_b)
        c = threading.Thread(target=thread_c)
        b.start()
        # Wait until thread B is inside the SQLite wait
        time.sleep(0.15)
        c.start()
        c.join(timeout=10)
        b.join(timeout=10)
        holder.release()
        assert not c.is_alive()
        assert not b.is_alive()
        assert results == ["c:timeout", "b:timeout"]
        # Thread C must not wait for thread B's SQLite wait to finish
        assert c_elapsed[0] < 0.3

    def test_concurrent_acquire_has_single_winner(self, tmp_path):
        """Concurrent acquires on the same instance must yield exactly one winner,
        the loser timing out on its own budget"""
        lock_file = tmp_path / "a.lock"
        holder = SQLiteFileLock(lock_file, timeout=0)
        # Loser budget 0.2s must be shorter than the winner's hold time of 0.5s
        shared = SQLiteFileLock(lock_file, timeout=0.2)
        holder.acquire()
        results = []

        def try_acquire(tag):
            try:
                with shared:
                    results.append(f"{tag}:acquired")
                    # Hold the lock long enough to force the loser to time out
                    time.sleep(0.5)
            except Timeout:
                results.append(f"{tag}:timeout")

        b = threading.Thread(target=try_acquire, args=("b",))
        c = threading.Thread(target=try_acquire, args=("c",))
        b.start()
        c.start()
        time.sleep(0.1)
        holder.release()
        b.join(timeout=10)
        c.join(timeout=10)
        assert not b.is_alive()
        assert not c.is_alive()
        winners = [r for r in results if r.endswith(":acquired")]
        losers = [r for r in results if r.endswith(":timeout")]
        assert len(winners) == 1
        assert len(losers) == 1


class TestCrossProcess:
    """Test cases for locking across processes"""

    def test_exclusion_between_processes(self, tmp_path):
        """A child process should not acquire a lock held by the parent"""
        lock_file = tmp_path / "a.lock"
        lock = SQLiteFileLock(lock_file, timeout=0)
        lock.acquire()
        code = (
            "import sys\n"
            "from alasio.ext.file.filelock import SQLiteFileLock, Timeout\n"
            "try:\n"
            "    with SQLiteFileLock(sys.argv[1], timeout=0.5):\n"
            "        print('ACQUIRED')\n"
            "except Timeout:\n"
            "    print('TIMEOUT')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code, str(lock_file)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        lock.release()
        assert result.returncode == 0
        assert "TIMEOUT" in result.stdout

    def test_parent_acquires_after_child_release(self, tmp_path):
        """The parent should acquire the lock after the child process releases it"""
        lock_file = tmp_path / "a.lock"
        flag_file = tmp_path / "release.flag"
        proc = subprocess.Popen(
            [sys.executable, "-c", CHILD_HOLD, str(lock_file), str(flag_file)],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert proc.stdout.readline().strip() == "HELD"
            flag_file.touch()
            lock = SQLiteFileLock(lock_file, timeout=10)
            lock.acquire()
            assert lock.is_locked
            lock.release()
        finally:
            proc.wait(timeout=10)
            proc.kill()


class TestDestructor:
    """Test cases for the destructor fallback"""

    def test_del_releases_lock(self, tmp_path):
        """Destroying the lock object should release the underlying lock"""
        lock_file = tmp_path / "a.lock"
        lock = SQLiteFileLock(lock_file, timeout=0)
        lock.acquire()
        del lock
        gc.collect()
        lock2 = SQLiteFileLock(lock_file, timeout=0)
        lock2.acquire()
        lock2.release()
