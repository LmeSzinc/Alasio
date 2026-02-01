from alasio.device.config import DeviceConfig
from alasio.logger import logger


class DeviceBase:
    def __init__(self, config: DeviceConfig):
        logger.hr('Device', level=1)
        self.config = config
