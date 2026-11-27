"""
Test that a transaction context always returns its cursor to the pool.

A leaked cursor is not a small thing: with pool_size=1 the only connection
stays checked out and every later cursor() waits for it. A transaction can end
without a commit in two ways -- BEGIN is rejected by another writer, or the
commit itself is rejected by a reader -- and both have to release the cursor.
"""
import sqlite3

import pytest

from alasio.db.conn import ConnectionPool, SqlitePoolCursor
from alasio.logger import logger

pytestmark = [
    pytest.mark.parallel,
]


class TestTransactionCursorRelease:
    """A transaction always returns its cursor, however it ends"""

    def test_begin_failure_returns_cursor(self, temp_db):
        """A rejected BEGIN must not leak the only connection of the pool"""
        pool = ConnectionPool(temp_db, pool_size=1)
        try:
            with pool.cursor() as cursor:
                # A short busy timeout keeps the test fast
                cursor.execute("PRAGMA busy_timeout=50")
                # temp_db is shared by every test of the process, and Windows
                # may keep the file after the previous test: start clean
                cursor.execute("DROP TABLE IF EXISTS test")
                cursor.execute("CREATE TABLE test (id INTEGER)")
                cursor.commit()

            other = sqlite3.connect(temp_db, timeout=0.05, isolation_level='')
            try:
                # Another writer holds RESERVED, so BEGIN EXCLUSIVE is rejected
                other.execute("BEGIN IMMEDIATE")
                with pytest.raises(sqlite3.OperationalError, match='locked'):
                    with pool.exclusive_transaction():
                        pass
                # The cursor came back even though BEGIN failed
                assert len(pool.idle_workers) == 1
                assert len(pool.all_workers) == 1
                with pool.cursor() as cursor:
                    cursor.execute("SELECT COUNT(*) as cnt FROM test")
                    assert cursor.fetchone()['cnt'] == 0
            finally:
                other.rollback()
                other.close()
        finally:
            pool.release_all()

    def test_commit_failure_rolls_back_and_returns_cursor(self, temp_db, monkeypatch):
        """A rejected COMMIT undoes the transaction and leaves no dev error"""
        pool = ConnectionPool(temp_db, pool_size=1)
        try:
            with pool.cursor() as cursor:
                cursor.execute("DROP TABLE IF EXISTS test")
                cursor.execute("CREATE TABLE test (id INTEGER)")
                cursor.commit()

            def failing_commit(self):
                raise sqlite3.OperationalError('database is locked')

            monkeypatch.setattr(SqlitePoolCursor, 'commit', failing_commit)
            with logger.mock_capture_writer() as capture:
                with pytest.raises(sqlite3.OperationalError, match='locked'):
                    with pool.exclusive_transaction() as cursor:
                        cursor.execute("INSERT INTO test VALUES (1)")
            # The context undid the statement: no half-written state, and no
            # developer-error report blaming the caller for somebody's lock
            assert 'DEV ERROR' not in capture.concat()

            monkeypatch.undo()
            with pool.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) as cnt FROM test")
                assert cursor.fetchone()['cnt'] == 0
            assert len(pool.idle_workers) == 1
        finally:
            pool.release_all()

    def test_open_transaction_is_reported(self, temp_db):
        """Control: a cursor closed with an open transaction does report it"""
        pool = ConnectionPool(temp_db, pool_size=1)
        try:
            with pool.cursor() as cursor:
                cursor.execute("DROP TABLE IF EXISTS test")
                cursor.execute("CREATE TABLE test (id INTEGER)")
                cursor.commit()

            with logger.mock_capture_writer() as capture:
                with pool.cursor() as cursor:
                    cursor.execute("INSERT INTO test VALUES (1)")
            assert 'DEV ERROR' in capture.concat()
        finally:
            pool.release_all()
