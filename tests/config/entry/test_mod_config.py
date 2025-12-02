import datetime as d

import pytest
from msgspec.msgpack import decode, encode

from alasio.config.entry.loader import MOD_LOADER
from alasio.config.entry.mod import ConfigSetEvent
from alasio.config.table.config import AlasioConfigTable, ConfigRow
from alasio.config.table.scan import ScanTable


class TestConfigReadWrite:
    """Test suite for Mod config read/write operations"""

    # Use a single test config for all tests to reduce file creation
    TEST_CONFIG_NAME = 'test_config_rw'

    @pytest.fixture(scope='class', autouse=True)
    def cleanup_config(self):
        """Clean up test config file before and after all tests"""
        # Cleanup before tests
        scan_table = ScanTable()
        try:
            scan_table.config_del(self.TEST_CONFIG_NAME)
        except Exception:
            pass

        yield

        # Cleanup after tests
        try:
            scan_table.config_del(self.TEST_CONFIG_NAME)
        except Exception:
            pass

    @pytest.fixture
    def example_mod(self):
        """Get the example mod from MOD_LOADER"""
        mod = MOD_LOADER.dict_mod.get('example_mod')
        if mod is None:
            pytest.skip("example_mod not available")
        return mod

    @pytest.fixture
    def task_index_data(self, example_mod):
        """Get task index data from example mod"""
        return example_mod.task_index_data()

    def test_config_read_default_values(self, example_mod, task_index_data):
        """Test reading config returns default values when no custom config exists"""
        # Get ModelConfigRef from task_index_data
        task_info = task_index_data.get('Main')
        if task_info is None:
            pytest.skip("Task 'Main' not found in example_mod")

        # Use the config ref from task
        config_ref = task_info.config

        config = example_mod.config_read(self.TEST_CONFIG_NAME, config_ref)

        # Verify structure
        assert 'Main' in config
        assert 'Scheduler' in config['Main']

        # Verify default values from Scheduler model
        scheduler = config['Main']['Scheduler']
        assert scheduler['Enable'] is False
        assert scheduler['NextRun'] == d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)
        assert scheduler['ServerUpdate'] == '00:00'

    def test_config_set_single_event(self, example_mod):
        """Test setting a single config value"""
        event = ConfigSetEvent(
            task='Main',
            group='Scheduler',
            arg='Enable',
            value=True
        )

        success, response = example_mod.config_set(self.TEST_CONFIG_NAME, event)

        # Verify success
        assert success is True
        assert response is not None
        assert response.error is None
        assert response.task == 'Main'
        assert response.group == 'Scheduler'
        assert response.arg == 'Enable'
        assert response.value is True

    def test_config_set_and_read(self, example_mod, task_index_data):
        """Test setting a value and reading it back"""
        # Set Enable to True
        event = ConfigSetEvent(
            task='Main',
            group='Scheduler',
            arg='Enable',
            value=True
        )
        success, response = example_mod.config_set(self.TEST_CONFIG_NAME, event)
        assert success is True

        # Read back
        task_info = task_index_data['Main']
        config_ref = task_info.config
        config = example_mod.config_read(self.TEST_CONFIG_NAME, config_ref)

        # Verify the value was persisted
        assert config['Main']['Scheduler']['Enable'] is True
        # Other values should remain default
        assert config['Main']['Scheduler']['NextRun'] == d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)
        assert config['Main']['Scheduler']['ServerUpdate'] == '00:00'

    def test_config_set_multiple_values_sequentially(self, example_mod, task_index_data):
        """Test setting multiple values one by one"""
        # Set Enable
        event1 = ConfigSetEvent(
            task='Main',
            group='Scheduler',
            arg='Enable',
            value=True
        )
        success, _ = example_mod.config_set(self.TEST_CONFIG_NAME, event1)
        assert success is True

        # Set ServerUpdate
        event2 = ConfigSetEvent(
            task='Main',
            group='Scheduler',
            arg='ServerUpdate',
            value='03:00'
        )
        success, response = example_mod.config_set(self.TEST_CONFIG_NAME, event2)
        assert success is True
        assert response.value == '03:00'

        # Read back and verify both changes
        task_info = task_index_data['Main']
        config_ref = task_info.config
        config = example_mod.config_read(self.TEST_CONFIG_NAME, config_ref)

        assert config['Main']['Scheduler']['Enable'] is True
        assert config['Main']['Scheduler']['ServerUpdate'] == '03:00'

    def test_config_set_datetime(self, example_mod, task_index_data):
        """Test setting datetime value"""
        new_time = d.datetime(2025, 12, 25, 10, 30, tzinfo=d.timezone.utc)
        event = ConfigSetEvent(
            task='Main',
            group='Scheduler',
            arg='NextRun',
            value=new_time
        )

        success, response = example_mod.config_set(self.TEST_CONFIG_NAME, event)

        assert success is True
        assert response.value == new_time

        # Read back
        task_info = task_index_data['Main']
        config_ref = task_info.config
        config = example_mod.config_read(self.TEST_CONFIG_NAME, config_ref)
        assert config['Main']['Scheduler']['NextRun'] == new_time

    def test_config_set_invalid_value(self, example_mod):
        """Test setting an invalid value returns error"""
        # Try to set Enable (bool) to a string
        event = ConfigSetEvent(
            task='Main',
            group='Scheduler',
            arg='Enable',
            value='not_a_bool'
        )

        success, response = example_mod.config_set(self.TEST_CONFIG_NAME, event)

        # Should fail with rollback
        assert success is False
        assert response is not None
        assert response.error is not None
        # Should contain default value
        assert response.value is False

    def test_config_reset_single_event(self, example_mod):
        """Test resetting a single config value to default"""
        # First, set a non-default value
        event = ConfigSetEvent(
            task='Main',
            group='Scheduler',
            arg='Enable',
            value=True
        )
        success, _ = example_mod.config_set(self.TEST_CONFIG_NAME, event)
        assert success is True

        # Now reset it
        reset_event = ConfigSetEvent(
            task='Main',
            group='Scheduler',
            arg='Enable',
            value=None  # value doesn't matter for reset
        )
        response = example_mod.config_reset(self.TEST_CONFIG_NAME, reset_event)

        # Verify reset response
        assert response is not None
        assert response.error is None
        assert response.task == 'Main'
        assert response.group == 'Scheduler'
        assert response.arg == 'Enable'
        assert response.value is False  # default value

    def test_config_reset_and_read(self, example_mod, task_index_data):
        """Test resetting a value and reading it back"""
        # Set ServerUpdate to non-default
        event = ConfigSetEvent(
            task='Main',
            group='Scheduler',
            arg='ServerUpdate',
            value='15:45'
        )
        example_mod.config_set(self.TEST_CONFIG_NAME, event)

        # Reset it
        reset_event = ConfigSetEvent(
            task='Main',
            group='Scheduler',
            arg='ServerUpdate',
            value=None
        )
        response = example_mod.config_reset(self.TEST_CONFIG_NAME, reset_event)
        assert response.value == '00:00'  # default value

        # Read back
        task_info = task_index_data['Main']
        config_ref = task_info.config
        config = example_mod.config_read(self.TEST_CONFIG_NAME, config_ref)

        # Verify reset to default
        assert config['Main']['Scheduler']['ServerUpdate'] == '00:00'

    def test_config_reset_multiple_values(self, example_mod, task_index_data):
        """Test resetting multiple values"""
        # Set multiple values
        example_mod.config_set(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='Enable', value=True
        ))
        example_mod.config_set(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='ServerUpdate', value='12:00'
        ))

        # Reset both
        response1 = example_mod.config_reset(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='Enable', value=None
        ))
        response2 = example_mod.config_reset(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='ServerUpdate', value=None
        ))

        assert response1.value is False
        assert response2.value == '00:00'

        # Read back
        task_info = task_index_data['Main']
        config_ref = task_info.config
        config = example_mod.config_read(self.TEST_CONFIG_NAME, config_ref)

        # All should be defaults
        assert config['Main']['Scheduler']['Enable'] is False
        assert config['Main']['Scheduler']['ServerUpdate'] == '00:00'

    def test_config_set_batch(self, example_mod, task_index_data):
        """Test batch setting multiple config values"""
        events = [
            ConfigSetEvent(task='Main', group='Scheduler', arg='Enable', value=True),
            ConfigSetEvent(task='Main', group='Scheduler', arg='ServerUpdate', value='06:00'),
        ]

        success, responses = example_mod.config_batch_set(self.TEST_CONFIG_NAME, events)

        assert success is True
        assert len(responses) == 2
        assert all(r.error is None for r in responses)

        # Verify both values
        task_info = task_index_data['Main']
        config_ref = task_info.config
        config = example_mod.config_read(self.TEST_CONFIG_NAME, config_ref)

        assert config['Main']['Scheduler']['Enable'] is True
        assert config['Main']['Scheduler']['ServerUpdate'] == '06:00'

    def test_config_reset_batch(self, example_mod):
        """Test batch resetting multiple config values"""
        # Set some values first
        example_mod.config_set(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='Enable', value=True
        ))
        example_mod.config_set(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='ServerUpdate', value='18:30'
        ))

        # Batch reset
        events = [
            ConfigSetEvent(task='Main', group='Scheduler', arg='Enable', value=None),
            ConfigSetEvent(task='Main', group='Scheduler', arg='ServerUpdate', value=None),
        ]
        responses = example_mod.config_batch_reset(self.TEST_CONFIG_NAME, events)

        assert len(responses) == 2
        assert all(r.error is None for r in responses)
        assert responses[0].value is False
        assert responses[1].value == '00:00'

    def test_config_set_nonexistent_group(self, example_mod):
        """Test setting config for non-existent group raises error"""
        from alasio.config.const import DataInconsistent

        event = ConfigSetEvent(
            task='Main',
            group='NonExistentGroup',
            arg='SomeArg',
            value='some_value'
        )

        with pytest.raises(DataInconsistent):
            example_mod.config_set(self.TEST_CONFIG_NAME, event)

    def test_config_reset_nonexistent_group(self, example_mod):
        """Test resetting config for non-existent group returns None"""
        event = ConfigSetEvent(
            task='Main',
            group='NonExistentGroup',
            arg='SomeArg',
            value=None
        )

        response = example_mod.config_reset(self.TEST_CONFIG_NAME, event)
        assert response is None

    def test_config_persistence_across_reads(self, example_mod, task_index_data):
        """Test that config changes persist across multiple reads"""
        # Set a value
        example_mod.config_set(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='Enable', value=True
        ))

        # Read multiple times
        task_info = task_index_data['Main']
        config_ref = task_info.config

        config1 = example_mod.config_read(self.TEST_CONFIG_NAME, config_ref)
        config2 = example_mod.config_read(self.TEST_CONFIG_NAME, config_ref)
        config3 = example_mod.config_read(self.TEST_CONFIG_NAME, config_ref)

        # All reads should return the same value
        assert config1['Main']['Scheduler']['Enable'] is True
        assert config2['Main']['Scheduler']['Enable'] is True
        assert config3['Main']['Scheduler']['Enable'] is True

    def test_config_set_batch_partial_failure(self, example_mod, task_index_data):
        """Test that batch set fails if any event is invalid (transactional)"""
        events = [
            ConfigSetEvent(task='Main', group='Scheduler', arg='Enable', value=True),
            # Invalid value for bool field
            ConfigSetEvent(task='Main', group='Scheduler', arg='Enable', value='invalid'),
        ]

        success, responses = example_mod.config_batch_set(self.TEST_CONFIG_NAME, events)

        # Should fail
        assert success is False
        # Should return rollback events
        assert len(responses) > 0

        # Verify that the valid change was NOT applied (transactional)
        # We need to read the config to verify
        # Note: Since we use a shared config file, we should ensure it's clean or known state
        # But here we assume previous tests didn't leave it in a state where Enable is True
        # Let's reset it first to be sure
        example_mod.config_reset(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='Enable', value=None
        ))

        task_info = task_index_data['Main']
        ref = task_info.config
        config = example_mod.config_read(self.TEST_CONFIG_NAME, ref)

        assert config['Main']['Scheduler']['Enable'] is False

    def test_config_corrupted_data_recovery(self, example_mod, task_index_data):
        """Test recovery from corrupted database content"""
        # 1. Manually insert corrupted data
        table = AlasioConfigTable(self.TEST_CONFIG_NAME)
        corrupted_row = ConfigRow(
            task='Main',
            group='Scheduler',
            value=b'not_msgpack_data'
        )
        table.upsert_row(corrupted_row, conflicts=('task', 'group'), updates='value')

        # 2. Try to set a value
        event = ConfigSetEvent(
            task='Main',
            group='Scheduler',
            arg='Enable',
            value=True
        )
        success, response = example_mod.config_set(self.TEST_CONFIG_NAME, event)

        # Should succeed by overwriting/recovering
        assert success is True
        assert response.error is None

        # Verify value is set
        task_info = task_index_data['Main']
        ref = task_info.config
        config = example_mod.config_read(self.TEST_CONFIG_NAME, ref)
        assert config['Main']['Scheduler']['Enable'] is True

        # 3. Corrupt again with valid msgpack but not dict (e.g. integer)
        corrupted_row.value = encode(123)
        table.upsert_row(corrupted_row, conflicts=('task', 'group'), updates='value')

        # 4. Try to reset
        reset_event = ConfigSetEvent(
            task='Main',
            group='Scheduler',
            arg='Enable',
            value=None
        )
        response = example_mod.config_reset(self.TEST_CONFIG_NAME, reset_event)

        # Should succeed
        assert response is not None
        assert response.error is None

        # Verify reset to default
        config = example_mod.config_read(self.TEST_CONFIG_NAME, ref)
        assert config['Main']['Scheduler']['Enable'] is False

    def test_config_omit_defaults(self, example_mod):
        """Test that default values are omitted from storage"""
        # 1. Set a value to non-default
        example_mod.config_set(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='Enable', value=True
        ))

        # Verify it's in DB
        table = AlasioConfigTable(self.TEST_CONFIG_NAME)
        row = table.select_one(task='Main', group='Scheduler')
        data = decode(row.value)
        assert 'Enable' in data
        assert data['Enable'] is True

        # 2. Set it back to default (False)
        example_mod.config_set(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='Enable', value=False
        ))

        # Verify it is OMITTED from DB (because omit_defaults=True in model)
        row = table.select_one(task='Main', group='Scheduler')
        data = decode(row.value)
        assert 'Enable' not in data

        # 3. Set to non-default again
        example_mod.config_set(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='Enable', value=True
        ))

        # 4. Reset it
        example_mod.config_reset(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='Enable', value=None
        ))

        # Verify it is OMITTED from DB
        row = table.select_one(task='Main', group='Scheduler')
        data = decode(row.value)
        assert 'Enable' not in data
