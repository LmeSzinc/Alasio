"""
Test ImmediateTransaction: a BEGIN IMMEDIATE transaction on ConnectionPool.

BEGIN IMMEDIATE takes RESERVED at BEGIN: other writers wait, readers do not.
That is what a read-modify-write needs -- the read and the write that depends
on it share one lock window, and no reader is blocked while it runs.
"""
import sqlite3

import pytest

from alasio.logger import logger

pytestmark = [
    pytest.mark.parallel,
]


class TestImmediateTransaction:
    """BEGIN IMMEDIATE transaction semantics"""

    def test_commit(self, pool):
        """Test immediate transaction successful commit"""
        with pool.immediate_transaction() as cursor:
            cursor.execute("DROP TABLE IF EXISTS test")
            cursor.execute("CREATE TABLE test (id INTEGER, name TEXT)")
            cursor.execute("INSERT INTO test VALUES (1, 'Alice')")

        with pool.cursor() as cursor:
            cursor.execute("SELECT * FROM test")
            rows = cursor.fetchall()
        assert len(rows) == 1
        assert rows[0]['name'] == 'Alice'

    def test_rollback(self, pool):
        """Test immediate transaction rollback on exception"""
        with pool.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS test")
            cursor.execute("CREATE TABLE test (id INTEGER)")
            cursor.commit()

        with pytest.raises(ValueError):
            with pool.immediate_transaction() as cursor:
                cursor.execute("INSERT INTO test VALUES (1)")
                raise ValueError("Test error")

        with pool.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as cnt FROM test")
            assert cursor.fetchone()['cnt'] == 0

    def test_read_modify_write(self, pool):
        """A read and the write that depends on it share one lock window"""
        with pool.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS test")
            cursor.execute("CREATE TABLE test (id INTEGER, value BLOB)")
            cursor.execute("INSERT INTO test VALUES (1, ?)", (b'old',))
            cursor.commit()

        with pool.immediate_transaction() as c:
            row = c.execute("SELECT value FROM test WHERE id=1").fetchone()
            assert row['value'] == b'old'
            changed = c.execute(
                "UPDATE test SET value=? WHERE id=1 AND value=?", (b'new', row['value'])).rowcount
            assert changed == 1

        with pool.cursor() as cursor:
            cursor.execute("SELECT value FROM test WHERE id=1")
            assert cursor.fetchone()['value'] == b'new'

    def test_does_not_block_readers(self, pool, temp_db):
        """Readers are served while an immediate transaction holds the write lock"""
        with pool.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS test")
            cursor.execute("CREATE TABLE test (id INTEGER)")
            cursor.commit()

        reader = sqlite3.connect(temp_db, timeout=0.05, isolation_level='')
        try:
            with pool.immediate_transaction() as cursor:
                cursor.execute("INSERT INTO test VALUES (1)")
                # The write lock is held, but a reader is not blocked and sees
                # the state before the transaction
                assert reader.execute("SELECT COUNT(*) FROM test").fetchone()[0] == 0
                reader.rollback()

            # Commit landed, so the row is visible now
            assert reader.execute("SELECT COUNT(*) FROM test").fetchone()[0] == 1
            reader.rollback()
        finally:
            reader.close()

    def test_blocks_other_writers(self, pool, temp_db):
        """Another writer waits for the transaction and then gives up"""
        with pool.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS test")
            cursor.execute("CREATE TABLE test (id INTEGER)")
            cursor.commit()

        other = sqlite3.connect(temp_db, timeout=0.05, isolation_level='')
        try:
            with pool.immediate_transaction() as c:
                c.execute("INSERT INTO test VALUES (1)")
                with pytest.raises(sqlite3.OperationalError, match='locked'):
                    other.execute("INSERT INTO test VALUES (2)")
        finally:
            other.rollback()
            other.close()

    def test_commit_blocked_by_reader_rolls_back(self, pool, temp_db):
        """A reader holding a read transaction makes the commit fail cleanly"""
        with pool.cursor() as cursor:
            cursor.execute("PRAGMA busy_timeout=50")
            cursor.execute("DROP TABLE IF EXISTS test")
            cursor.execute("CREATE TABLE test (id INTEGER)")
            cursor.execute("INSERT INTO test VALUES (1)")
            cursor.commit()

        reader = sqlite3.connect(temp_db, timeout=0.05, isolation_level='')
        try:
            # An open read transaction makes the commit fail: COMMIT needs the
            # exclusive lock for a moment
            reader.execute("BEGIN")
            assert reader.execute("SELECT COUNT(*) FROM test").fetchone()[0] == 1
            with logger.mock_capture_writer() as capture:
                with pytest.raises(sqlite3.OperationalError, match='locked'):
                    with pool.immediate_transaction() as cursor:
                        # The statement itself goes through: only COMMIT needs
                        # the lock the reader is holding
                        assert cursor.execute("INSERT INTO test VALUES (2)").rowcount == 1
            # The context rolled the statement back instead of leaving an open
            # transaction for close() to report as a developer error
            assert 'DEV ERROR' not in capture.concat()
        finally:
            reader.rollback()
            reader.close()

        with pool.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as cnt FROM test")
            assert cursor.fetchone()['cnt'] == 1


class TestExclusiveTransactionBlocksReaders:
    """BEGIN EXCLUSIVE locks readers out where BEGIN IMMEDIATE does not"""

    def test_exclusive_transaction_blocks_readers(self, pool, temp_db):
        with pool.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS test")
            cursor.execute("CREATE TABLE test (id INTEGER)")
            cursor.commit()

        reader = sqlite3.connect(temp_db, timeout=0.05, isolation_level='')
        try:
            with pool.exclusive_transaction() as cursor:
                cursor.execute("INSERT INTO test VALUES (1)")
                with pytest.raises(sqlite3.OperationalError, match='locked'):
                    reader.execute("SELECT COUNT(*) FROM test").fetchone()
                reader.rollback()
        finally:
            reader.close()


class TestSqlitePoolImmediateTransaction:
    """immediate_transaction() of the global pool by file"""

    def test_immediate_transaction_method(self, sqlite_pool, temp_db):
        """Test immediate_transaction method"""
        with sqlite_pool.immediate_transaction(temp_db) as cursor:
            cursor.execute("DROP TABLE IF EXISTS test")
            cursor.execute("CREATE TABLE test (id INTEGER)")
            cursor.execute("INSERT INTO test VALUES (1)")

        with sqlite_pool.cursor(temp_db) as cursor:
            cursor.execute("SELECT COUNT(*) as cnt FROM test")
            assert cursor.fetchone()['cnt'] == 1
