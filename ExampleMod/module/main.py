import time

from alasio.logger import logger


class ExampleError(Exception):
    pass


def raise_example_error():
    # show some local value in traceback
    local_value = 1
    raise ExampleError


class Scheduler:
    def __init__(self, config_name):
        self.config_name = config_name

    def run(self):
        logger.info('OMG it just run')
        logger.hr0('hr0')
        logger.hr1('hr1')
        logger.hr2('hr2')
        logger.hr3('hr3')
        logger.debug('DEBUG')
        logger.info('INFO')
        logger.warning('WARNING')
        logger.error('ERROR')
        logger.critical('CRITICAL')
        try:
            raise_example_error()
        except ExampleError as e:
            logger.exception(e)

        time.sleep(3)
        logger.info('end')
