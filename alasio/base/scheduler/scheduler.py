import threading
import time
from datetime import datetime
from typing import Dict

from alasio.backend.worker.bridge import BackendBridge
from alasio.base.exception import *
from alasio.base.scheduler.configwatcher import ConfigWatcher
from alasio.base.scheduler.inflect import Inflection
from alasio.base.timer import now
from alasio.config.config_generated import AlasioConfigGenerated
from alasio.device.base import DeviceBase
from alasio.device.config import DeviceConfig
from alasio.ext.cache import cached_property
from alasio.ext.singleton import Singleton
from alasio.logger import logger


class FailureRecord(metaclass=Singleton):
    def __init__(self):
        self._lock = threading.Lock()
        # Failure count of tasks
        # Key: str, task name, value: int, failure count
        self.record: Dict[str, int] = {}

    def mark_task_result(self, task, success):
        """
        Args:
            task (str):
            success (bool):

        Returns:
            int: failure count
        """
        with self._lock:
            if success:
                self.record.pop(task, None)
                return 0
            else:
                count = self.record.get(task, 0)
                count += 1
                self.record[task] = count
                return count


def interruptable_sleep(second):
    """
    Args:
        second (int | float):

    Returns:
        bool: True if waited, False if early stopped
    """
    end = time.perf_counter() + second
    backend = BackendBridge()
    while 1:
        if backend.scheduler_stopping.is_set():
            raise SchedulerStop()
        time.sleep(1)
        if time.perf_counter() >= end:
            return True


class AlasioScheduler:
    def __init__(self, config_name):
        self.config_name = config_name
        # Skip first restart
        self.is_first_task = True

    def create_config(self):
        return AlasioConfigGenerated(self.config_name)

    @cached_property
    def config(self) -> AlasioConfigGenerated:
        try:
            return self.create_config()
        except RequestHumanTakeover as e:
            logger.critical(e)
            raise SchedulerStop
        except Exception as e:
            logger.exception(e)
            raise SchedulerStop

    def create_device(self):
        device_config = DeviceConfig.from_config(self.config)
        return DeviceBase(device_config)

    @cached_property
    def device(self) -> DeviceBase:
        try:
            return self.create_device()
        except RequestHumanTakeover as e:
            logger.critical(e)
            raise SchedulerStop
        except Exception as e:
            logger.exception(e)
            raise SchedulerStop

    def restart_device(self):
        raise NotImplemented

    def restart_game(self):
        raise NotImplemented

    def stop_game(self):
        raise NotImplemented

    def stop_device(self):
        raise NotImplemented

    def goto_main(self):
        raise NotImplemented

    def _run_task(self, task):
        """
        Args:
            task (str):

        Returns:
            bool: If run success
        """
        name = Inflection.from_string(task).to_snake_case()
        try:
            func = self.__getattribute__(name)
        except AttributeError:
            logger.critical(f'Task function not defined: "{name}"')
            raise SchedulerStop
        try:
            func()
        except TaskStop:
            return True
        except GameNotRunningError as e:
            logger.warning(e)
            self.config.task_call('RestartGame')
            return False
        except EmulatorNotRunningError as e:
            logger.warning(e)
            self.config.task_call('RestartDevice')
            return False
        except (GameWaitTooLongError, GameTooManyClickError) as e:
            logger.error(e)
            self._save_error_log()
            logger.warning(f'Game stuck, game will be restarted in 10 seconds')
            self.config.task_call('RestartGame')
            interruptable_sleep(10)
            return False
        except GameBugError as e:
            logger.error(e)
            self._save_error_log()
            logger.warning('An error has occurred in game client, game will be restarted in 10 seconds')
            self.config.task_call('RestartGame')
            interruptable_sleep(10)
            return False
        except GamePageUnknownError as e:
            logger.error(e)
            return False
        except RequestHumanTakeover as e:
            logger.critical(e)
            raise SchedulerStop
        except ScriptError as e:
            logger.exception(e)
            logger.critical('This is likely to be a mistake of developers, but sometimes just random issues')
            raise SchedulerStop
        except SchedulerStop:
            raise
        except Exception as e:
            logger.exception(e)
            self._save_error_log()
            raise SchedulerStop

    def _save_error_log(self):
        pass

    def _wait_future(self, task: str, future: datetime):
        """
        Args:
            task:
            future: datetime with tzinfo

        Returns:
            bool: True if waited to future, False if early stopped
        """
        future = future.astimezone()
        if future <= now():
            return True
        logger.info(f'Wait until {future} for task `{task}`')

        # release
        method = self.config.Optimization.WhenTaskQueueEmpty
        if method == 'stop_game':
            logger.info('Stop game during wait')
            self._run_task('stop_game')
        elif method == 'stop_device':
            logger.info('Stop device during wait')
            self._run_task('stop_device')
        elif method == 'goto_main':
            logger.info('Goto main page during wait')
            self._run_task('goto_main')
        elif method == 'stay_there':
            logger.info('Stay there during wait')
        else:
            logger.warning(f'Unknown Optimization.WhenTaskQueueEmpty={method}, treat as stay_there')
        self.device.on_idle()

        # wait
        watcher = ConfigWatcher(self.config_name).init()
        backend = BackendBridge()
        count = 0
        while 1:
            time.sleep(1)
            count += 1
            # check scheduler_stopping every 1s
            if backend.scheduler_stopping.is_set():
                raise SchedulerStop
            # check if reached future
            if future > now():
                return True
            # check if config modified every 5s
            if count % 5 == 0:
                if watcher.is_modified():
                    return False

    def _task_loop(self):
        backend = BackendBridge()
        if backend.scheduler_stopping.is_set():
            raise SchedulerStop
        # get next task
        try:
            task = self.config.get_next_task()
        except RequestHumanTakeover:
            raise SchedulerStop
        # init task
        self.config.task = task.TaskName
        self.config.init_task()
        # wait task
        waited = self._wait_future(task=task.TaskName, future=task.NextRun)
        if not waited:
            return False
        # skip restart
        if self.is_first_task:
            if task.TaskName in ['Restart', 'RestartDevice', 'RestartGame']:
                self.config.task_delay(server_update=True)
                return True

        # Run
        logger.info(f'Scheduler: Start task `{task}`')
        self.device.on_task_switch()
        logger.hr0(task.TaskName)
        success = self._run_task(task.TaskName)
        logger.info(f'Scheduler: End task `{task}`')
        self.is_first_task = False

        # check failure
        failure = FailureRecord().mark_task_result(task=task.TaskName, success=success)
        if failure >= 3:
            logger.critical(f"Task `{task}` failed 3 or more times.")
            logger.critical("Possible reason #1: You haven't used it correctly. "
                            "Please read the help text of the options.")
            logger.critical("Possible reason #2: There is a problem with this task. "
                            "Please contact developers or try to fix it yourself.")
            logger.critical('Request human takeover')
            raise SchedulerStop
        if success:
            return True
        elif self.config.Error.HandleError:
            return True
        else:
            raise SchedulerStop

    def run(self):
        logger.info(f'Start scheduler loop: {self.config_name}')
        while 1:
            try:
                self._task_loop()
            except SchedulerStop:
                break
