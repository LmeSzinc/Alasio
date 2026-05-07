import functools
from functools import cached_property as functools_cached_property

import pytest
from msgspec import Struct

from ExampleMod.module.config.const import entry
from alasio.config.base import AlasioConfigBase
from alasio.config.entry.mod import Mod
from alasio.config.group_proxy import GroupProxy, batch_set
from alasio.db.conn import SQLITE_POOL
from alasio.ext.cache import cached_property as alasio_cached_property
from alasio.logger import logger


# Define a custom decorator with wraps
def custom_deco_wrapped(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapper


# Define a custom decorator without wraps
def custom_deco_unwrapped(func):
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapper


class ProxyTestGroup(Struct, dict=True):
    Value1: int = 0
    Value2: int = 0

    @batch_set
    def simple_batch(self):
        """1. Trigger save only once"""
        self.Value1 = 1
        self.Value2 = 2

    @batch_set
    def nested_batch(self):
        """2. Nested batch_set, should not re-enter context (depth should manage this)"""
        self.Value1 = 3
        self.simple_batch()

    def no_batch(self):
        """3. No batch_set, should not enter context"""
        self.Value1 = 4
        self.Value2 = 5

    @property
    @batch_set
    def property_batch(self):
        """4. @property @batch_set: should behave the same as 1"""
        self.Value1 = 6
        self.Value2 = 7
        return 0

    @functools_cached_property
    @batch_set
    def functools_cached_batch(self):
        """5. functools.cached_property @batch_set: should behave the same as 1"""
        self.Value1 = 8
        self.Value2 = 9
        return 1

    @alasio_cached_property
    @batch_set
    def alasio_cached_batch(self):
        """5. alasio.ext.cache.cached_property @batch_set: should behave the same as 1"""
        self.Value1 = 10
        self.Value2 = 11
        return 2

    @custom_deco_wrapped
    @batch_set
    def custom_wrapped_batch(self):
        """6. Custom deco with wraps: should behave the same as 1"""
        self.Value1 = 12
        self.Value2 = 13

    @custom_deco_unwrapped
    @batch_set
    def custom_unwrapped_batch(self):
        """7. Custom deco without wraps: warning and save on every set"""
        self.Value1 = 14
        self.Value2 = 15

    @classmethod
    @batch_set
    def class_batch(cls):
        """8. classmethod should not enter context"""
        return "class"

    @staticmethod
    @batch_set
    def static_batch():
        """8. staticmethod should not enter context"""
        return "static"


class MockMod:
    def __init__(self):
        self.name = "MockMod"
        self.entry = None

    def get_group_model(self, file, cls):
        return ProxyTestGroup

    def config_set(self, name, event):
        return None, type('Response', (), {'error': None})

    def config_batch_set(self, name, events):
        return None, [type('Response', (), {'error': None}) for _ in events]

    def task_index_data(self):
        return {
            'Main': type('TaskRef', (), {
                'group': {
                    'TestGroup': type('GroupRef', (), {
                        'task': 'Main', 'file': 'test.py', 'cls': 'ProxyTestGroup'
                    })
                }
            })
        }


@pytest.fixture(scope='module')
def example_mod():
    """Get the example mod from MOD_LOADER"""
    mod = Mod(entry)
    return mod


@pytest.fixture(autouse=True)
def cleanup_memory_db():
    """Clear memory database after each test"""
    yield
    # delete_file(':memory:') will release the pool and clear the database
    SQLITE_POOL.delete_file(':memory:')


class TestGroupProxy:
    """Test suite for GroupProxy wrapper"""

    TEST_CONFIG_NAME = ':memory:'

    @pytest.fixture
    def config(self, example_mod):
        """Create test config instance"""

        class MyConfig(AlasioConfigBase):
            entry = example_mod.entry
            Scheduler: "scheduler.Scheduler"

        return MyConfig(self.TEST_CONFIG_NAME, task='Main')

    def test_proxy_getattr(self, config):
        """Test GroupProxy attribute access"""
        proxy = config.Scheduler
        assert type(proxy) is GroupProxy

        # Should proxy to underlying object
        assert proxy.Enable is False
        assert proxy.ServerUpdate == '00:00'

    def test_proxy_setattr_registers_modify(self, config):
        """Test GroupProxy attribute setting registers modification"""
        config.auto_save = False

        proxy = config.Scheduler
        proxy.Enable = True

        # Should register modification
        key = ('Main', 'Scheduler', 'Enable')
        assert key in config._modified

    def test_proxy_repr(self, config):
        """Test GroupProxy __repr__"""
        proxy = config.Scheduler
        repr_str = repr(proxy)

        # Should proxy to underlying object's repr
        assert 'Scheduler' in repr_str or 'Enable' in repr_str

    def test_proxy_str(self, config):
        """Test GroupProxy __str__"""
        proxy = config.Scheduler
        str_str = str(proxy)

        # Should proxy to underlying object's str
        assert 'Scheduler' in str_str or 'Enable' in str_str


class TestModelProxyBatchSet:
    TEST_CONFIG_NAME = ':memory:'

    @pytest.fixture
    def config(self):
        class Entry:
            name = 'MockMod'
            root = '.'
            path_config = '.'
            path_assets = '.'

            @staticmethod
            def alasio():
                return Entry

        class MyConfig(AlasioConfigBase):
            entry = Entry
            TestGroup: "test.ProxyTestGroup"

            def __init__(self, *args, **kwargs):
                self.save_count = 0
                super().__init__(*args, **kwargs)

            def save(self):
                self.save_count += 1
                return super().save()

        # Mock mod in self.mod
        cfg = MyConfig(self.TEST_CONFIG_NAME, task='')
        cfg.mod = MockMod()
        cfg.mod.entry = cfg.entry
        cfg.task = 'Main'
        cfg.init_task()
        # Reset save count after initialization
        cfg.save_count = 0
        return cfg

    def test_1_simple_batch(self, config):
        """
        Method decorated with batch_set should only trigger save once
        """
        config.TestGroup.simple_batch()
        # Value1 = 1, Value2 = 2. Each would trigger save if not batched.
        # Plus any initial saves during init? init_task might trigger save if auto_save is True and there are
        # modifications.
        # But here it's fresh.
        # We check save_count.
        assert config.TestGroup.Value1 == 1
        assert config.TestGroup.Value2 == 2
        # One save for the batch
        assert config.save_count == 1

    def test_2_nested_batch(self, config):
        """
        Method decorated with batch_set calling another batch_set decorated method should not re-enter context
        """
        config.TestGroup.nested_batch()
        # nested_batch sets Value1=3, then simple_batch sets Value1=1, Value2=2
        assert config.TestGroup.Value1 == 1
        assert config.TestGroup.Value2 == 2
        # Only one save for the outermost batch
        assert config.save_count == 1

    def test_3_no_batch(self, config):
        """
        Method without decorator should not enter context
        """
        config.TestGroup.no_batch()
        assert config.TestGroup.Value1 == 4
        assert config.TestGroup.Value2 == 5
        # Each modification triggers a save
        assert config.save_count == 2

    def test_4_property_batch(self, config):
        """
        Method with multiple decorators @property and @batch_set should behave the same as case 1
        """
        # Accessing the property should trigger the batch context
        _ = config.TestGroup.property_batch
        assert config.TestGroup.Value1 == 6
        assert config.TestGroup.Value2 == 7
        assert config.save_count == 1

    def test_5_functools_cached_batch(self, config):
        """
        Method with multiple decorators @functools.cached_property and @batch_set should behave the same as case 1
        """
        _ = config.TestGroup.functools_cached_batch
        assert config.TestGroup.Value1 == 8
        assert config.TestGroup.Value2 == 9
        assert config.save_count == 1

    def test_5_alasio_cached_batch(self, config):
        """
        Method with multiple decorators @alasio_cached_property and @batch_set should behave the same as case 1
        """
        _ = config.TestGroup.alasio_cached_batch
        assert config.TestGroup.Value1 == 10
        assert config.TestGroup.Value2 == 11
        assert config.save_count == 1

    def test_6_custom_wrapped_batch(self, config):
        """
        Method with multiple decorators custom decorator and @batch_set should behave the same as case 1
        """
        config.TestGroup.custom_wrapped_batch()
        assert config.TestGroup.Value1 == 12
        assert config.TestGroup.Value2 == 13
        assert config.save_count == 1

    def test_7_custom_unwrapped_batch(self, config):
        """
        Method with multiple decorators custom decorator and @batch_set,
        but the custom decorator does not use @functools.wraps()
        """
        with logger.mock_capture_writer() as capture:
            config.TestGroup.custom_unwrapped_batch()

        assert config.TestGroup.Value1 == 14
        assert config.TestGroup.Value2 == 15
        # No batch detected because of missing wraps and name mismatch (wrapper != custom_unwrapped_batch)
        # So it should trigger save on each set
        assert config.save_count == 2

        # Check for warning
        assert any("Un-wrapped decorator detected" in log['m'] for log in capture.backend.logs)

    def test_8_class_static_methods(self, config):
        """classmethod and staticmethod should not enter context"""
        # They should return values normally but not trigger batching
        # Actually they don't modify instance values in this test, but we check if they work
        assert config.TestGroup.class_batch() == "class"
        assert config.TestGroup.static_batch() == "static"
        assert config.save_count == 0
