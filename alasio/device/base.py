from threading import Lock
from typing import Optional

from alasio.backend.worker.bridge import BackendBridge
from alasio.backend.worker.event import ConfigEvent
from alasio.device.config import DeviceConfig
from alasio.logger import logger


class DeviceBase:
    def __init__(self, config: DeviceConfig):
        logger.hr('Device', level=1)
        self.config = config

    def on_idle(self):
        """
        Release connections and cache during scheduler wait
        Subclasses needs to implement this
        """
        pass

    def on_task_switch(self):
        """
        Clear stuck record and click record when task switched
        Subclasses needs to implement this
        """
        pass

    def backend_send_preview(self, image) -> "Optional[Lock]":
        """
        Send image preview to backend if preview requested and same config
        """
        if not self.config.config_name:
            return
        backend = BackendBridge()
        if not backend.inited or not backend.config_name:
            return

        flag = backend.preview_requested.get_and_clear()
        if flag:
            # local import to avoid importing opencv globally
            from alasio.base.image.imfile import image_preview
            data = image_preview(image)
            return backend.send(ConfigEvent(t='Preview', v=data))
