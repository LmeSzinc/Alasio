import datetime as d

import pytest

from alasio.config.base import AlasioConfigBase, ModelProxy
from alasio.config.const import DataInconsistent
from alasio.config.entry.loader import MOD_LOADER
from alasio.config.table.config import AlasioConfigTable, ConfigRow
from alasio.config.table.scan import ScanTable


# Module-level fixtures shared across all test classes
@pytest.fixture(scope='module')
def example_mod():
    """Get the example mod from MOD_LOADER"""
    mod = MOD_LOADER.dict_mod.get('example_mod')
    if mod is None:
        pytest.skip("example_mod not available")
    return mod


@pytest.fixture(scope='module')
def config_cls(example_mod):
    """Create a dynamic config class for testing"""

    class MyConfig(AlasioConfigBase):
        entry = example_mod.entry
        # Annotation mapping group name to "nav.Class"
        Scheduler: "scheduler.Scheduler"

    return MyConfig


@pytest.fixture(scope='module', autouse=True)
def cleanup_all_configs():
    """Clean up all test config files before and after all tests"""
    scan_table = ScanTable()
    test_configs = [
        'test_config_base',
        'test_config_modify',
        'test_config_override',
        'test_config_override_unbound',
        'test_config_temporary',
        'test_config_const',
        'test_model_proxy',
        'test_config_edge',
        'test_config_edge_bad',
        'test_config_edge_bad2',
    ]

    # Cleanup before tests
    for config_name in test_configs:
        try:
            scan_table.config_del(config_name)
        except Exception:
            pass

    yield

    # Cleanup after tests
    for config_name in test_configs:
        try:
            scan_table.config_del(config_name)
        except Exception:
            pass


class TestAlasioConfigBase:
    """Test suite for AlasioConfigBase lifecycle management"""

    # Use a single test config for all tests to reduce file creation
    TEST_CONFIG_NAME = 'test_config_base'

    def test_config_initialization(self, config_cls):
        """Test basic config initialization"""
        config = config_cls(self.TEST_CONFIG_NAME, task='Main')

        assert config.config_name == self.TEST_CONFIG_NAME
        assert config.task == 'Main'
        assert config.auto_save is True
        assert config._batch_depth == 0
        assert config.is_template_config is False

    def test_template_config_readonly(self, config_cls):
        """Test that template config has auto_save disabled"""
        config = config_cls('template_test', task='Main')

        assert config.is_template_config is True
        assert config.auto_save is False

    def test_config_no_entry_raises_error(self):
        """Test that config without entry raises DataInconsistent"""

        class BadConfig(AlasioConfigBase):
            pass

        with pytest.raises(DataInconsistent):
            BadConfig('test', task='Main')

    def test_bound_group_access(self, config_cls):
        """Test accessing bound group returns ModelProxy"""
        config = config_cls(self.TEST_CONFIG_NAME, task='Main')

        # Scheduler is bound to Main task
        scheduler = config.Scheduler
        assert type(scheduler) is ModelProxy
        assert scheduler._task == 'Main'
        assert scheduler._group == 'Scheduler'

    def test_bound_group_default_values(self, config_cls):
        """Test bound group has correct default values"""
        config = config_cls(self.TEST_CONFIG_NAME, task='Main')

        assert config.Scheduler.Enable is False
        assert config.Scheduler.NextRun == d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)
        assert config.Scheduler.ServerUpdate == '00:00'

    def test_unbound_group_fallback(self, config_cls):
        """Test accessing unbound group triggers fallback"""

        class ConfigWithUnbound(AlasioConfigBase):
            entry = config_cls.entry
            # UnboundGroup is not in Main task
            UnboundGroup: "scheduler.Scheduler"

        config = ConfigWithUnbound(self.TEST_CONFIG_NAME, task='Main')

        # Access should trigger fallback, not bound to task
        group = config.UnboundGroup
        # Should be plain Struct, not ModelProxy
        assert type(group).__name__ == 'Scheduler'
        # Default values should work
        assert group.Enable is False

    def test_invalid_group_access_raises_error(self, config_cls):
        """Test accessing non-existent group raises AttributeError"""
        config = config_cls(self.TEST_CONFIG_NAME, task='Main')

        with pytest.raises(AttributeError):
            _ = config.NonExistentGroup

    def test_init_task_clears_cache(self, config_cls):
        """Test that init_task clears cache and reloads from DB"""
        config = config_cls(self.TEST_CONFIG_NAME, task='Main')
        config.auto_save = False

        # Set a value without saving
        config.Scheduler.Enable = True
        assert config.Scheduler.Enable is True
        assert len(config.modified) == 1

        # Re-init should clear modifications and reload from DB (which has default False)
        config.init_task()
        assert config.Scheduler.Enable is False
        assert len(config.modified) == 0

    def test_init_task_with_no_task(self, config_cls):
        """Test init_task with empty task does nothing"""
        config = config_cls(self.TEST_CONFIG_NAME, task='')

        # Should not raise error
        config.init_task()

        # dict_value should be empty
        assert len(config.dict_value) == 0

    def test_init_task_with_invalid_task(self, config_cls):
        """Test initialization with invalid task raises KeyError"""
        # KeyError happens during __init__ when task is invalid
        with pytest.raises(KeyError):
            config_cls(self.TEST_CONFIG_NAME, task='InvalidTask')


class TestConfigModification:
    """Test suite for config modification and saving"""

    TEST_CONFIG_NAME = 'test_config_modify'

    @pytest.fixture
    def config(self, example_mod):
        """Create test config instance"""

        class MyConfig(AlasioConfigBase):
            entry = example_mod.entry
            Scheduler: "scheduler.Scheduler"

        return MyConfig(self.TEST_CONFIG_NAME, task='Main')

    def test_single_modification_auto_save(self, config):
        """Test single modification triggers auto save"""
        # Set value
        config.Scheduler.Enable = True

        # Check modified dict is cleared after auto save
        assert len(config.modified) == 0

        # Verify persisted to DB
        table = AlasioConfigTable(self.TEST_CONFIG_NAME)
        row = table.select_one(task='Main', group='Scheduler')
        assert row is not None

        # Re-init and verify
        config.init_task()
        assert config.Scheduler.Enable is True

    def test_multiple_modifications_auto_save(self, config):
        """Test multiple modifications each trigger auto save"""
        config.Scheduler.Enable = True
        config.Scheduler.ServerUpdate = '12:00'

        # Re-init and verify
        config.init_task()
        assert config.Scheduler.Enable is True
        assert config.Scheduler.ServerUpdate == '12:00'

    def test_modification_without_auto_save(self, config):
        """Test modification without auto save"""
        config.auto_save = False

        config.Scheduler.Enable = True

        # Modified dict should contain the change
        assert len(config.modified) == 1
        key = ('Main', 'Scheduler', 'Enable')
        assert key in config.modified

        # Manual save
        config.save()
        assert len(config.modified) == 0

    def test_batch_set_context_manager(self, config):
        """Test batch_set context manager"""
        with config.batch_set():
            config.Scheduler.Enable = True
            config.Scheduler.ServerUpdate = '15:00'
            # Should not save yet
            assert len(config.modified) == 2

        # Should save after context exit
        assert len(config.modified) == 0

        # Verify persisted
        config.init_task()
        assert config.Scheduler.Enable is True
        assert config.Scheduler.ServerUpdate == '15:00'

    def test_nested_batch_set(self, config):
        """Test nested batch_set context managers"""
        with config.batch_set():
            config.Scheduler.Enable = True
            assert config._batch_depth == 1

            with config.batch_set():
                config.Scheduler.ServerUpdate = '18:00'
                assert config._batch_depth == 2
                # Should not save yet
                assert len(config.modified) == 2

            # Should not save at inner exit
            assert config._batch_depth == 1
            assert len(config.modified) == 2

        # Should save after outermost exit
        assert config._batch_depth == 0
        assert len(config.modified) == 0

    def test_batch_set_without_auto_save(self, config):
        """Test batch_set when auto_save is disabled"""
        config.auto_save = False

        with config.batch_set():
            config.Scheduler.Enable = True
            config.Scheduler.ServerUpdate = '20:00'

        # Should not save even after context exit
        assert len(config.modified) == 2

    def test_save_empty_modifications(self, config):
        """Test save() with no modifications does nothing"""
        result = config.save()
        assert result is False

    def test_register_modify(self, config):
        """Test register_modify directly"""
        config.auto_save = False

        config.register_modify(
            task='Main',
            group='Scheduler',
            arg='Enable',
            value=True
        )

        # Check modified dict
        key = ('Main', 'Scheduler', 'Enable')
        assert key in config.modified
        event = config.modified[key]
        assert event.task == 'Main'
        assert event.group == 'Scheduler'
        assert event.arg == 'Enable'
        assert event.value is True


class TestConfigOverride:
    """Test suite for config override functionality"""

    TEST_CONFIG_NAME = 'test_config_override'

    @pytest.fixture
    def config(self, example_mod):
        """Create test config instance"""

        class MyConfig(AlasioConfigBase):
            entry = example_mod.entry
            Scheduler: "scheduler.Scheduler"

        return MyConfig(self.TEST_CONFIG_NAME, task='Main')

    def test_override_config_value(self, config):
        """Test override changes config value in memory"""
        # Default is False
        assert config.Scheduler.Enable is False

        # Override
        prev_config, prev_const = config.override(Scheduler_Enable=True)

        # Value should change
        assert config.Scheduler.Enable is True

        # Should return previous value
        assert prev_config['Scheduler']['Enable'] is False

    def test_override_persistence_across_init_task(self, config):
        """Test that override persists across init_task"""
        config.override(Scheduler_Enable=True)
        assert config.Scheduler.Enable is True

        # Re-init task
        config.init_task()

        # Override should persist
        assert config.Scheduler.Enable is True

    def test_override_multiple_values(self, config):
        """Test overriding multiple values at once"""
        prev_config, prev_const = config.override(
            Scheduler_Enable=True,
            Scheduler_ServerUpdate='06:00'
        )

        assert config.Scheduler.Enable is True
        assert config.Scheduler.ServerUpdate == '06:00'

        # Check previous values
        assert prev_config['Scheduler']['Enable'] is False
        assert prev_config['Scheduler']['ServerUpdate'] == '00:00'

    def test_override_does_not_save_to_db(self, config):
        """Test that override does not trigger DB save"""
        config.override(Scheduler_Enable=True)

        # Check DB - should be empty or have default values
        config.init_task()
        table = AlasioConfigTable(self.TEST_CONFIG_NAME)
        rows = table.select()

        # No rows or Scheduler row has default values
        scheduler_row = None
        for row in rows:
            if row.task == 'Main' and row.group == 'Scheduler':
                scheduler_row = row
                break

        # Either no row exists, or row exists but doesn't contain Enable key
        # (since default values are omitted)
        if scheduler_row:
            from msgspec.msgpack import decode
            data = decode(scheduler_row.value)
            # Enable should not be in data (omitted as default)
            # or if present, should be False (default)
            if 'Enable' in data:
                assert data['Enable'] is False

    def test_override_invalid_group(self, config):
        """Test overriding invalid group logs warning but doesn't crash"""
        prev_config, prev_const = config.override(InvalidGroup_Arg=True)

        # Should not crash, just return empty
        assert len(prev_config) == 0

    def test_override_invalid_arg(self, config):
        """Test overriding invalid arg logs warning but doesn't crash"""
        prev_config, prev_const = config.override(Scheduler_InvalidArg=True)

        # Should not crash, just return empty or skip
        if 'Scheduler' in prev_config:
            assert 'InvalidArg' not in prev_config['Scheduler']

    def test_override_on_unbound_group(self, config):
        """Test overriding value on unbound group creates it"""

        # Create config with unbound group
        class ConfigWithUnbound(AlasioConfigBase):
            entry = config.mod.entry
            UnboundScheduler: "scheduler.Scheduler"

        cfg = ConfigWithUnbound(self.TEST_CONFIG_NAME + '_unbound', task='Main')

        # Override before accessing group
        cfg.override(UnboundScheduler_Enable=True)

        # Now access the group
        assert cfg.UnboundScheduler.Enable is True

    def test_override_updates_existing_override(self, config):
        """Test that override can update existing override"""
        config.override(Scheduler_Enable=True)
        assert config.Scheduler.Enable is True

        # Override again with different value
        prev_config, prev_const = config.override(Scheduler_Enable=False)

        assert config.Scheduler.Enable is False
        # Previous value should be the overridden value (True)
        assert prev_config['Scheduler']['Enable'] is True


class TestConfigTemporary:
    """Test suite for temporary override context manager"""

    TEST_CONFIG_NAME = 'test_config_temporary'

    @pytest.fixture
    def config(self, example_mod):
        """Create test config instance"""

        class MyConfig(AlasioConfigBase):
            entry = example_mod.entry
            Scheduler: "scheduler.Scheduler"

        return MyConfig(self.TEST_CONFIG_NAME, task='Main')

    def test_temporary_override(self, config):
        """Test temporary override changes and restores value"""
        assert config.Scheduler.Enable is False

        with config.temporary(Scheduler_Enable=True):
            assert config.Scheduler.Enable is True

        # Should restore to original
        assert config.Scheduler.Enable is False

    def test_temporary_multiple_values(self, config):
        """Test temporary override with multiple values"""
        assert config.Scheduler.Enable is False
        assert config.Scheduler.ServerUpdate == '00:00'

        with config.temporary(
                Scheduler_Enable=True,
                Scheduler_ServerUpdate='09:00'
        ):
            assert config.Scheduler.Enable is True
            assert config.Scheduler.ServerUpdate == '09:00'

        # Should restore to original
        assert config.Scheduler.Enable is False
        assert config.Scheduler.ServerUpdate == '00:00'

    def test_nested_temporary_override(self, config):
        """Test nested temporary override"""
        assert config.Scheduler.Enable is False

        with config.temporary(Scheduler_Enable=True):
            assert config.Scheduler.Enable is True

            with config.temporary(Scheduler_Enable=False):
                assert config.Scheduler.Enable is False

            # Should restore to outer temporary
            assert config.Scheduler.Enable is True

        # Should restore to original
        assert config.Scheduler.Enable is False

    def test_temporary_persists_across_init_task(self, config):
        """Test temporary override persists within context even after init_task"""
        with config.temporary(Scheduler_Enable=True):
            assert config.Scheduler.Enable is True

            # Re-init task inside context
            config.init_task()

            # Override should persist
            assert config.Scheduler.Enable is True

        # Should restore to original after context
        assert config.Scheduler.Enable is False

    def test_temporary_restores_previous_override(self, config):
        """Test that temporary restores previous override state"""
        # Set initial override
        config.override(Scheduler_Enable=True)
        assert config.Scheduler.Enable is True

        with config.temporary(Scheduler_Enable=False):
            assert config.Scheduler.Enable is False

        # Should restore to previous override (True)
        assert config.Scheduler.Enable is True

    def test_temporary_with_exception(self, config):
        """Test that temporary restores values even when exception occurs"""
        assert config.Scheduler.Enable is False

        try:
            with config.temporary(Scheduler_Enable=True):
                assert config.Scheduler.Enable is True
                raise ValueError("Test exception")
        except ValueError:
            pass

        # Should still restore to original
        assert config.Scheduler.Enable is False

    def test_temporary_does_not_save_to_db(self, config):
        """Test that temporary does not trigger DB save"""
        with config.temporary(Scheduler_Enable=True):
            pass

        # Check DB - should be empty or have default values
        config.init_task()
        table = AlasioConfigTable(self.TEST_CONFIG_NAME)
        rows = table.select()

        # No rows or Scheduler row has default values
        scheduler_row = None
        for row in rows:
            if row.task == 'Main' and row.group == 'Scheduler':
                scheduler_row = row
                break

        # Either no row exists, or row exists but doesn't contain Enable key
        if scheduler_row:
            from msgspec.msgpack import decode
            data = decode(scheduler_row.value)
            if 'Enable' in data:
                assert data['Enable'] is False


class TestConfigConstOverride:
    """Test suite for const override functionality"""

    TEST_CONFIG_NAME = 'test_config_const'

    @pytest.fixture
    def config_with_const(self, example_mod):
        """Create test config with const values"""

        class MyConfig(AlasioConfigBase):
            entry = example_mod.entry
            Scheduler: "scheduler.Scheduler"
            # Add a const value
            TEST_CONST = 100

        return MyConfig(self.TEST_CONFIG_NAME, task='Main')

    def test_override_const_value(self, config_with_const):
        """Test overriding const value"""
        assert config_with_const.TEST_CONST == 100

        prev_config, prev_const = config_with_const.override(TEST_CONST=200)

        assert config_with_const.TEST_CONST == 200
        assert prev_const['TEST_CONST'] == 100

    def test_override_const_persists_across_init_task(self, config_with_const):
        """Test const override persists across init_task"""
        config_with_const.override(TEST_CONST=300)
        assert config_with_const.TEST_CONST == 300

        config_with_const.init_task()

        assert config_with_const.TEST_CONST == 300

    def test_temporary_const_override(self, config_with_const):
        """Test temporary const override"""
        assert config_with_const.TEST_CONST == 100

        with config_with_const.temporary(TEST_CONST=400):
            assert config_with_const.TEST_CONST == 400

        assert config_with_const.TEST_CONST == 100

    def test_override_invalid_const(self, config_with_const):
        """Test overriding non-existent const logs warning"""
        prev_config, prev_const = config_with_const.override(INVALID_CONST=500)

        # Should not crash, just return empty
        assert len(prev_const) == 0

    def test_override_mixed_config_and_const(self, config_with_const):
        """Test overriding both config and const values"""
        prev_config, prev_const = config_with_const.override(
            Scheduler_Enable=True,
            TEST_CONST=250
        )

        assert config_with_const.Scheduler.Enable is True
        assert config_with_const.TEST_CONST == 250

        assert prev_config['Scheduler']['Enable'] is False
        assert prev_const['TEST_CONST'] == 100


class TestModelProxy:
    """Test suite for ModelProxy wrapper"""

    TEST_CONFIG_NAME = 'test_model_proxy'

    @pytest.fixture
    def config(self, example_mod):
        """Create test config instance"""

        class MyConfig(AlasioConfigBase):
            entry = example_mod.entry
            Scheduler: "scheduler.Scheduler"

        return MyConfig(self.TEST_CONFIG_NAME, task='Main')

    def test_proxy_getattr(self, config):
        """Test ModelProxy attribute access"""
        proxy = config.Scheduler
        assert type(proxy) is ModelProxy

        # Should proxy to underlying object
        assert proxy.Enable is False
        assert proxy.ServerUpdate == '00:00'

    def test_proxy_setattr_registers_modify(self, config):
        """Test ModelProxy attribute setting registers modification"""
        config.auto_save = False

        proxy = config.Scheduler
        proxy.Enable = True

        # Should register modification
        key = ('Main', 'Scheduler', 'Enable')
        assert key in config.modified

    def test_proxy_repr(self, config):
        """Test ModelProxy __repr__"""
        proxy = config.Scheduler
        repr_str = repr(proxy)

        # Should proxy to underlying object's repr
        assert 'Scheduler' in repr_str or 'Enable' in repr_str

    def test_proxy_str(self, config):
        """Test ModelProxy __str__"""
        proxy = config.Scheduler
        str_str = str(proxy)

        # Should proxy to underlying object's str
        assert 'Scheduler' in str_str or 'Enable' in str_str


class TestConfigEdgeCases:
    """Test suite for edge cases and error handling"""

    TEST_CONFIG_NAME = 'test_config_edge'

    @pytest.fixture
    def config(self, example_mod):
        """Create test config instance"""

        class MyConfig(AlasioConfigBase):
            entry = example_mod.entry
            Scheduler: "scheduler.Scheduler"

        return MyConfig(self.TEST_CONFIG_NAME, task='Main')

    def test_group_construct_invalid_annotation(self, config):
        """Test _group_construct with invalid annotation format"""

        class BadConfig(AlasioConfigBase):
            entry = config.mod.entry
            # Invalid annotation without dot
            BadGroup: "scheduler"

        cfg = BadConfig(self.TEST_CONFIG_NAME + '_bad', task='Main')

        with pytest.raises(DataInconsistent):
            _ = cfg.BadGroup

    def test_group_construct_nonexistent_model(self, config):
        """Test _group_construct with non-existent model file"""

        class BadConfig(AlasioConfigBase):
            entry = config.mod.entry
            # Non-existent model
            BadGroup: "nonexistent.NonExistentClass"

        cfg = BadConfig(self.TEST_CONFIG_NAME + '_bad2', task='Main')

        with pytest.raises(DataInconsistent):
            _ = cfg.BadGroup

    def test_corrupted_db_data_recovery(self, config):
        """Test that corrupted DB data is handled gracefully"""
        # Insert corrupted data
        table = AlasioConfigTable(self.TEST_CONFIG_NAME)
        corrupted_row = ConfigRow(
            task='Main',
            group='Scheduler',
            value=b'corrupted_not_msgpack'
        )
        table.upsert_row(corrupted_row, conflicts=('task', 'group'), updates='value')

        # Re-init should handle gracefully
        config.init_task()

        # Should use default values
        assert config.Scheduler.Enable is False

    def test_lowercase_attribute_access_raises_error(self, config):
        """Test that lowercase attribute access raises AttributeError"""
        with pytest.raises(AttributeError):
            _ = config.lowercase_attr

    def test_empty_attribute_name_raises_error(self, config):
        """Test that empty attribute name raises AttributeError"""
        # This is a defensive test
        with pytest.raises(AttributeError):
            _ = getattr(config, '')

    def test_override_format_invalid(self, config):
        """Test override with invalid format logs warning"""
        # Invalid format: no underscore separator
        prev_config, prev_const = config.override(InvalidFormat=True)

        # Should not crash
        assert len(prev_config) == 0
        assert len(prev_const) == 0
