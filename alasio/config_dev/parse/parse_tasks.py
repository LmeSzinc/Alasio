from typing import List

import msgspec
from msgspec import Struct, field

from alasio.ext.cache import cached_property
from alasio.ext.deep import deep_iter_depth1, deep_set
from alasio.ext.file.yamlfile import read_yaml
from alasio.ext.path import PathStr
from .parse_args import DefinitionError


def populate_task(value: dict) -> dict:
    """
    Populate shortened task definition
    """
    if type(value) is not dict:
        raise DefinitionError('Not a dict')

    # group
    group = value.get('group', [])
    if type(group) is list:
        group = [f'{g}' for g in group]
    else:
        raise DefinitionError('Not a list')
    value['group'] = group

    # display
    display = value.get('display', [])
    # convert display="flatten" and display="group"
    if display == 'group':
        display = [group]
    elif display == 'flatten':
        display = [[g] for g in group]
    elif type(display) is list:
        # populate simple list to nested list
        if display:
            display = [d if type(d) is list else [str(d)] for d in display]
        else:
            # Allow empty display, probably an internal task
            pass
    else:
        raise DefinitionError('display should be "flatten" or "group" or a list of groups')
    value['display'] = display

    return value


class TaskData(Struct):
    # List of arg groups in this task
    group: List[str] = field(default_factory=list)
    # Groups to display on GUI
    # Groups in the outer list will be displayed as standalone groups.
    # Groups in the inner list will be flattened into one group.
    display: List[List[str]] = field(default_factory=list)


class ParseTasks:
    # Path to *.args.yaml
    file: PathStr

    @cached_property
    def tasks_file(self):
        # {aside}.args.yaml -> {aside}.tasks.yaml
        return self.file.with_multisuffix('.tasks.yaml')

    @cached_property
    def tasks_data(self):
        """
        Structured data of {aside}.tasks.yaml

        Returns:
            dict[str, TaskData]:
                key: {task_name}
                value: TaskData
        """
        output = {}
        data = read_yaml(self.tasks_file)
        for task_name, value in deep_iter_depth1(data):
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
