import time
from collections import deque
from datetime import datetime
from typing import TYPE_CHECKING

from alasio.backend.worker.bridge import BackendBridge
from alasio.backend.worker.event import ConfigEvent
from alasio.base.image.imfile import image_encode
from alasio.device.config import DeviceConfig
from alasio.ext.cache import cached_property_threadsafe
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

    @cached_property_threadsafe
    def screenshot_deque(self):
        """
        A screenshot cache save last screenshot on error

        Returns:
            deque[dict]: a deque of {"time": datetime, "image": np.ndarray}
                datetime is timezone native
        """
        try:
            length = int(self.config.Error_ScreenshotLength)
        except ValueError:
            logger.error(f'Error.ScreenshotLength={self.config.Error_ScreenshotLength} is not an integer')
            length = 1
        # Limit in 1~300
        length = max(1, min(length, 300))
        return deque(maxlen=length)

    def screenshot_deque_append(self, image):
        """
        Add image to screenshot cache

        Args:
            image (np.ndarray):
        """
        data = {'time': datetime.now(), 'image': image}
        self.screenshot_deque.append(data)

    def screenshot_deque_iter(self):
        """
        Iter screenshot cache to write

        Yields:
            tuple[str, bytes]: (filename, image_bytes)
        """
        for data in self.screenshot_deque:
            imtime = datetime.strftime(data['time'], '%Y-%m-%d_%H-%M-%S-%f')
            image = image_encode(data['image'], ext='webp')
            file = f'{imtime}.webp'
            yield file, image

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

    def backend_send_preview(self, force=None):
        """
        Send image preview to backend if preview requested and same config

        Args:
            force (bool): True to force update preview even if backend did not request an update
        """
        if not self.config.config_name:
            return
        backend = BackendBridge()
        if not backend.inited or not backend.config_name:
            return

        if force is None:
            force = backend.preview_requested.get_and_clear()
            if not force:
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
        from alasio.base.image.impreview import image_preview
        now = self._image_time
        if now <= 0:
            # this shouldn't happen
            now = time.time()
        data = image_preview(image, now=now)
        self._last_preview_time = now
        return backend.send(ConfigEvent(t='Preview', v=data)).acquire()

    def backend_send_preview_stop(self):
        """
        Send preview stopped signal to backend
        """
        if not self.config.config_name:
            return
        backend = BackendBridge()
        if not backend.inited or not backend.config_name:
            return

        # local import to avoid importing opencv globally
        from alasio.base.image.impreview import image_preview_stop
        data = image_preview_stop()
        backend.send(ConfigEvent(t='Preview', v=data)).acquire()
        # clear image cache, so it never sends again until next screenshot call
        self.on_idle()
