"""
Tests for AlasioTable.get_data_version()
"""
import msgspec
import pytest

from alasio.db.table import AlasioTable


# ============================================================================
# Test Models and Tables
# ============================================================================

class VersionRow(msgspec.Struct):
    """Simple model for testing data_version"""
    id: int = 0
    value: str = ''


class DataVersionTable(AlasioTable):
    """Simple table for testing data_version PRAGMA"""
    TABLE_NAME = 'test_data_version'
    CREATE_TABLE = '''
        CREATE TABLE "{TABLE_NAME}" (
            "id" INTEGER PRIMARY KEY AUTOINCREMENT,
            "value" TEXT NOT NULL
        )
    '''
    MODEL = VersionRow


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def table():
    """Create a DataVersionTable using in-memory database"""
    t = DataVersionTable(':memory:')
    t.create_table()
    return t


# ============================================================================
# Tests
# ============================================================================

class TestGetDataVersion:
    """Tests for AlasioTable.get_data_version()"""

    def test_returns_int(self, table):
        """get_data_version should return an int"""
        result = table.get_data_version()
        assert isinstance(result, int)
        assert result >= 0

    def test_with_explicit_cursor(self, table):
        """get_data_version with an explicit cursor should work"""
        with table.cursor() as c:
            result = table.get_data_version(_cursor_=c)
            assert isinstance(result, int)
            assert result >= 0

    def test_with_cursor_and_without_cursor_consistency(self, table):
        """With and without explicit cursor should return the same version"""
        v1 = table.get_data_version()
        with table.cursor() as c:
            v2 = table.get_data_version(_cursor_=c)
        assert v1 == v2

    def test_multiple_calls_return_same_value(self, table):
        """Multiple calls without modifications should return the same version"""
        v1 = table.get_data_version()
        v2 = table.get_data_version()
        v3 = table.get_data_version()
        assert v1 == v2 == v3

    def test_on_file_database(self, tmp_path):
        """Test data_version works on a file-based database"""
        db_path = str(tmp_path / 'test_version.db')
        t = DataVersionTable(db_path)
        t.create_table()

        result = t.get_data_version()
        assert isinstance(result, int)
        assert result >= 0

    def test_on_multiple_file_databases(self, tmp_path):
        """Different file databases have independent data_version"""
        db1 = str(tmp_path / 'test_version_1.db')
        db2 = str(tmp_path / 'test_version_2.db')

        t1 = DataVersionTable(db1)
        t1.create_table()
        t2 = DataVersionTable(db2)
        t2.create_table()

        v1 = t1.get_data_version()
        v2 = t2.get_data_version()
        assert isinstance(v1, int)
        assert isinstance(v2, int)
