from typing import Dict, Optional

import msgspec
from msgspec import field

from alasio.config.entry.utils import validate_task_name
from alasio.config_dev.parse.base import ParseBase
from alasio.config_dev.parse.parse_args import DefinitionError
from alasio.ext.cache import cached_property
from alasio.ext.deep import deep_iter_depth1, deep_set


class TaskGroup(msgspec.Struct):
    # A wrapper for group reference
    task: str
    group: str
    # group validation model
    model: str = ''

    @classmethod
    def from_group(cls, task: str, group: str, value: "dict | str | TaskGroup") -> "TaskGroup":
        """
        Populate group reference to TaskGroup object
        Example input:
            # for basic group: task=task, group=group, model=group
            None
            # for variant group: task=task, group=group, model=value
            "Scheduler"
            # for cross-task group: task="Commission", group=group, model=""
            # `model` is the model of the target group, real model will be set in MRO build
            {"task": "Commission"}
        """
        if type(value) is cls:
            return value
        if type(value) is dict:
            task = value.get('task', task)
            return cls(task=task, group=str(group))
        if value:
            # others treat as str
            return cls(task=task, group=str(group), model=str(value))
        else:
            # None
            return cls(task=task, group=str(group), model=str(group))

    @classmethod
    def from_display(cls, task: str, value: "dict | str | TaskGroup"):
        """
        Populate display reference to TaskGroup object
        Example input:
            # to display in-task group: task=task, group="Scheduler", model=""
            # real model will be set in MRO build
            "Scheduler"
            {"group": "Preset"}
            # to display cross-task group: task="Commission", group="preset", model=""
            # `model` is the model of the target group, real model will be set in MRO build
            {"task": "Commission", "group": "Preset"}
        """
        if type(value) is cls:
            return value
        if type(value) is dict:
            task = value.get('task', task)
            group = value.get('group', '')
            if not group:
                raise DefinitionError('Missing key "group" in group reference', keys=[task, 'displays'], value=value)
            return cls(task=task, group=str(group))
        # others treat as str
        return cls(task=task, group=str(value))


class DisplayCard(msgspec.Struct, dict=True):
    task: str
    # display {group_name}._info as card info
    raw_info: str
    # key: group name
    groups: Dict[str, TaskGroup]
    info: str = ''

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
            raw_info = row.get('info', '')
            groups = row.get('groups', {})
            if not raw_info and not groups:
                # may be a dict like {"task": "Commission", "group": "Preset"}
                raw_info = ''
                groups = [TaskGroup.from_display(task, row)]
        else:
            raw_info = ''
            groups = row
        if type(groups) is list:
            groups = [TaskGroup.from_display(task, r) for r in groups]
        elif type(groups) is dict:
            groups = [TaskGroup.from_display(task, groups)]
        else:
            # others treat as str
            groups = [TaskGroup.from_display(task, str(groups))]

        # use the first group that is not Scheduler
        if not raw_info:
            for group in groups:
                if group.group.startswith('Scheduler'):
                    continue
                raw_info = group.group
                break
        # no luck, just use the first group
        if not raw_info:
            for group in groups:
                raw_info = group.group
                break
        # build groups dict
        dict_groups = {}
        for group in groups:
            if group.group in dict_groups:
                raise DefinitionError(
                    f'Duplicate group to display: "{group.group}"', keys=[task, 'displays'], value=group)
            dict_groups[group.group] = group
        return cls(task=task, raw_info=raw_info, groups=dict_groups)


class CardNameBuilder:
    def __init__(self):
        self.cards = {}

    def add(self, card: DisplayCard):
        name = f'card-{card.task}-{card.raw_info}'
        if name not in self.cards:
            self.cards[name] = card
            return name
        # duplicate name, increase index
        index = 2
        while True:
            name2 = f'{name}{index}'
            if name2 not in self.cards:
                self.cards[name2] = card
                return name2
            index += 1

    @classmethod
    def from_cards(cls, list_cards: "list[DisplayCard]") -> "dict[str, DisplayCard]":
        """
        Convert list of cards to dict, with unique key name
        """
        builder = cls()
        for card in list_cards:
            builder.add(card)
        return builder.cards


class TaskData(msgspec.Struct, dict=True):
    task: str
    # groups to bind at runtime
    # key: group name
    groups: Dict[str, TaskGroup] = field(default_factory=dict)
    # cards to display on frontend
    # key: card name
    displays: Dict[str, DisplayCard] = field(default_factory=dict)
    # whether to globally bind all groups
    global_bind: bool = False
    parser: "Optional[ParseTasks]" = None

    @classmethod
    def from_task_data(cls, task: str, data: dict):
        if type(data) is not dict:
            raise DefinitionError('Task data must be a dict', keys=[task], value=data)
        task = str(task)
        # Example groups:
        # - A dict of values that satisfy TaskGroup.from_row()
        #   key: group name
        #   value: group validation model, or None to use group name as validation model
        raw_groups = data.get('groups', {})
        if type(raw_groups) is not dict:
            raise DefinitionError('Task groups must be a dict', keys=[task, 'groups'], value=raw_groups)
        groups = {}
        for group_name, value in raw_groups.items():
            # check group_name
            if not validate_task_name(group_name):
                raise DefinitionError(
                    f'Group name format invalid: "{group_name}"',
                    keys=[task, 'groups'], value=group_name
                )
            if group_name in groups:
                raise DefinitionError('Duplicate group in task', keys=[task, 'groups'], value=group_name)
            row = TaskGroup.from_group(task, group_name, value)
            groups[group_name] = row

        # Example displays:
        # - "group" to show each group as standalone card
        # - "flat" to show all groups in one card
        # - A list of values that satisfy DisplayCard.from_card()
        raw_displays = data.get('displays', 'flat')
        cards: "list[DisplayCard]"
        if type(raw_displays) is list:
            cards = [DisplayCard.from_card(task, r) for r in raw_displays]
        else:
            # treat as shorten instruction
            if raw_displays == 'group':
                cards = [DisplayCard.from_card(task, [r]) for r in groups.values()]
            elif raw_displays == 'flat':
                cards = [DisplayCard.from_card(task, list(groups.values()))]
            elif not raw_displays or raw_displays in ['none', 'None']:
                # empty display probably an internal task
                cards = []
            else:
                raise DefinitionError('display should be "flat" or "group" or "null" or a list of groups',
                                      keys=[task, 'displays'], value=raw_displays)
        # others
        global_bind = bool(data.get('global_bind', False))
        # build object
        displays = CardNameBuilder.from_cards(cards)
        return cls(task=task, groups=groups, displays=displays, global_bind=global_bind)

    @cached_property
    def has_scheduler(self):
        scheduler = False
        for group in self.groups.values():
            if not group.group.startswith('Scheduler'):
                continue
            # validate task with scheduler
            if group.task and self.task != group.task:
                raise DefinitionError(
                    'Task should not reference scheduler of another task',
                    keys=[self.task, 'groups'], value=group.group
                )
            if self.global_bind:
                raise DefinitionError(
                    'Global bind task should not have group "Scheduler"',
                    keys=[self.task, 'groups'], value=group.group
                )
            scheduler = True
        return scheduler


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
        data = self.read_tasks_yaml()
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
                _ = task.has_scheduler
                task.parser = self
            except DefinitionError as e:
                e.file = self.tasks_file
                raise
            # Set
            deep_set(output, keys=[task_name], value=task)

        return output
