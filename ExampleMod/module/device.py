from alasio.base.scheduler.task_entry import alasio_task
from alasio.logger import logger


class Device:
    """
    Scheduler tasks that control the device and the game. They are called by
    AlasioScheduler with the task name, see alasio.base.scheduler.scheduler_task

    RestartDevice and RestartGame are scheduled tasks, and they are empty (no time
    setting of their own), an empty task must delay itself at the end, otherwise
    the scheduler runs it again immediately. StopDevice, StopGame and GotoMain are
    called during waiting, they do not delay, because the delayed task would be the
    task the scheduler is waiting for, not the task they belong to.
    """

    def __init__(self, config, device):
        self.config = config
        self.device = device

    @alasio_task('RestartDevice')
    def restart_device(self):
        logger.info('Restart device')
        # an empty task must delay itself, or the scheduler runs it again immediately
        self.config.task_delay(server_update=True)

    @alasio_task('RestartGame')
    def restart_game(self):
        logger.info('Restart game')
        # an empty task must delay itself, same as RestartDevice
        self.config.task_delay(server_update=True)

    @alasio_task('StopDevice')
    def stop_device(self):
        logger.info('Stop device')

    @alasio_task('StopGame')
    def stop_game(self):
        logger.info('Stop game')

    @alasio_task('GotoMain')
    def goto_main(self):
        logger.info('Goto main')
