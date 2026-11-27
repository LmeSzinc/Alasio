"""
Test ImmediateTransaction: BEGIN IMMEDIATE transaction support on AlasioTable
"""
import sqlite3

import pytest

from tests.db.table.conftest import User


def test_immediate_transaction_creation(user_table):
    """Test creating an immediate transaction"""
    with user_table.immediate_transaction() as cursor:
        assert cursor is not None
        # Cursor should have table info, so a first write can create the table
        assert cursor.TABLE_NAME == user_table.TABLE_NAME
        assert cursor.CREATE_TABLE == user_table.CREATE_TABLE


def test_immediate_transaction_insert(user_table, sample_users):
    """Test inserting within immediate transaction"""
    with user_table.immediate_transaction() as c:
        user_table.insert_row(sample_users[:2], _cursor_=c)

    # Should be committed after context exit
    assert user_table.select_count() == 2


def test_immediate_transaction_rollback_on_exception(user_table):
    """Test that transaction rolls back on exception"""
    with pytest.raises(ValueError):
        with user_table.immediate_transaction() as c:
            user = User(id=0, name='Alice', age=25, email='alice@example.com')
            user_table.insert_row(user, _cursor_=c)
            raise ValueError("Test exception")

    assert user_table.select_count() == 0


def test_immediate_transaction_read_then_write(user_table, sample_users):
    """A read and the write that depends on it happen under one lock"""
    user_table.insert_row(sample_users[:1])

    with user_table.immediate_transaction() as c:
        row = user_table.select_one(name='Alice', _cursor_=c)
        updated = User(id=row.id, name=row.name, age=row.age + 1, email=row.email)
        user_table.update_row(updated, _cursor_=c)

    assert user_table.select_one(name='Alice').age == 26


def test_immediate_transaction_with_update(user_table, sample_users):
    """Test update within immediate transaction"""
    user_table.insert_row(sample_users[:1])
    user = user_table.select_one(name='Alice')

    with user_table.immediate_transaction() as c:
        updated = User(id=user.id, name='Alice Updated', age=30, email=user.email)
        user_table.update_row(updated, _cursor_=c)

    result = user_table.select_by_pk(user.id)
    assert result.name == 'Alice Updated'


def test_immediate_transaction_with_delete(user_table, sample_users):
    """Test delete within immediate transaction"""
    user_table.insert_row(sample_users[:2])

    with user_table.immediate_transaction() as c:
        user_table.delete(name='Alice', _cursor_=c)

    assert user_table.select_one(name='Alice') is None
    assert user_table.select_count() == 1


def test_immediate_transaction_with_upsert(user_table, sample_users):
    """Test upsert within immediate transaction"""
    user_table.insert_row(sample_users[:1])
    existing = user_table.select_one(name='Alice')

    with user_table.immediate_transaction() as c:
        updated = User(id=existing.id, name='Alice', age=30, email='new@example.com')
        user_table.upsert_row(updated, _cursor_=c)

    result = user_table.select_by_pk(existing.id)
    assert result.age == 30


def test_immediate_transaction_mixed_operations(user_table, sample_users):
    """Test mixed operations in immediate transaction"""
    user_table.insert_row(sample_users[:3])

    with user_table.immediate_transaction() as c:
        # Delete one
        user_table.delete(name='Bob', _cursor_=c)
        # Update existing
        alice = user_table.select_one(name='Alice', _cursor_=c)
        user_table.update_row(User(id=alice.id, name=alice.name, age=30, email=alice.email), _cursor_=c)
        # Insert one
        user_table.insert_row(User(id=0, name='David', age=28, email='david@example.com'), _cursor_=c)

    assert user_table.select_count() == 3
    assert user_table.select_one(name='Alice').age == 30
    assert user_table.select_one(name='Bob') is None
    assert user_table.select_one(name='David') is not None


def test_immediate_transaction_auto_creates_table(user_table):
    """The cursor carries CREATE_TABLE, so a first write creates the table"""
    user_table.drop_table()

    with user_table.immediate_transaction() as c:
        user_table.insert_row(User(id=0, name='Alice', age=25, email='alice@example.com'), _cursor_=c)

    assert user_table.select_count() == 1


def test_immediate_transaction_multiple_operations(user_table, sample_users):
    """Test multiple insert batches in one transaction"""
    with user_table.immediate_transaction() as c:
        user_table.insert_row(sample_users[:2], _cursor_=c)
        assert len(user_table.select(_cursor_=c)) == 2
        user_table.insert_row(sample_users[2:], _cursor_=c)
        assert len(user_table.select(_cursor_=c)) == 5

    assert user_table.select_count() == 5


def test_immediate_transaction_prevents_partial_commit(user_table, sample_users):
    """Test that all operations commit or none do"""
    with pytest.raises(RuntimeError):
        with user_table.immediate_transaction() as c:
            user_table.insert_row(sample_users[:2], _cursor_=c)
            user_table.insert_row(sample_users[2:4], _cursor_=c)
            raise RuntimeError("Abort transaction")

    assert user_table.select_count() == 0


def test_immediate_transaction_auto_close_cursor(user_table):
    """The cursor is closed on success, so the pool can hand it out again"""
    with user_table.immediate_transaction() as c:
        user_table.insert_row(User(id=0, name='Test', age=25, email='test@example.com'), _cursor_=c)

    with pytest.raises(sqlite3.ProgrammingError):
        c.execute('SELECT 1')
    assert user_table.select_count() == 1


def test_immediate_transaction_closes_cursor_on_exception(user_table):
    """The cursor is closed when the body raises, after the rollback"""
    with pytest.raises(ValueError):
        with user_table.immediate_transaction() as c:
            raise ValueError("Test exception")

    with pytest.raises(sqlite3.ProgrammingError):
        c.execute('SELECT 1')
    assert user_table.select_count() == 0
