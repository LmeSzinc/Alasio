import time
from threading import Lock
from typing import Optional, TYPE_CHECKING

from alasio.backend.worker.bridge import BackendBridge
from alasio.backend.worker.event import ConfigEvent
from alasio.device.config import DeviceConfig
from alasio.logger import logger

if TYPE_CHECKING:
    import numpy as np


class DeviceBase:
    def __init__(self, config: DeviceConfig):
        logger.hr('Device', level=1)
        self.config = config
        self.image: "np.ndarray" = None
        self._image_time = 0.
        self._last_preview_time = 0.

    def on_idle(self):
        """
        Release connections and cache during scheduler wait
        Subclasses needs to implement this
        """
        self.image = None
        self._image_time = 0.

    def on_task_switch(self):
        """
        Clear stuck record and click record when task switched
        Subclasses needs to implement this
        """
        pass

    def screenshot(self):
        """
        Method that take screenshot, set `self.image` and `self._image_time`
        Subclasses needs to implement this

        Returns:
            np.ndarray:
        """
        raise NotImplementedError

    def has_cached_screenshot(self):
        """
        Returns:
            bool:
        """
        try:
            image = self.image
        except AttributeError:
            return False
        return image is not None

    def ensure_first_screenshot(self):
        """
        Returns:
            np.ndarray:
        """
        try:
            image = self.image
            if image is not None:
                return image
        except AttributeError:
            pass
        return self.screenshot()

    def backend_send_preview(self, flag=None) -> "Optional[Lock]":
        """
        Send image preview to backend if preview requested and same config
        """
        if not self.config.config_name:
            return
        backend = BackendBridge()
        if not backend.inited or not backend.config_name:
            return

        if flag is None:
            flag = backend.preview_requested.get_and_clear()
            if not flag:
                return

        try:
            image = self.image
        except AttributeError:
            # logger.warning(f'Failed to send preview to backend, {e}')
            return
        if image is None:
            # logger.warning('Failed to send preview to backend, image is None')
            return
        if self._last_preview_time > 0 and self._last_preview_time >= self._image_time:
            # image already send
            return

        # local import to avoid importing opencv globally
        from alasio.base.image.imfile import image_preview
        now = self._image_time
        if now <= 0:
            # this shouldn't happen
            now = time.time()
        data = image_preview(image, now=now)
        self._last_preview_time = now
        return backend.send(ConfigEvent(t='Preview', v=data))
