import datetime as d
import functools
import typing as t
from functools import cached_property as functools_cached_property

import msgspec as m
import pytest
import typing_extensions as e
from msgspec import Struct

from ExampleMod.module.config.const import entry
from alasio.base.timer import getnow
from alasio.config.alasio.group_proxy import GroupProxy, batch_set
from alasio.config.alasio.store_model import DashboardAmount, DashboardBase
from alasio.config.base import AlasioConfigBase
from alasio.config.entry.mod import Mod
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

    @alasio_cached_property
    def side_effect_cached(self):
        """9. A cached property with side effect to track calculation"""
        # Ensure 'self' is the underlying Struct, not the GroupProxy
        if type(self) is not ProxyTestGroup:
            raise TypeError(f"Expected ProxyTestGroup, got {type(self)}")
        self.Value1 += 1
        return self.Value1 + 100

    @batch_set
    def batch_call_cached(self):
        """10. Access another cached property within a batch_set method"""
        return self.side_effect_cached

    @functools_cached_property
    def side_effect_functools_cached(self):
        """11. A functools cached property with side effect to track calculation"""
        # Ensure 'self' is the underlying Struct, not the GroupProxy
        if type(self) is not ProxyTestGroup:
            raise TypeError(f"Expected ProxyTestGroup, got {type(self)}")
        self.Value2 += 1
        return self.Value2 + 200

    @batch_set
    def batch_call_functools_cached(self):
        """12. Access another functools cached property within a batch_set method"""
        return self.side_effect_functools_cached


class MockResponse:
    """Minimal mock for config set response."""
    def __init__(self):
        self.error = None


class MockMod:
    def __init__(self):
        self.name = "MockMod"
        self.entry = None

    def get_group_model(self, file, cls):
        return ProxyTestGroup

    def config_set(self, name, event):
        return None, [MockResponse()]

    def config_batch_set(self, name, events):
        return None, [MockResponse() for _ in events]

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
    with logger.mock_capture_writer():
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
        # One save for the batch
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

    def test_9_no_config_direct_group(self):
        """
        When creating a group directly without config (no GroupProxy),
        batch_set decorated methods should simply set attributes without any extra effects.
        The _alasio_batch_set flag is only consumed by GroupProxy.__getattr__,
        so calling methods on a raw struct has no batching/save behavior.
        """
        group = ProxyTestGroup()

        # 1. simple_batch: sets Value1=1, Value2=2
        group.simple_batch()
        assert group.Value1 == 1
        assert group.Value2 == 2

        # 2. nested_batch: sets Value1=3, then simple_batch sets Value1=1, Value2=2
        group.nested_batch()
        assert group.Value1 == 1
        assert group.Value2 == 2

        # 3. no_batch: sets Value1=4, Value2=5 (no decorator at all)
        group.no_batch()
        assert group.Value1 == 4
        assert group.Value2 == 5

        # 4. property_batch: @property @batch_set, accessing property sets values and returns 0
        result = group.property_batch
        assert result == 0
        assert group.Value1 == 6
        assert group.Value2 == 7

        # 5. functools_cached_batch: @functools.cached_property @batch_set
        result = group.functools_cached_batch
        assert result == 1
        assert group.Value1 == 8
        assert group.Value2 == 9

        # 5. alasio_cached_batch: @alasio_cached_property @batch_set
        result = group.alasio_cached_batch
        assert result == 2
        assert group.Value1 == 10
        assert group.Value2 == 11

        # 6. custom_wrapped_batch: @custom_deco_wrapped @batch_set
        group.custom_wrapped_batch()
        assert group.Value1 == 12
        assert group.Value2 == 13

        # 7. custom_unwrapped_batch: @custom_deco_unwrapped @batch_set
        group.custom_unwrapped_batch()
        assert group.Value1 == 14
        assert group.Value2 == 15

        # 8. classmethod and staticmethod: should work normally
        assert group.class_batch() == "class"
        assert group.static_batch() == "static"

    def test_10_cached_property_on_proxy(self, config):
        """
        If a cached_property is accessed within a @batch_set method,
        it should be cached on the underlying Struct, not the GroupProxy.
        """
        proxy = config.TestGroup
        obj = proxy._obj

        # Ensure it's not yet cached
        assert 'side_effect_cached' not in obj.__dict__

        # Call the batch method which accesses the cached property
        # Value1 starts at 0. side_effect_cached increments it to 1 and returns 101.
        res = proxy.batch_call_cached()
        assert res == 101
        assert proxy.Value1 == 1

        # It should be cached on the underlying object
        assert 'side_effect_cached' in obj.__dict__
        assert obj.__dict__['side_effect_cached'] == 101

        # Subsequent access should not recalculate
        res2 = proxy.batch_call_cached()
        assert res2 == 101
        assert proxy.Value1 == 1  # Still 1

    def test_11_functools_cached_property_on_proxy(self, config):
        """
        If a functools.cached_property is accessed within a @batch_set method,
        it should be cached on the underlying Struct, not the GroupProxy.
        """
        proxy = config.TestGroup
        obj = proxy._obj

        # Ensure it's not yet cached
        assert 'side_effect_functools_cached' not in obj.__dict__

        # Call the batch method which accesses the cached property
        # Value2 starts at 0. side_effect_functools_cached increments it to 1 and returns 201.
        res = proxy.batch_call_functools_cached()
        assert res == 201
        assert proxy.Value2 == 1

        # It should be cached on the underlying object
        assert 'side_effect_functools_cached' in obj.__dict__
        assert obj.__dict__['side_effect_functools_cached'] == 201

        # Subsequent access should not recalculate
        res2 = proxy.batch_call_functools_cached()
        assert res2 == 201
        assert proxy.Value2 == 1  # Still 1


# ---- Test struct for dashboard expiration ----

class DashboardExpireTestGroup(DashboardAmount):
    """
    DashboardAmount with ge=0, le=100 and ServerUpdate set to '00:00'
    """
    Value: e.Annotated[int, m.Meta(ge=0, le=100)] = 0
    ServerUpdate: t.Literal['00:00'] = '00:00'


class MockDashboardMod:
    """Mock mod that returns DashboardExpireTestGroup"""
    def __init__(self):
        self.name = "MockDashboardMod"
        self.entry = None

    def get_group_model(self, file, cls):
        return DashboardExpireTestGroup

    def config_set(self, name, event):
        return None, type('Response', (), {'error': None})

    def config_batch_set(self, name, events):
        return None, [type('Response', (), {'error': None}) for _ in events]

    def task_index_data(self):
        return {
            'Main': type('TaskRef', (), {
                'group': {
                    'DashboardExpireTestGroup': type('GroupRef', (), {
                        'task': 'Main', 'file': 'test.py', 'cls': 'DashboardExpireTestGroup'
                    })
                }
            })
        }


class TestDashboardExpire:
    """Test is_expired() and update() through GroupProxy"""

    TEST_CONFIG_NAME = ':memory:'

    @pytest.fixture
    def config(self):
        """Create test config with DashboardExpireTestGroup"""
        class Entry:
            name = 'MockDashboardMod'
            root = '.'
            path_config = '.'
            path_assets = '.'
            @staticmethod
            def alasio():
                return Entry

        class MyConfig(AlasioConfigBase):
            entry = Entry
            DashboardExpireTestGroup: "test.DashboardExpireTestGroup"

        cfg = MyConfig(self.TEST_CONFIG_NAME, task='')
        cfg.mod = MockDashboardMod()
        cfg.mod.entry = cfg.entry
        cfg.task = 'Main'
        cfg.init_task()
        cfg.save_count = 0
        return cfg

    def test_is_expired_not_expired(self, config):
        """
        is_expired() returns False when Time is before the next update.
        Current time (now) is before tomorrow's 00:00 (UTC+8), so not expired.
        """
        obj = config.DashboardExpireTestGroup._obj
        now = getnow()
        # Set Time to now — not expired
        obj.Time = now
        assert not config.DashboardExpireTestGroup.is_expired()

    def test_is_expired_expired(self, config):
        """
        is_expired() returns True when Time is after the next update.
        Time set to well in the future (now + 2 days) is after the next 00:00.
        """
        obj = config.DashboardExpireTestGroup._obj
        now = getnow()
        future = now + d.timedelta(days=2)
        obj.Time = future
        assert config.DashboardExpireTestGroup.is_expired()

    def test_update_resets_when_expired(self, config):
        """
        update() calls reset() when is_expired() returns True.
        Value is reset to meta.ge (0).
        """
        obj = config.DashboardExpireTestGroup._obj
        now = getnow()
        future = now + d.timedelta(days=2)
        obj.Value = 50
        obj.Time = future
        config.DashboardExpireTestGroup.update()
        assert obj.Value == 0
        # Total is not relevant for DashboardAmount

    def test_update_does_nothing_when_not_expired(self, config):
        """
        update() does NOT call reset() when is_expired() returns False.
        Value remains unchanged.
        """
        obj = config.DashboardExpireTestGroup._obj
        now = getnow()
        obj.Value = 50
        obj.Time = now
        config.DashboardExpireTestGroup.update()
        assert obj.Value == 50

    def test_is_expired_empty_server_update(self, config):
        """
        When ServerUpdate is changed to '', is_expired() returns False
        without calling get_servertime().
        """
        obj = config.DashboardExpireTestGroup._obj
        obj.ServerUpdate = ''
        future = getnow() + d.timedelta(days=365)
        obj.Time = future
        assert not config.DashboardExpireTestGroup.is_expired()