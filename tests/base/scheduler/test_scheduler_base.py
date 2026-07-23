"""
Tests for AlasioScheduler.

Uses mock data to cover all code paths in scheduler.py without
depending on actual config/database infrastructure.
"""

from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

from alasio.base.exception import (
    EmulatorNotRunningError, GameBugError, GameNotRunningError, GamePageUnknownError, GameStuckError,
    GameTooManyClickError, RequestHumanTakeover, ScriptError, TaskStop
)
from alasio.base.scheduler.scheduler import AlasioScheduler, SchedulerError, SchedulerStop, interruptable_sleep
from alasio.base.scheduler.task_record import TaskRecord, TaskTooManyExecutionsError, TaskTooManyFailuresError
from alasio.config.entry.model import TaskItem
from alasio.ext.cache import InstanceCacheOperation
from alasio.logger import logger
from alasio.testing.patch_time import PatchTime

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_backend_patchers = []


@pytest.fixture(autouse=True)
def _mute_logs():
    """Capture all logger output during tests to avoid writing to real files."""
    with logger.mock_capture_writer():
        yield


@pytest.fixture(autouse=True)
def _cleanup_backend_patchers():
    """Stop all BackendBridge patchers after each test."""
    yield
    while _backend_patchers:
        p = _backend_patchers.pop()
        try:
            p.stop()
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _clear_task_record():
    """Clear TaskRecord singleton before each test."""
    TaskRecord.singleton_clear()
    yield


@pytest.fixture
def scheduler():
    """Create a bare AlasioScheduler with a mocked config."""
    s = AlasioScheduler("test_config")
    # Clear cached_property so each fixture user gets a fresh property evaluation
    InstanceCacheOperation.pop(s, "config")
    InstanceCacheOperation.pop(s, "device")
    return s


def _make_task_item(task_name, next_run=None):
    """Helper to create a TaskItem with sensible defaults."""
    if next_run is None:
        next_run = datetime.now(timezone.utc).replace(microsecond=0)
    return TaskItem(TaskName=task_name, NextRun=next_run)


def _cache_config(scheduler, **attrs):
    """
    Pre-set a mock config object on the scheduler's cached_property.
    Returns the mock config for assertions.
    """
    config = mock.MagicMock()
    # Default attribute values
    config.task = ""
    config.Error.HandleError = True
    config.Error_ScreenshotLength = 1
    config.Optimization.WhenTaskQueueEmpty = "stay_there"
    config.batch_set.return_value.__enter__ = mock.MagicMock()
    config.batch_set.return_value.__exit__ = mock.MagicMock()
    for key, value in attrs.items():
        # Handle dotted attribute paths like 'Optimization__WhenTaskQueueEmpty'
        if "__" in key:
            parts = key.split("__")
            target = config
            for part in parts[:-1]:
                target = getattr(target, part)
            setattr(target, parts[-1], value)
        else:
            setattr(config, key, value)
    InstanceCacheOperation.set(scheduler, "config", config)
    return config


def _cache_device(scheduler):
    """Pre-set a mock device on the scheduler's cached_property."""
    device = mock.MagicMock()
    device.screenshot_deque_iter.return_value = iter([])
    InstanceCacheOperation.set(scheduler, "device", device)
    return device


def _patch_backend(**attrs):
    """Patch BackendBridge on the scheduler module and return the mock instance."""
    patcher = mock.patch("alasio.base.scheduler.scheduler.BackendBridge")
    mock_cls = patcher.start()
    _backend_patchers.append(patcher)
    mock_instance = mock_cls.return_value
    # Set default attribute values
    mock_instance.scheduler_stopping.is_set.return_value = False
    mock_instance.inited = False
    for key, value in attrs.items():
        # Handle dotted attribute paths like 'scheduler_stopping__is_set'
        if "__" in key:
            parts = key.split("__")
            target = mock_instance
            for part in parts[:-1]:
                target = getattr(target, part)
            setattr(target, parts[-1], value)
        else:
            setattr(mock_instance, key, value)
    return mock_instance


# ---------------------------------------------------------------------------
# interruptable_sleep
# ---------------------------------------------------------------------------


class TestInterruptableSleep:
    """Tests for the standalone interruptable_sleep() function."""

    def test_sleep_completes(self):
        """Sleep returns True after the full duration."""
        with mock.patch(
            "alasio.base.scheduler.scheduler.time.perf_counter"
        ) as mock_time:
            with mock.patch(
                "alasio.base.scheduler.scheduler.BackendBridge"
            ) as mock_bridge:
                mock_bridge.return_value.scheduler_stopping.is_set.return_value = False
                mock_time.side_effect = [100.0, 100.001, 100.002, 100.01]
                result = interruptable_sleep(0.01)
                assert result is True

    def test_sleep_raises_scheduler_stop(self):
        """Sleep raises SchedulerStop when backend signals stop."""
        backend = mock.MagicMock()
        backend.scheduler_stopping.is_set.side_effect = [False, True]
        with mock.patch(
            "alasio.base.scheduler.scheduler.BackendBridge", return_value=backend
        ):
            with mock.patch(
                "alasio.base.scheduler.scheduler.time.perf_counter", return_value=100.0
            ):
                with pytest.raises(SchedulerStop):
                    interruptable_sleep(5)


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestAlasioSchedulerInit:
    """Tests for AlasioScheduler.__init__, create_config, create_device."""

    def test_init_stores_config_name(self):
        """Constructor stores config_name and sets skip_first_tasks."""
        s = AlasioScheduler("my_config")
        assert s.config_name == "my_config"
        assert s.skip_first_tasks == {"Restart", "RestartDevice", "RestartGame"}

    def test_create_config_returns_alasio_config(self):
        """create_config() delegates to AlasioConfigBase."""
        s = AlasioScheduler("test_config")
        with mock.patch("alasio.config.base.AlasioConfigBase") as MockCls:
            cfg = s.create_config()
            MockCls.assert_called_once_with("test_config")
            assert cfg is MockCls.return_value

    def test_config_cached_property_caches(self, scheduler):
        """Accessing .config twice returns the same cached object."""
        with mock.patch("alasio.config.base.AlasioConfigBase") as MockCls:
            c1 = scheduler.config
            c2 = scheduler.config
            assert c1 is c2
            # create_config should only be called once
            assert MockCls.call_count == 1

    def test_config_raises_scheduler_error_on_request_human_takeover(self, scheduler):
        """config cached_property raises SchedulerError on RequestHumanTakeover."""
        with mock.patch.object(
            scheduler,
            "create_config",
            side_effect=RequestHumanTakeover("manual intervention needed"),
        ):
            with pytest.raises(SchedulerError):
                _ = scheduler.config

    def test_config_raises_scheduler_error_on_generic_exception(self, scheduler):
        """config cached_property raises SchedulerError on generic Exception."""
        with mock.patch.object(
            scheduler, "create_config", side_effect=ValueError("something broke")
        ):
            with pytest.raises(SchedulerError):
                _ = scheduler.config

    def test_create_device(self, scheduler):
        """create_device() uses DeviceConfig.from_config and DeviceBase."""
        _cache_config(scheduler)
        with mock.patch("alasio.device.config.DeviceConfig") as MockDC:
            with mock.patch("alasio.device.base.DeviceBase") as MockDB:
                dev = scheduler.create_device()
                MockDC.from_config.assert_called_once()
                MockDB.assert_called_once_with(MockDC.from_config.return_value)
                assert dev is MockDB.return_value

    def test_device_cached_property_caches(self, scheduler):
        """Accessing .device twice returns the same cached object."""
        _cache_config(scheduler)
        InstanceCacheOperation.pop(scheduler, "device")
        with mock.patch("alasio.device.config.DeviceConfig") as MockDC:
            with mock.patch("alasio.device.base.DeviceBase") as MockDB:
                d1 = scheduler.device
                d2 = scheduler.device
                assert d1 is d2
                assert MockDB.call_count == 1

    def test_device_raises_scheduler_error_on_request_human_takeover(self, scheduler):
        """device cached_property raises SchedulerError on RequestHumanTakeover."""
        with mock.patch.object(
            scheduler,
            "create_device",
            side_effect=RequestHumanTakeover("manual intervention"),
        ):
            with pytest.raises(SchedulerError):
                _ = scheduler.device

    def test_device_raises_scheduler_error_on_generic_exception(self, scheduler):
        """device cached_property raises SchedulerError on generic Exception."""
        with mock.patch.object(
            scheduler, "create_device", side_effect=RuntimeError("device failure")
        ):
            with pytest.raises(SchedulerError):
                _ = scheduler.device


# ---------------------------------------------------------------------------
# _run_task
# ---------------------------------------------------------------------------


class TestAlasioSchedulerRunTask:
    """Tests for _run_task() covering all exception branches."""

    def test_task_function_not_found(self, scheduler):
        """_run_task raises SchedulerError when no method matches the task name."""
        _cache_config(scheduler)
        _cache_device(scheduler)
        with pytest.raises(SchedulerError):
            scheduler._run_task("NonExistentTask")

    def test_task_success(self, scheduler):
        """_run_task returns True when the task function succeeds."""
        _cache_config(scheduler)
        _cache_device(scheduler)
        scheduler.my_task = mock.MagicMock(return_value=None)
        result = scheduler._run_task("MyTask")
        assert result is True
        scheduler.my_task.assert_called_once()

    def test_task_raises_task_stop(self, scheduler):
        """_run_task returns True when the task function raises TaskStop."""
        _cache_config(scheduler)
        _cache_device(scheduler)

        def _raise_task_stop():
            raise TaskStop("normal stop")

        scheduler.my_task = _raise_task_stop
        result = scheduler._run_task("MyTask")
        assert result is True

    def test_game_not_running(self, scheduler):
        """_run_task returns False and queues RestartGame when GameNotRunningError."""
        _cache_device(scheduler)
        config = _cache_config(scheduler)

        def _raise_game_not_running():
            raise GameNotRunningError("game is not running")

        scheduler.my_task = _raise_game_not_running

        result = scheduler._run_task("MyTask")
        assert result is False
        config.task_call.assert_called_once_with("RestartGame")

    def test_emulator_not_running(self, scheduler):
        """_run_task returns False and queues RestartDevice when EmulatorNotRunningError."""
        _cache_device(scheduler)
        config = _cache_config(scheduler)

        def _raise_emu_not_running():
            raise EmulatorNotRunningError("emu is not running")

        scheduler.my_task = _raise_emu_not_running

        result = scheduler._run_task("MyTask")
        assert result is False
        config.task_call.assert_called_once_with("RestartDevice")

    def test_game_stuck_error(self, scheduler):
        """_run_task returns False, saves error log, and queues RestartGame on GameStuckError."""
        _cache_device(scheduler)
        config = _cache_config(scheduler)

        def _raise_game_stuck():
            raise GameStuckError("game stuck")

        scheduler.my_task = _raise_game_stuck

        with mock.patch.object(scheduler, "_save_error_log") as mock_save:
            with mock.patch("alasio.base.scheduler.scheduler.interruptable_sleep"):
                result = scheduler._run_task("MyTask")
        assert result is False
        mock_save.assert_called_once()
        config.task_call.assert_called_once_with("RestartGame")

    def test_game_too_many_click_error(self, scheduler):
        """_run_task handles GameTooManyClickError same as GameStuckError."""
        _cache_device(scheduler)
        config = _cache_config(scheduler)

        def _raise_too_many_click():
            raise GameTooManyClickError("too many clicks")

        scheduler.my_task = _raise_too_many_click

        with mock.patch.object(scheduler, "_save_error_log") as mock_save:
            with mock.patch("alasio.base.scheduler.scheduler.interruptable_sleep"):
                result = scheduler._run_task("MyTask")
        assert result is False
        mock_save.assert_called_once()
        config.task_call.assert_called_once_with("RestartGame")

    def test_game_bug_error(self, scheduler):
        """_run_task returns False, saves error log, and queues RestartGame on GameBugError."""
        _cache_device(scheduler)
        config = _cache_config(scheduler)

        def _raise_game_bug():
            raise GameBugError("game bug")

        scheduler.my_task = _raise_game_bug

        with mock.patch.object(scheduler, "_save_error_log") as mock_save:
            with mock.patch("alasio.base.scheduler.scheduler.interruptable_sleep"):
                result = scheduler._run_task("MyTask")
        assert result is False
        mock_save.assert_called_once()
        config.task_call.assert_called_once_with("RestartGame")

    def test_game_page_unknown_error(self, scheduler):
        """_run_task returns False on GamePageUnknownError without saving error."""
        _cache_config(scheduler)
        _cache_device(scheduler)

        def _raise_page_unknown():
            raise GamePageUnknownError("unknown page")

        scheduler.my_task = _raise_page_unknown

        with mock.patch.object(scheduler, "_save_error_log") as mock_save:
            result = scheduler._run_task("MyTask")
        assert result is False
        mock_save.assert_not_called()

    def test_request_human_takeover(self, scheduler):
        """_run_task raises SchedulerError on RequestHumanTakeover."""
        _cache_config(scheduler)
        _cache_device(scheduler)

        def _raise_takeover():
            raise RequestHumanTakeover("user intervention required")

        scheduler.my_task = _raise_takeover

        with pytest.raises(SchedulerError):
            scheduler._run_task("MyTask")

    def test_script_error(self, scheduler):
        """_run_task raises SchedulerError on ScriptError."""
        _cache_config(scheduler)
        _cache_device(scheduler)

        def _raise_script_error():
            raise ScriptError("developer mistake")

        scheduler.my_task = _raise_script_error

        with pytest.raises(SchedulerError):
            scheduler._run_task("MyTask")

    def test_generic_exception(self, scheduler):
        """_run_task raises SchedulerError on any other Exception."""
        _cache_config(scheduler)
        _cache_device(scheduler)

        def _raise_generic():
            raise RuntimeError("unexpected error")

        scheduler.my_task = _raise_generic

        with mock.patch.object(scheduler, "_save_error_log") as mock_save:
            with pytest.raises(SchedulerError):
                scheduler._run_task("MyTask")
            mock_save.assert_called_once()


# ---------------------------------------------------------------------------
# _save_error_log
# ---------------------------------------------------------------------------


class TestAlasioSchedulerSaveErrorLog:
    """Tests for _save_error_log()."""

    def test_save_error_log_creates_zip(self, scheduler):
        """_save_error_log writes a zip with logs and screenshots."""
        config = _cache_config(scheduler, config_name="test_config")
        device = _cache_device(scheduler)
        # Provide two screenshots for the deque
        device.screenshot_deque_iter.return_value = iter(
            [
                ("shot1.webp", b"image1data"),
                ("shot2.webp", b"image2data"),
            ]
        )

        mock_error_zip = mock.MagicMock()
        mock_log_writer = mock.MagicMock()
        mock_log_writer.file = "/path/to/log.txt"

        # We need to patch logger._writer.file
        with mock.patch.object(logger, "_writer", mock_log_writer):
            with mock.patch(
                "alasio.base.scheduler.scheduler.ErrorZipWriter"
            ) as MockZip:
                with mock.patch(
                    "alasio.base.scheduler.scheduler.env.PROJECT_ROOT"
                ) as mock_root:
                    mock_root.joinpath.return_value = (
                        "log/error/2026-01-01_00-00-00-000000_test_config.zip"
                    )
                    MockZip.return_value.__enter__.return_value = mock_error_zip

                    scheduler._save_error_log()

        MockZip.assert_called_once()
        mock_error_zip.add_log.assert_called_once_with("/path/to/log.txt")
        # Should add both screenshots
        assert mock_error_zip.add_image.call_count == 2


# ---------------------------------------------------------------------------
# _send_scheduler_running
# ---------------------------------------------------------------------------


class TestAlasioSchedulerSendSchedulerRunning:
    """Tests for _send_scheduler_running()."""

    def test_send_running_task_when_backend_inited(self, scheduler):
        """When backend is initialized, sends TaskQueue event with running task."""
        backend = _patch_backend(inited=True)
        scheduler._send_scheduler_running("Main")
        backend.send.assert_called_once()
        event = backend.send.call_args[0][0]
        assert event.t == "TaskQueue"
        assert event.v == {"running": "Main"}

    def test_send_none_when_task_is_none(self, scheduler):
        """Sends TaskQueue event with running=None when no task is active."""
        backend = _patch_backend(inited=True)
        scheduler._send_scheduler_running(None)
        backend.send.assert_called_once()
        event = backend.send.call_args[0][0]
        assert event.t == "TaskQueue"
        assert event.v == {"running": None}

    def test_skips_when_backend_not_inited(self, scheduler):
        """Does not send events when backend is not initialized."""
        backend = _patch_backend(inited=False)
        scheduler._send_scheduler_running("Main")
        backend.send.assert_not_called()


# ---------------------------------------------------------------------------
# _on_task_switch
# ---------------------------------------------------------------------------


class TestAlasioSchedulerOnTaskSwitch:
    """Tests for _on_task_switch()."""

    def test_on_task_switch_resets_states_and_sends_running(self, scheduler):
        """_on_task_switch resets task states, notifies device, and sends running task."""
        _patch_backend(inited=True)
        device = _cache_device(scheduler)
        _cache_config(scheduler)

        with mock.patch("alasio.base.scheduler.scheduler.TaskState") as MockTS:
            scheduler._on_task_switch("Main")

        MockTS.reset_all_subclasses.assert_called_once()
        device.on_task_switch.assert_called_once()

    def test_on_task_switch_sends_running_task(self, scheduler):
        """_on_task_switch sends the running task to backend via _send_scheduler_running."""
        _cache_config(scheduler)
        _cache_device(scheduler)

        with mock.patch.object(scheduler, "_send_scheduler_running") as mock_send:
            with mock.patch("alasio.base.scheduler.scheduler.TaskState"):
                scheduler._on_task_switch("Main")

        mock_send.assert_called_once_with("Main")

    def test_on_task_switch_none(self, scheduler):
        """_on_task_switch sends None when starting the scheduler."""
        _cache_config(scheduler)
        _cache_device(scheduler)

        with mock.patch.object(scheduler, "_send_scheduler_running") as mock_send:
            with mock.patch("alasio.base.scheduler.scheduler.TaskState"):
                scheduler._on_task_switch(None)

        mock_send.assert_called_once_with(None)


# ---------------------------------------------------------------------------
# _on_game_stop
# ---------------------------------------------------------------------------


class TestAlasioSchedulerOnGameStop:
    """Tests for _on_game_stop()."""

    def test_sends_preview_stop_to_device(self, scheduler):
        """_on_game_stop sends a stop preview signal through the device."""
        device = _cache_device(scheduler)
        scheduler._on_game_stop()
        device.backend_send_preview_stop.assert_called_once()


# ---------------------------------------------------------------------------
# _on_idle
# ---------------------------------------------------------------------------


class TestAlasioSchedulerOnIdle:
    """Tests for _on_idle()."""

    def test_on_idle_resets_states_and_device(self, scheduler):
        """_on_idle resets task states, sends force preview, and notifies device."""
        _patch_backend()
        device = _cache_device(scheduler)

        with mock.patch("alasio.base.scheduler.scheduler.TaskState") as MockTS:
            scheduler._on_idle()

        MockTS.reset_all_subclasses.assert_called_once()
        device.backend_send_preview.assert_called_once_with(force=True)
        device.on_idle.assert_called_once()

    def test_on_idle_sends_no_running_task(self, scheduler):
        """_on_idle sends running=None to backend."""
        _cache_device(scheduler)

        with mock.patch.object(scheduler, "_send_scheduler_running") as mock_send:
            with mock.patch("alasio.base.scheduler.scheduler.TaskState"):
                scheduler._on_idle()
        mock_send.assert_called_once_with(None)


# ---------------------------------------------------------------------------
# _wait_future
# ---------------------------------------------------------------------------


class TestAlasioSchedulerWaitFuture:
    """Tests for _wait_future()."""

    def test_future_already_passed(self, scheduler):
        """Returns True immediately when future <= now."""
        now = datetime.now(timezone.utc).replace(microsecond=0)
        past = now - timedelta(seconds=10)
        result = scheduler._wait_future("Main", past)
        assert result is True

    def test_wait_stop_game(self, scheduler):
        """When WhenTaskQueueEmpty is 'stop_game', game is stopped during wait."""
        config = _cache_config(scheduler, Optimization__WhenTaskQueueEmpty="stop_game")
        _cache_device(scheduler)

        future = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=1)

        _patch_backend()

        with mock.patch("alasio.base.scheduler.scheduler.ConfigWatcher") as MockWatcher:
            watcher = MockWatcher.return_value
            watcher.init.return_value = watcher
            watcher.is_modified.return_value = True
            with mock.patch("alasio.base.scheduler.scheduler.getnow") as mock_getnow:
                mock_getnow.return_value = datetime.now(timezone.utc).replace(
                    microsecond=0
                )
                with mock.patch.object(scheduler, "_run_task") as mock_run:
                    with mock.patch.object(scheduler, "_on_game_stop") as mock_stop:
                        result = scheduler._wait_future("Main", future)

        assert result is False
        mock_run.assert_any_call("stop_game")
        mock_stop.assert_called_once()
        config.release.assert_called_once()

    def test_wait_stop_device(self, scheduler):
        """When WhenTaskQueueEmpty is 'stop_device', device is stopped during wait."""
        _cache_config(scheduler, Optimization__WhenTaskQueueEmpty="stop_device")
        _cache_device(scheduler)

        _patch_backend()
        with PatchTime():
            future = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(
                hours=1
            )
            with mock.patch(
                "alasio.base.scheduler.scheduler.ConfigWatcher"
            ) as MockWatcher:
                watcher = MockWatcher.return_value
                watcher.init.return_value = watcher
                watcher.is_modified.return_value = True
                with mock.patch.object(scheduler, "_run_task") as mock_run:
                    with mock.patch.object(scheduler, "_on_game_stop") as mock_stop:
                        result = scheduler._wait_future("Main", future)

        assert result is False
        mock_run.assert_any_call("stop_device")
        mock_stop.assert_called_once()

    def test_wait_goto_main(self, scheduler):
        """When WhenTaskQueueEmpty is 'goto_main', main page navigation is run."""
        _cache_config(scheduler, Optimization__WhenTaskQueueEmpty="goto_main")
        _cache_device(scheduler)

        _patch_backend()
        with PatchTime():
            future = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(
                hours=1
            )
            with mock.patch(
                "alasio.base.scheduler.scheduler.ConfigWatcher"
            ) as MockWatcher:
                watcher = MockWatcher.return_value
                watcher.init.return_value = watcher
                watcher.is_modified.return_value = True
                with mock.patch.object(scheduler, "_run_task") as mock_run:
                    result = scheduler._wait_future("Main", future)

        assert result is False
        mock_run.assert_any_call("goto_main")

    def test_wait_stay_there(self, scheduler):
        """When WhenTaskQueueEmpty is 'stay_there', no extra action is taken."""
        _cache_config(scheduler, Optimization__WhenTaskQueueEmpty="stay_there")
        _cache_device(scheduler)

        _patch_backend()
        with PatchTime():
            future = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(
                hours=1
            )
            with mock.patch(
                "alasio.base.scheduler.scheduler.ConfigWatcher"
            ) as MockWatcher:
                watcher = MockWatcher.return_value
                watcher.init.return_value = watcher
                watcher.is_modified.return_value = True
                with mock.patch.object(scheduler, "_run_task") as mock_run:
                    result = scheduler._wait_future("Main", future)

        assert result is False
        mock_run.assert_not_called()

    def test_wait_unknown_method(self, scheduler):
        """Unknown WhenTaskQueueEmpty falls back to stay_there with a warning."""
        _cache_config(scheduler, Optimization__WhenTaskQueueEmpty="fly_away")
        _cache_device(scheduler)

        _patch_backend()
        with PatchTime():
            future = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(
                hours=1
            )
            with mock.patch(
                "alasio.base.scheduler.scheduler.ConfigWatcher"
            ) as MockWatcher:
                watcher = MockWatcher.return_value
                watcher.init.return_value = watcher
                watcher.is_modified.return_value = True
                with mock.patch.object(scheduler, "_run_task") as mock_run:
                    with logger.mock_capture_writer() as capture:
                        result = scheduler._wait_future("Main", future)

        assert result is False
        mock_run.assert_not_called()
        assert capture.fd.any_contains("Unknown Optimization.WhenTaskQueueEmpty")

    def test_wait_reached_future(self, scheduler):
        """When timer reaches the future time, returns True and recovers."""
        config = _cache_config(scheduler, Optimization__WhenTaskQueueEmpty="stay_there")
        _cache_device(scheduler)

        backend = _patch_backend()

        with PatchTime():
            future = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(
                seconds=1
            )
            with mock.patch(
                "alasio.base.scheduler.scheduler.ConfigWatcher"
            ) as MockWatcher:
                watcher = MockWatcher.return_value
                watcher.init.return_value = watcher
                watcher.is_modified.return_value = False
                with mock.patch.object(scheduler, "_on_idle"):
                    result = scheduler._wait_future("Main", future)

        assert result is True
        backend.send_worker_state.assert_any_call("scheduler-waiting")
        backend.send_worker_state.assert_any_call("running")
        backend.preview_requested.set.assert_called_once()
        config.init_task.assert_called_once()

    def test_wait_modified_config_returns_false(self, scheduler):
        """When config is modified during wait, returns False without recovering."""
        config = _cache_config(scheduler, Optimization__WhenTaskQueueEmpty="stay_there")
        _cache_device(scheduler)

        _patch_backend()
        with PatchTime():
            future = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(
                hours=1
            )
            with mock.patch(
                "alasio.base.scheduler.scheduler.ConfigWatcher"
            ) as MockWatcher:
                watcher = MockWatcher.return_value
                watcher.init.return_value = watcher
                watcher.is_modified.side_effect = [False] * 9 + [True]
                with mock.patch.object(scheduler, "_on_idle"):
                    result = scheduler._wait_future("Main", future)

        assert result is False
        config.init_task.assert_not_called()

    def test_wait_scheduler_stopping(self, scheduler):
        """Raises SchedulerStop when backend signals stopping during wait."""
        _cache_config(scheduler, Optimization__WhenTaskQueueEmpty="stay_there")
        _cache_device(scheduler)

        backend = _patch_backend()
        backend.scheduler_stopping.is_set.side_effect = [False, True]

        with PatchTime():
            future = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(
                hours=1
            )
            with mock.patch(
                "alasio.base.scheduler.scheduler.ConfigWatcher"
            ) as MockWatcher:
                watcher = MockWatcher.return_value
                watcher.init.return_value = watcher
                with mock.patch.object(scheduler, "_on_idle"):
                    with pytest.raises(SchedulerStop):
                        scheduler._wait_future("Main", future)


# ---------------------------------------------------------------------------
# _skip_first_tasks
# ---------------------------------------------------------------------------


class TestAlasioSchedulerSkipFirstTasks:
    """Tests for _skip_first_tasks()."""

    def test_not_a_skip_task(self, scheduler):
        """Returns False when next_task is not a skip task."""
        scheduler.skip_first_tasks = {"Restart"}
        config = _cache_config(scheduler)

        pending = [
            _make_task_item("Main"),
            _make_task_item("Commission"),
        ]
        next_task = _make_task_item("Main")

        result = scheduler._skip_first_tasks(pending_tasks=pending, next_task=next_task)
        assert result is False
        config.batch_set.assert_not_called()

    def test_skip_single_restart_task(self, scheduler):
        """Skips a single Restart task."""
        scheduler.skip_first_tasks = {"Restart"}
        config = _cache_config(scheduler)

        pending = [
            _make_task_item("Restart"),
            _make_task_item("Main"),
        ]
        next_task = _make_task_item("Restart")

        result = scheduler._skip_first_tasks(pending_tasks=pending, next_task=next_task)

        assert result is True
        config.task_delay.assert_called_once_with(server_update=True, task="Restart")
        assert "Restart" not in scheduler.skip_first_tasks

    def test_skip_multiple_restart_tasks(self, scheduler):
        """Skips multiple consecutive restart tasks."""
        scheduler.skip_first_tasks = {"Restart", "RestartDevice", "RestartGame"}
        config = _cache_config(scheduler)

        pending = [
            _make_task_item("Restart"),
            _make_task_item("RestartDevice"),
            _make_task_item("RestartGame"),
            _make_task_item("Main"),
        ]
        next_task = _make_task_item("Restart")

        result = scheduler._skip_first_tasks(pending_tasks=pending, next_task=next_task)

        assert result is True
        assert config.task_delay.call_count == 3
        assert "Restart" not in scheduler.skip_first_tasks
        assert "RestartDevice" not in scheduler.skip_first_tasks
        assert "RestartGame" not in scheduler.skip_first_tasks

    def test_skip_stops_at_non_restart_task(self, scheduler):
        """Skips only the leading restart tasks, stops at the first non-restart one."""
        scheduler.skip_first_tasks = {"Restart", "RestartDevice"}
        config = _cache_config(scheduler)

        pending = [
            _make_task_item("Restart"),
            _make_task_item("Main"),  # non-restart stops iteration
            _make_task_item("RestartDevice"),
        ]
        next_task = _make_task_item("Restart")

        result = scheduler._skip_first_tasks(pending_tasks=pending, next_task=next_task)

        assert result is True
        # Only the first task (Restart) should be delayed
        config.task_delay.assert_called_once_with(server_update=True, task="Restart")

    def test_skip_only_on_first_occurrence(self, scheduler):
        """After skipping, skip_first_tasks set no longer contains the skipped names."""
        scheduler.skip_first_tasks = {"Restart"}
        config = _cache_config(scheduler)

        pending = [_make_task_item("Restart"), _make_task_item("Main")]
        next_task = _make_task_item("Restart")

        scheduler._skip_first_tasks(pending_tasks=pending, next_task=next_task)
        assert "Restart" not in scheduler.skip_first_tasks

        # Second call with same tasks: Restart is no longer in skip set
        next_task2 = _make_task_item("Main")
        result2 = scheduler._skip_first_tasks(
            pending_tasks=pending, next_task=next_task2
        )
        assert result2 is False


# ---------------------------------------------------------------------------
# _task_loop
# ---------------------------------------------------------------------------


class TestAlasioSchedulerTaskLoop:
    """Tests for _task_loop()."""

    def test_task_loop_stop_on_scheduler_stopping(self, scheduler):
        """_task_loop raises SchedulerStop if backend signals stop before anything."""
        backend = _patch_backend()
        backend.scheduler_stopping.is_set.return_value = True
        with pytest.raises(SchedulerStop):
            scheduler._task_loop()

    def test_task_loop_stop_on_request_human_takeover(self, scheduler):
        """_task_loop raises SchedulerError when get_next_task raises RequestHumanTakeover."""
        _patch_backend()
        config = _cache_config(scheduler)
        config.get_next_task.side_effect = RequestHumanTakeover("no tasks enabled")

        with pytest.raises(SchedulerError):
            scheduler._task_loop()

    def test_task_loop_skip_first_tasks(self, scheduler):
        """_task_loop returns False when first tasks are skipped."""
        _patch_backend()
        config = _cache_config(scheduler)
        _cache_device(scheduler)

        now = datetime.now(timezone.utc).replace(microsecond=0)
        pending = [_make_task_item("Restart"), _make_task_item("Main")]
        next_task = _make_task_item("Restart", now)
        config.get_next_task.return_value = (pending, [], next_task)

        result = scheduler._task_loop()
        assert result is False
        config.task_delay.assert_called_once_with(server_update=True, task="Restart")

    def test_task_loop_wait_not_reached(self, scheduler):
        """_task_loop returns False when wait doesn't reach the future."""
        _patch_backend()
        config = _cache_config(scheduler)
        _cache_device(scheduler)

        now = datetime.now(timezone.utc).replace(microsecond=0)
        future = now + timedelta(hours=1)  # future is ahead
        pending = [_make_task_item("Main")]
        next_task = _make_task_item("Main", future)
        config.get_next_task.return_value = (pending, [], next_task)

        with mock.patch.object(scheduler, "_wait_future", return_value=False):
            result = scheduler._task_loop()

        assert result is False
        assert config.task == "Main"
        config.init_task.assert_called_once()

    def test_task_loop_run_success(self, scheduler):
        """_task_loop returns True after a successful task run."""
        _patch_backend()
        config = _cache_config(scheduler)
        _cache_device(scheduler)

        now = datetime.now(timezone.utc).replace(microsecond=0)
        pending = [_make_task_item("Main")]
        next_task = _make_task_item("Main", now)
        config.get_next_task.return_value = (pending, [], next_task)

        # Provide a task function
        scheduler.main = mock.MagicMock(return_value=None)

        result = scheduler._task_loop()
        assert result is True
        scheduler.main.assert_called_once()
        assert "Restart" not in scheduler.skip_first_tasks

    def test_task_loop_run_failure_handle_error_true(self, scheduler):
        """_task_loop returns True when task fails but Error.HandleError is True."""
        _patch_backend()
        config = _cache_config(scheduler, Error__HandleError=True)
        _cache_device(scheduler)

        now = datetime.now(timezone.utc).replace(microsecond=0)
        pending = [_make_task_item("Main")]
        next_task = _make_task_item("Main", now)
        config.get_next_task.return_value = (pending, [], next_task)

        def _fail():
            raise GamePageUnknownError("unknown page")

        scheduler.main = _fail

        result = scheduler._task_loop()
        assert result is True

    def test_task_loop_run_failure_handle_error_false(self, scheduler):
        """_task_loop raises SchedulerError when task fails and Error.HandleError is False."""
        _patch_backend()
        config = _cache_config(scheduler, Error__HandleError=False)
        _cache_device(scheduler)

        now = datetime.now(timezone.utc).replace(microsecond=0)
        pending = [_make_task_item("Main")]
        next_task = _make_task_item("Main", now)
        config.get_next_task.return_value = (pending, [], next_task)

        def _fail():
            raise GamePageUnknownError("unknown page")

        scheduler.main = _fail

        with pytest.raises(SchedulerError):
            scheduler._task_loop()

    def test_task_loop_too_many_executions(self, scheduler):
        """_task_loop raises SchedulerError when task hits execution limit."""
        _patch_backend()
        config = _cache_config(scheduler)
        _cache_device(scheduler)

        now = datetime.now(timezone.utc).replace(microsecond=0)
        pending = [_make_task_item("Main")]
        next_task = _make_task_item("Main", now)
        config.get_next_task.return_value = (pending, [], next_task)

        scheduler.main = mock.MagicMock(return_value=None)

        with mock.patch.object(
            TaskRecord,
            "mark_task_result",
            side_effect=TaskTooManyExecutionsError("Main", 4, 3),
        ):
            with pytest.raises(SchedulerError):
                scheduler._task_loop()

    def test_task_loop_too_many_failures(self, scheduler):
        """_task_loop raises SchedulerError when task hits failure limit."""
        _patch_backend()
        config = _cache_config(scheduler)
        _cache_device(scheduler)

        now = datetime.now(timezone.utc).replace(microsecond=0)
        pending = [_make_task_item("Main")]
        next_task = _make_task_item("Main", now)
        config.get_next_task.return_value = (pending, [], next_task)

        scheduler.main = mock.MagicMock(return_value=None)

        with mock.patch.object(
            TaskRecord,
            "mark_task_result",
            side_effect=TaskTooManyFailuresError("Main", 3, 3),
        ):
            with pytest.raises(SchedulerError):
                scheduler._task_loop()


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------


class TestAlasioSchedulerRun:
    """Tests for run()."""

    def test_run_loop_until_scheduler_stop(self, scheduler):
        """run() loops _task_loop until SchedulerStop, then cleans up."""
        _patch_backend()
        _cache_config(scheduler)
        _cache_device(scheduler)

        # Simulate: task loop runs twice then raises SchedulerStop
        calls = mock.MagicMock()
        calls.side_effect = [None, None, SchedulerStop()]

        with mock.patch.object(scheduler, "_task_loop", side_effect=calls):
            with mock.patch.object(scheduler, "_on_task_switch") as mock_switch:
                with mock.patch.object(scheduler, "_on_idle") as mock_idle:
                    scheduler.run()

        # Should call _on_task_switch initially and then once more at the end (both with None)
        assert mock_switch.call_count == 2
        mock_switch.assert_any_call(None)
        mock_idle.assert_called_once()

    def test_run_task_loop_returns_false_then_stop(self, scheduler):
        """run() continues loop even when _task_loop returns False."""
        _patch_backend()
        _cache_config(scheduler)
        _cache_device(scheduler)

        calls = mock.MagicMock()
        calls.side_effect = [False, False, SchedulerStop()]

        with mock.patch.object(scheduler, "_task_loop", side_effect=calls):
            with mock.patch.object(scheduler, "_on_task_switch"):
                with mock.patch.object(scheduler, "_on_idle"):
                    scheduler.run()

    def test_run_calls_on_task_switch_at_start(self, scheduler):
        """run() calls _on_task_switch(None) when starting."""
        _patch_backend()
        _cache_config(scheduler)
        _cache_device(scheduler)

        with mock.patch.object(scheduler, "_task_loop", side_effect=SchedulerStop()):
            with mock.patch.object(scheduler, "_on_task_switch") as mock_switch:
                with mock.patch.object(scheduler, "_on_idle"):
                    scheduler.run()

        # _on_task_switch should be called at least once at start
        mock_switch.assert_any_call(None)

    def test_run_scheduler_error_sends_error_state(self, scheduler):
        """run() sends worker_state 'error' and breaks on SchedulerError."""
        backend = _patch_backend(inited=True)
        _cache_config(scheduler)
        _cache_device(scheduler)

        with mock.patch.object(
            scheduler, "_task_loop", side_effect=SchedulerError("something failed")
        ):
            with mock.patch.object(scheduler, "_on_task_switch"):
                with mock.patch.object(scheduler, "_on_idle"):
                    scheduler.run()

        backend.send_worker_state.assert_any_call("error")
