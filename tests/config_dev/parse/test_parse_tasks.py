"""
Tests of TaskData construction.

Unlike GroupData / ArgData, {nav}.tasks.yaml is parsed by hand, so
forbid_unknown_fields on the structs is a declaration only. The explicit key
checks in the from_* classmethods are the enforcement.

The parser back reference is runtime state, not a field: it lives in
TaskData.__dict__, so a TaskData stays encodable.
"""

import msgspec
import pytest

from alasio.config_dev.parse.base import DefinitionError
from alasio.config_dev.parse.parse_tasks import ParseTasks, TaskData
from alasio.ext.path import PathStr
from alasio.testing.filesystem import fs  # noqa: F401


class TestTaskData:
    def test_minimal(self):
        task = TaskData.from_task_data('Task', {})
        assert task.task == 'Task'
        assert task.groups == {}
        assert task.global_bind is False

    def test_global_bind(self):
        task = TaskData.from_task_data('Task', {'global_bind': True})
        assert task.global_bind is True

    def test_parser_is_not_a_field(self):
        """The parser back reference is runtime state, it must not be encoded."""
        assert 'parser' not in TaskData.__struct_fields__
        assert TaskData.from_task_data('Task', {}).parser is None

    def test_group_references(self):
        task = TaskData.from_task_data('Task', {
            'groups': {
                'Base': None,
                'Variant': 'Other',
                'Cross': {'task': 'OtherTask'},
            },
        })
        assert task.groups['Base'].task == 'Task'
        assert task.groups['Base'].model == 'Base'
        assert task.groups['Variant'].model == 'Other'
        assert task.groups['Cross'].task == 'OtherTask'
        # model is resolved in MRO build for cross-task references
        assert task.groups['Cross'].model == ''

    def test_display_group_reference(self):
        task = TaskData.from_task_data('Task', {
            'groups': {'G': None},
            'displays': [{'task': 'Other', 'group': 'G'}],
        })
        card = task.displays['card-Task-G']
        assert card.raw_info == 'G'
        assert card.groups['G'].task == 'Other'
        assert card.groups['G'].group == 'G'

    def test_display_card_with_groups(self):
        task = TaskData.from_task_data('Task', {
            'groups': {'G': None},
            'displays': [{'info': 'G', 'groups': ['G']}],
        })
        card = task.displays['card-Task-G']
        assert card.raw_info == 'G'
        assert list(card.groups) == ['G']

    @pytest.mark.parametrize('displays, expected', [
        ('flat', ['card-Task-G']),
        ('group', ['card-Task-G']),
        (None, []),
        ('none', []),
    ])
    def test_short_display_forms(self, displays, expected):
        task = TaskData.from_task_data('Task', {
            'groups': {'G': None},
            'displays': displays,
        })
        assert list(task.displays) == expected


class TestTaskDataSerialization:
    def test_parser_is_kept_out_of_the_encoding(self):
        """
        A task whose parser is set still encodes, and the parser is absent.

        The parser is not a msgspec type, encoding it would fail, so the back
        reference lives in __dict__ instead of being a field.
        """
        task = TaskData.from_task_data('Task', {'groups': {'G': None}})
        task.parser = object()
        assert msgspec.json.encode(task) == (
            b'{"task":"Task",'
            b'"groups":{"G":{"task":"Task","group":"G","model":"G"}},'
            b'"displays":{"card-Task-G":{"task":"Task","raw_info":"G",'
            b'"groups":{"G":{"task":"Task","group":"G","model":"G"}},"info":""}},'
            b'"global_bind":false}'
        )

    def test_round_trip(self):
        """A decoded task equals the source task, the parser is not restored."""
        task = TaskData.from_task_data('Task', {
            'groups': {'Base': None, 'Variant': 'Other'},
            'displays': 'group',
            'global_bind': True,
        })
        task.parser = object()
        decoded = msgspec.json.decode(msgspec.json.encode(task), type=TaskData)
        assert decoded == task
        assert decoded.parser is None

    def test_to_builtins(self):
        task = TaskData.from_task_data('Task', {'global_bind': True})
        task.parser = object()
        assert msgspec.to_builtins(task) == {
            'task': 'Task', 'groups': {},
            'displays': {'card-Task-': {
                'task': 'Task', 'raw_info': '', 'groups': {}, 'info': ''}},
            'global_bind': True,
        }

    def test_parser_is_rejected_on_input(self):
        """`parser` is an internal output, it is not an allowed yaml key."""
        with pytest.raises(DefinitionError) as e:
            TaskData.from_task_data('Task', {'parser': '/a/main.tasks.yaml'})
        assert e.value.msg == "Unknown task field: 'parser'"
        assert e.value.keys == ['Task']

    def test_parser_assignment(self):
        task = TaskData.from_task_data('Task', {})
        parser = object()
        task.parser = parser
        assert task.parser is parser
        assert task.__dict__ == {'parser': parser}


class TestTaskDataUnknownFields:
    def test_unknown_task_field(self):
        with pytest.raises(DefinitionError) as e:
            TaskData.from_task_data('Task', {'grops': {}})
        assert e.value.msg == "Unknown task field: 'grops'"
        assert e.value.keys == ['Task']
        assert e.value.value == {'grops': {}}

    def test_unknown_group_reference_field(self):
        with pytest.raises(DefinitionError) as e:
            TaskData.from_task_data('Task', {
                'groups': {'G': {'task': 'Other', 'modle': 'X'}},
            })
        assert e.value.msg == "Unknown group reference field: 'modle'"
        assert e.value.keys == ['Task', 'groups', 'G']
        assert e.value.value == {'task': 'Other', 'modle': 'X'}

    def test_unknown_display_field(self):
        with pytest.raises(DefinitionError) as e:
            TaskData.from_task_data('Task', {
                'groups': {'G': None},
                'displays': [{'info': 'G', 'group': 'G'}],
            })
        assert e.value.msg == "Unknown display field: 'group'"
        assert e.value.keys == ['Task', 'displays']

    def test_unknown_display_group_field(self):
        with pytest.raises(DefinitionError) as e:
            TaskData.from_task_data('Task', {
                'groups': {'G': None},
                'displays': [{'task': 'Other', 'grop': 'G'}],
            })
        assert e.value.msg == "Unknown display group field: 'grop'"
        assert e.value.keys == ['Task', 'displays']


class TestParseTasks:
    def test_unknown_field_gets_file(self, fs):
        fs.create_file('/project/module/config/main/main.args.yaml', contents='')
        fs.create_file('/project/module/config/main/main.tasks.yaml', contents="""\
Task:
  grops: {}
""")
        parser = ParseTasks(PathStr.new('/project/module/config/main/main.args.yaml'))
        with pytest.raises(DefinitionError) as e:
            _ = parser.tasks_data
        assert e.value.msg == "Unknown task field: 'grops'"
        assert e.value.file == parser.tasks_file
        assert e.value.keys == ['Task']
