from typing import List

import msgspec
from msgspec import Struct, field

from alasio.ext.cache import cached_property
from alasio.ext.deep import deep_iter_depth1, deep_set
from alasio.ext.file.yamlfile import read_yaml
from .base import ParseBase, iscapitalized
from .parse_args import DefinitionError


class TaskGroup(msgspec.Struct):
    # A wrapper for group reference "{group}" and "{task_ref}.{group}"
    task: str
    group: str

    # TaskGroup can also reference "{group}._info" as "_info"
    inforef: str = ''

    @classmethod
    def from_str(cls, task_group: str, ref=False) -> "TaskGroup":
        """
        Convert "{group}" and "{task_ref}.{group}" to TaskGroup object
        """
        # info reference
        if ref and ':' in task_group:
            ref, _, name = task_group.partition(':')
            if ref == 'inforef':
                return TaskGroup(task='', group='', inforef=name)
            raise DefinitionError(f'Info reference tag can only be "inforef", got "{task_group}"')
        # reference a cross-task group
        if '.' in task_group:
            task, _, group = task_group.rpartition('.')
            return TaskGroup(task=task, group=group)
        # reference an in-task group
        return TaskGroup(task='', group=task_group)

    @classmethod
    def from_liststr(cls, list_groups: "list[str]", ref=False) -> "list[TaskGroup]":
        return [cls.from_str(str(g), ref=ref) for g in list_groups]


def populate_task(value: dict) -> dict:
    """
    Populate shortened task definition
    """
    if type(value) is not dict:
        raise DefinitionError('Not a dict')

    # group
    group = value.get('group', [])
    if type(group) is list:
        # yaml has dynamic type, we need all in string
        group = TaskGroup.from_liststr(group)
        # check if group is unique
        visited = set()
        for g in group:
            if g.group in visited:
                raise DefinitionError(f'Duplicate group at "{g}"')
            visited.add(g.group)
    else:
        raise DefinitionError('Not a list')
    value['group'] = group

    # display
    display = value.get('display', [])
    # convert display="flat" and display="group"
    if display == 'group':
        display = [[g] for g in group]
    elif display == 'flat':
        display = [group]
    elif type(display) is list:
        # populate simple list to nested list
        if display:
            display = [
                TaskGroup.from_liststr(d, ref=True) if type(d) is list
                else TaskGroup.from_liststr([str(d)], ref=True) for d in display]
        else:
            # Allow empty display, probably an internal task
            pass
    else:
        raise DefinitionError('display should be "flat" or "group" or a list of groups')
    value['display'] = display

    return value


class TaskData(Struct):
    # List of arg groups in this task
    group: List[TaskGroup] = field(default_factory=list)
    # Groups to display on GUI
    # Groups in the outer list will be displayed as standalone groups.
    # Groups in the inner list will be flattened into one group.
    display: List[List[TaskGroup]] = field(default_factory=list)


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
        for task_name, value in deep_iter_depth1(data):
            # check task_name
            if not task_name.isalnum():
                raise DefinitionError(
                    'Task name must be alphanumeric',
                    file=self.tasks_file, keys=[], value=task_name
                )
            if not iscapitalized(task_name):
                raise DefinitionError(
                    'Task name must be capitalized',
                    file=self.tasks_file, keys=[], value=task_name
                )
            # Create TaskData object from manual arg definition
            try:
                value = populate_task(value)
            except DefinitionError as e:
                e.file = self.tasks_file
                e.keys = [task_name]
                e.value = value
                raise
            try:
                task = msgspec.convert(value, TaskData)
            except msgspec.ValidationError as e:
                ne = DefinitionError(e, file=self.tasks_file, keys=[task_name], value=value)
                raise ne
            # Set
            deep_set(output, keys=[task_name], value=task)
            # print(msgspec.json.encode(arg))

        return output
