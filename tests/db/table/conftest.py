"""
Shared pytest fixtures and test models for AlasioTable testing
"""
import os
import time

import msgspec
import pytest

from alasio.db.conn import SQLITE_POOL
from alasio.db.table import AlasioTable

# ============================================================================
# Test directory setup
# ============================================================================

TEST_DIR = os.path.dirname(os.path.abspath(__file__))
TEST_DATA_DIR = os.path.join(TEST_DIR, 'test_data')


def setup_test_dir():
    """Create test data directory if not exists"""
    os.makedirs(TEST_DATA_DIR, exist_ok=True)


def cleanup_test_dir():
    """Clean up test data directory"""
    if os.path.exists(TEST_DATA_DIR):
        for file in os.listdir(TEST_DATA_DIR):
            file_path = os.path.join(TEST_DATA_DIR, file)
            try:
                if os.path.isfile(file_path):
                    os.unlink(file_path)
            except Exception:
                pass


# ============================================================================
# Test Models
# ============================================================================

class User(msgspec.Struct):
    """Simple user model for testing"""
    id: int = 0
    name: str = ''
    age: int = 0
    email: str = ''


class Product(msgspec.Struct):
    """Product model with unique constraint for testing"""
    id: int = 0
    sku: str = ''
    name: str = ''
    price: float = 0.0
    stock: int = 0


# ============================================================================
# Test Table Classes
# ============================================================================

class UserTable(AlasioTable):
    """User table for testing"""
    TABLE_NAME = 'users'
    PRIMARY_KEY = 'id'
    AUTO_INCREMENT = 'id'
    CREATE_TABLE = '''
        CREATE TABLE "{TABLE_NAME}" (
            "id" INTEGER PRIMARY KEY AUTOINCREMENT,
            "name" TEXT NOT NULL,
            "age" INTEGER NOT NULL,
            "email" TEXT NOT NULL
        )
    '''
    MODEL = User

    def select_count(self, **kwargs):
        """Count rows matching the condition"""
        return len(self.select(**kwargs))

    def select_by_pk(self, pk_value):
        """Select row by primary key"""
        return self.select_one(**{self.PRIMARY_KEY: pk_value})


class ProductTable(AlasioTable):
    """Product table with unique constraint for testing"""
    TABLE_NAME = 'products'
    PRIMARY_KEY = 'id'
    AUTO_INCREMENT = 'id'
    CREATE_TABLE = '''
        CREATE TABLE "{TABLE_NAME}" (
            "id" INTEGER PRIMARY KEY AUTOINCREMENT,
            "sku" TEXT NOT NULL UNIQUE,
            "name" TEXT NOT NULL,
            "price" REAL NOT NULL,
            "stock" INTEGER NOT NULL,
            UNIQUE ("sku", "name")
        )
    '''
    MODEL = Product

    def select_count(self, **kwargs):
        """Count rows matching the condition"""
        return len(self.select(**kwargs))

    def select_by_pk(self, pk_value):
        """Select row by primary key"""
        return self.select_one(**{self.PRIMARY_KEY: pk_value})


# ============================================================================
# Shared Fixtures
# ============================================================================

@pytest.fixture(scope='session', autouse=True)
def test_environment():
    """Setup and teardown test environment"""
    setup_test_dir()
    yield
    cleanup_test_dir()


@pytest.fixture
def temp_db():
    """Create temporary database file"""
    db_path = os.path.join(TEST_DATA_DIR, f'test_{os.getpid()}_{id(temp_db)}.db')
    yield db_path

    # Release all connections to this database
    try:
        SQLITE_POOL.delete_file(db_path)
    except Exception:
        pass

    # Cleanup - wait a bit for Windows to release file locks
    time.sleep(0.05)
    try:
        os.unlink(db_path)
    except (FileNotFoundError, PermissionError, OSError):
        # On Windows, file may still be locked, it's ok
        pass


@pytest.fixture
def memory_db():
    """Use in-memory database for testing"""
    return ':memory:'


@pytest.fixture
def user_table(temp_db):
    """Create a UserTable instance with temp database"""
    table = UserTable(temp_db)
    table.create_table()
    yield table
    # Cleanup is handled by temp_db fixture


@pytest.fixture
def product_table(temp_db):
    """Create a ProductTable instance with temp database"""
    table = ProductTable(temp_db)
    table.create_table()
    yield table


@pytest.fixture
def user_table_memory(memory_db):
    """Create a UserTable instance with memory database"""
    table = UserTable(memory_db)
    table.create_table()
    return table


@pytest.fixture
def sample_users():
    """Sample user data for testing"""
    return [
        User(id=0, name='Alice', age=25, email='alice@example.com'),
        User(id=0, name='Bob', age=30, email='bob@example.com'),
        User(id=0, name='Charlie', age=35, email='charlie@example.com'),
        User(id=0, name='David', age=28, email='david@example.com'),
        User(id=0, name='Eve', age=32, email='eve@example.com'),
    ]


@pytest.fixture
def sample_products():
    """Sample product data for testing"""
    return [
        Product(id=0, sku='SKU001', name='Laptop', price=999.99, stock=10),
        Product(id=0, sku='SKU002', name='Mouse', price=29.99, stock=50),
        Product(id=0, sku='SKU003', name='Keyboard', price=79.99, stock=30),
        Product(id=0, sku='SKU004', name='Monitor', price=299.99, stock=15),
    ]
