from datetime import datetime, timedelta

from msgspec import NODEFAULT

from alasio.backend.worker.bridge import BackendBridge
from alasio.backend.worker.event import ConfigEvent
from alasio.base.exception import RequestHumanTakeover, ScriptError, TaskStop
from alasio.base.pretty import dict2kv
from alasio.base.servertime import nearest_future, random_time
from alasio.base.timer import getnow
from alasio.config.base.config_access import AlasioConfigBaseAccess
from alasio.config.entry.model import TaskItem
from alasio.logger import logger


class AlasioConfigBaseTask(AlasioConfigBaseAccess):
    """Mixin for task scheduling methods of AlasioConfigBaseAccess."""

    def get_task_schedule(self):
        """
        Returns:
            tuple[list[TaskItem], list[Task]]:
        """
        with self._lock:
            self.config_cache()
            # build scheduler groups
            for task, _ in self.mod.iter_task_scheduler_group():
                self._cross_get_group(task, 'Scheduler')
            # calculate scheduler
            pending_task, waiting_task = self.mod.get_task_schedule(
                self.config_name, dict_row=self._dict_row, dict_group=self._dict_group)
        # send backend event
        backend = BackendBridge()
        if backend.inited:
            # note that pending_task and waiting_task may contain running task
            # frontend should remove it
            data = {'pending': pending_task, 'waiting': waiting_task}
            backend.send(ConfigEvent(t='TaskQueue', v=data))
        return pending_task, waiting_task

    def get_next_task(self) -> TaskItem:
        pending_task, waiting_task = self.get_task_schedule()
        # get first task
        if pending_task:
            logger.info(f'Pending tasks: {[f.TaskName for f in pending_task]}')
            task = pending_task[0]
            task.NextRun = task.NextRun.astimezone()
            logger.attr('Task', task)
            return task
        if waiting_task:
            logger.info('No task pending')
            task = waiting_task[0]
            task.NextRun = task.NextRun.astimezone()
            logger.attr('Task', task)
            return task
        raise RequestHumanTakeover('No task waiting or pending, please enable at least on task')

    def task_switched(self):
        """
        Check if scheduler needs to switch task.

        Returns:
            bool: If task switched

        Examples:
            if self.config.task_switched():
                # do task specific cleanup
                self.campaign.ensure_auto_search_exit()
                self.config.task_stop()
        """
        prev = self.task

        # check update event
        backend = BackendBridge()
        if backend.scheduler_stopping.is_set():
            logger.info(f'Stop task {prev}, scheduler stopping')
            return True

        # reload
        self.release()

        # check task switch
        new = self.get_next_task().TaskName
        if prev == new:
            logger.info(f'Continue task `{new}`')
            return False
        else:
            logger.info(f'Switch task `{prev}` to `{new}`')
            return True

    @staticmethod
    def task_stop(message=''):
        """
        Helper method to stop current task.
        """
        raise TaskStop(message)

    def check_task_switch(self, message='Task switched'):
        """
        Stop current task if task switched.

        Raises:
            TaskEnd:
        """
        if self.task_switched():
            self.task_stop(message=message)

    def task_delay(self, minute=None, server_update=None, target=None, task=None):
        """
        Set "Scheduler.NextRun" to delay task.
        At lease one argument should be set.
        If multiple arguments are set, task will be delayed to the nearest future.

        Args:
            minute (int | str | float | tuple[int, int], list[int]):
                Delay several minutes, or random minutes like (delay_min, delay_max), or "10~30", "10, 30"
            server_update (bool | list[str] | str):
                True to delay to the nearest Scheduler.ServerUpdate
                list[str] or str to delay to given server update, like "00:00", ["00:00", "12:00"], "00:00, 12:00"
            target (datetime):
                Delay to given target
            task (str):
                None to delay current task
                str to delay given task
        """
        futures = []
        if minute is not None:
            delay = int(random_time(minute) * 60)
            futures.append(getnow() + timedelta(seconds=delay))
        if server_update is not None:
            if server_update is True:
                try:
                    server_update = self.Scheduler.ServerUpdate
                except AttributeError:
                    logger.warning(f'DataInconsistent: Missing Scheduler.ServerUpdate in config')
                    server_update = None
            if server_update is not None:
                futures.append(self.servertime.get_next_update(server_update))
        if target is not None:
            target = target.astimezone()
            futures.append(target)

        kv = dict2kv({'minute': minute, 'server_update': server_update, 'target': target}, drop_none=True)
        if futures:
            run = nearest_future(futures)
        else:
            raise ScriptError(f'Missing argument in task_delay(), should set at least one')
        if not task:
            task = self.task
        if not task:
            raise ScriptError(f'Empty task, cannot call task_delay()')

        logger.info(f"Delay task `{task}` to {run} ({kv})")
        self.cross_set(task, 'Scheduler', 'NextRun', run)
        self.get_task_schedule()

    def get_all_tasks(self):
        """
        Get all task names that have a Scheduler group.

        Returns:
            list[str]: List of task names with Scheduler, in task index order.
        """
        return [task_name for task_name, _ in self.mod.iter_task_scheduler_group()]

    def task_limit_nextrun(self, task, future):
        """
        Limit the next run time of one or more tasks.
        If the task's next run is later than ``future``, set it to ``now``.

        Args:
            task (str | list[str]): Task name or list of task names.
            future (datetime): Upper bound for next run. Tasks with a next run
                later than this time will be reset to now.
        """
        now = getnow().replace(microsecond=0)
        if isinstance(task, str):
            task = [task]

        for t in task:
            next_run = self.cross_get(t, 'Scheduler', 'NextRun', default=None)
            if next_run is None:
                continue
            if next_run > future:
                # logger.info(f'Limit next run of task `{t}` from {next_run} to {now}')
                self.cross_set(t, 'Scheduler', 'NextRun', now)

    def is_task_enabled(self, task):
        return bool(self.cross_get(task, 'Scheduler', 'Enable', default=False))

    def task_call(self, task, force_call=True):
        """
        Call another task to run.

        That task will run when current task finished.
        But it might not be run because:
        - Other tasks should run first according to SCHEDULER_PRIORITY
        - Task is disabled by user

        Args:
            task (str): Task name to call, such as `Restart`
            force_call (bool):

        Returns:
            bool: If called.
        """
        if force_call or self.is_task_enabled(task):
            logger.info(f'Task call: {task}')
            with self.batch_set():
                group = self._cross_get_group(task, 'Scheduler')
                if group is not NODEFAULT and group.__class__.__name__ == 'SchedulerStatic':
                    self.cross_set(task, 'Scheduler', 'Enable', 'enabled')
                else:
                    self.cross_set(task, 'Scheduler', 'Enable', True)
                self.cross_set(task, 'Scheduler', 'NextRun', getnow())
            return True
        else:
            logger.info(f'Task call: {task} (skipped because disabled by user)')
            return False
