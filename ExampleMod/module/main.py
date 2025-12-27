import time

from alasio.logger import logger


class Scheduler:
    def __init__(self, config_name):
        self.config_name = config_name

    def run(self):
        logger.info('OMG it just run')
        time.sleep(3)
        logger.info('end')
