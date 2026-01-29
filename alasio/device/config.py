from typing import Optional, TYPE_CHECKING

from typing_extensions import Self

from alasio.ext.cache import cached_property
from alasio.logger import logger

if TYPE_CHECKING:
    # avoid circular import
    from alasio.config.base import AlasioConfigBase, BatchSetContext, TemporaryContext


class EmptyContext:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class DeviceConfig:
    def __init__(self):
        self._inited = False
        self.config: "Optional[AlasioConfigBase]" = None

        # Group `Emulator`
        self.Emulator_Serial = 'auto'
        self.Emulator_PackageName = 'auto'
        self.Emulator_ScreenshotMethod = 'auto'  # auto, ADB, ADB_nc, uiautomator2, aScreenCap, aScreenCap_nc,
        # DroidCast, DroidCast_raw, nemu_ipc, ldopengl
        self.Emulator_ControlMethod = 'MaaTouch'  # ADB, uiautomator2, minitouch, Hermit, MaaTouch
        self.Emulator_ScreenshotDedithering = False
        self.Emulator_AdbRestart = False

        # Group `EmulatorInfo`
        self.EmulatorInfo_Emulator = 'auto'  # auto, NoxPlayer, NoxPlayer64, BlueStacks4, BlueStacks5,
        # BlueStacks4HyperV, BlueStacks5HyperV, LDPlayer3, LDPlayer4, LDPlayer9, MuMuPlayer, MuMuPlayerX,
        # MuMuPlayer12, MEmuPlayer
        self.EmulatorInfo_name = None
        self.EmulatorInfo_path = None

        # Group `Error`
        self.Error_HandleError = True
        self.Error_SaveError = True
        self.Error_OnePushConfig = 'provider: null'
        self.Error_ScreenshotLength = 1

        # Group `Optimization`
        self.Optimization_ScreenshotInterval = 0.3
        self.Optimization_CombatScreenshotInterval = 1.0
        self.Optimization_TaskHoardingDuration = 0
        self.Optimization_WhenTaskQueueEmpty = 'goto_main'  # stay_there, goto_main, close_game

        """
        module.device
        """
        self.DEVICE_OVER_HTTP = False
        self.FORWARD_PORT_RANGE = (20000, 21000)
        self.REVERSE_SERVER_PORT = 7903

        self.ASCREENCAP_FILEPATH_LOCAL = './bin/ascreencap'
        self.ASCREENCAP_FILEPATH_REMOTE = '/data/local/tmp/ascreencap'

        # 'DroidCast', 'DroidCast_raw'
        self.DROIDCAST_VERSION = 'DroidCast'
        self.DROIDCAST_FILEPATH_LOCAL = './bin/DroidCast/DroidCast_raw-release-1.0.apk'
        self.DROIDCAST_FILEPATH_REMOTE = '/data/local/tmp/DroidCast_raw.apk'

        self.MINITOUCH_FILEPATH_REMOTE = '/data/local/tmp/minitouch'

        self.HERMIT_FILEPATH_LOCAL = './bin/hermit/hermit.apk'

        self.SCRCPY_FILEPATH_LOCAL = './bin/scrcpy/scrcpy-server-v1.20.jar'
        self.SCRCPY_FILEPATH_REMOTE = '/data/local/tmp/scrcpy-server-v1.20.jar'

        self.MAATOUCH_FILEPATH_LOCAL = './bin/MaaTouch/maatouchsync'
        self.MAATOUCH_FILEPATH_REMOTE = '/data/local/tmp/maatouchsync'

        # end
        self._inited = True

    @cached_property
    def _device_bind_keys(self):
        """
        Returns:
            dict[str, tuple[str, str]]:
        """
        keys = {}
        for key in self.__dict__:
            if key.startswith('_'):
                continue
            # must be `Group_Arg`
            group, sep, arg = key.partition('_')
            if not group or not sep or not arg or '_' in arg:
                continue
            # not const name in upper and not function name in lower
            if group.isupper() or group.islower() or arg.isupper() or arg.islower():
                continue
            keys[key] = (group, arg)
        return keys

    @classmethod
    def from_config(cls, config: "AlasioConfigBase") -> Self:
        obj = cls()
        obj.config = config
        for name, key in obj._device_bind_keys.items():
            group, arg = key
            try:
                group_data = getattr(config, group)
                value = getattr(group_data, arg)
            except AttributeError:
                logger.warning(f'DeviceConfig.from_config: Missing key in config "{group}.{arg}"')
                continue
            # set property but avoid triggering broadcast
            object.__setattr__(obj, name, value)

        return obj

    def __setattr__(self, key, value):
        super().__setattr__(key, value)
        # ignore property set in __init__
        if not self._inited:
            return

        # broadcast changes to config
        if key in self._device_bind_keys:
            try:
                group, arg = self._device_bind_keys[key]
            except (KeyError, TypeError):
                logger.warning(f'DeviceConfig: Failed to proxy setattr, invalid key "{key}"')
                return
            if self.config is None:
                logger.warning('DeviceConfig: Failed to proxy setattr, config is None')
                return
            try:
                group_data = getattr(self.config, group)
                setattr(group_data, arg, value)
            except AttributeError:
                logger.warning(f'DeviceConfig: Failed to proxy setattr, missing key in config "{group}.{arg}"')
                return

    def batch_set(self) -> "BatchSetContext":
        """
        Context manager to suppress auto-save for batch modifications.
        Saves are triggered only when the outermost context exits.
        """
        if self.config is None:
            logger.warning('DeviceConfig: Failed to proxy batch_set(), config is None')
            return EmptyContext()
        return self.config.batch_set()

    def override(self, **kwargs):
        """
        Permanently override config values in memory.
        These overrides persist across init_task() calls but are not saved to file.

        Returns:
            tuple[dict[str, dict[str, Any]], dict[str, Any]]: prev_config, prev_const
        """
        if self.config is None:
            logger.warning('DeviceConfig: Failed to proxy override(), config is None')
            return {}, {}
        return self.config.override(**kwargs)

    def temporary(self, **kwargs) -> "TemporaryContext":
        """
        Temporarily override config values in memory within a context.
        Restores previous values (from DB or previous overrides) upon exit.
        """
        if self.config is None:
            logger.warning('DeviceConfig: Failed to proxy temporary(), config is None')
            return EmptyContext(**kwargs)
        return self.config.temporary(**kwargs)

    @property
    def is_template_config(self):
        if self.config is None:
            return True
        return self.config.is_template_config

    @property
    def task(self):
        if self.config is None:
            return ''
        return self.config.task
