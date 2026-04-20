from datetime import datetime

from msgspec import NODEFAULT, Struct

from alasio.base.filter import parse_filter
from alasio.config.entry.mod_base import ModBase
from alasio.config.entry.model import TaskItem
from alasio.config.table.config import AlasioConfigTable, ConfigRow
from alasio.ext.cache import cached_property
from alasio.ext.file.loadpy import LOADPY_CACHE
from alasio.ext.msgspec_error import load_msgpack_with_default
from alasio.logger import logger


class ModScheduler(ModBase):
    """
    Scheduler
    """

    @cached_property
    def _dict_task_priority(self) -> "dict[str, int]":
        """
        Returns:
            key: task_name, value: priority, starting from 1, smaller for higher priority
        """
        file = self.path_config / 'const.py'
        try:
            # one-time load, use load_resource instead
            module = LOADPY_CACHE.load_resource(file)
        except ImportError as e:
            # DataInconsistent error.
            # We just log warnings to reduce runtime crash, fallback to no task priority
            logger.warning(
                f'DataInconsistent: failed to import model.py "{file}": {e}')
            return {}
        try:
            priority = module.ConfigConst.SCHEDULER_PRIORITY
        except AttributeError:
            logger.warning(
                f'DataInconsistent: Missing ConfigConst.SCHEDULER_PRIORITY in "{file}"')
            return {}
        return self.parse_scheduler_priority(priority)

    @staticmethod
    def parse_scheduler_priority(priority: str) -> "dict[str, int]":
        priority = parse_filter(priority)
        dict_priority = {}
        for i, task in enumerate(priority, start=1):
            dict_priority[task] = i
        return dict_priority

    def iter_task_scheduler_group(self, dict_task_priority=None):
        """
        Args:
            dict_task_priority (dict[str, int] | None):

        Yields:
            tuple[str, ModelGroupRef]:
        """
        if not dict_task_priority:
            dict_task_priority = self._dict_task_priority
        group_name = 'Scheduler'
        for task_name, task_data in self.task_index_data().items():
            if task_name not in dict_task_priority:
                continue
            model_ref = task_data.group.get(group_name, None)
            if not model_ref:
                continue
            # skip cross-task ref
            if model_ref.task != task_name:
                continue
            yield task_name, model_ref

    def get_task_schedule(
            self,
            config_name,
            dict_row: "dict[tuple[str, str], bytes]" = None,
            dict_group: "dict[tuple[str, str], Struct]" = None,
            dict_task_priority: "dict[str, int]" = None,

    ) -> "tuple[list[TaskItem], list[TaskItem]]":
        """
        Args:
            config_name:
            dict_row:
            dict_group:
            dict_task_priority:

        Returns:
            list_pending, list_waiting
        """
        if not dict_row:
            table = AlasioConfigTable(config_name)
            rows: "list[ConfigRow]" = table.select(group='Scheduler')
            dict_row = {}
            for row in rows:
                dict_row[(row.task, row.group)] = row.value

        list_pending = []
        list_waiting = []
        group_name = 'Scheduler'
        if not dict_task_priority:
            dict_task_priority = self._dict_task_priority
        now = datetime.now().astimezone()
        for task_name, model_ref in self.iter_task_scheduler_group(dict_task_priority):
            # try to get from dict_group
            if dict_group:
                group = dict_group.get((task_name, group_name), None)
            else:
                group = None

            # try to get from dict_row
            if group is None:
                model = self.get_group_model(file=model_ref.file, cls=model_ref.cls)
                if model is None:
                    continue
                value = dict_row.get((task_name, group_name), NODEFAULT)
                if value is NODEFAULT:
                    # construct a default value from model
                    try:
                        data = model()
                    except Exception as e:
                        logger.warning(
                            f'DataInconsistent: Class "{model_ref.cls}" in "{model_ref.file}" '
                            f'failed to construct: {e}')
                        continue
                else:
                    # validate value
                    data, errors = load_msgpack_with_default(value, model=model)
                    # for error in errors:
                    #     logger.warning(f'Config data inconsistent at {row.task}.{row.group}: {error}')
                    if data is NODEFAULT:
                        # Failed to convert
                        continue
                group = data
            # set
            try:
                if not group.Enable:
                    continue
                is_waiting = group.NextRun > now
            except AttributeError:
                # this shouldn't happen, unless Scheduler is not properly defined
                continue
            except TypeError:
                # TypeError: can't compare offset-naive and offset-aware datetimes
                continue
            if is_waiting:
                list_waiting.append(TaskItem(TaskName=task_name, NextRun=group.NextRun))
            else:
                list_pending.append(TaskItem(TaskName=task_name, NextRun=group.NextRun))

        list_waiting.sort(key=lambda x: (x.NextRun, dict_task_priority.get(x.TaskName, 0)))
        list_pending.sort(key=lambda x: dict_task_priority.get(x.TaskName, 0))
        return list_pending, list_waiting
