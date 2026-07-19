import datetime as d

import pytest
from msgspec.msgpack import decode, encode

from alasio.config.entry.loader import MOD_LOADER
from alasio.config.entry.model import ConfigSetEvent
from alasio.config.table.config import AlasioConfigTable, ConfigRow
from alasio.db.conn import SQLITE_POOL
from alasio.ext import env
from alasio.logger import logger

env.ALASIO_ROOT.chdir_here()


class ModConfigTestBase:
    """Base class for Mod config tests"""

    TEST_CONFIG_NAME = ':memory:'

    @pytest.fixture(autouse=True)
    def cleanup_memory_db(self):
        """Clear memory database after each test"""
        with logger.mock_capture_writer():
            yield
            SQLITE_POOL.delete_file(':memory:')

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


class TestConfigRead(ModConfigTestBase):
    """Tests for config_read"""

    def test_config_read_default_values(self, example_mod, task_index_data):
        """Test reading config returns default values when no custom config exists"""
        task_info = task_index_data.get('Main')
        if task_info is None:
            pytest.skip("Task 'Main' not found in example_mod")

        config_ref = task_info.config
        config = example_mod.config_read(self.TEST_CONFIG_NAME, config_ref)

        assert 'Main' in config
        assert 'Scheduler' in config['Main']

        scheduler = config['Main']['Scheduler']
        assert scheduler['Enable'] is False
        assert scheduler['NextRun'] == d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)
        assert scheduler['ServerUpdate'] == '00:00'


class TestConfigSet(ModConfigTestBase):
    """Tests for config_set"""

    def test_config_set_single_event(self, example_mod):
        """Test setting a single config value"""
        event = ConfigSetEvent(
            task='Main', group='Scheduler', arg='Enable', value=True)

        success, responses = example_mod.config_set(self.TEST_CONFIG_NAME, event)

        assert success is True
        assert len(responses) == 1
        assert responses[0].error is None
        assert responses[0].task == 'Main'
        assert responses[0].group == 'Scheduler'
        assert responses[0].arg == 'Enable'
        assert responses[0].value is True

    def test_config_set_and_read(self, example_mod, task_index_data):
        """Test setting a value and reading it back"""
        event = ConfigSetEvent(
            task='Main', group='Scheduler', arg='Enable', value=True)
        success, _ = example_mod.config_set(self.TEST_CONFIG_NAME, event)
        assert success is True

        task_info = task_index_data['Main']
        config_ref = task_info.config
        config = example_mod.config_read(self.TEST_CONFIG_NAME, config_ref)

        assert config['Main']['Scheduler']['Enable'] is True
        assert config['Main']['Scheduler']['NextRun'] == d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)
        assert config['Main']['Scheduler']['ServerUpdate'] == '00:00'

    def test_config_set_multiple_values_sequentially(self, example_mod, task_index_data):
        """Test setting multiple values one by one"""
        success, _ = example_mod.config_set(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='Enable', value=True))
        assert success is True

        success, responses = example_mod.config_set(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='ServerUpdate', value='03:00'))
        assert success is True
        assert responses[0].value == '03:00'

        task_info = task_index_data['Main']
        config_ref = task_info.config
        config = example_mod.config_read(self.TEST_CONFIG_NAME, config_ref)
        assert config['Main']['Scheduler']['Enable'] is True
        assert config['Main']['Scheduler']['ServerUpdate'] == '03:00'

    def test_config_set_datetime(self, example_mod, task_index_data):
        """Test setting datetime value"""
        new_time = d.datetime(2025, 12, 25, 10, 30, tzinfo=d.timezone.utc)
        event = ConfigSetEvent(
            task='Main', group='Scheduler', arg='NextRun', value=new_time)

        success, responses = example_mod.config_set(self.TEST_CONFIG_NAME, event)
        assert success is True
        assert responses[0].value == new_time

        task_info = task_index_data['Main']
        config_ref = task_info.config
        config = example_mod.config_read(self.TEST_CONFIG_NAME, config_ref)
        assert config['Main']['Scheduler']['NextRun'] == new_time

    def test_config_set_invalid_value(self, example_mod):
        """Test setting an invalid value returns error"""
        event = ConfigSetEvent(
            task='Main', group='Scheduler', arg='Enable', value='not_a_bool')

        success, responses = example_mod.config_set(self.TEST_CONFIG_NAME, event)

        assert success is False
        assert len(responses) == 1
        assert responses[0].error is not None
        assert responses[0].value is False

    def test_config_set_nonexistent_group(self, example_mod):
        """Test setting config for non-existent group returns validation error"""
        event = ConfigSetEvent(
            task='Main', group='NonExistentGroup', arg='SomeArg', value='some_value')

        success, responses = example_mod.config_set(self.TEST_CONFIG_NAME, event)
        assert success is False
        assert len(responses) == 1
        assert responses[0].error is not None

    def test_config_set_nonexistent_arg(self, example_mod):
        """Test setting config for non-existent arg returns validation error"""
        event = ConfigSetEvent(
            task='Main', group='Scheduler', arg='NonExistentArg', value='some_value')

        success, responses = example_mod.config_set(self.TEST_CONFIG_NAME, event)
        assert success is False
        assert len(responses) == 1
        assert responses[0].error is not None

    def test_config_set_preserves_unchanged_fields(self, example_mod, task_index_data):
        """
        Test that config_set preserves fields not in the set event.

        config_set only modifies the single field specified in the event,
        leaving all other fields untouched. This test guards against regressions
        where a future code change might cause config_set to overwrite
        existing field values with model defaults.
        """
        now = d.datetime(2026, 7, 19, 12, 0, 0, tzinfo=d.timezone.utc)
        old_time = now - d.timedelta(hours=1)

        # 1. Set up initial state: Enable=True, NextRun=old_time
        success, _ = example_mod.config_batch_set(self.TEST_CONFIG_NAME, [
            ConfigSetEvent(task='Main', group='Scheduler', arg='Enable', value=True),
            ConfigSetEvent(task='Main', group='Scheduler', arg='NextRun', value=old_time),
        ])
        assert success is True

        task_info = task_index_data['Main']
        config_ref = task_info.config
        config = example_mod.config_read(self.TEST_CONFIG_NAME, config_ref)
        assert config['Main']['Scheduler']['Enable'] is True
        assert config['Main']['Scheduler']['NextRun'] == old_time

        # 2. Call config_set with ONLY NextRun
        new_time = now
        success, responses = example_mod.config_set(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='NextRun', value=new_time))
        assert success is True
        assert len(responses) == 1
        assert responses[0].error is None

        # 3. Read back - Enable should still be True!
        config = example_mod.config_read(self.TEST_CONFIG_NAME, config_ref)
        assert config['Main']['Scheduler']['Enable'] is True
        assert config['Main']['Scheduler']['NextRun'] == new_time

        # 4. Verify Enable is still physically stored in DB
        table = AlasioConfigTable(self.TEST_CONFIG_NAME)
        row = table.select_one(task='Main', group='Scheduler')
        data = decode(row.value)
        assert 'Enable' in data
        assert data['Enable'] is True


class TestConfigReset(ModConfigTestBase):
    """Tests for config_reset"""

    def test_config_reset_single_event(self, example_mod):
        """Test resetting a single config value to default"""
        # First, set a non-default value
        success, _ = example_mod.config_set(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='Enable', value=True))
        assert success is True

        # Now reset it
        reset_event = ConfigSetEvent(
            task='Main', group='Scheduler', arg='Enable', value=None)
        response = example_mod.config_reset(self.TEST_CONFIG_NAME, reset_event)

        assert response is not None
        assert response.error is None
        assert response.task == 'Main'
        assert response.group == 'Scheduler'
        assert response.arg == 'Enable'
        assert response.value is False  # default value

    def test_config_reset_and_read(self, example_mod, task_index_data):
        """Test resetting a value and reading it back"""
        # Set ServerUpdate to non-default
        example_mod.config_set(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='ServerUpdate', value='15:45'))

        # Reset it
        reset_event = ConfigSetEvent(
            task='Main', group='Scheduler', arg='ServerUpdate', value=None)
        response = example_mod.config_reset(self.TEST_CONFIG_NAME, reset_event)
        assert response.value == '00:00'  # default value

        # Read back
        task_info = task_index_data['Main']
        config_ref = task_info.config
        config = example_mod.config_read(self.TEST_CONFIG_NAME, config_ref)
        assert config['Main']['Scheduler']['ServerUpdate'] == '00:00'

    def test_config_reset_multiple_values(self, example_mod, task_index_data):
        """Test resetting multiple values"""
        # Set multiple values
        example_mod.config_set(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='Enable', value=True))
        example_mod.config_set(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='ServerUpdate', value='12:00'))

        # Reset both
        response1 = example_mod.config_reset(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='Enable', value=None))
        response2 = example_mod.config_reset(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='ServerUpdate', value=None))

        assert response1.value is False
        assert response2.value == '00:00'

        # Read back
        task_info = task_index_data['Main']
        config_ref = task_info.config
        config = example_mod.config_read(self.TEST_CONFIG_NAME, config_ref)
        assert config['Main']['Scheduler']['Enable'] is False
        assert config['Main']['Scheduler']['ServerUpdate'] == '00:00'

    def test_config_reset_nonexistent_group(self, example_mod):
        """Test resetting config for non-existent group returns None"""
        event = ConfigSetEvent(
            task='Main', group='NonExistentGroup', arg='SomeArg', value=None)

        response = example_mod.config_reset(self.TEST_CONFIG_NAME, event)
        assert response is None


class TestConfigBatchSet(ModConfigTestBase):
    """Tests for config_batch_set"""

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

        task_info = task_index_data['Main']
        config_ref = task_info.config
        config = example_mod.config_read(self.TEST_CONFIG_NAME, config_ref)
        assert config['Main']['Scheduler']['Enable'] is True
        assert config['Main']['Scheduler']['ServerUpdate'] == '06:00'

    def test_config_batch_set_partial_failure(self, example_mod, task_index_data):
        """Test that batch set fails if any event is invalid (transactional)"""
        events = [
            ConfigSetEvent(task='Main', group='Scheduler', arg='Enable', value=True),
            ConfigSetEvent(task='Main', group='Scheduler', arg='Enable', value='invalid'),
        ]

        success, responses = example_mod.config_batch_set(self.TEST_CONFIG_NAME, events)

        assert success is False
        assert len(responses) > 0

        # Verify that the valid change was NOT applied (transactional)
        example_mod.config_reset(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='Enable', value=None))

        task_info = task_index_data['Main']
        ref = task_info.config
        config = example_mod.config_read(self.TEST_CONFIG_NAME, ref)
        assert config['Main']['Scheduler']['Enable'] is False

    def test_config_batch_set_nonexistent_group(self, example_mod):
        """Test batch_set with non-existent group returns all events in rollback"""
        events = [
            ConfigSetEvent(task='Main', group='NonExistentGroup', arg='SomeArg', value='some_value'),
            ConfigSetEvent(task='Main', group='Scheduler', arg='Enable', value=True),
        ]

        success, responses = example_mod.config_batch_set(self.TEST_CONFIG_NAME, events)
        assert success is False
        assert len(responses) == 2
        errors = [r.error for r in responses]
        assert any(e is not None for e in errors)
        enable_events = [r for r in responses if r.arg == 'Enable']
        assert len(enable_events) == 1
        assert enable_events[0].value is True
        assert enable_events[0].error is None

    def test_config_batch_set_nonexistent_arg(self, example_mod):
        """Test batch_set with non-existent arg returns all events in rollback"""
        events = [
            ConfigSetEvent(task='Main', group='Scheduler', arg='Enable', value=True),
            ConfigSetEvent(task='Main', group='Scheduler', arg='NonExistentArg', value='some_value'),
        ]

        success, responses = example_mod.config_batch_set(self.TEST_CONFIG_NAME, events)
        assert success is False
        assert len(responses) == 2
        errors = [r.error for r in responses]
        assert any(e is not None for e in errors)
        enable_events = [r for r in responses if r.arg == 'Enable']
        assert len(enable_events) == 1
        assert enable_events[0].value is True
        assert enable_events[0].error is None

    def test_config_batch_set_invalid_value(self, example_mod):
        """Test batch_set with a single invalid value event returns rollback"""
        events = [ConfigSetEvent(task='Main', group='Scheduler', arg='Enable', value='not_a_bool')]

        success, responses = example_mod.config_batch_set(self.TEST_CONFIG_NAME, events)
        assert success is False
        assert len(responses) == 1
        assert responses[0].error is not None
        assert responses[0].value is False

    def test_config_batch_set_invalid_value_and_nonexistent_arg(self, example_mod):
        """Test batch_set with both invalid value and nonexistent arg in same group.
        convert() validates the entire dict at once, so only the first error is returned."""
        events = [
            ConfigSetEvent(task='Main', group='Scheduler', arg='Enable', value='not_a_bool'),
            ConfigSetEvent(task='Main', group='Scheduler', arg='NonExistentArg', value='some_value'),
        ]

        success, responses = example_mod.config_batch_set(self.TEST_CONFIG_NAME, events)
        assert success is False
        assert len(responses) == 1
        assert responses[0].error is not None

    def test_config_batch_set_nonexistent_group_and_valid(self, example_mod):
        """Test batch_set with nonexistent group and valid event in another group"""
        events = [
            ConfigSetEvent(task='NonExistentTask', group='Scheduler', arg='Enable', value=True),
            ConfigSetEvent(task='Main', group='Scheduler', arg='ServerUpdate', value='06:00'),
        ]

        success, responses = example_mod.config_batch_set(self.TEST_CONFIG_NAME, events)
        assert success is False
        assert len(responses) == 2
        errors = [r.error for r in responses]
        assert any(e is not None for e in errors)
        server_events = [r for r in responses if r.arg == 'ServerUpdate']
        assert len(server_events) == 1
        assert server_events[0].value == '06:00'
        assert server_events[0].error is None

    def test_config_batch_set_invalid_value_in_other_task(self, example_mod):
        """Test batch_set with invalid value in one task and valid in another task"""
        events = [
            ConfigSetEvent(task='Main', group='Scheduler', arg='ServerUpdate', value='06:00'),
            ConfigSetEvent(task='Alas', group='Game', arg='PackageName', value='invalid_package'),
        ]

        success, responses = example_mod.config_batch_set(self.TEST_CONFIG_NAME, events)
        assert success is False
        assert len(responses) == 2
        errors = [r.error for r in responses]
        assert any(e is not None for e in errors)
        server_events = [r for r in responses if r.arg == 'ServerUpdate']
        assert len(server_events) == 1
        assert server_events[0].value == '06:00'
        assert server_events[0].error is None

    def test_config_batch_set_preserves_unchanged_fields(self, example_mod, task_index_data):
        """
        Test that config_batch_set preserves fields not in the batch set events.

        Regression test for a bug where config_batch_set would overwrite existing
        field values with model defaults when those fields were not included in
        the batch set events.

        Bug scenario:
        1. Main.Scheduler has Enable=True, NextRun=old_time in DB
        2. config_batch_set is called with ONLY a NextRun event (no Enable event)
        3. Inside config_batch_set: convert({'NextRun': new_time}, Scheduler)
           creates Scheduler(Enable=False, NextRun=new_time) using model defaults
        4. asdict(value_obj) returns {'Enable': False, 'NextRun': new_time}
        5. All expanded fields (including Enable=False) are applied to existing
           object, overwriting Enable=True with Enable=False
        6. With omit_defaults=True, Enable=False is omitted from encoded output
        7. Enable is permanently lost from DB
        """
        now = d.datetime(2026, 7, 19, 12, 0, 0, tzinfo=d.timezone.utc)
        old_time = now - d.timedelta(hours=1)

        # 1. Set up initial state: Enable=True, NextRun=old_time
        success, _ = example_mod.config_batch_set(self.TEST_CONFIG_NAME, [
            ConfigSetEvent(task='Main', group='Scheduler', arg='Enable', value=True),
            ConfigSetEvent(task='Main', group='Scheduler', arg='NextRun', value=old_time),
        ])
        assert success is True

        task_info = task_index_data['Main']
        config_ref = task_info.config
        config = example_mod.config_read(self.TEST_CONFIG_NAME, config_ref)
        assert config['Main']['Scheduler']['Enable'] is True
        assert config['Main']['Scheduler']['NextRun'] == old_time

        table = AlasioConfigTable(self.TEST_CONFIG_NAME)
        row = table.select_one(task='Main', group='Scheduler')
        data = decode(row.value)
        assert 'Enable' in data
        assert data['Enable'] is True

        # 2. Call config_batch_set with ONLY NextRun event
        new_time = now
        success, responses = example_mod.config_batch_set(self.TEST_CONFIG_NAME, [
            ConfigSetEvent(task='Main', group='Scheduler', arg='NextRun', value=new_time),
        ])
        assert success is True
        assert len(responses) == 1
        assert responses[0].error is None

        # 3. Read back - Enable should still be True!
        config = example_mod.config_read(self.TEST_CONFIG_NAME, config_ref)
        assert config['Main']['Scheduler']['Enable'] is True, \
            'BUG: config_batch_set overwrote Enable=True with default False ' \
            'because asdict() expanded the validated model and all fields ' \
            '(including Enable=False) were applied to the existing object'
        assert config['Main']['Scheduler']['NextRun'] == new_time

        # 4. Verify Enable is still physically stored in DB
        row = table.select_one(task='Main', group='Scheduler')
        data = decode(row.value)
        assert 'Enable' in data
        assert data['Enable'] is True


class TestConfigBatchReset(ModConfigTestBase):
    """Tests for config_batch_reset"""

    def test_config_reset_batch(self, example_mod):
        """Test batch resetting multiple config values"""
        # Set some values first
        example_mod.config_set(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='Enable', value=True))
        example_mod.config_set(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='ServerUpdate', value='18:30'))

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


class TestConfigGroupReset(ModConfigTestBase):
    """Tests for config_group_reset and config_group_batch_reset"""

    def test_config_group_reset(self, example_mod, task_index_data):
        """Test resetting an entire group"""
        # 1. Set multiple values in Scheduler
        example_mod.config_batch_set(self.TEST_CONFIG_NAME, [
            ConfigSetEvent(task='Main', group='Scheduler', arg='Enable', value=True),
            ConfigSetEvent(task='Main', group='Scheduler', arg='ServerUpdate', value='10:00'),
        ])

        task_info = task_index_data['Main']
        config_ref = task_info.config
        config = example_mod.config_read(self.TEST_CONFIG_NAME, config_ref)
        assert config['Main']['Scheduler']['Enable'] is True
        assert config['Main']['Scheduler']['ServerUpdate'] == '10:00'

        # 2. Reset entire group
        event = ConfigSetEvent(task='Main', group='Scheduler', arg='', value=None)
        responses = example_mod.config_group_reset(self.TEST_CONFIG_NAME, event)

        # 3. Verify responses
        assert len(responses) >= 3
        dict_response = {r.arg: r.value for r in responses}
        assert dict_response['Enable'] is False
        assert dict_response['ServerUpdate'] == '00:00'
        assert dict_response['NextRun'] == d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)

        # 4. Verify DB content
        table = AlasioConfigTable(self.TEST_CONFIG_NAME)
        row = table.select_one(task='Main', group='Scheduler')
        assert row is not None
        assert row.value == b'\x80'

        # 5. Read back and verify all are defaults
        config = example_mod.config_read(self.TEST_CONFIG_NAME, config_ref)
        assert config['Main']['Scheduler']['Enable'] is False
        assert config['Main']['Scheduler']['ServerUpdate'] == '00:00'

    def test_config_group_batch_reset(self, example_mod, task_index_data):
        """Test batch resetting entire groups"""
        example_mod.config_batch_set(self.TEST_CONFIG_NAME, [
            ConfigSetEvent(task='Main', group='Scheduler', arg='Enable', value=True),
            ConfigSetEvent(task='Main', group='Scheduler', arg='ServerUpdate', value='10:00'),
        ])

        events = [
            ConfigSetEvent(task='Main', group='Scheduler', arg='', value=None),
        ]
        responses = example_mod.config_group_batch_reset(self.TEST_CONFIG_NAME, events)

        assert len(responses) >= 3
        dict_response = {r.arg: r.value for r in responses}
        assert dict_response['Enable'] is False

        table = AlasioConfigTable(self.TEST_CONFIG_NAME)
        row = table.select_one(task='Main', group='Scheduler')
        assert row is not None
        assert row.value == b'\x80'


class TestConfigPersistence(ModConfigTestBase):
    """Tests for config persistence across reads"""

    def test_config_persistence_across_reads(self, example_mod, task_index_data):
        """Test that config changes persist across multiple reads"""
        example_mod.config_set(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='Enable', value=True))

        task_info = task_index_data['Main']
        config_ref = task_info.config

        config1 = example_mod.config_read(self.TEST_CONFIG_NAME, config_ref)
        config2 = example_mod.config_read(self.TEST_CONFIG_NAME, config_ref)
        config3 = example_mod.config_read(self.TEST_CONFIG_NAME, config_ref)

        assert config1['Main']['Scheduler']['Enable'] is True
        assert config2['Main']['Scheduler']['Enable'] is True
        assert config3['Main']['Scheduler']['Enable'] is True


class TestConfigCorruptedData(ModConfigTestBase):
    """Tests for recovery from corrupted database content"""

    def test_config_corrupted_data_recovery(self, example_mod, task_index_data):
        """Test recovery from corrupted database content"""
        # 1. Manually insert corrupted data
        table = AlasioConfigTable(self.TEST_CONFIG_NAME)
        corrupted_row = ConfigRow(
            task='Main', group='Scheduler', value=b'not_msgpack_data')
        table.upsert_row(corrupted_row, conflicts=('task', 'group'), updates='value')

        # 2. Try to set a value
        event = ConfigSetEvent(
            task='Main', group='Scheduler', arg='Enable', value=True)
        success, responses = example_mod.config_set(self.TEST_CONFIG_NAME, event)

        assert success is True
        assert len(responses) == 1
        assert responses[0].error is None

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
            task='Main', group='Scheduler', arg='Enable', value=None)
        response = example_mod.config_reset(self.TEST_CONFIG_NAME, reset_event)

        assert response is not None
        assert response.error is None

        # Verify reset to default
        config = example_mod.config_read(self.TEST_CONFIG_NAME, ref)
        assert config['Main']['Scheduler']['Enable'] is False


class TestConfigOmitDefaults(ModConfigTestBase):
    """Tests for omit_defaults behavior"""

    def test_config_omit_defaults(self, example_mod):
        """Test that default values are omitted from storage"""
        # 1. Set a value to non-default
        example_mod.config_set(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='Enable', value=True))

        table = AlasioConfigTable(self.TEST_CONFIG_NAME)
        row = table.select_one(task='Main', group='Scheduler')
        assert row is not None
        data = decode(row.value)
        assert 'Enable' in data
        assert data['Enable'] is True

        # 2. Set it back to default (False)
        example_mod.config_set(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='Enable', value=False))

        row = table.select_one(task='Main', group='Scheduler')
        assert row is not None
        data = decode(row.value)
        assert 'Enable' not in data

        # 3. Set to non-default again
        example_mod.config_set(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='Enable', value=True))

        # 4. Reset it
        example_mod.config_reset(self.TEST_CONFIG_NAME, ConfigSetEvent(
            task='Main', group='Scheduler', arg='Enable', value=None))

        row = table.select_one(task='Main', group='Scheduler')
        assert row is not None
        data = decode(row.value)
        assert 'Enable' not in data
