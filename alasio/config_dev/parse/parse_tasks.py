from typing import List

import msgspec
from msgspec import field

from alasio.config.entry.utils import validate_task_name
from alasio.config_dev.parse.base import ParseBase
from alasio.config_dev.parse.parse_args import DefinitionError
from alasio.ext.cache import cached_property
from alasio.ext.deep import deep_iter_depth1, deep_set
from alasio.ext.file.yamlfile import read_yaml


class TaskGroup(msgspec.Struct):
    # A wrapper for group reference
    task: str
    group: str

    @classmethod
    def from_row(cls, task: str, row: "dict | str | TaskGroup") -> "TaskGroup":
        """
        Populate group reference to TaskGroup object
        Example input:
            "Scheduler"
            {"task": "Commission", "group": "Preset"}
        """
        if type(row) is cls:
            return row
        if type(row) is dict:
            task = row.get('task', task)
            group = row.get('group', '')
            if not group:
                raise DefinitionError('Missing key "group" in group reference', keys=[task], value=row)
            return cls(task=task, group=str(group))
        else:
            # others treat as str
            return cls(task=task, group=str(row))


class DisplayCard(msgspec.Struct, dict=True):
    task: str
    # display {group_name}._info as card info
    info: str
    groups: List[TaskGroup]

    @classmethod
    def from_card(cls, task: str, row: "dict | str | list | TaskGroup"):
        """
        Populate card definition to DisplayCard object
        Example input:
            "Scheduler"
            {"info": "Fleet", "groups": "Fleet1"}
            {"info": "Fleet", "groups": ["Fleet1", "Fleet2"]}
            ["Fleet1", "Fleet2"]
            {"task": "Commission", "group": "Preset"}
            [{"task": "Commission", "group": "Preset"}, "Custom"]
            {"info": "Commission", "groups": {"task": "Commission", "group": "Preset"}}
            {"info": "Commission", "groups": [{"task": "Commission", "group": "Preset"}, "Custom"]}
        """
        if type(row) is dict:
            info = row.get('info', '')
            groups = row.get('groups', [])
            if not info and not groups:
                # may be a dict like {"task": "Commission", "group": "Preset"}
                info = ''
                groups = [TaskGroup.from_row(task, row)]
        else:
            info = ''
            groups = row
        if type(groups) is list:
            groups = [TaskGroup.from_row(task, r) for r in groups]
        elif type(groups) is dict:
            groups = [TaskGroup.from_row(task, groups)]
        else:
            # others treat as str
            groups = [TaskGroup.from_row(task, str(groups))]
        # use the first group that is not Scheduler
        if not info:
            for group in groups:
                if group.group == 'Scheduler':
                    continue
                info = group.group
                break
        # no luck, just use the first group
        if not info:
            for group in groups:
                info = group.group
                break
        return cls(task=task, info=info, groups=groups)


class TaskData(msgspec.Struct):
    task: str
    # groups to bind at runtime
    groups: List[TaskGroup] = field(default_factory=list)
    # cards to display on frontend
    displays: List[DisplayCard] = field(default_factory=list)
    # whether to globally bind all groups
    global_bind: bool = False

    @classmethod
    def from_task_data(cls, task: str, data: dict):
        if type(data) is not dict:
            raise DefinitionError('Task data must be a dict', keys=[task], value=data)
        task = str(task)
        # Example groups:
        # - A list of values that satisfy TaskGroup.from_row()
        groups = data.get('groups', [])
        if type(groups) is not list:
            raise DefinitionError('Task groups must be a list', keys=[task, 'groups'], value=data)
        groups = [TaskGroup.from_row(task, r) for r in groups]
        # Example displays:
        # - "group" to show each group as standalone card
        # - "flat" to show all groups in one card
        # - A list of values that satisfy DisplayCard.from_card()
        displays = data.get('displays', 'group')
        if type(displays) is list:
            displays = [DisplayCard.from_card(task, r) for r in displays]
        else:
            # treat as shorten instruction
            if displays == 'group':
                displays = [DisplayCard.from_card(task, [r]) for r in groups]
            elif displays == 'flat':
                displays = [DisplayCard.from_card(task, groups)]
            elif not displays or displays in ['none', 'None']:
                # empty display probably an internal task
                displays = []
            else:
                raise DefinitionError('display should be "flat" or "group" or "null" or a list of groups',
                                      keys=[task, 'displays'], value=displays)
        # others
        global_bind = bool(data.get('global_bind', False))
        # build object
        return cls(task=task, groups=groups, displays=displays, global_bind=global_bind)


class ParseTasks(ParseBase):
    @cached_property
    def tasks_data(self):
        """
        Structured data of {nav}.tasks.yaml

        Returns:
            dict[str, TaskData]:
                key: {task_name}
                value: TaskData
        """
        output = {}
        data = read_yaml(self.tasks_file)
        for task_name, data in deep_iter_depth1(data):
            # check task_name
            if not validate_task_name(task_name):
                raise DefinitionError(
                    f'Task name format invalid: "{task_name}"',
                    file=self.tasks_file, keys=[], value=task_name
                )
            # Create TaskData object from manual arg definition
            try:
                task = TaskData.from_task_data(task=task_name, data=data)
            except DefinitionError as e:
                e.file = self.tasks_file
                raise
            # validate task with scheduler
            for group in task.groups:
                if task.task != group.task and group.group == 'Scheduler':
                    if group.task:
                        raise DefinitionError(
                            'Task should not reference scheduler of another task',
                            file=self.tasks_file, keys=[task_name, 'groups'], value='Scheduler'
                        )
                    if task.global_bind:
                        raise DefinitionError(
                            'Global bind task should not have group "Scheduler"',
                            file=self.tasks_file, keys=[task_name, 'groups'], value='Scheduler'
                        )
            # Set
            deep_set(output, keys=[task_name], value=task)

        return output
