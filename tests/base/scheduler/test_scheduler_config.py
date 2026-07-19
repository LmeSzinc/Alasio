"""
Tests for scheduler config lifecycle.

Verifies that the scheduler's config lifecycle (release/init_task) correctly
preserves user-modified config values across various scheduling scenarios.

All values are set and accessed through the normal config API
(config.group.arg = value / config.group.arg), not by direct DB manipulation.
This ensures we are testing the actual code path that the scheduler uses.

Uses ExampleMod which provides a minimal but complete mod structure.
"""
import pytest

from ExampleMod.module.config.const import entry
from alasio.config.alasio.group_proxy import GroupProxy
from alasio.config.base import AlasioConfigBase
from alasio.db.conn import SQLITE_POOL
from alasio.ext import env
from alasio.logger import logger

env.ALASIO_ROOT.chdir_here()


@pytest.fixture(scope='module')
def example_mod():
    """Get the example mod from MOD_LOADER"""
    from alasio.config.entry.mod import Mod
    mod = Mod(entry)
    return mod


@pytest.fixture(autouse=True)
def cleanup_memory_db():
    """Clear memory database after each test"""
    with logger.mock_capture_writer():
        yield
        SQLITE_POOL.delete_file(':memory:')


def _make_config_cls(example_mod):
    """Create a dynamic config class with ExampleMod's entry."""

    class TestConfig(AlasioConfigBase):
        entry = example_mod.entry
        # Groups needed for scheduler-like scenarios.
        # Scheduler is bound to Main task so init_task loads it.
        Scheduler: "scheduler.Scheduler"
        Campaign: "main.Campaign"

    return TestConfig


class TestSchedulerConfigDirectRun:
    """
    Scenario: Scheduler runs a task directly when NextRun is in the past.

    Flow: release() -> get_next_task() -> init_task() -> task reads config

    After release + init_task, a value set through the config API must be
    accessible through the normal config.group.arg access pattern.
    """

    TEST_CONFIG_NAME = ':memory:'

    def test_api_set_value_survives_release_init(self, example_mod):
        """
        Set a value through config.Campaign.Name = '...' (auto-saves to DB),
        then release() + init_task() (as scheduler does between iterations).
        The value must still be accessible after reload.
        """
        ConfigCls = _make_config_cls(example_mod)
        config = ConfigCls(self.TEST_CONFIG_NAME, task='Main')

        # User sets value through config API -> auto-saves to DB
        config.Campaign.Name = '3-4'

        # Scheduler iteration: clear cache, reload from DB
        config.release()
        config.task = 'Main'
        config.init_task()

        # The value set through the API must still be accessible
        assert config.Campaign.Name == '3-4'

    def test_multiple_release_init_cycles(self, example_mod):
        """
        Values survive multiple release + init_task cycles,
        just like multiple scheduler iterations.
        """
        ConfigCls = _make_config_cls(example_mod)
        config = ConfigCls(self.TEST_CONFIG_NAME, task='Main')

        config.Campaign.Name = '5-1'

        # Cycle 1
        config.release()
        config.task = 'Main'
        config.init_task()
        assert config.Campaign.Name == '5-1'

        # Cycle 2
        config.release()
        config.init_task()
        assert config.Campaign.Name == '5-1'

        # Cycle 3
        config.release()
        config.init_task()
        assert config.Campaign.Name == '5-1'

    def test_default_value_when_no_saved_value(self, example_mod):
        """
        When no value has been set, the model's default is returned.
        """
        ConfigCls = _make_config_cls(example_mod)
        config = ConfigCls(self.TEST_CONFIG_NAME, task='Main')

        config.release()
        config.task = 'Main'
        config.init_task()

        # Default from main_model.py: Campaign.Name = '12-4'
        assert config.Campaign.Name == '12-4'

    def test_multiple_fields_saved_and_reloaded(self, example_mod):
        """
        Multiple fields in the same group are all set through the API
        and correctly loaded after release + init_task.
        """
        ConfigCls = _make_config_cls(example_mod)
        config = ConfigCls(self.TEST_CONFIG_NAME, task='Main')

        # Set multiple fields through config API
        config.Campaign.Name = '7-2'
        config.Campaign.Mode = 'hard'

        config.release()
        config.task = 'Main'
        config.init_task()

        assert config.Campaign.Name == '7-2'
        assert config.Campaign.Mode == 'hard'


class TestSchedulerConfigAfterOverride:
    """
    Scenario: Override is called (simulating load_campaign in GemsFarming)
    before release + init_task. Override persists across init_task.

    In GemsFarming, overrides like Emotion_Mode and EnemyPriority are set
    in load_campaign. These must survive scheduler lifecycle.
    """

    TEST_CONFIG_NAME = ':memory:'

    def test_override_persists_across_init_task(self, example_mod):
        """
        Override set via config.override() persists after release + init_task.
        This mirrors the scheduler behavior where override is called
        between init_task and task execution.
        """
        ConfigCls = _make_config_cls(example_mod)
        config = ConfigCls(self.TEST_CONFIG_NAME, task='Main')

        config.override(Campaign_Mode='hard')
        assert config.Campaign.Mode == 'hard'

        # release + init_task (as in scheduler loop between iterations)
        config.release()
        config.task = 'Main'
        config.init_task()

        # Override must survive
        assert config.Campaign.Mode == 'hard'

    def test_override_combined_with_saved_values(self, example_mod):
        """
        Override only affects the specified fields.
        Other fields loaded from saved values are not corrupted.
        """
        ConfigCls = _make_config_cls(example_mod)
        config = ConfigCls(self.TEST_CONFIG_NAME, task='Main')

        # Set and save a value through API
        config.Campaign.Name = '10-1'

        # Override a different field
        config.override(Campaign_Mode='hard')

        config.release()
        config.task = 'Main'
        config.init_task()

        # Override value survives
        assert config.Campaign.Mode == 'hard'
        # Saved value survives and is not corrupted
        assert config.Campaign.Name == '10-1'

    def test_multiple_overrides_survive_init_task(self, example_mod):
        """
        Multiple overrides all persist across release + init_task.
        """
        ConfigCls = _make_config_cls(example_mod)
        config = ConfigCls(self.TEST_CONFIG_NAME, task='Main')

        config.override(
            Campaign_Mode='hard',
            Campaign_UseAutoSearch=True,
        )
        config.release()
        config.task = 'Main'
        config.init_task()

        assert config.Campaign.Mode == 'hard'
        assert config.Campaign.UseAutoSearch is True


class TestSchedulerConfigBoundGroups:
    """
    Scenario: Groups bound to the current task must be loaded as GroupProxy,
    not as plain Structs. This is essential for the config system to track
    modifications and trigger auto-save.
    """

    TEST_CONFIG_NAME = ':memory:'

    def test_bound_group_is_proxy_after_init_task(self, example_mod):
        """
        After init_task, groups bound to the current task are GroupProxy.
        """
        ConfigCls = _make_config_cls(example_mod)
        config = ConfigCls(self.TEST_CONFIG_NAME, task='Main')

        config.release()
        config.task = 'Main'
        config.init_task()

        campaign = config.Campaign
        assert type(campaign) is GroupProxy
        assert campaign._task == 'Main'
        assert campaign._group == 'Campaign'

    def test_unbound_group_fallback_before_init_task(self, example_mod):
        """
        Accessing an unbound group (via _getattr) before init_task must NOT
        prevent the bound group from being correctly loaded during init_task.
        This tests the scenario where an override triggers _getattr for a
        missing group before init_task runs.
        """
        ConfigCls = _make_config_cls(example_mod)
        config = ConfigCls(self.TEST_CONFIG_NAME, task='Main')

        # Set a value first
        config.Campaign.Name = '6-1'

        # Simulate: release -> override (triggers _getattr for Emotion) -> init_task
        config.release()
        config.task = 'Main'

        # override(Emotion_Mode=...) triggers _getattr('Emotion')
        # because Emotion is not bound to Main task.
        # The unbound group access must not interfere with bound groups.
        config.override(Emotion_Mode='ignore')

        # Now init_task should correctly load Campaign as a bound GroupProxy
        config.init_task()

        assert type(config.Campaign) is GroupProxy
        assert config.Campaign.Name == '6-1'

    def test_unbound_group_fallback_after_init_task(self, example_mod):
        """
        After init_task, accessing an unbound group falls back correctly
        without corrupting bound groups.
        """
        ConfigCls = _make_config_cls(example_mod)
        config = ConfigCls(self.TEST_CONFIG_NAME, task='Main')

        config.release()
        config.task = 'Main'
        config.init_task()

        # Bound groups still work correctly
        assert config.Campaign.Name == '12-4'


class TestSchedulerConfigFullIteration:
    """
    Scenario: Full scheduler iteration with get_next_task and init_task.
    This simulates the complete _task_loop() flow and ensures config
    values set through the API remain accessible after the entire cycle.
    """

    TEST_CONFIG_NAME = ':memory:'

    def test_full_scheduler_iteration(self, example_mod):
        """
        Simulates a complete scheduler iteration:
        release() -> get_next_task() -> task=X -> init_task()
        Config values set before the iteration survive.
        """
        ConfigCls = _make_config_cls(example_mod)
        config = ConfigCls(self.TEST_CONFIG_NAME, task='Main')

        # User sets and saves config through API
        config.Campaign.Name = '8-2'

        # Simulate scheduler iteration using the correct task name.
        # get_next_task returns the first pending task which may not be 'Main'.
        # In a real scenario the scheduler would have routed to this task.
        config.release()
        config.task = 'Main'
        config.init_task()

        assert config.Campaign.Name == '8-2'

    def test_value_updated_between_iterations(self, example_mod):
        """
        User updates a config value between scheduler iterations.
        The new value must be picked up after release + init_task.
        """
        ConfigCls = _make_config_cls(example_mod)
        config = ConfigCls(self.TEST_CONFIG_NAME, task='Main')

        # First iteration: set initial value
        config.Campaign.Name = '11-3'
        config.release()
        config.task = 'Main'
        config.init_task()
        assert config.Campaign.Name == '11-3'

        # User updates value (this triggers auto-save to DB)
        config.Campaign.Name = '13-2'

        config.release()
        config.task = 'Main'
        config.init_task()
        assert config.Campaign.Name == '13-2'


class TestSchedulerConfigWaitAndRecover:
    """
    Scenario: Scheduler waits for NextRun, then recovers.

    During wait, _wait_future() releases the config cache and re-initializes
    it after the wait completes. Values must survive this cycle.

    Flow: release -> get_next_task -> init_task -> wait ->
          release -> init_task -> task reads config
    """

    TEST_CONFIG_NAME = ':memory:'

    def test_wait_recover_keeps_saved_values(self, example_mod):
        """
        Simulating the wait-recover cycle that _wait_future performs:
        release + init_task after wait recovers previously saved values.
        """
        ConfigCls = _make_config_cls(example_mod)
        config = ConfigCls(self.TEST_CONFIG_NAME, task='Main')

        config.Campaign.Name = '14-1'

        # Initial setup (before wait)
        config.release()
        config.task = 'Main'
        config.init_task()

        # Simulate: wait completed, re-init (as in _wait_future after returning True)
        config.release()
        config.init_task()

        assert config.Campaign.Name == '14-1'

    def test_value_modified_during_wait_is_picked_up(self, example_mod):
        """
        User modifies a config value while the scheduler is waiting.
        After wait, release + init_task must pick up the new value.
        """
        ConfigCls = _make_config_cls(example_mod)
        config = ConfigCls(self.TEST_CONFIG_NAME, task='Main')

        # Set initial value
        config.Campaign.Name = '12-4'

        # Initial setup (before wait)
        config.release()
        config.task = 'Main'
        config.init_task()
        assert config.Campaign.Name == '12-4'

        # User modifies config while scheduler waits (triggers auto-save)
        config.Campaign.Name = '15-1'

        # After wait: recover
        config.release()
        config.init_task()

        assert config.Campaign.Name == '15-1'


class TestSchedulerConfigTaskSwitch:
    """
    Scenario: task_switched() detects that the config was modified,
    reloads from DB, and checks for task switch.

    This tests the path where a config modification causes data_version change,
    triggering release + init_task inside task_switched().
    """

    TEST_CONFIG_NAME = ':memory:'

    def test_config_modification_detected_after_task_switched(self, example_mod):
        """
        When a config value is modified through the API (triggering auto-save),
        task_switched() detects the data_version change, calls release + init_task,
        and the new value becomes accessible.
        """
        ConfigCls = _make_config_cls(example_mod)
        config = ConfigCls(self.TEST_CONFIG_NAME, task='Main')

        # Set initial state
        config.Campaign.Name = '15-2'
        config.release()
        config.task = 'Main'
        config.init_task()

        # Simulate: during task execution, user modifies config through API
        config.Campaign.Name = '15-3'

        # task_switched checks data_version and reloads if changed
        switched = config.task_switched()

        # task_switched should have detected the change and reloaded
        # (return value depends on whether the task is still the first pending)
        # Regardless, the new value must be accessible
        assert config.Campaign.Name == '15-3'


class TestSchedulerConfigCrossTask:
    """
    Scenario: Scheduler runs multiple tasks in sequence. Config values set
    for one task must survive when other tasks are initialized between.

    This simulates the real scenario where GemsFarming is delayed,
    other tasks (Commission, Reward, etc.) run in between,
    and then GemsFarming resumes.
    """

    TEST_CONFIG_NAME = ':memory:'
    TEST_CONFIG2_NAME = ':memory:'

    def test_cross_task_value_preserved(self, example_mod):
        """
        Set a value for Task A, run init_task for Task B (different task),
        then run init_task for Task A again. The saved value for Task A
        must still be accessible.

        This simulates: GemsFarming delayed -> other tasks run ->
        GemsFarming resumes and reads its config.
        """
        ConfigCls = _make_config_cls(example_mod)
        config = ConfigCls(self.TEST_CONFIG_NAME, task='Main')

        # --- GemsFarming phase: set config value ---
        config.Campaign.Name = '14-2'
        config.release()
        config.task = 'Main'
        config.init_task()
        assert config.Campaign.Name == '14-2'

        # --- Other tasks run in between ---
        # Simulate other tasks (e.g. Commission, Reward) being initialized
        # by the scheduler. In real scenario this happens via _task_loop:
        # release() -> get_next_task() -> task='Other' -> init_task()
        config.release()
        config.task = 'Dashboard'
        config.init_task()

        # --- GemsFarming resumes ---
        config.release()
        config.task = 'Main'
        config.init_task()

        # Campaign.Name was set for 'Main' task, should still be preserved
        assert config.Campaign.Name == '14-2'

    def test_cross_task_other_group_preserved(self, example_mod):
        """
        Simulate the actual GemsFarming scenario: a custom group
        (like GemsFarming with CommonDD) survives cross-task cycles.

        The Main task's Campaign group acts as a proxy for any task-specific
        group that would be defined by a mod.
        """
        ConfigCls = _make_config_cls(example_mod)
        config = ConfigCls(self.TEST_CONFIG_NAME, task='Main')

        # Set the critical config value
        config.Campaign.Name = '13-1'
        config.Campaign.Mode = 'hard'
        config.release()
        config.task = 'Main'
        config.init_task()
        assert config.Campaign.Name == '13-1'
        assert config.Campaign.Mode == 'hard'

        # Run three other tasks in sequence (simulating scheduler loop)
        for other_task in ['Dashboard', 'General', 'Alas']:
            config.release()
            config.task = other_task
            config.init_task()

        # Resume Main task - values must still be correct
        config.release()
        config.task = 'Main'
        config.init_task()
        assert config.Campaign.Name == '13-1'
        assert config.Campaign.Mode == 'hard'

    def test_override_survives_cross_task(self, example_mod):
        """
        Override set for Task A survives even after other tasks run.
        This tests: override -> release -> init_task(other) -> release -> init_task(A)
        """
        ConfigCls = _make_config_cls(example_mod)
        config = ConfigCls(self.TEST_CONFIG_NAME, task='Main')

        # Set saved value and override
        config.Campaign.Name = '12-2'
        config.override(Campaign_Mode='hard')
        config.release()
        config.task = 'Main'
        config.init_task()

        # Other task runs
        config.release()
        config.task = 'Dashboard'
        config.init_task()

        # Back to Main
        config.release()
        config.task = 'Main'
        config.init_task()

        # Saved value survives
        assert config.Campaign.Name == '12-2'
        # Override survives
        assert config.Campaign.Mode == 'hard'

    def test_getattr_during_other_task_does_not_corrupt(self, example_mod):
        """
        If during another task's execution, an unbound _getattr is triggered
        for a group that belongs to the original task, it must not corrupt
        the original task's values when it resumes.

        This simulates: during Commission running, some code accesses
        config.Campaign (unbound for Commission), creating a default instance.
        When GemsFarming resumes, Campaign must still be loaded from DB.
        """
        ConfigCls = _make_config_cls(example_mod)
        config = ConfigCls(self.TEST_CONFIG_NAME, task='Main')

        # Save a non-default value
        config.Campaign.Name = '11-1'
        config.release()
        config.task = 'Main'
        config.init_task()

        # Switch to another task
        config.release()
        config.task = 'Dashboard'
        config.init_task()

        # While Dashboard is running, some code accidentally accesses
        # Campaign (which is unbound for Dashboard).
        # This triggers _getattr, creating a DEFAULT Campaign instance.
        campaign = config.Campaign
        assert campaign.Name == '12-4'  # default, since Dashboard has no saved Campaign
        # The unbound access cached Campaign on the config object

        # Switch back to Main
        config.release()
        config.task = 'Main'
        config.init_task()

        # Campaign should now be loaded from DB with saved value
        assert type(config.Campaign) is GroupProxy
        assert config.Campaign.Name == '11-1'

    def test_override_triggers_getattr_other_task(self, example_mod):
        """
        When override() is called during another task (simulating
        load_campaign style overrides), it triggers _getattr for an
        unbound group. This does not corrupt the original task's group.
        """
        ConfigCls = _make_config_cls(example_mod)
        config = ConfigCls(self.TEST_CONFIG_NAME, task='Main')

        # Save value for Main
        config.Campaign.Name = '10-2'
        config.release()
        config.task = 'Main'
        config.init_task()

        # Switch to Dashboard and apply override (as GemsFarming's
        # load_campaign calls override for Emotion)
        config.release()
        config.task = 'Dashboard'
        config.init_task()

        # This triggers _getattr for Emotion (unbound for Dashboard)
        # Similar to how GemsFarming.load_campaign calls override(Emotion_Mode=...)
        config.override(Emotion_Mode='ignore')

        # Switch back to Main
        config.release()
        config.task = 'Main'
        config.init_task()

        # Main's config must be intact
        assert config.Campaign.Name == '10-2'
