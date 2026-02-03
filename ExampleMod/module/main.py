import time

from alasio.base.pretty import pretty_time
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
        logger.info('Text with\nnew line')
        logger.info('Super long text. ' * 30)
        logger.debug('DEBUG')
        logger.info('INFO')
        logger.warning('WARNING')
        logger.error('ERROR')
        logger.critical('CRITICAL')
        try:
            raise_example_error()
        except ExampleError as e:
            logger.exception(e)

        time.sleep(2)
        start = time.perf_counter()
        count = 1200
        for n in range(count):
            logger.info(f'[{n}] key = self.dict_config_to_topic.get((event.task, event.group, event.arg))')
        cost = time.perf_counter() - start
        # Print 1200 logs in 75.579ms, 62.983us each, 15877 log/s
        logger.info(f'Print {count} logs in {pretty_time(cost)}, '
                    f'{pretty_time(cost / count)} each, '
                    f'{int(count / cost)} log/s')
        logger.info('end')
