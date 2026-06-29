from typing import Optional, Union

from msgspecerror import get_class_annotation

from alasio.base.exception import ScriptError
from alasio.base.timer import T_TIMER_LIMIT, Timer
from alasio.config.base import AlasioConfigBase
from alasio.device.base import DeviceBase
from alasio.device.config import DeviceConfig
from alasio.logger import logger


class ModuleBase:
    # subclasses may override these
    config: AlasioConfigBase
    device: DeviceBase

    def __init__(
            self,
            config: Union[AlasioConfigBase, str],
            device: Union[DeviceBase, str, None] = None,
            task: str = '',
    ):
        """
        Args:
            config (AzurLaneConfig, str):
                Name of the user config under ./config
            device (Device, str):
                To reuse a device.
                If None, create a new Device object.
                If str, create a new Device object and use the given device as serial.
            task (str):
                Bind a task only for dev purpose. Usually to be "" for auto task scheduling.
        """
        # build config
        if isinstance(config, AlasioConfigBase):
            self.config = config
            if task:
                self.config.task = task
        elif isinstance(config, str):
            try:
                cls = get_class_annotation(self.__class__, 'config')
            except AttributeError:
                raise ScriptError(f'Missing "config" annotation in module') from None
            self.config = cls(config, task=task)
        else:
            logger.warning('ModuleBase received an unknown config, using it anyway')
            self.config = config
            if task:
                self.config.task = task

        # build device
        if isinstance(device, DeviceBase):
            self.device = device
        elif device is None:
            try:
                cls = get_class_annotation(self.__class__, 'device')
            except AttributeError:
                raise ScriptError(f'Missing "config" annotation in module') from None
            device_config = DeviceConfig.from_config(self.config)
            self.device = cls(device_config)
        elif isinstance(device, str):
            try:
                cls = get_class_annotation(self.__class__, 'device')
            except AttributeError:
                raise ScriptError(f'Missing "config" annotation in module') from None
            self.config.override(Emulator_Serial=device)
            device_config = DeviceConfig.from_config(self.config)
            self.device = cls(device_config)
        else:
            logger.warning('ModuleBase received an unknown device, using it anyway')
            self.device = device

    def loop(self, *, skip_first=True, timeout: Optional[T_TIMER_LIMIT] = None):
        """
        A syntactic sugar to start a state loop

        Args:
            skip_first (bool): Usually to be True to reuse the previous screenshot
            timeout: Seconds of timeout, or (second, count), or a Timer object

        Yields:
            np.ndarray: screenshot

        Examples:
            # state machine that handle clicking until destination
            for _ in self.loop():
                if self.appear(...):
                    break
                if self.appear_then_click(...):
                    continue

        Examples:
            # state machine with timeout
            for _ in self.loop(timeout=2):
                if self.appear(...):
                    logger.info('Wait success')
                    break
            else:
                logger.warning('Wait timeout')
        """
        if timeout is not None:
            timeout = Timer.from_seconds(timeout).reset()

        while 1:
            if timeout is not None:
                if timeout.reached():
                    return

            if skip_first:
                skip_first = False
            else:
                self.device.screenshot()

            try:
                yield self.device.image
                continue
            except AttributeError:
                pass

            # no cached screenshot, take new screenshot anyway
            self.device.screenshot()
            yield self.device.image
