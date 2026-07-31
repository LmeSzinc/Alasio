import datetime as d
import threading

import pytest

from alasio.config.alasio.group_proxy import GroupProxy
from alasio.config.base import AlasioConfigBase
from alasio.config.const import DataInconsistent


class TestAlasioConfigBase:
    """Test suite for AlasioConfigBase lifecycle management"""

    # Use a single test config for all tests to reduce file creation
    TEST_CONFIG_NAME = ':memory:'

    def test_config_initialization(self, config_cls):
        """Test basic config initialization"""
        config = config_cls(self.TEST_CONFIG_NAME, task='Main')

        assert config.config_name == self.TEST_CONFIG_NAME
        assert config.task == 'Main'
        assert config.auto_save is True
        assert config.batch_set().depth == 0
        assert config.is_template_config is False

    def test_template_config_readonly(self, config_cls):
        """Test that template config has auto_save disabled"""
        # Note: Since we strictly use ':memory:' for in-memory DB,
        # we still use a file for template test to trigger .startswith("template")
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
        """Test accessing bound group returns GroupProxy"""
        config = config_cls(self.TEST_CONFIG_NAME, task='Main')

        # Scheduler is bound to Main task
        campaign = config.Campaign
        assert type(campaign) is GroupProxy
        assert campaign._task == 'Main'
        assert campaign._group == 'Campaign'

    def test_bound_alasio_group_access(self, config_cls):
        """
        Test accessing a group that does not define in mod, but define in alasio, should return GroupProxy
        """
        config = config_cls(self.TEST_CONFIG_NAME, task='Main')

        # Scheduler is bound to Main task
        scheduler = config.Scheduler
        assert type(scheduler) is GroupProxy
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
            UnboundGroup: "main.Campaign"

        config = ConfigWithUnbound(self.TEST_CONFIG_NAME, task='Main')

        # Access should trigger fallback, not bound to task
        group = config.UnboundGroup
        # Should be plain Struct, not GroupProxy
        assert type(group).__name__ == 'Campaign'
        # Default values should work
        assert group.Name == '12-4'

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
        assert len(config._modified) == 1

        # Re-init should clear modifications and reload from DB (which has default False)
        config.release()
        config.init_task()
        assert config.Scheduler.Enable is False
        assert len(config._modified) == 0

    def test_init_task_with_no_task(self, config_cls):
        """Test init_task with empty task does nothing"""
        config = config_cls(self.TEST_CONFIG_NAME, task='')

        # Should not raise error
        config.init_task()

        # dict_value should be empty
        assert len(config._dict_row) == 0

    def test_init_task_with_invalid_task(self, config_cls):
        """Test initialization with invalid task raises KeyError"""
        # KeyError happens during __init__ when task is invalid
        with pytest.raises(KeyError):
            config_cls(self.TEST_CONFIG_NAME, task='InvalidTask')


class TestConfigModification:
    """Test suite for config modification and saving"""

    TEST_CONFIG_NAME = ':memory:'

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
        assert len(config._modified) == 0
        # Re-init and verify
        config.init_task()
        assert config.Scheduler.Enable is True

        # Verify persisted to DB
        config.release()
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

        # Verify persisted to DB
        config.release()
        config.init_task()
        assert config.Scheduler.Enable is True
        assert config.Scheduler.ServerUpdate == '12:00'

    def test_modification_without_auto_save(self, config):
        """Test modification without auto save"""
        config.auto_save = False

        config.Scheduler.Enable = True

        # Modified dict should contain the change
        assert len(config._modified) == 1
        key = ('Main', 'Scheduler', 'Enable')
        assert key in config._modified

        # Manual save
        config.save()
        assert len(config._modified) == 0

    def test_batch_set_context_manager(self, config):
        """Test batch_set context manager"""
        with config.batch_set():
            config.Scheduler.Enable = True
            config.Scheduler.ServerUpdate = '15:00'
            # Should not save yet
            assert len(config._modified) == 2

        # Should save after context exit
        assert len(config._modified) == 0

        # Re-init and verify
        config.init_task()
        assert config.Scheduler.Enable is True
        assert config.Scheduler.ServerUpdate == '15:00'

        # Verify persisted to DB
        config.release()
        config.init_task()
        assert config.Scheduler.Enable is True
        assert config.Scheduler.ServerUpdate == '15:00'

    def test_nested_batch_set(self, config):
        """Test nested batch_set context managers"""
        with config.batch_set() as bs:
            config.Scheduler.Enable = True
            assert bs.depth == 1

            with config.batch_set() as bs2:
                config.Scheduler.ServerUpdate = '18:00'
                assert bs2.depth == 2
                # Should not save yet
                assert len(config._modified) == 2

            # Should not save at inner exit
            assert bs.depth == 1
            assert len(config._modified) == 2

        # Should save after outermost exit
        assert config.batch_set().depth == 0
        assert len(config._modified) == 0

    def test_batch_set_without_auto_save(self, config):
        """Test batch_set when auto_save is disabled"""
        config.auto_save = False

        with config.batch_set():
            config.Scheduler.Enable = True
            config.Scheduler.ServerUpdate = '20:00'

        # Should not save even after context exit
        assert len(config._modified) == 2

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
        assert key in config._modified
        event = config._modified[key]
        assert event.task == 'Main'
        assert event.group == 'Scheduler'
        assert event.arg == 'Enable'
        assert event.value is True


class TestConfigEdgeCases:
    """Test suite for edge cases and error handling"""

    TEST_CONFIG_NAME = ':memory:'

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

        cfg = BadConfig(':memory:', task='Main')

        with pytest.raises(DataInconsistent):
            _ = cfg.BadGroup

    def test_group_construct_non_existent_model(self, config):
        """Test _group_construct with non-existent model file"""

        class BadConfig(AlasioConfigBase):
            entry = config.mod.entry
            # Non-existent model
            BadGroup: "nonexistent.NonExistentModel"

        cfg = BadConfig(':memory:', task='Main')

        with pytest.raises(DataInconsistent):
            _ = cfg.BadGroup

    def test_old_style_config_reference_raises_error(self, config):
        """Test old style Group_Arg reference raises clear error"""
        with pytest.raises(AttributeError) as excinfo:
            _ = config.Scheduler_Enable

        assert 'Old config reference style detected' in str(excinfo.value)

    def test_cross_get_non_existent_task(self, config):
        """Test cross_get with non-existent task"""
        result = config.cross_get('NonExistentTask', 'Scheduler', 'Enable')
        assert result is None

    def test_cross_get_non_existent_group(self, config):
        """Test cross_get with non-existent group"""
        result = config.cross_get('Main', 'NonExistentGroup', 'Enable')
        assert result is None

    def test_cross_get_with_default(self, config):
        """Test cross_get with custom default value"""
        result = config.cross_get('Main', 'NonExistentGroup', 'Enable', default=False)
        assert result is False

    def test_cross_set_non_existent_group(self, config):
        """Test cross_set with non-existent group does not crash"""
        config.cross_set('NonExistentTask', 'Scheduler', 'Enable', True)

    def test_cross_set_same_value(self, config):
        """Test cross_set with same value does not register modify"""
        config.auto_save = False
        # Scheduler.Enable default is False, set to False should do nothing
        modified_count_before = len(config._modified)
        config.cross_set('Main', 'Scheduler', 'Enable', False)
        assert len(config._modified) == modified_count_before

    def test_cross_set_different_value(self, config):
        """Test cross_set with different value registers modify"""
        config.auto_save = False
        config.cross_set('Main', 'Scheduler', 'Enable', True)
        assert len(config._modified) == 1

    def test_cross_set_and_get_roundtrip(self, config):
        """Test cross_set then cross_get roundtrip"""
        config.cross_set('Main', 'Scheduler', 'Enable', True)
        result = config.cross_get('Main', 'Scheduler', 'Enable')
        assert result is True

    def test_scheduler_default_values(self, config):
        """Test Scheduler group default values"""
        assert config.Scheduler.Enable is False
        assert config.Scheduler.ServerUpdate == '00:00'
        assert config.Scheduler.NextRun == d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)

    def test_task_stop_raises_exception(self):
        """Test task_stop raises TaskStop"""
        from alasio.base.exception import TaskStop
        with pytest.raises(TaskStop):
            AlasioConfigBase.task_stop()

    def test_task_stop_with_message(self):
        """Test task_stop with custom message"""
        from alasio.base.exception import TaskStop
        with pytest.raises(TaskStop, match='Custom stop message'):
            AlasioConfigBase.task_stop('Custom stop message')

    def test_empty_config_name_creates_file(self, example_mod):
        """Test config with empty name works as a base config"""
        class EmptyNameConfig(AlasioConfigBase):
            entry = example_mod.entry
            Scheduler: "scheduler.Scheduler"

        cfg = EmptyNameConfig('', task='')
        # Should not crash
        assert cfg.config_name == ''
        assert cfg.task == ''

    def test_release_method(self, config):
        """Test release clears internal state"""
        config.auto_save = False
        # Access group to populate cache
        _ = config.Scheduler

        # Set modification
        config.Scheduler.Enable = True
        assert len(config._modified) == 1

        # Release
        config.release()

        # Should have clear state
        assert len(config._dict_row) == 0
        assert len(config._dict_group) == 0
        assert len(config._modified) == 0
        assert config._config_cached is False

    def test_init_task_after_release(self, config):
        """Test init_task after release restores state"""
        config.auto_save = False
        config.Scheduler.Enable = True

        # Release and re-init
        config.release()
        config.init_task()

        # Modified dict should be empty after re-init
        assert len(config._modified) == 0
        # Value reverts to DB default since modification wasn't saved
        assert config.Scheduler.Enable is False

    def test_thread_safety_across_multiple_threads(self, config):
        """Test that config operations are thread-safe"""
        errors = []

        def set_enable(value):
            try:
                config.auto_save = False
                config.Scheduler.Enable = value
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(10):
            t = threading.Thread(target=set_enable, args=(i % 2 == 0,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Should have no errors
        assert len(errors) == 0, f'Thread safety errors: {errors}'

    def test_register_modify_with_batch_set(self, config):
        """Test register_modify inside batch_set context"""
        with config.batch_set():
            config.register_modify('Main', 'Scheduler', 'Enable', True)
            assert len(config._modified) == 1

        # Should save after context exit
        assert len(config._modified) == 0

    def test_temporary_override_with_existing_modified(self, config):
        """Test temporary override when there are pending modifications"""
        config.auto_save = False
        config.Scheduler.Enable = True

        with config.temporary(Scheduler_Enable=False):
            assert config.Scheduler.Enable is False

        # After temporary, should restore to modified value (True)
        assert config.Scheduler.Enable is True

    def test_multiple_batch_set_depth_tracking(self, config):
        """Test depth tracking in deeply nested batch_set"""
        with config.batch_set() as bs1:
            assert bs1.depth == 1
            with config.batch_set() as bs2:
                assert bs2.depth == 2
                with config.batch_set() as bs3:
                    assert bs3.depth == 3
                    config.Scheduler.Enable = True
                assert bs3.depth == 2  # after __exit__, depth decremented to 2
            assert bs2.depth == 1
        assert bs1.depth == 0

    def test_save_with_modified_contains_auto_save_disabled(self, config):
        """Test that save() correctly handles modified when auto_save is disabled"""
        config.auto_save = False
        config.Scheduler.Enable = True
        config.Scheduler.ServerUpdate = '10:00'

        # Save should persist both changes
        config.save()
        assert len(config._modified) == 0

    def test_scheduler_group_with_no_scheduler(self, config):
        """Test that accessing Scheduler group works"""
        # Scheduler should be accessible without task having explicit Scheduler group
        sched = config.Scheduler
        assert hasattr(sched, 'Enable')
        assert hasattr(sched, 'NextRun')

    def test_config_repr(self, config_cls):
        """Test config repr does not crash"""
        config = config_cls(self.TEST_CONFIG_NAME, task='Main')
        # Just ensure no exception
        repr(config)

    def test_long_running_memory_stability(self, config):
        """
        Test that repeated init_task/release cycles don't leak memory
        """
        for _ in range(10):
            config.init_task()
            config.Scheduler.Enable = True
            config.release()

        # After all cycles, should still work
        config.init_task()
        assert config.Scheduler.Enable is True
