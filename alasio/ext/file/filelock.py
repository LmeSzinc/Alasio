"""
Cross-platform file lock based on SQLite exclusive transactions.

The lock is implemented as a ``BEGIN EXCLUSIVE`` transaction on a SQLite
database file, so it works across processes and threads on Windows, macOS and
Linux. The public interface is compatible with the third-party
filelock.FileLock library.

Notes:
    - The lock file is a SQLite database file and stays on disk after release.
      Deleting it would break mutual exclusion: a new file could be created and
      locked while another process still holds a lock on the old one.
    - The lock is released automatically when the holding process crashes or
      exits, because the operating system releases the underlying file locks
      and SQLite rolls back unfinished transactions.
"""
import os
import sqlite3
import threading
from pathlib import Path


class Timeout(Exception):
    """
    Raised when the lock cannot be acquired within the timeout
    (consistent with filelock.Timeout).

    Args:
        lock_file (str): Path of the lock file
    """

    def __init__(self, lock_file):
        self.lock_file = lock_file
        super().__init__(f"Timeout occurred trying to acquire lock for: {lock_file}")


class SQLiteFileLock:
    """
    Cross-platform file lock based on SQLite exclusive transactions
    (``BEGIN EXCLUSIVE``).

    The interface is fully compatible with the third-party filelock.FileLock
    library.
    """

    def __init__(self, lock_file, timeout=-1):
        """
        Args:
            lock_file (str or Path): Lock file path, parent directories are
                created automatically if missing
            timeout (float): Timeout in seconds. Defaults to -1.
                -1 waits indefinitely (blocking);
                 0 is non-blocking, raises Timeout immediately if the lock
                   cannot be acquired;
                > 0 waits at most N seconds
        """
        self._lock_file = str(Path(lock_file).resolve())
        self._default_timeout = timeout

        # Internal state, supports reentrant locking within the same instance
        self._conn = None
        self._lock_counter = 0
        self._thread_lock = threading.Lock()

    @property
    def lock_file(self):
        """
        Returns:
            str: Absolute path of the lock file
        """
        return self._lock_file

    @property
    def timeout(self):
        """
        Returns:
            float: Default timeout in seconds
        """
        return self._default_timeout

    @timeout.setter
    def timeout(self, value):
        """
        Args:
            value (float): Default timeout in seconds
        """
        self._default_timeout = value

    @property
    def is_locked(self):
        """
        Returns:
            bool: True if the current instance holds the lock
        """
        with self._thread_lock:
            return self._lock_counter > 0

    def acquire(self, timeout=None, poll_interval=0.05):
        """
        Acquire the lock.

        Args:
            timeout (float): Overrides the constructor timeout. Defaults to None.
            poll_interval (float): Kept for compatibility with the filelock
                interface, the SQLite C layer polls automatically.
                Defaults to 0.05.

        Returns:
            SQLiteFileLock: self

        Raises:
            Timeout: If the lock cannot be acquired within the timeout
        """
        with self._thread_lock:
            if timeout is None:
                timeout = self._default_timeout

            # Reentrant lock: repeated acquire() on the same instance must not deadlock
            if self._lock_counter > 0:
                self._lock_counter += 1
                return self

            # Ensure the parent directory exists
            parent_dir = os.path.dirname(self._lock_file)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)

            # Convert to the SQLite C API timeout in seconds:
            # < 0 waits forever, 0 is non-blocking.
            # 315360000.0 is 10 years, effectively infinite for a blocking wait
            sql_timeout = 315360000.0 if timeout < 0 else max(0.0, float(timeout))

            conn = None
            try:
                # Open a connection and rely on SQLite's native busy_timeout mechanism
                conn = sqlite3.connect(
                    self._lock_file,
                    timeout=sql_timeout,
                    check_same_thread=False,
                )

                # Start an exclusive transaction, which takes an OS-level exclusive file lock
                conn.execute("BEGIN EXCLUSIVE")

                self._conn = conn
                self._lock_counter = 1
                return self

            except sqlite3.OperationalError as e:
                # Close the connection that failed to acquire the lock
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

                # Convert the "database is locked" error to Timeout
                err_msg = str(e).lower()
                if "locked" in err_msg or "busy" in err_msg:
                    raise Timeout(self._lock_file) from e
                raise e

    def release(self, force=False):
        """
        Release the lock.

        Args:
            force (bool): If True, ignore the reentrancy counter and release
                the lock immediately. Defaults to False.
        """
        with self._thread_lock:
            if self._lock_counter == 0:
                return

            if force:
                self._lock_counter = 1

            self._lock_counter -= 1

            # Only close the connection and release the file lock when the counter drops to zero
            if self._lock_counter == 0:
                if self._conn:
                    try:
                        # End the transaction
                        self._conn.rollback()
                    except Exception:
                        pass
                    finally:
                        try:
                            # Close the connection, the OS releases the exclusive lock
                            self._conn.close()
                        except Exception:
                            pass
                        self._conn = None

    def __enter__(self):
        """
        Acquire the lock when entering the context.

        Returns:
            SQLiteFileLock: self
        """
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Release the lock when leaving the context.
        """
        self.release()

    def __del__(self):
        """Safe fallback to release the lock when the object is destroyed."""
        self.release(force=True)
