from typing import Any, Optional, Union

import msgspec
from msgspecerror import get_class_annotation

from alasio.base.exception import ScriptError
from alasio.base.timer import T_TIMER_LIMIT, Timer
from alasio.config.base import AlasioConfigBase
from alasio.device.base import DeviceBase
from alasio.device.config import DeviceConfig
from alasio.logger import logger


class LoopState(msgspec.Struct):
    """
    The device state yielded by ModuleBase.loop()

    Attributes:
        image (np.ndarray): Screenshot of the iteration, None if the device has no screenshot
        hierarchy (etree._Element): UI hierarchy of the iteration, None if the device has no hierarchy
        timeout (T_TIMER_LIMIT): Timer of the loop, None if the loop has no timeout
        count (int): Number of iterations that captured the device,
            a screenshot and a hierarchy dump of the same iteration count 1
    """
    # Annotated as Any, importing numpy and lxml at module level would cost every
    # module the whole image stack, see DeviceBase
    image: Any = None
    hierarchy: Any = None
    timeout: Optional[T_TIMER_LIMIT] = None
    count: int = 0


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

    def loop(self, *, skip_first=True, timeout: Optional[T_TIMER_LIMIT] = None,
             screenshot=True, hierarchy=False):
        """
        A syntactic sugar to start a state loop

        Args:
            skip_first (bool): Usually to be True to reuse the screenshot and hierarchy
                of the device, False to capture them before the first iteration
            timeout: Seconds of timeout, or (second, count), or a Timer object
            screenshot (bool): True to take a screenshot on each iteration
            hierarchy (bool): True to dump the UI hierarchy on each iteration

        Yields:
            LoopState: The device state of the iteration, `count` is the number
                of iterations this loop captured the device for so far

        Examples:
            # state machine that handle clicking until destination
            for state in self.loop():
                if self.appear(...):
                    break
                if self.appear_then_click(...):
                    continue

        Examples:
            # state machine with timeout
            for state in self.loop(timeout=2):
                if self.appear(...):
                    logger.info('Wait success')
                    break
            else:
                logger.warning('Wait timeout')

        Examples:
            # state machine that reads the UI hierarchy, the screenshot is not needed
            for state in self.loop(screenshot=False, hierarchy=True, skip_first=False):
                if state.hierarchy is not None:
                    break
        """
        if timeout is not None:
            timeout = Timer.from_seconds(timeout).reset()

        count = 0
        while 1:
            if timeout is not None:
                if timeout.reached():
                    return

            if skip_first:
                skip_first = False
            else:
                if screenshot:
                    self.device.screenshot()
                if hierarchy:
                    self.device.dump_hierarchy()
                # the screenshot and the hierarchy dump of an iteration are one
                # capture of the device state
                if screenshot or hierarchy:
                    count += 1

            yield LoopState(
                image=self.device.image,
                hierarchy=self.device.hierarchy,
                timeout=timeout,
                count=count,
            )

    def loop_screenshot(self, *, skip_first=True, timeout: Optional[T_TIMER_LIMIT] = None):
        """
        Shortcut of `loop()` taking screenshots only, see `loop()`

        Args:
            skip_first (bool): Usually to be True to reuse the screenshot of the device
            timeout: Seconds of timeout, or (second, count), or a Timer object

        Yields:
            LoopState:
        """
        return self.loop(
            skip_first=skip_first, timeout=timeout,
            screenshot=True, hierarchy=False,
        )

    def loop_hierarchy(self, *, skip_first=True, timeout: Optional[T_TIMER_LIMIT] = None):
        """
        Shortcut of `loop()` dumping the UI hierarchy only, the screenshot is not taken, see `loop()`

        Args:
            skip_first (bool): Usually to be True to reuse the hierarchy of the device
            timeout: Seconds of timeout, or (second, count), or a Timer object

        Yields:
            LoopState:
        """
        return self.loop(
            skip_first=skip_first, timeout=timeout,
            screenshot=False, hierarchy=True,
        )

    def loop_screenshot_hierarchy(self, *, skip_first=True, timeout: Optional[T_TIMER_LIMIT] = None):
        """
        Shortcut of `loop()` taking a screenshot and dumping the UI hierarchy, see `loop()`

        Args:
            skip_first (bool): Usually to be True to reuse the screenshot and hierarchy
                of the device
            timeout: Seconds of timeout, or (second, count), or a Timer object

        Yields:
            LoopState:
        """
        return self.loop(
            skip_first=skip_first, timeout=timeout,
            screenshot=True, hierarchy=True,
        )
