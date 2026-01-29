import msgspec

from alasio.config.base import BatchSetContext, TemporaryContext
from alasio.device.config import DeviceConfig


class EmulatorGroup(msgspec.Struct):
    Serial: str = 'auto'
    PackageName: str = 'auto'
    ScreenshotMethod: str = 'auto'
    ControlMethod: str = 'auto'
    ScreenshotDedithering: bool = False
    AdbRestart: bool = False


class EmulatorInfoGroup(msgspec.Struct):
    Emulator: str = 'auto'
    name: str = ''
    path: str = ''


class ErrorGroup(msgspec.Struct):
    HandleError: bool = True
    SaveError: bool = True
    OnePushConfig: str = 'provider: null'
    ScreenshotLength: int = 1


class OptimizationGroup(msgspec.Struct):
    ScreenshotInterval: float = 0.3
    CombatScreenshotInterval: float = 1.0
    TaskHoardingDuration: int = 0
    WhenTaskQueueEmpty: str = 'goto_main'


class MockConfig:
    def __init__(self):
        self.Emulator = EmulatorGroup(
            Serial='127.0.0.1:5555',
            PackageName='com.example.game',
            ScreenshotMethod='ADB',
            ControlMethod='ADB',
            ScreenshotDedithering=True,
            AdbRestart=True
        )
        self.EmulatorInfo = EmulatorInfoGroup(
            Emulator='NoxPlayer',
            name='Nox',
            path='C:/Nox'
        )
        self.Error = ErrorGroup(
            HandleError=False,
            SaveError=False,
            OnePushConfig='provider: telegram',
            ScreenshotLength=5
        )
        self.Optimization = OptimizationGroup(
            ScreenshotInterval=0.5,
            CombatScreenshotInterval=2.0,
            TaskHoardingDuration=10,
            WhenTaskQueueEmpty='stay_there'
        )
        self.save_count = 0
        self.auto_save = True
        self._batch_depth = 0

    def save(self):
        self.save_count += 1

    def batch_set(self):
        return BatchSetContext(self)

    def override(self, **kwargs):
        prev_config = {}
        for key, value in kwargs.items():
            if '_' in key:
                group, arg = key.split('_', 1)
                group_obj = getattr(self, group)
                prev_config[key] = getattr(group_obj, arg)
                setattr(group_obj, arg, value)
        return prev_config, {}

    def temporary(self, **kwargs):
        return TemporaryContext(self, **kwargs)


class TestDeviceConfig:
    def test_bare_construct(self):
        """
        Test that DeviceConfig can be bare constructed
        """
        _ = DeviceConfig()

    def test_from_config_loading(self):
        """
        Test that DeviceConfig.from_config correctly loads values from AlasioConfigBase
        """
        mock_config = MockConfig()
        device_config = DeviceConfig.from_config(mock_config)
        print(mock_config.Emulator.Serial)

        # Check some loaded values
        assert device_config.Emulator_Serial == '127.0.0.1:5555'
        assert device_config.Emulator_PackageName == 'com.example.game'
        assert device_config.Error_HandleError is False
        assert device_config.Optimization_ScreenshotInterval == 0.5
        assert device_config.EmulatorInfo_Emulator == 'NoxPlayer'

    def test_broadcast_to_config(self):
        """
        Test that setting attributes on DeviceConfig broadcasts changes to AlasioConfigBase
        """
        mock_config = MockConfig()
        device_config = DeviceConfig.from_config(mock_config)

        # Change value on device_config
        device_config.Emulator_Serial = '127.0.0.1:62001'
        # Verify it's broadcasted to mock_config
        assert mock_config.Emulator.Serial == '127.0.0.1:62001'

        # Change another value
        device_config.Optimization_ScreenshotInterval = 0.1
        assert mock_config.Optimization.ScreenshotInterval == 0.1

        # Change a boolean value
        device_config.Error_HandleError = True
        assert mock_config.Error.HandleError is True

    def test_no_broadcast_without_config(self, capsys):
        """
        Test that DeviceConfig does not broadcast if self.config is None
        """
        mock_config = MockConfig()
        device_config = DeviceConfig()

        # device_config.config is None by default (based on current implementation)
        device_config.Emulator_Serial = '127.0.0.1:62001'

        # Value in mock_config should remain unchanged
        assert mock_config.Emulator.Serial == '127.0.0.1:5555'
        # Should log a warning
        captured = capsys.readouterr()
        assert "DeviceConfig: Failed to proxy setattr, config is None" in captured.out

    def test_internal_attribute_no_broadcast(self):
        """
        Test that internal attributes or non-bind keys are not broadcasted
        """
        mock_config = MockConfig()
        device_config = DeviceConfig.from_config(mock_config)

        # DEVICE_OVER_HTTP is not in _device_bind_keys
        device_config.DEVICE_OVER_HTTP = True
        assert device_config.DEVICE_OVER_HTTP is True
        # No group for this, so no broadcast (and no crash)

        # _config is internal
        device_config._some_internal = 123
        assert device_config._some_internal == 123

    def test_missing_group_in_config(self, capsys):
        """
        Test handling of missing groups in the provided config
        """

        class IncompleteConfig:
            def __init__(self):
                # Missing Emulator group
                self.Error = ErrorGroup(HandleError=True)
                self.Optimization = OptimizationGroup(ScreenshotInterval=0.3)
                self.EmulatorInfo = EmulatorInfoGroup(Emulator='auto')

        incomplete_config = IncompleteConfig()
        device_config = DeviceConfig.from_config(incomplete_config)

        # Should log warnings during from_config
        captured = capsys.readouterr()
        assert 'DeviceConfig.from_config: Missing key in config "Emulator.Serial"' in captured.out

        # Now test broadcast to missing group
        device_config.Emulator_Serial = '127.0.0.1:5555'

        captured = capsys.readouterr()
        assert 'DeviceConfig: Failed to proxy setattr, missing key in config "Emulator.Serial"' in captured.out

    def test_batch_set(self):
        """
        Test that DeviceConfig.batch_set correctly proxies to AlasioConfigBase.batch_set
        """
        mock_config = MockConfig()
        device_config = DeviceConfig.from_config(mock_config)

        # MockConfig.save is not called by DeviceConfig directly, 
        # but AlasioConfigBase.register_modify usually triggers save().
        # In our MockConfig, we don't have register_modify, 
        # but DeviceConfig calls setattr on mock_config.Emulator which is a msgspec.Struct.
        # msgspec.Struct doesn't trigger anything on setattr by default.

        # However, DeviceConfig.batch_set calls mock_config.batch_set()
        with device_config.batch_set():
            device_config.Emulator_Serial = '127.0.0.1:62001'
            device_config.Optimization_ScreenshotInterval = 0.1
            # save_count should still be 0 because we are in batch_set
            assert mock_config.save_count == 0

        # After exiting batch_set, save_count should be 1
        assert mock_config.save_count == 1
        assert mock_config.Emulator.Serial == '127.0.0.1:62001'
        assert mock_config.Optimization.ScreenshotInterval == 0.1

    def test_batch_set_no_config(self, capsys):
        """
        Test that DeviceConfig.batch_set handles missing config gracefully
        """
        device_config = DeviceConfig()
        with device_config.batch_set():
            device_config.Emulator_Serial = '127.0.0.1:62001'

        captured = capsys.readouterr()
        assert 'DeviceConfig: Failed to proxy' in captured.out

    def test_override(self):
        """
        Test that DeviceConfig.override correctly proxies to AlasioConfigBase.override
        """
        mock_config = MockConfig()
        device_config = DeviceConfig.from_config(mock_config)

        # Initial value
        assert device_config.Emulator_Serial == '127.0.0.1:5555'

        # Override
        device_config.override(Emulator_Serial='127.0.0.1:62001')

        # Value should be changed in both
        assert mock_config.Emulator.Serial == '127.0.0.1:62001'
        # Note: device_config attributes are not automatically updated unless from_config is called again
        # or if we implement a way to sync them. 
        # But wait, DeviceConfig attributes are just values copied in from_config.
        # If we change mock_config, device_config won't know unless it's a proxy.
        # In current implementation, DeviceConfig attributes are NOT proxies to AlasioConfigBase.
        # They are just values. BroadCast only goes from DeviceConfig -> AlasioConfigBase.

        # However, if we call override on device_config, it proxies to mock_config.
        # If we want device_config to reflect the change, we might need to update it.
        # But the request is just to "add proxy for override and temporary".

        assert mock_config.Emulator.Serial == '127.0.0.1:62001'
