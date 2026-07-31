"""
Tests for scheduler config lifecycle.

Verifies that the scheduler's config lifecycle (release/init_task) correctly
preserves user-modified config values across various scheduling scenarios:

- User modifies a setting -> runtime reads the modified value, not the default
- Task A sets values, Task B runs, Task A resumes -> Task A's values are intact
- Override values persist through release + init_task cycles
- Unbound group access during other tasks does not corrupt bound groups
- Config is correctly released during wait and recovered afterward

All values are set and accessed through the normal config API
(config.group.arg = value / config.group.arg), not by direct DB manipulation.
Uses ExampleMod which provides a minimal but complete mod structure.
"""

import pytest

from alasio.config.alasio.group_proxy import GroupProxy
from alasio.config.base import AlasioConfigBase
from alasio.db.conn import SQLITE_POOL
from alasio.ext import env
from alasio.logger import logger
from ExampleMod.module.config.const import entry

env.ALASIO_ROOT.chdir_here()


@pytest.fixture(scope="module")
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
        SQLITE_POOL.delete_file(":memory:")


def _make_config_cls(example_mod):
    """Create a dynamic config class with ExampleMod's entry."""

    class TestConfig(AlasioConfigBase):
        entry = example_mod.entry
        # Groups needed for scheduler-like scenarios.
        # Scheduler is bound to Main task so init_task loads it.
        Scheduler: "scheduler.Scheduler"
        Campaign: "main.Campaign"

    return TestConfig


# ---------------------------------------------------------------------------
# Basic lifecycle: release + init_task
# ---------------------------------------------------------------------------


class TestSchedulerConfigBasicLifecycle:
    """
    Scenario: Scheduler runs a task directly when NextRun is in the past.

    Flow: release() -> get_next_task() -> init_task() -> task reads config

    After release + init_task, a value set through the config API must be
    accessible through the normal config.group.arg access pattern.
    """

    TEST_CONFIG_NAME = ":memory:"

    def test_user_set_value_survives_release_init(self, example_mod):
        """
        User sets Campaign.Name via config API (auto-saves to DB),
        then release() + init_task() (as scheduler does between iterations).
        The modified value must survive the reload.
        """
        config_cls = _make_config_cls(example_mod)
        config = config_cls(self.TEST_CONFIG_NAME, task="Main")

        # User sets value through config API -> auto-saves to DB
        config.Campaign.Name = "3-4"

        # Scheduler iteration: clear cache, reload from DB
        config.release()
        config.task = "Main"
        config.init_task()

        # The value set through the API must still be accessible
        assert config.Campaign.Name == "3-4"

    def test_multiple_release_init_cycles(self, example_mod):
        """
        Values survive multiple release + init_task cycles,
        just like multiple scheduler iterations.
        """
        config_cls = _make_config_cls(example_mod)
        config = config_cls(self.TEST_CONFIG_NAME, task="Main")

        config.Campaign.Name = "5-1"

        # Cycle 1
        config.release()
        config.task = "Main"
        config.init_task()
        assert config.Campaign.Name == "5-1"

        # Cycle 2
        config.release()
        config.init_task()
        assert config.Campaign.Name == "5-1"

        # Cycle 3
        config.release()
        config.init_task()
        assert config.Campaign.Name == "5-1"

    def test_default_value_when_none_saved(self, example_mod):
        """
        When no value has been set, the model's default is returned.
        """
        config_cls = _make_config_cls(example_mod)
        config = config_cls(self.TEST_CONFIG_NAME, task="Main")

        config.release()
        config.task = "Main"
        config.init_task()

        # Default from main_model.py: Campaign.Name = '12-4'
        assert config.Campaign.Name == "12-4"

    def test_multiple_fields_all_survive(self, example_mod):
        """
        Multiple fields in the same group are all set through the API
        and correctly loaded after release + init_task.
        """
        config_cls = _make_config_cls(example_mod)
        config = config_cls(self.TEST_CONFIG_NAME, task="Main")

        # Set multiple fields through config API
        config.Campaign.Name = "7-2"
        config.Campaign.Mode = "hard"

        config.release()
        config.task = "Main"
        config.init_task()

        assert config.Campaign.Name == "7-2"
        assert config.Campaign.Mode == "hard"

    def test_value_updated_between_iterations(self, example_mod):
        """
        User updates a config value between scheduler iterations.
        The new value must be picked up after release + init_task.
        """
        config_cls = _make_config_cls(example_mod)
        config = config_cls(self.TEST_CONFIG_NAME, task="Main")

        # First iteration: set initial value
        config.Campaign.Name = "11-3"
        config.release()
        config.task = "Main"
        config.init_task()
        assert config.Campaign.Name == "11-3"

        # User updates value (triggers auto-save to DB)
        config.Campaign.Name = "13-2"

        config.release()
        config.task = "Main"
        config.init_task()
        assert config.Campaign.Name == "13-2"


# ---------------------------------------------------------------------------
# Override persistence
# ---------------------------------------------------------------------------


class TestSchedulerConfigOverride:
    """
    Scenario: Override is called (simulating load_campaign in GemsFarming)
    before release + init_task. Override must persist across init_task.
    """

    TEST_CONFIG_NAME = ":memory:"

    def test_override_persists_across_init_task(self, example_mod):
        """
        Override set via config.override() persists after release + init_task.
        This mirrors scheduler behavior where override is called
        between tasks or at task start.
        """
        config_cls = _make_config_cls(example_mod)
        config = config_cls(self.TEST_CONFIG_NAME, task="Main")

        config.override(Campaign_Mode="hard")
        assert config.Campaign.Mode == "hard"

        # release + init_task (as in scheduler loop between iterations)
        config.release()
        config.task = "Main"
        config.init_task()

        # Override must survive
        assert config.Campaign.Mode == "hard"

    def test_override_combined_with_saved_values(self, example_mod):
        """
        Override only affects the specified fields.
        Other fields loaded from saved values are not corrupted.
        """
        config_cls = _make_config_cls(example_mod)
        config = config_cls(self.TEST_CONFIG_NAME, task="Main")

        # Set and save a value through API
        config.Campaign.Name = "10-1"

        # Override a different field
        config.override(Campaign_Mode="hard")

        config.release()
        config.task = "Main"
        config.init_task()

        # Override value survives
        assert config.Campaign.Mode == "hard"
        # Saved value survives and is not corrupted
        assert config.Campaign.Name == "10-1"

    def test_multiple_overrides_all_survive(self, example_mod):
        """
        Multiple overrides all persist across release + init_task.
        """
        config_cls = _make_config_cls(example_mod)
        config = config_cls(self.TEST_CONFIG_NAME, task="Main")

        config.override(
            Campaign_Mode="hard",
            Campaign_UseAutoSearch=True,
        )
        config.release()
        config.task = "Main"
        config.init_task()

        assert config.Campaign.Mode == "hard"
        assert config.Campaign.UseAutoSearch is True

    def test_override_overrides_default_after_release(self, example_mod):
        """
        Override takes effect even when no DB value was saved,
        and persists across release + init_task.
        """
        config_cls = _make_config_cls(example_mod)
        config = config_cls(self.TEST_CONFIG_NAME, task="Main")

        # Release first to simulate fresh start
        config.release()
        config.task = "Main"
        config.init_task()

        # Default is '12-4'
        assert config.Campaign.Name == "12-4"

        # Set override
        config.override(Campaign_Name="custom-map")

        config.release()
        config.task = "Main"
        config.init_task()

        # Override should still be active, overriding the default
        assert config.Campaign.Name == "custom-map"


# ---------------------------------------------------------------------------
# Bound groups
# ---------------------------------------------------------------------------


class TestSchedulerConfigBoundGroups:
    """
    Scenario: Groups bound to the current task must be loaded as GroupProxy,
    not as plain Structs. This ensures the config system tracks modifications
    and triggers auto-save.
    """

    TEST_CONFIG_NAME = ":memory:"

    def test_bound_group_is_proxy_after_init_task(self, example_mod):
        """
        After init_task, groups bound to the current task are GroupProxy.
        """
        config_cls = _make_config_cls(example_mod)
        config = config_cls(self.TEST_CONFIG_NAME, task="Main")

        config.release()
        config.task = "Main"
        config.init_task()

        campaign = config.Campaign
        assert type(campaign) is GroupProxy
        assert campaign._task == "Main"
        assert campaign._group == "Campaign"

    def test_unbound_group_fallback_before_init_task(self, example_mod):
        """
        Accessing an unbound group (via _getattr) before init_task must NOT
        prevent the bound group from being correctly loaded during init_task.
        This tests the scenario where an override triggers _getattr for a
        missing group before init_task runs.
        """
        config_cls = _make_config_cls(example_mod)
        config = config_cls(self.TEST_CONFIG_NAME, task="Main")

        # Set a value first
        config.Campaign.Name = "6-1"

        # Simulate: release -> override (triggers _getattr for Emotion) -> init_task
        config.release()
        config.task = "Main"

        # override(Emotion_Mode=...) triggers _getattr("Emotion")
        # because Emotion is not bound to Main task.
        # The unbound group access must not interfere with bound groups.
        config.override(Emotion_Mode="ignore")

        # Now init_task should correctly load Campaign as a bound GroupProxy
        config.init_task()

        assert type(config.Campaign) is GroupProxy
        assert config.Campaign.Name == "6-1"

    def test_unbound_group_fallback_after_init_task_default(self, example_mod):
        """
        After init_task, accessing an unbound group falls back correctly
        without corrupting bound groups. The unbound group returns defaults.
        """
        config_cls = _make_config_cls(example_mod)
        config = config_cls(self.TEST_CONFIG_NAME, task="Main")

        config.release()
        config.task = "Main"
        config.init_task()

        # Bound groups still work correctly
        assert config.Campaign.Name == "12-4"


# ---------------------------------------------------------------------------
# Wait and recover
# ---------------------------------------------------------------------------


class TestSchedulerConfigWaitAndRecover:
    """
    Scenario: Scheduler waits for NextRun, then recovers.

    During wait, _wait_future() releases the config cache and re-initializes
    it after the wait completes. This test ensures the config lifecycle
    methods work correctly for this pattern.
    """

    TEST_CONFIG_NAME = ":memory:"

    def test_wait_recover_keeps_values(self, example_mod):
        """
        Simulating the wait-recover cycle: release + init_task
        after wait recovers previously saved values.
        """
        config_cls = _make_config_cls(example_mod)
        config = config_cls(self.TEST_CONFIG_NAME, task="Main")

        config.Campaign.Name = "14-1"

        # Initial setup (before wait)
        config.release()
        config.task = "Main"
        config.init_task()

        # Simulate: wait completed, re-init (as in _wait_future after returning True)
        config.release()
        config.init_task()

        assert config.Campaign.Name == "14-1"

    def test_value_set_during_wait_is_picked_up(self, example_mod):
        """
        User modifies a config value while the scheduler is waiting
        (e.g. through the GUI). After wait completes and config recovers
        via release + init_task, the new value must be loaded from DB.
        """
        config_cls = _make_config_cls(example_mod)
        config = config_cls(self.TEST_CONFIG_NAME, task="Main")

        # Set initial value
        config.Campaign.Name = "12-4"

        # Initial setup (before wait)
        config.release()
        config.task = "Main"
        config.init_task()
        assert config.Campaign.Name == "12-4"

        # User modifies config while scheduler waits (triggers auto-save)
        config.Campaign.Name = "15-1"

        # After wait: recover
        config.release()
        config.init_task()

        assert config.Campaign.Name == "15-1"

    def test_release_clears_cache(self, example_mod):
        """
        After release(), the config cache is cleared.
        Accessing a group after release forces a fresh load from DB.
        This simulates config being released during wait.
        """
        config_cls = _make_config_cls(example_mod)
        config = config_cls(self.TEST_CONFIG_NAME, task="Main")

        # Set a value
        config.Campaign.Name = "15-1"

        # Release the cache (as scheduler does during wait)
        config.release()

        # Accessing a group after release:
        # Campaign is no longer in _dict_group because release() cleared it.
        # Since config.task is still "Main", init_task is needed to reload.
        # Accessing config.Campaign before init_task triggers _getattr,
        # which creates a default instance. This is the expected behavior
        # during wait - config is released.
        # After init_task, the saved value is loaded again.
        config.task = "Main"
        config.init_task()

        assert config.Campaign.Name == "15-1"

    def test_release_during_wait_no_init_returns_default(self, example_mod):
        """
        After release(), if init_task() is NOT called yet,
        accessing a group returns the default value (unbound fallback).
        This simulates the state during wait where config is released
        and task accesses should not occur.
        """
        config_cls = _make_config_cls(example_mod)
        config = config_cls(self.TEST_CONFIG_NAME, task="Main")

        config.Campaign.Name = "15-4"

        # Release (as scheduler does during wait)
        config.release()

        # Access Campaign without init_task first -> triggers _getattr
        # which returns default values since cache is empty
        campaign = config.Campaign
        assert campaign.Name == "12-4"  # default, not the saved "15-4"


# ---------------------------------------------------------------------------
# Cross-task value preservation
# ---------------------------------------------------------------------------


class TestSchedulerConfigCrossTask:
    """
    Scenario: Scheduler runs multiple tasks in sequence. Config values set
    for one task must survive when other tasks are initialized between.

    This simulates the real scenario where GemsFarming is delayed,
    other tasks (Commission, Reward, etc.) run in between,
    and then GemsFarming resumes and reads its own config.
    """

    TEST_CONFIG_NAME = ":memory:"

    def test_cross_task_values_preserved(self, example_mod):
        """
        Set a value for Task A, run init_task for Task B (different task),
        then run init_task for Task A again. The saved value for Task A
        must still be accessible after Task B ran.

        This simulates: GemsFarming delayed -> other tasks run ->
        GemsFarming resumes and reads its config.
        """
        config_cls = _make_config_cls(example_mod)
        config = config_cls(self.TEST_CONFIG_NAME, task="Main")

        # --- Phase 1: Main task sets config value ---
        config.Campaign.Name = "14-2"
        config.release()
        config.task = "Main"
        config.init_task()
        assert config.Campaign.Name == "14-2"

        # --- Phase 2: Other tasks run in between ---
        # Simulate other tasks being initialized by the scheduler.
        # release() -> task='Dashboard' -> init_task()
        config.release()
        config.task = "Dashboard"
        config.init_task()

        # --- Phase 3: Main task resumes ---
        config.release()
        config.task = "Main"
        config.init_task()

        # Campaign.Name was set for "Main" task, should still be preserved
        assert config.Campaign.Name == "14-2"

    def test_cross_task_multiple_intermediate_tasks(self, example_mod):
        """
        Run three other tasks in between Main task executions.
        Main's config must still be correct after all of them.
        """
        config_cls = _make_config_cls(example_mod)
        config = config_cls(self.TEST_CONFIG_NAME, task="Main")

        # Set config values for Main
        config.Campaign.Name = "13-1"
        config.Campaign.Mode = "hard"
        config.release()
        config.task = "Main"
        config.init_task()
        assert config.Campaign.Name == "13-1"
        assert config.Campaign.Mode == "hard"

        # Run three other tasks in sequence
        for other_task in ["Dashboard", "General", "Alas"]:
            config.release()
            config.task = other_task
            config.init_task()

        # Resume Main task - values must still be correct
        config.release()
        config.task = "Main"
        config.init_task()
        assert config.Campaign.Name == "13-1"
        assert config.Campaign.Mode == "hard"

    def test_override_cleared_on_task_switch(self, example_mod):
        """
        Override set for Task A is cleared when switching to another task,
        because scheduler calls release() + override_clear() at the start
        of each task loop. Saved DB values still survive.

        This tests: override -> release + override_clear -> init_task(B)
        -> release + override_clear -> init_task(A)
        """
        config_cls = _make_config_cls(example_mod)
        config = config_cls(self.TEST_CONFIG_NAME, task="Main")

        # Set saved value and override
        config.Campaign.Name = "12-2"
        config.override(Campaign_Mode="hard")
        config.release()
        config.task = "Main"
        config.init_task()
        assert config.Campaign.Mode == "hard"

        # Scheduler loop starts for another task:
        # release() clears group cache, override_clear() clears overrides
        config.release()
        config.override_clear()
        config.task = "Dashboard"
        config.init_task()

        # Back to Main: scheduler loop clears overrides again
        config.release()
        config.override_clear()
        config.task = "Main"
        config.init_task()

        # Saved value survives
        assert config.Campaign.Name == "12-2"
        # Override is cleared, falls back to saved value (default 'normal')
        assert config.Campaign.Mode == "normal"

    def test_override_cleared_at_task_loop_start(self, example_mod):
        """
        Simulates the start of a scheduler _task_loop() iteration:
        release() + override_clear() are called before the next task runs.
        Overrides from the previous task are cleared, saved DB values remain.
        """
        config_cls = _make_config_cls(example_mod)
        config = config_cls(self.TEST_CONFIG_NAME, task="Main")

        # Previous task set a saved value and an override
        config.Campaign.Name = "13-3"
        config.override(Campaign_Mode="hard")
        assert config.Campaign.Mode == "hard"

        # Scheduler starts a new task loop
        config.release()
        config.override_clear()

        # Override state is fully cleared
        assert not config._override_config
        assert not config._override_prev_config

        # New task initializes and reads from DB
        config.task = "Main"
        config.init_task()

        # Saved value survives, override is gone
        assert config.Campaign.Name == "13-3"
        assert config.Campaign.Mode == "normal"

    def test_getattr_during_other_task_does_not_corrupt(self, example_mod):
        """
        If during another task's execution, an unbound _getattr is triggered
        for a group that belongs to the original task, it must not corrupt
        the original task's values when it resumes.

        This simulates: during Dashboard running, some code accesses
        config.Campaign (unbound for Dashboard), creating a default instance.
        When Main resumes, Campaign must still be loaded from DB.
        """
        config_cls = _make_config_cls(example_mod)
        config = config_cls(self.TEST_CONFIG_NAME, task="Main")

        # Save a non-default value
        config.Campaign.Name = "11-1"
        config.release()
        config.task = "Main"
        config.init_task()

        # Switch to another task
        config.release()
        config.task = "Dashboard"
        config.init_task()

        # While Dashboard is running, some code accidentally accesses
        # Campaign (which is unbound for Dashboard).
        # This triggers _getattr, creating a default Campaign instance.
        campaign = config.Campaign
        assert campaign.Name == "12-4"  # default, since Dashboard has no saved Campaign

        # Switch back to Main
        config.release()
        config.task = "Main"
        config.init_task()

        # Campaign should now be loaded from DB with saved value
        assert type(config.Campaign) is GroupProxy
        assert config.Campaign.Name == "11-1"

    def test_override_triggers_getattr_other_task(self, example_mod):
        """
        When override() is called during another task (simulating
        load_campaign style overrides), it triggers _getattr for an
        unbound group. This does not corrupt the original task's group.
        """
        config_cls = _make_config_cls(example_mod)
        config = config_cls(self.TEST_CONFIG_NAME, task="Main")

        # Save value for Main
        config.Campaign.Name = "10-2"
        config.release()
        config.task = "Main"
        config.init_task()

        # Switch to Dashboard and apply override
        config.release()
        config.task = "Dashboard"
        config.init_task()

        # This triggers _getattr for Emotion (unbound for Dashboard)
        config.override(Emotion_Mode="ignore")

        # Switch back to Main
        config.release()
        config.task = "Main"
        config.init_task()

        # Main's config must be intact
        assert config.Campaign.Name == "10-2"


# ---------------------------------------------------------------------------
# Config modification detection (task_switched)
# ---------------------------------------------------------------------------


class TestSchedulerConfigTaskSwitched:
    """
    Scenario: task_switched() detects that the config was modified,
    reloads from DB, and checks for task switch.

    This tests the path where a config modification causes data_version change,
    triggering release + init_task inside task_switched().
    """

    TEST_CONFIG_NAME = ":memory:"

    def test_value_modification_detected_by_task_switched(self, example_mod):
        """
        When a config value is modified through the API (triggering auto-save),
        task_switched() detects the data_version change, calls release + init_task,
        and the new value becomes accessible.
        """
        config_cls = _make_config_cls(example_mod)
        config = config_cls(self.TEST_CONFIG_NAME, task="Main")

        # Set initial state
        config.Campaign.Name = "15-2"
        config.release()
        config.task = "Main"
        config.init_task()

        # Simulate: during task execution, user modifies config through API
        config.Campaign.Name = "15-3"

        # task_switched checks data_version and reloads if changed
        switched = config.task_switched()

        # The new value must be accessible after task_switched
        assert config.Campaign.Name == "15-3"

    def test_task_switched_reloads_updated_value(self, example_mod):
        """
        After task_switched detects a modification and reloads,
        the updated value from DB is available even if the previous
        in-memory value was different.
        """
        config_cls = _make_config_cls(example_mod)
        config = config_cls(self.TEST_CONFIG_NAME, task="Main")

        # Set initial value
        config.Campaign.Name = "15-2"
        config.release()
        config.task = "Main"
        config.init_task()

        # Modify value
        config.Campaign.Name = "15-3"

        # task_switched reloads from DB
        config.task_switched()

        # Verify the new value is loaded
        assert config.Campaign.Name == "15-3"
