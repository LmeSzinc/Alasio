import time
from datetime import datetime
from typing import TYPE_CHECKING

from alasio.backend.worker.bridge import BackendBridge
from alasio.backend.worker.event import ConfigEvent
from alasio.base.exception import (
    EmulatorNotRunningError, GameBugError, GameNotRunningError, GamePageUnknownError, GameStuckError,
    GameTooManyClickError, RequestHumanTakeover, ScriptError, TaskStop
)
from alasio.base.scheduler.configwatcher import ConfigWatcher
from alasio.base.scheduler.task_record import TaskRecord, TaskTooManyExecutionsError, TaskTooManyFailuresError
from alasio.base.state import TaskState
from alasio.base.timer import getnow
from alasio.ext import env
from alasio.ext.cache import cached_property
from alasio.ext.inflect import Inflection
from alasio.logger import logger
from alasio.logger.error import ErrorZipWriter

if TYPE_CHECKING:
    from alasio.config.base import AlasioConfigBase
    from alasio.device.base import DeviceBase


class SchedulerStop(Exception):
    """
    Internal exception that raises when receiving scheduler-stopping event from BackendBridge
    or when user intervention is requested.
    Scheduler loop will exit gracefully without sending an error event.
    """
    pass


class SchedulerError(Exception):
    """
    Internal exception that raises when an error occurs during scheduler operation
    (e.g., ScriptError, task failure, unexpected exception).
    Scheduler loop will exit and send an error event to BackendBridge.
    """
    pass


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
            raise SchedulerStop
        time.sleep(0.5)
        if time.perf_counter() >= end:
            return True


class AlasioScheduler:
    def __init__(self, config_name):
        self.config_name = config_name
        # Skip first restart
        self.skip_first_tasks = {'Restart', 'RestartDevice', 'RestartGame'}

    def create_config(self):
        from alasio.config.base import AlasioConfigBase
        return AlasioConfigBase(self.config_name)

    @cached_property
    def config(self) -> "AlasioConfigBase":
        try:
            return self.create_config()
        except RequestHumanTakeover as e:
            logger.critical(e)
            raise SchedulerError
        except Exception as e:
            logger.exception(e)
            raise SchedulerError

    def create_device(self):
        from alasio.device.base import DeviceBase
        from alasio.device.config import DeviceConfig
        device_config = DeviceConfig.from_config(self.config)
        return DeviceBase(device_config)

    @cached_property
    def device(self) -> "DeviceBase":
        try:
            return self.create_device()
        except RequestHumanTakeover as e:
            logger.critical(e)
            raise SchedulerError
        except Exception as e:
            logger.exception(e)
            raise SchedulerError

    def restart_device(self):
        raise NotImplementedError

    def restart_game(self):
        raise NotImplementedError

    def stop_game(self):
        raise NotImplementedError

    def stop_device(self):
        raise NotImplementedError

    def goto_main(self):
        raise NotImplementedError

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
            raise SchedulerError
        try:
            func()
            return True
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
        except (GameStuckError, GameTooManyClickError) as e:
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
            self._save_error_log()
            raise SchedulerError
        except RequestHumanTakeover as e:
            logger.critical(e)
            raise SchedulerError
        except ScriptError as e:
            logger.exception(e)
            logger.critical('This is likely to be a mistake of developers, but sometimes just random issues')
            raise SchedulerError
        except SchedulerStop:
            raise
        except SchedulerError:
            raise
        except Exception as e:
            logger.exception(e)
            self._save_error_log()
            raise SchedulerError

    def _save_error_log(self):
        """
        Save logs of last task and screenshots to /log/error/{time}_{config}.zip
        Zipfile has:
        - {time}.webp
        - log.txt
        """
        now = datetime.now().strftime('%Y-%m-%d_%H-%M-%S-%f')
        path = f'log/error/{now}_{self.config_name}.zip'
        logger.warning(f'Saving error: {path}')

        path = env.PROJECT_ROOT.joinpath(path)
        with ErrorZipWriter(path) as zipfile:
            zipfile.add_log(logger._writer.file)
            for file, image in self.device.screenshot_deque_iter():
                zipfile.add_image(image, file)

    def _send_scheduler_running(self, task):
        """
        send running task to backend

        Args:
            task (str | None):
        """
        backend = BackendBridge()
        if backend.inited:
            running = task if task else None
            event = ConfigEvent(t='TaskQueue', v={'running': running})
            backend.send(event)

    def _on_task_switch(self, task):
        """
        Callback function before running a task

        Args:
            task (str | None):
        """
        TaskState.reset_all_subclasses()
        self.device.on_task_switch()
        self._send_scheduler_running(task)

    def _on_game_stop(self):
        """
        Callback function when game stops

        Sends a stop preview signal to the backend
        """
        self.device.backend_send_preview_stop()

    def _on_idle(self):
        """
        Callback function on scheduler idle
        """
        TaskState.reset_all_subclasses()
        # send last screenshot on idle
        self.device.backend_send_preview(force=True)
        self.device.on_idle()
        self._send_scheduler_running(None)

    def _wait_future(self, task: str, future: datetime):
        """
        Args:
            task:
            future: datetime with tzinfo

        Returns:
            bool: True if waited to future, False if early stopped
        """
        if future <= getnow():
            return True
        logger.info(f'Wait until {future} for task `{task}`')

        # run before idle
        method = self.config.Optimization.WhenTaskQueueEmpty
        run = False
        if method == 'stop_game':
            logger.info('Stop game during wait')
            self._run_task('stop_game')
            self._on_game_stop()
            run = True
        elif method == 'stop_device':
            logger.info('Stop device during wait')
            self._run_task('stop_device')
            self._on_game_stop()
            run = True
        elif method == 'goto_main':
            logger.info('Goto main page during wait')
            self._run_task('goto_main')
            run = True
        elif method == 'stay_there':
            logger.info('Stay there during wait')
        else:
            logger.warning(f'Unknown Optimization.WhenTaskQueueEmpty={method}, treat as stay_there')

        # release
        self._on_idle()
        self.config.release()
        if run:
            # re-log, incase task logs flush wait message
            logger.info(f'Wait until {future} for task `{task}`')

        # wait
        watcher = ConfigWatcher(self.config_name).init()
        backend = BackendBridge()
        backend.send_worker_state('scheduler-waiting')
        count = 0
        while 1:
            time.sleep(0.5)
            count += 1
            # check scheduler_stopping every 0.5s
            if backend.scheduler_stopping.is_set():
                logger.info('SchedulerStop: backend request scheduler-stopping')
                raise SchedulerStop
            # check if reached future
            if getnow() > future:
                reached = True
                break
            # check if config modified every 5s
            if count % 10 == 0:
                if watcher.is_modified():
                    reached = False
                    break

        # recover
        if reached:
            backend.send_worker_state('running')
            # send first screenshot on recover
            backend.preview_requested.set()
            self.config.init_task()

        return reached

    def _skip_first_tasks(self, pending_tasks, next_task):
        """
        skip restart tasks at startup

        Args:
            pending_tasks (list[TaskItem]):
            next_task (TaskItem):

        Returns:
            bool: True if skipped
        """
        if next_task.TaskName not in self.skip_first_tasks:
            return False

        restart_tasks = []
        # find til the first non-restart task
        for task in pending_tasks:
            if task.TaskName in self.skip_first_tasks:
                restart_tasks.append(task.TaskName)
            else:
                break

        logger.info(f'Skip first tasks: {restart_tasks}')
        with self.config.batch_set():
            for name in restart_tasks:
                self.config.task_delay(server_update=True, task=name)
        for name in restart_tasks:
            self.skip_first_tasks.discard(name)
        return True

    def _task_loop(self):
        backend = BackendBridge()
        if backend.scheduler_stopping.is_set():
            logger.info('SchedulerStop: backend request scheduler-stopping')
            raise SchedulerStop
        # get next task
        self.config.release()
        self.config.override_clear()
        try:
            pending_tasks, _, task = self.config.get_next_task()
        except RequestHumanTakeover:
            raise SchedulerError

        if self._skip_first_tasks(pending_tasks=pending_tasks, next_task=task):
            return False

        # init task
        self.config.task = task.TaskName
        self.config.init_task()
        # wait task
        reached = self._wait_future(task=task.TaskName, future=task.NextRun)
        if not reached:
            return False

        # Run
        logger.info(f'Scheduler: Start task `{task.TaskName}`')
        self._on_task_switch(task.TaskName)
        logger.hr0(task.TaskName)
        success = self._run_task(task.TaskName)
        logger.info(f'Scheduler: End task `{task.TaskName}`')
        self.skip_first_tasks.clear()

        # Check task record constraints (execution frequency and failure count)
        try:
            TaskRecord().mark_task_result(task=task.TaskName, success=success)
        except TaskTooManyExecutionsError as e:
            logger.critical(e)
            logger.critical("Possible reason #1: You haven't used it correctly. "
                            "Please read the help text of the options.")
            logger.critical("Possible reason #2: There is a problem with this task. "
                            "Please contact developers or try to fix it yourself.")
            logger.critical('Request human takeover')
            raise SchedulerError
        except TaskTooManyFailuresError as e:
            logger.critical(e)
            logger.critical("Possible reason #1: You haven't used it correctly. "
                            "Please read the help text of the options.")
            logger.critical("Possible reason #2: There is a problem with this task. "
                            "Please contact developers or try to fix it yourself.")
            logger.critical('Request human takeover')
            raise SchedulerError
        if success:
            return True
        elif self.config.Error.HandleError:
            return True
        else:
            raise SchedulerError

    def run(self):
        logger.info(f'Start scheduler loop: {self.config_name}')
        self._on_task_switch(None)
        while 1:
            logger.check_rotate()
            try:
                self._task_loop()
            except SchedulerStop:
                break
            except SchedulerError as e:
                backend = BackendBridge()
                if backend.inited:
                    backend.send_worker_state('error')
                break
        self._on_task_switch(None)
        self._on_idle()
