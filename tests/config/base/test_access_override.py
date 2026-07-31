import pytest

from alasio.config.base import AlasioConfigBase
from alasio.config.table.config import AlasioConfigTable


class TestConfigOverride:
    """Test suite for config override functionality"""

    TEST_CONFIG_NAME = ':memory:'

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
            UnboundScheduler: "main.Campaign"

        cfg = ConfigWithUnbound(':memory:', task='Main')

        # Override before accessing group
        cfg.override(UnboundScheduler_Name='a3')

        # Now access the group
        assert cfg.UnboundScheduler.Name == 'a3'

    def test_override_updates_existing_override(self, config):
        """Test that override can update existing override"""
        config.override(Scheduler_Enable=True)
        assert config.Scheduler.Enable is True

        # Override again with different value
        prev_config, prev_const = config.override(Scheduler_Enable=False)

        assert config.Scheduler.Enable is False
        # Previous value should be the overridden value (True)
        assert prev_config['Scheduler']['Enable'] is True


class TestConfigOverrideClear:
    """Test suite for override_clear functionality"""

    TEST_CONFIG_NAME = ':memory:'

    @pytest.fixture
    def config(self, example_mod):
        """Create test config instance"""

        class MyConfig(AlasioConfigBase):
            entry = example_mod.entry
            Scheduler: "scheduler.Scheduler"

        return MyConfig(self.TEST_CONFIG_NAME, task='Main')

    def test_override_clear_basic(self, config):
        """Test override_clear restores original DB values"""
        assert config.Scheduler.Enable is False

        config.override(Scheduler_Enable=True)
        assert config.Scheduler.Enable is True

        config.override_clear()
        assert config.Scheduler.Enable is False

    def test_override_clear_multiple(self, config):
        """Test override_clear with multiple overridden values"""
        assert config.Scheduler.Enable is False
        assert config.Scheduler.ServerUpdate == '00:00'

        config.override(
            Scheduler_Enable=True,
            Scheduler_ServerUpdate='06:00'
        )
        assert config.Scheduler.Enable is True
        assert config.Scheduler.ServerUpdate == '06:00'

        config.override_clear()
        assert config.Scheduler.Enable is False
        assert config.Scheduler.ServerUpdate == '00:00'

    def test_override_clear_no_op(self, config):
        """Test override_clear without any overrides does not crash"""
        # Should not raise
        config.override_clear()
        assert config.Scheduler.Enable is False

    def test_override_clear_after_init_task(self, config):
        """Test override_clear after init_task resets to DB values"""
        # Persist a value to DB first
        config.Scheduler.Enable = True
        config.Scheduler.ServerUpdate = '08:00'

        # Override with different values
        config.override(
            Scheduler_Enable=False,
            Scheduler_ServerUpdate='12:00'
        )
        assert config.Scheduler.Enable is False
        assert config.Scheduler.ServerUpdate == '12:00'

        # Clear overrides
        config.override_clear()

        # Should restore to DB values (True, '08:00'), not defaults
        assert config.Scheduler.Enable is True
        assert config.Scheduler.ServerUpdate == '08:00'

    def test_override_clear_after_init_task_reload(self, config):
        """Test that after override_clear, init_task loads DB values correctly"""
        # Persist a value to DB
        config.Scheduler.Enable = True

        # Override
        config.override(Scheduler_Enable=False)
        assert config.Scheduler.Enable is False

        # Clear overrides
        config.override_clear()
        assert config.Scheduler.Enable is True

        # Re-init task - should still have DB values
        config.init_task()
        assert config.Scheduler.Enable is True

    def test_override_clear_partial(self, config):
        """Test clearing only some overrides by calling override with new values then clear"""
        config.override(
            Scheduler_Enable=True,
            Scheduler_ServerUpdate='06:00'
        )
        assert config.Scheduler.Enable is True
        assert config.Scheduler.ServerUpdate == '06:00'

        # Override again with just one value
        config.override(Scheduler_Enable=False)
        assert config.Scheduler.Enable is False
        assert config.Scheduler.ServerUpdate == '06:00'

        # Clear all overrides
        config.override_clear()

        # Both should be restored
        assert config.Scheduler.Enable is False
        assert config.Scheduler.ServerUpdate == '00:00'

    def test_override_clear_same_key_multiple_times(self, config):
        """Test that clearing after multiple overrides of the same key restores the original value"""
        assert config.Scheduler.ServerUpdate == '00:00'

        # Override the same key multiple times with different values
        config.override(Scheduler_ServerUpdate='06:00')
        config.override(Scheduler_ServerUpdate='07:00')
        config.override(Scheduler_ServerUpdate='08:00')
        assert config.Scheduler.ServerUpdate == '08:00'

        # Clear all overrides
        config.override_clear()

        # Should restore to the original value before the first override
        # rather than the value before the last override ('07:00')
        assert config.Scheduler.ServerUpdate == '00:00'

    def test_override_clear_with_previous_override(self, config):
        """Test override_clear works correctly with multiple sequential overrides"""
        config.override(Scheduler_Enable=True)
        assert config.Scheduler.Enable is True

        # Second override
        config.override(Scheduler_ServerUpdate='10:00')
        assert config.Scheduler.ServerUpdate == '10:00'

        # Clear all
        config.override_clear()

        # Should restore default values
        assert config.Scheduler.Enable is False
        assert config.Scheduler.ServerUpdate == '00:00'

    def test_override_clear_does_not_save_to_db(self, config):
        """Test override_clear does not affect DB values"""
        config.Scheduler.Enable = True

        config.release()
        config.init_task()
        assert config.Scheduler.Enable is True

        # Override
        config.override(Scheduler_Enable=False)
        assert config.Scheduler.Enable is False

        # Clear
        config.override_clear()
        assert config.Scheduler.Enable is True

        # Check DB still has True
        config.release()
        config.init_task()
        assert config.Scheduler.Enable is True

    def test_override_clear_does_not_raise_on_unbound_group(self, config):
        """Test override_clear on an overridden unbound group does not crash"""

        class ConfigWithUnbound(AlasioConfigBase):
            entry = config.mod.entry
            UnboundScheduler: "main.Campaign"

        cfg = ConfigWithUnbound(':memory:', task='Main')

        # Override on unbound group
        cfg.override(UnboundScheduler_Name='a3')
        assert cfg.UnboundScheduler.Name == 'a3'

        # Clear - should not crash
        cfg.override_clear()

    def test_override_clear_after_release(self, config):
        """Test that override -> release -> override_clear is safe"""
        config.override(Scheduler_Enable=True)
        assert config.Scheduler.Enable is True

        # Release clears group cache, group object no longer exists
        config.release()
        assert 'Scheduler' not in config.__dict__

        # Clear should skip restoring missing group, not crash
        config.override_clear()
        assert not config._override_config
        assert not config._override_prev_config

        # Re-init loads original value from DB, no override applied
        config.init_task()
        assert config.Scheduler.Enable is False

    def test_override_clear_does_not_bind_released_unbound_group(self, config):
        """
        Test that override_clear does not bind a previously unbound group
        that has been released from the instance dict
        """
        class ConfigWithUnbound(AlasioConfigBase):
            entry = config.mod.entry
            UnboundScheduler: "main.Campaign"

        cfg = ConfigWithUnbound(':memory:', task='Main')

        # Override on unbound group creates the group object
        cfg.override(UnboundScheduler_Name='a3')
        assert cfg.UnboundScheduler.Name == 'a3'
        assert 'UnboundScheduler' in cfg.__dict__

        # Release clears the group object from the instance dict
        cfg.release()
        assert 'UnboundScheduler' not in cfg.__dict__

        # Clear should not trigger _getattr fallback to re-bind the group
        cfg.override_clear()
        assert 'UnboundScheduler' not in cfg.__dict__

        # Accessing again falls back to unbound group with default value
        assert cfg.UnboundScheduler.Name == '12-4'


class TestConfigTemporary:
    """Test suite for temporary override context manager"""

    TEST_CONFIG_NAME = ':memory:'

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

    TEST_CONFIG_NAME = ':memory:'

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

    def test_override_clear_const(self, config_with_const):
        """Test override_clear restores const to original value"""
        assert config_with_const.TEST_CONST == 100

        config_with_const.override(TEST_CONST=200)
        assert config_with_const.TEST_CONST == 200

        config_with_const.override_clear()
        assert config_with_const.TEST_CONST == 100

    def test_override_clear_const_after_multiple(self, config_with_const):
        """Test override_clear restores const after multiple override calls"""
        assert config_with_const.TEST_CONST == 100

        config_with_const.override(TEST_CONST=200)
        config_with_const.override(TEST_CONST=300)

        config_with_const.override_clear()
        assert config_with_const.TEST_CONST == 100

    def test_override_clear_mixed_config_and_const(self, config_with_const):
        """Test override_clear with both config and const overrides"""
        config_with_const.override(
            Scheduler_Enable=True,
            TEST_CONST=250
        )

        assert config_with_const.Scheduler.Enable is True
        assert config_with_const.TEST_CONST == 250

        config_with_const.override_clear()

        assert config_with_const.Scheduler.Enable is False
        assert config_with_const.TEST_CONST == 100

    def test_override_clear_const_with_init_task(self, config_with_const):
        """Test override_clear on const followed by init_task"""
        config_with_const.override(TEST_CONST=200)
        assert config_with_const.TEST_CONST == 200

        config_with_const.override_clear()
        assert config_with_const.TEST_CONST == 100

        # init_task should also keep original value
        config_with_const.init_task()
        assert config_with_const.TEST_CONST == 100
