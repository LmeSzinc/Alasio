"""
Tests of ParseTasks.tasks_data file reading and error location.

ParseTasks reads {nav}.tasks.yaml through ParseBase.read_tasks_yaml(), checks
the whole file against TasksDefinition in one msgspec.convert(), then builds
one TaskData per task. Cross-task and cross-nav checks (does the target
task/group exist, is a display model generated) happen in the MRO build; here
only the per-file structure and the source file of every error are checked.

msgspec reports a broken mapping entry as `$[...]`, so the location of a
validated file keeps that placeholder: it names the broken field but not the
task or the group that owns it, and the offending value is not collected.
Resolving the placeholder is left to msgspecerror for now, it only changes the
hint a developer reads, not what the parser accepts.
"""

import pytest

from alasio.config_dev.parse.base import DefinitionError
from alasio.config_dev.parse.parse_tasks import DisplayCard, ParseTasks
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.testing.filesystem import fs  # noqa: F401
from tests.config_dev.parse.parse_tasks.helpers import make_parser


class TestTasksDataEmpty:
    def test_missing_file(self, fs):
        assert make_parser(fs).tasks_data == {}

    def test_empty_file(self, fs):
        assert make_parser(fs, tasks_yaml='').tasks_data == {}

    def test_empty_mapping(self, fs):
        assert make_parser(fs, tasks_yaml='{}\n').tasks_data == {}

    def test_null_document(self, fs):
        assert make_parser(fs, tasks_yaml='null\n').tasks_data == {}

    def test_malformed_yaml(self, fs):
        parser = make_parser(fs, tasks_yaml='Task: [\n')
        with pytest.raises(DefinitionError) as e:
            _ = parser.tasks_data
        assert e.value.msg.startswith('Invalid yaml: ')
        assert e.value.file == parser.tasks_file

    def test_yaml_root_is_list(self, fs):
        parser = make_parser(fs, tasks_yaml='- Task\n- Other\n')
        with pytest.raises(DefinitionError) as e:
            _ = parser.tasks_data
        assert e.value.msg == 'Expected a mapping at yaml root, got list'
        assert e.value.file == parser.tasks_file
        assert e.value.value == ['Task', 'Other']

    def test_yaml_root_is_scalar(self, fs):
        parser = make_parser(fs, tasks_yaml='Task\n')
        with pytest.raises(DefinitionError) as e:
            _ = parser.tasks_data
        assert e.value.msg == 'Expected a mapping at yaml root, got str'
        assert e.value.file == parser.tasks_file
        assert e.value.value == 'Task'


class TestTasksData:
    def test_one_task(self, fs):
        parser = make_parser(fs, tasks_yaml="""\
Task:
  groups:
    Group:
  displays: null
""")
        data = parser.tasks_data
        assert list(data) == ['Task']
        task = data['Task']
        assert task.task == 'Task'
        assert list(task.groups) == ['Group']
        assert task.groups['Group'].model == 'Group'
        assert task.displays == {}
        assert task.global_bind is False
        assert task.parser is parser

    def test_multiple_tasks_keep_yaml_order(self, fs):
        parser = make_parser(fs, tasks_yaml="""\
Beta:
  displays: null
Alpha:
  displays: null
""")
        assert list(parser.tasks_data) == ['Beta', 'Alpha']

    def test_task_without_groups(self, fs):
        parser = make_parser(fs, tasks_yaml="""\
Internal:
  displays: null
""")
        task = parser.tasks_data['Internal']
        assert task.groups == {}
        assert task.displays == {}

    def test_default_displays_without_groups(self, fs):
        """A task without groups has nothing to display and no empty card."""
        parser = make_parser(fs, tasks_yaml="""\
Internal: {}
""")
        task = parser.tasks_data['Internal']
        assert task.groups == {}
        assert task.displays == {}

    def test_null_groups(self, fs):
        parser = make_parser(fs, tasks_yaml="""\
Task:
  groups:
  displays: null
""")
        assert parser.tasks_data['Task'].groups == {}

    def test_default_flat_display(self, fs):
        parser = make_parser(fs, tasks_yaml="""\
Task:
  groups:
    Scheduler:
    Group:
""")
        task = parser.tasks_data['Task']
        assert list(task.displays) == ['card-Task-Group']
        assert list(task.displays['card-Task-Group'].groups) == ['Scheduler', 'Group']

    def test_group_display(self, fs):
        parser = make_parser(fs, tasks_yaml="""\
Task:
  groups:
    Scheduler:
    Group:
  displays: group
""")
        task = parser.tasks_data['Task']
        assert list(task.displays) == ['card-Task-Scheduler', 'card-Task-Group']

    def test_cards_with_info_ref(self, fs):
        parser = make_parser(fs, tasks_yaml="""\
Task:
  groups:
    Scheduler:
    Fleet1:
    Fleet2:
  displays:
    - [{info: Task}, Scheduler]
    - [{info: Fleet}, Fleet1, Fleet2]
""")
        task = parser.tasks_data['Task']
        assert list(task.displays) == ['card-Task-Task', 'card-Task-Fleet']
        assert task.displays['card-Task-Task'].raw_info == 'Task'
        assert list(task.displays['card-Task-Task'].groups) == ['Scheduler']
        assert list(task.displays['card-Task-Fleet'].groups) == ['Fleet1', 'Fleet2']

    def test_cross_task_display(self, fs):
        parser = make_parser(fs, tasks_yaml="""\
Task:
  groups:
    Game:
  displays:
    - [Game]
    - [{task: Device, group: Emulator}]
    - [{info: RestartDevice}, {task: RestartDevice, group: Scheduler}]
""")
        task = parser.tasks_data['Task']
        assert list(task.displays) == [
            'card-Task-Game', 'card-Task-Emulator', 'card-Task-RestartDevice']
        assert task.displays['card-Task-Emulator'].groups['Emulator'].task == 'Device'
        assert task.displays['card-Task-RestartDevice'].groups['Scheduler'].task == 'RestartDevice'

    def test_group_bindings(self, fs):
        parser = make_parser(fs, tasks_yaml="""\
Task:
  groups:
    Basic:
    Variant: Other
    Cross:
      task: OtherTask
  displays: null
""")
        groups = parser.tasks_data['Task'].groups
        assert groups['Basic'].model == 'Basic'
        assert groups['Variant'].model == 'Other'
        assert groups['Cross'].task == 'OtherTask'
        assert groups['Cross'].model == ''

    def test_every_task_gets_the_parser(self, fs):
        parser = make_parser(fs, tasks_yaml="""\
Task1:
  displays: null
Task2:
  displays: null
""")
        for task in parser.tasks_data.values():
            assert task.parser is parser

    def test_cached_property(self, fs):
        parser = make_parser(fs, tasks_yaml="""\
Task:
  displays: null
""")
        first = parser.tasks_data
        second = parser.tasks_data
        assert first is second
        assert 'tasks_data' in parser.__dict__


class TestTasksDataNameError:
    @pytest.mark.parametrize('task_name', ['task', 'Task_1', '1Task', 'Task-1'], ids=[
        'lower', 'underscore', 'digit-first', 'dash',
    ])
    def test_task_name_format(self, fs, task_name):
        parser = make_parser(fs, tasks_yaml=f'{task_name}:\n  displays: null\n')
        with pytest.raises(DefinitionError) as e:
            _ = parser.tasks_data
        assert e.value.msg == f'Task name format invalid: "{task_name}"'
        assert e.value.file == parser.tasks_file
        assert e.value.keys == []
        assert e.value.value == task_name

    def test_task_name_must_be_a_str(self, fs):
        """A yaml key like `1:` is an int, it must not reach re.match()."""
        parser = make_parser(fs, tasks_yaml='1:\n  displays: null\n')
        with pytest.raises(DefinitionError) as e:
            _ = parser.tasks_data
        assert e.value.msg == 'Task name format invalid: "1"'
        assert e.value.file == parser.tasks_file
        assert e.value.keys == []
        assert e.value.value == 1

    def test_name_error_comes_before_the_structure_error(self, fs):
        """A misspelled task name is reported before the body is checked."""
        parser = make_parser(fs, tasks_yaml="""\
task:
  grops: {}
""")
        with pytest.raises(DefinitionError) as e:
            _ = parser.tasks_data
        assert e.value.msg == 'Task name format invalid: "task"'
        assert e.value.value == 'task'


class TestTasksDataError:
    @pytest.mark.parametrize('body, msg', [
        ('', 'Expected `object`, got `null` - at `$[...]`'),
        ('  - Group\n', 'Expected `object`, got `array` - at `$[...]`'),
        (' Group\n', 'Expected `object`, got `str` - at `$[...]`'),
    ], ids=['null', 'list', 'str'])
    def test_task_body_must_be_a_mapping(self, fs, body, msg):
        parser = make_parser(fs, tasks_yaml=f'Task:\n{body}')
        with pytest.raises(DefinitionError) as e:
            _ = parser.tasks_data
        assert e.value.msg == msg
        assert e.value.file == parser.tasks_file
        # msgspec reports the task key as a `...` placeholder
        assert e.value.keys == ['...']
        assert 'value=' not in str(e.value)

    def test_unknown_field_gets_file(self, fs):
        parser = make_parser(fs, tasks_yaml="""\
Task:
  grops: {}
""")
        with pytest.raises(DefinitionError) as e:
            _ = parser.tasks_data
        assert e.value.msg == 'Object contains unknown field `grops` - at `$[...]`'
        assert e.value.file == parser.tasks_file
        assert e.value.keys == ['...', 'grops']
        assert 'value=' not in str(e.value)

    def test_group_error_gets_file(self, fs):
        parser = make_parser(fs, tasks_yaml="""\
Task:
  groups:
    group:
""")
        with pytest.raises(DefinitionError) as e:
            _ = parser.tasks_data
        assert e.value.msg == 'Group name format invalid: "group"'
        assert e.value.file == parser.tasks_file
        assert e.value.keys == ['Task', 'groups']
        assert e.value.value == 'group'

    def test_display_error_gets_file(self, fs):
        parser = make_parser(fs, tasks_yaml="""\
Task:
  displays: nothing
""")
        with pytest.raises(DefinitionError) as e:
            _ = parser.tasks_data
        assert e.value.msg == 'display should be "flat" or "group" or "null" or a list of cards'
        assert e.value.file == parser.tasks_file
        assert e.value.keys == ['Task', 'displays']
        assert e.value.value == 'nothing'

    def test_scheduler_error_gets_file(self, fs):
        parser = make_parser(fs, tasks_yaml="""\
Task:
  groups:
    Scheduler:
      task: Other
""")
        with pytest.raises(DefinitionError) as e:
            _ = parser.tasks_data
        assert e.value.msg == 'Task should not reference scheduler of another task'
        assert e.value.file == parser.tasks_file
        assert e.value.keys == ['Task', 'groups']
        assert e.value.value == 'Scheduler'

    def test_error_is_not_cached(self, fs):
        parser = make_parser(fs, tasks_yaml="""\
Task:
  grops: {}
""")
        for _ in range(2):
            with pytest.raises(DefinitionError):
                _ = parser.tasks_data
        assert 'tasks_data' not in parser.__dict__


class TestErrorLocation:
    """
    The whole file is converted with msgspec, whose mapping errors carry a
    `$[...]` placeholder. Both the keys and the message keep the placeholder
    as msgspec reports it: it points at the broken field, and resolving it to
    the task and the group name is left to msgspecerror.
    """

    @pytest.mark.parametrize('tasks_yaml, keys, msg', [
        (
            'Task:\n  groups:\n    Group: 1\n',
            ['...', 'groups', '...'],
            'Expected `str | object | null`, got `int` - at `$[...].groups[...]`',
        ),
        (
            'Task:\n  groups:\n    Group:\n      task: 1\n',
            ['...', 'groups', '...', 'task'],
            'Expected `str`, got `int` - at `$[...].groups[...].task`',
        ),
        (
            'Task:\n  groups:\n    Group:\n      task: Task\n      model: Base\n',
            ['...', 'groups', '...', 'model'],
            'Object contains unknown field `model` - at `$[...].groups[...]`',
        ),
        (
            'Task:\n  groups: [1]\n',
            ['...', 'groups'],
            'Expected `object | null`, got `array` - at `$[...].groups`',
        ),
        (
            'Task:\n  displays:\n    - [{info: 1}]\n',
            ['...', 'displays', 0, 0, 'info'],
            'Expected `str`, got `int` - at `$[...].displays[0][0].info`',
        ),
        (
            'Task:\n  displays:\n    - {task: Device, group: Emulator}\n',
            ['...', 'displays', 0],
            'Expected `str | array`, got `object` - at `$[...].displays[0]`',
        ),
        (
            'Task:\n  displays: 0\n',
            ['...', 'displays'],
            'Expected `str | array | null`, got `int` - at `$[...].displays`',
        ),
        (
            'Task:\n  global_bind: "false"\n',
            ['...', 'global_bind'],
            'Expected `bool`, got `str` - at `$[...].global_bind`',
        ),
    ], ids=[
        'group-value', 'group-field', 'group-unknown-field', 'groups-type',
        'card-item-field', 'card-type', 'displays-type', 'global-bind',
    ])
    def test_location(self, fs, tasks_yaml, keys, msg):
        parser = make_parser(fs, tasks_yaml=tasks_yaml)
        with pytest.raises(DefinitionError) as e:
            _ = parser.tasks_data
        assert e.value.msg == msg
        assert e.value.file == parser.tasks_file
        assert e.value.keys == keys
        assert 'value=' not in str(e.value)

    def test_location_of_the_second_task(self, fs):
        parser = make_parser(fs, tasks_yaml="""\
Good:
  groups:
    Group:
Bad:
  groups:
    Group: 1
""")
        with pytest.raises(DefinitionError) as e:
            _ = parser.tasks_data
        # the task name is the placeholder, not `Bad`
        assert e.value.keys == ['...', 'groups', '...']

    def test_an_unencodable_value_is_still_reported(self, fs):
        """
        The location does not depend on the value being walkable, so a value
        that msgpack can not hold is reported like any other.
        """
        parser = make_parser(fs, tasks_yaml="""\
Task:
  groups:
    Group: 1000000000000000000000000000000000000000000
""")
        with pytest.raises(DefinitionError) as e:
            _ = parser.tasks_data
        assert e.value.msg == 'Expected `str | object | null`, got `int` - at `$[...].groups[...]`'
        assert e.value.file == parser.tasks_file
        assert e.value.keys == ['...', 'groups', '...']
        assert 'value=' not in str(e.value)


class TestDisplayFormat:
    @pytest.mark.parametrize('card, msg', [
        (
            '  - {task: Device, group: Emulator}\n',
            'Expected `str | array`, got `object` - at `$[...].displays[0]`',
        ),
        (
            '  - info: Fleet\n    groups: [Group]\n',
            'Expected `str | array`, got `object` - at `$[...].displays[0]`',
        ),
        ('  - 1\n', 'Expected `str | array`, got `int` - at `$[...].displays[0]`'),
    ], ids=['group-ref', 'info-with-groups', 'int'])
    def test_a_card_is_a_list_or_a_group_name(self, fs, card, msg):
        """
        A card is a list of items or the name of its only group. The pre-list
        format is rejected: a mapping is neither.
        """
        parser = make_parser(fs, tasks_yaml=f'Task:\n  displays:\n{card}')
        with pytest.raises(DefinitionError) as e:
            _ = parser.tasks_data
        assert e.value.msg == msg
        assert e.value.file == parser.tasks_file
        assert e.value.keys == ['...', 'displays', 0]
        assert 'value=' not in str(e.value)

    def test_a_group_name_is_a_card_of_one_group(self, fs):
        parser = make_parser(fs, tasks_yaml="""\
Task:
  groups:
    Scheduler:
    Group:
  displays:
    - Group
""")
        task = parser.tasks_data['Task']
        assert list(task.displays) == ['card-Task-Group']
        card = task.displays['card-Task-Group']
        assert card.raw_info == 'Group'
        assert list(card.groups) == ['Group']

    @pytest.mark.parametrize('card', [
        '  - Group\n',
        '  - [ Group ]\n',
    ], ids=['bare', 'list'])
    def test_a_group_name_equals_a_one_item_list(self, fs, card):
        parser = make_parser(fs, tasks_yaml=f'Task:\n  groups:\n    Group:\n  displays:\n{card}')
        card = parser.tasks_data['Task'].displays['card-Task-Group']
        assert card == DisplayCard.from_card('Task', ['Group'])

    def test_an_invalid_group_name_is_reported(self, fs):
        parser = make_parser(fs, tasks_yaml="""\
Task:
  groups:
    Group:
  displays:
    - group
""")
        with pytest.raises(DefinitionError) as e:
            _ = parser.tasks_data
        assert e.value.msg == 'Group name format invalid: "group"'
        assert e.value.file == parser.tasks_file
        assert e.value.keys == ['Task', 'displays']
        assert e.value.value == 'group'


class TestTasksDataYamlQuirks:
    def test_duplicate_task_key_keeps_the_last(self, fs):
        """
        PyYAML keeps the last duplicate key, so the parser never sees the
        first definition and can not report it.
        """
        parser = make_parser(fs, tasks_yaml="""\
Task:
  groups:
    First:
Task:
  groups:
    Second:
""")
        task = parser.tasks_data['Task']
        assert list(task.groups) == ['Second']

    def test_duplicate_group_key_keeps_the_last(self, fs):
        parser = make_parser(fs, tasks_yaml="""\
Task:
  groups:
    Group:
    Group: Variant
""")
        task = parser.tasks_data['Task']
        assert list(task.groups) == ['Group']
        assert task.groups['Group'].model == 'Variant'


class TestRealConfigs:
    """
    The tasks yaml of the repository itself, parsed through the real file
    loader. A format change that breaks the shipped configs fails here instead
    of in the generator.
    """

    @staticmethod
    def parse(file):
        """
        Args:
            file (str): Path to a {nav}.args.yaml of the repository

        Returns:
            dict[str, TaskData]: Parsed tasks
        """
        return ParseTasks(PathStr.new(file)).tasks_data

    def test_alasio_device(self):
        tasks = self.parse(env.ALASIO_ROOT.joinpath('alasio/config/alasio/device.args.yaml'))
        assert list(tasks) == ['Device', 'RestartDevice', 'RestartGame']
        assert tasks['Device'].global_bind is True
        assert list(tasks['Device'].groups) == ['Emulator', 'EmulatorInfo', 'Error', 'Optimization']
        assert tasks['Device'].displays == {}
        assert tasks['RestartDevice'].has_scheduler is True
        assert tasks['RestartDevice'].groups['Scheduler'].model == 'SchedulerStatic'

    def test_example_mod_main(self):
        tasks = self.parse(
            env.ALASIO_ROOT.joinpath('ExampleMod/module/config/main/main.args.yaml'))
        task = tasks['Main']
        assert task.has_scheduler is True
        assert task.groups['Scheduler'].model == 'SchedulerUedit'
        assert list(task.displays) == [
            'card-Main-Main',
            'card-Main-Campaign',
            'card-Main-StopCondition',
            'card-Main-Fleet',
            'card-Main-Emotion',
            'card-Main-Submarine',
            'card-Main-HpControl',
            'card-Main-EnemyPriority',
            'card-Main-Hard',
        ]
        fleet = task.displays['card-Main-Fleet']
        assert fleet.raw_info == 'Fleet'
        assert list(fleet.groups) == ['Fleet1', 'Fleet2']

    def test_example_mod_alas(self):
        tasks = self.parse(
            env.ALASIO_ROOT.joinpath('ExampleMod/module/config/alas/alas.args.yaml'))
        task = tasks['Alas']
        assert task.global_bind is True
        assert list(task.displays) == [
            'card-Alas-Game',
            'card-Alas-Emulator',
            'card-Alas-EmulatorInfo',
            'card-Alas-Error',
            'card-Alas-Optimization',
        ]
        assert task.displays['card-Alas-Emulator'].groups['Emulator'].task == 'Device'

        display = tasks['RestartDeviceDisplay']
        assert display.groups == {}
        card = display.displays['card-RestartDeviceDisplay-RestartDevice']
        assert card.raw_info == 'RestartDevice'
        assert list(card.groups) == ['Scheduler']
        assert card.groups['Scheduler'].task == 'RestartDevice'
