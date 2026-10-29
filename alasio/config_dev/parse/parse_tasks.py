from typing import Dict, List, Optional, Union

import msgspec
from msgspec import field
from msgspecerror import parse_msgspec_error

from alasio.config.entry.utils import validate_task_name
from alasio.config_dev.parse.base import ParseBase
from alasio.config_dev.parse.parse_args import DefinitionError
from alasio.ext.cache import cached_property


class TaskGroupRef(msgspec.Struct, forbid_unknown_fields=True):
    """
    Value of one entry of the `groups` mapping of a task.

    The group name is the key of the mapping, so it is not repeated here.
    An empty task means the group belongs to the task that declares it.
    """

    task: str = ''


class CardItem(msgspec.Struct, forbid_unknown_fields=True):
    """
    One item of a display card.

    An item is either an info reference or a group reference:

    - `{info: Fleet}` shows the `_info` of group Fleet as the card info, it is
      allowed only as the first item of a card;
    - `{task: Commission, group: Preset}` shows group Preset of task
      Commission. The task defaults to the task that owns the card, the model
      is resolved later by the MRO build.

    msgspec can not tell two struct types apart in an untagged union (both
    would have to be tagged in the yaml), so the two references share one
    struct whose fields are mutually exclusive, checked by
    DisplayCard.from_card().
    """

    info: str = ''
    group: str = ''
    task: str = ''


# Items of one card: a group name of the task itself, or a card reference
CardItemDefinition = Union[str, CardItem]
# One card of `displays`, an ordered list of group names and references
CardDefinition = List[CardItemDefinition]
# `displays` value: a short form string, a null, or a list of cards
DisplaysDefinition = Optional[Union[str, List[CardDefinition]]]


class TaskDefinition(msgspec.Struct, forbid_unknown_fields=True):
    """
    Input schema of one task of {nav}.tasks.yaml

    msgspec.convert() checks the yaml structure: unknown fields, value types,
    and the shape of `groups` / `displays`. Names and the runtime objects are
    built by TaskData.from_task_definition(), which receives the converted
    definition.

    Example input:
        groups:
          # basic group, model is the group name
          Fleet1:
          # variant group, the value is the validation model
          Fleet2: Fleet
          # cross-task group, the model is resolved by the MRO build
          Fleet3:
            task: Commission
        displays:
          - [{info: Fleet}, Fleet1, Fleet2]
          - [{task: Commission, group: Preset}]
        global_bind: false
    """

    # key: group name
    groups: Optional[Dict[str, Union[None, str, TaskGroupRef]]] = field(default_factory=dict)
    displays: DisplaysDefinition = 'flat'
    global_bind: bool = False


# Root of {nav}.tasks.yaml: task name -> task definition
TasksDefinition = Dict[str, TaskDefinition]


class TaskGroup(msgspec.Struct, forbid_unknown_fields=True):
    # A wrapper for group reference
    task: str
    group: str
    # group validation model
    model: str = ''

    @classmethod
    def from_group(cls, task: str, group: str, value: "None | str | TaskGroupRef") -> "TaskGroup":
        """
        Populate a group binding of the `groups` mapping.

        Args:
            task (str): Task that declares the group
            group (str): Group name, the key of the mapping
            value (None | str | TaskGroupRef):
                None for a basic group, the model is the group name
                str for a variant group, the value is the validation model
                TaskGroupRef for a cross-task group, the model is resolved by
                the MRO build

        Returns:
            TaskGroup: Group reference

        Raises:
            DefinitionError: A name is not a valid task name
        """
        keys = [task, 'groups', group]
        if value is None:
            return cls(task=task, group=group, model=group)
        if type(value) is str:
            if not validate_task_name(value):
                raise DefinitionError(
                    f'Group model format invalid: "{value}"', keys=keys, value=value)
            return cls(task=task, group=group, model=value)
        # A reference to the task itself is a basic group. The MRO build would
        # look up this same reference again, so it could never resolve the
        # model of the target group.
        ref_task = value.task
        if not ref_task or ref_task == task:
            return cls(task=task, group=group, model=group)
        if not validate_task_name(ref_task):
            raise DefinitionError(
                f'Task name format invalid: "{ref_task}"', keys=keys, value=ref_task)
        return cls(task=ref_task, group=group)

    @classmethod
    def from_display(cls, task: str, group: str, ref_task: str = '') -> "TaskGroup":
        """
        Populate a group reference of a display card.

        Args:
            task (str): Task that owns the card, used when ref_task is empty
            group (str): Group name to display
            ref_task (str): Task that owns the group, empty for the card task

        Returns:
            TaskGroup: Group reference, the model is resolved by the MRO build

        Raises:
            DefinitionError: A name is not a valid task name
        """
        keys = [task, 'displays']
        if not validate_task_name(group):
            raise DefinitionError(
                f'Group name format invalid: "{group}"', keys=keys, value=group)
        if ref_task and not validate_task_name(ref_task):
            raise DefinitionError(
                f'Task name format invalid: "{ref_task}"', keys=keys, value=ref_task)
        return cls(task=ref_task or task, group=group)


class DisplayCard(msgspec.Struct, dict=True, forbid_unknown_fields=True):
    task: str
    # display {group_name}._info as card info
    raw_info: str
    # key: group name
    groups: Dict[str, TaskGroup]
    info: str = ''

    @classmethod
    def from_card(cls, task: str, row: "list") -> "DisplayCard":
        """
        Build a card from one entry of `displays`.

        Example input:
            [Fleet1]
            [Fleet1, Fleet2]
            [{info: Fleet}, Fleet1, Fleet2]
            [{task: Commission, group: Preset}]
            [{info: Commission}, {task: Commission, group: Preset}, Custom]

        Args:
            task (str): Task that owns the card
            row (list[str | CardItem]): Items of the card, decoded by
                TaskDefinition

        Returns:
            DisplayCard: Card of the task

        Raises:
            DefinitionError: The card has no group, an item is not a valid
                reference, an info reference is not the first item, or a group
                is displayed twice in the same card
        """
        keys = [task, 'displays']
        raw_info = ''
        refs: "list[TaskGroup]" = []
        for index, item in enumerate(row):
            if type(item) is str:
                refs.append(TaskGroup.from_display(task, item))
                continue
            if item.info:
                if item.group or item.task:
                    raise DefinitionError(
                        'Display info ref must not have "group" or "task"', keys=keys, value=item)
                if index:
                    raise DefinitionError(
                        'Display info ref must be the first item of a card', keys=keys, value=item)
                if not validate_task_name(item.info):
                    raise DefinitionError(
                        f'Display info format invalid: "{item.info}"', keys=keys, value=item.info)
                raw_info = item.info
                continue
            if not item.group:
                raise DefinitionError(
                    'Display ref must have "info" or "group"', keys=keys, value=item)
            refs.append(TaskGroup.from_display(task, item.group, item.task))
        return cls.from_refs(task, raw_info, refs, row, keys)

    @classmethod
    def from_refs(cls, task: str, raw_info: str, refs: "list[TaskGroup]", source, keys) -> "DisplayCard":
        """
        Build a card from parsed group references.

        Used by the `group` and `flat` short forms, which display the groups
        already bound to the task.

        Args:
            task (str): Task that owns the card
            raw_info (str): Card info named by an info reference, empty to use
                the first group that is not a Scheduler group
            refs (list[TaskGroup]): Group references of the card
            source: Original definition, used as the error value
            keys (list): Error location

        Returns:
            DisplayCard: Card of the task

        Raises:
            DefinitionError: The card has no group or a group is displayed twice
        """
        # use the first group that is not Scheduler
        if not raw_info:
            for ref in refs:
                if ref.group.startswith('Scheduler'):
                    continue
                raw_info = ref.group
                break
        # no luck, just use the first group
        if not raw_info and refs:
            raw_info = refs[0].group
        # a card must show something
        if not raw_info:
            raise DefinitionError(
                'Display card must have at least one group or an info', keys=keys, value=source)
        # build groups dict
        dict_groups = {}
        for ref in refs:
            if ref.group in dict_groups:
                raise DefinitionError(
                    f'Duplicate group to display: "{ref.group}"', keys=keys, value=ref)
            dict_groups[ref.group] = ref
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


class TaskData(msgspec.Struct, dict=True, forbid_unknown_fields=True):
    """
    One task of {nav}.tasks.yaml

    `dict=True` is for the parser back reference only: the parser holds the file
    paths of the nav and forms a reference cycle with its tasks, so it is kept
    out of the msgspec fields (see the `parser` property). A TaskData therefore
    encodes to json / msgpack without the parser, which is the only member that
    msgspec can not handle.
    """

    task: str
    # groups to bind at runtime
    # key: group name
    groups: Dict[str, TaskGroup] = field(default_factory=dict)
    # cards to display on frontend
    # key: card name
    displays: Dict[str, DisplayCard] = field(default_factory=dict)
    # whether to globally bind all groups
    global_bind: bool = False

    @property
    def parser(self) -> "Optional[ParseTasks]":
        """
        Parser that owns this task, None when the task is built by hand.

        Runtime state only, not a msgspec field: it is kept in `__dict__` so a
        TaskData stays serializable. Use it for the file paths and the per nav
        state, never as part of the task definition.

        Returns:
            ParseTasks | None: Owning parser
        """
        return self.__dict__.get('parser', None)

    @parser.setter
    def parser(self, value):
        self.__dict__['parser'] = value

    @classmethod
    def from_task_definition(cls, task: str, definition: "TaskDefinition"):
        """
        Build a task from a definition decoded by TaskDefinition.

        Args:
            task (str): Task name, the key of the yaml mapping
            definition (TaskDefinition): Task definition, already checked by
                msgspec

        Returns:
            TaskData: Task with its groups and display cards

        Raises:
            DefinitionError: A name or a display card is invalid
        """
        task = str(task)

        # group bindings
        groups = {}
        for group_name, value in (definition.groups or {}).items():
            # check group_name
            if not validate_task_name(group_name):
                raise DefinitionError(
                    f'Group name format invalid: "{group_name}"',
                    keys=[task, 'groups'], value=group_name)
            groups[group_name] = TaskGroup.from_group(task, group_name, value)

        # display cards
        raw_displays = definition.displays
        cards: "list[DisplayCard]"
        if type(raw_displays) is list:
            cards = [DisplayCard.from_card(task, row) for row in raw_displays]
        else:
            # treat as shorten instruction
            if raw_displays == 'group':
                cards = [
                    DisplayCard.from_refs(task, '', [ref], raw_displays, [task, 'displays'])
                    for ref in groups.values()
                ]
            elif raw_displays == 'flat':
                # a task without groups has nothing to display
                if groups:
                    card = DisplayCard.from_refs(
                        task, '', list(groups.values()), raw_displays, [task, 'displays'])
                    cards = [card]
                else:
                    cards = []
            elif raw_displays is None or raw_displays in ('', 'none', 'None'):
                # empty display probably an internal task
                cards = []
            else:
                raise DefinitionError(
                    'display should be "flat" or "group" or "null" or a list of cards',
                    keys=[task, 'displays'], value=raw_displays)

        # build object
        displays = CardNameBuilder.from_cards(cards)
        return cls(task=task, groups=groups, displays=displays, global_bind=definition.global_bind)

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

        Raises:
            DefinitionError: The file does not satisfy TasksDefinition, a task
                name is invalid, or a task definition is invalid
        """
        data = self.read_tasks_yaml()
        # Check names before the structure, a misspelled task name is the more
        # likely mistake and its error is easier to read
        for task_name in data:
            if not validate_task_name(task_name):
                raise DefinitionError(
                    f'Task name format invalid: "{task_name}"',
                    file=self.tasks_file, keys=[], value=task_name
                )
        # The whole file is checked by msgspec, instead of one task at a time
        try:
            definitions = msgspec.convert(data, type=TasksDefinition)
        except msgspec.ValidationError as e:
            # The error location carries msgspec's `...` placeholder, so it
            # names the broken field but not the task or the group it belongs
            # to, and the offending value is not collected. Resolving the
            # placeholder is left to msgspecerror, the message and the file are
            # still enough for a developer to find the definition.
            error = parse_msgspec_error(e)
            raise DefinitionError(error.msg, file=self.tasks_file, keys=list(error.loc)) from e

        output = {}
        for task_name, definition in definitions.items():
            # Create TaskData object from the validated definition
            try:
                task = TaskData.from_task_definition(task=task_name, definition=definition)
                _ = task.has_scheduler
                task.parser = self
            except DefinitionError as e:
                e.file = self.tasks_file
                raise
            output[task_name] = task

        return output
