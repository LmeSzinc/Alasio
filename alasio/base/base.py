from typing import Union

from alasio.base.exception import ScriptError
from alasio.config.config_generated import AlasioConfigGenerated
from alasio.device.base import DeviceBase
from alasio.device.config import DeviceConfig
from alasio.ext.cache import cached_property
from alasio.ext.msgspec_error.parse_anno import get_annotations
from alasio.logger import logger


class ModuleBase:
    # subclasses may override these
    config: AlasioConfigGenerated
    device: DeviceBase

    @cached_property
    def _annotations(self):
        return get_annotations(self.__class__)

    def __init__(
            self,
            config: Union[AlasioConfigGenerated, str],
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
        if isinstance(config, AlasioConfigGenerated):
            self.config = config
            if task:
                self.config.task = task
        elif isinstance(config, str):
            try:
                cls = self._annotations['config']
            except KeyError:
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
                cls = self._annotations['device']
            except KeyError:
                raise ScriptError(f'Missing "config" annotation in module') from None
            device_config = DeviceConfig.from_config(self.config)
            self.device = cls(device_config)
        elif isinstance(device, str):
            try:
                cls = self._annotations['device']
            except KeyError:
                raise ScriptError(f'Missing "config" annotation in module') from None
            self.config.override(Emulator_Serial=device)
            device_config = DeviceConfig.from_config(self.config)
            self.device = cls(device_config)
        else:
            logger.warning('ModuleBase received an unknown device, using it anywa')
            self.device = device
