"""
Tests of TaskGroup.from_group() and TaskGroup.from_display().

TaskDefinition has already decoded the yaml into None / str / TaskGroupRef, so
these methods only validate names and fill in the implied model. A group
binding has three forms: None for a basic group, a str for a variant group (the
value is the validation model), and a TaskGroupRef for a cross-task group. A
display reference names a group of the task itself or of another task.
"""

import pytest

from alasio.config_dev.parse.base import DefinitionError
from alasio.config_dev.parse.parse_tasks import TaskGroup, TaskGroupRef


class TestFromGroup:
    def test_none_is_a_basic_group(self):
        group = TaskGroup.from_group('Task', 'Group', None)
        assert group == TaskGroup(task='Task', group='Group', model='Group')

    def test_str_is_a_variant_group(self):
        group = TaskGroup.from_group('Task', 'Group', 'Base')
        assert group == TaskGroup(task='Task', group='Group', model='Base')

    @pytest.mark.parametrize('model', ['', 'base', 'Base_1', '1Base', 'Base-1'], ids=[
        'empty', 'lower', 'underscore', 'digit-first', 'dash',
    ])
    def test_invalid_variant_model(self, model):
        with pytest.raises(DefinitionError) as e:
            TaskGroup.from_group('Task', 'Group', model)
        assert e.value.msg == f'Group model format invalid: "{model}"'
        assert e.value.keys == ['Task', 'groups', 'Group']
        assert e.value.value == model

    def test_ref_is_a_cross_task_group(self):
        group = TaskGroup.from_group('Task', 'Group', TaskGroupRef(task='Other'))
        assert group == TaskGroup(task='Other', group='Group', model='')

    def test_empty_ref_is_a_basic_group(self):
        group = TaskGroup.from_group('Task', 'Group', TaskGroupRef())
        assert group == TaskGroup(task='Task', group='Group', model='Group')

    def test_ref_to_the_task_itself_is_a_basic_group(self):
        """
        The MRO build looks up the same reference again for a self reference,
        so it could never resolve the model of the target group.
        """
        group = TaskGroup.from_group('Task', 'Group', TaskGroupRef(task='Task'))
        assert group == TaskGroup(task='Task', group='Group', model='Group')

    @pytest.mark.parametrize('ref_task', ['other', 'Other_1', '1Other', 'Other-1'], ids=[
        'lower', 'underscore', 'digit-first', 'dash',
    ])
    def test_invalid_ref_task(self, ref_task):
        with pytest.raises(DefinitionError) as e:
            TaskGroup.from_group('Task', 'Group', TaskGroupRef(task=ref_task))
        assert e.value.msg == f'Task name format invalid: "{ref_task}"'
        assert e.value.keys == ['Task', 'groups', 'Group']
        assert e.value.value == ref_task


class TestFromDisplay:
    def test_group_of_the_task(self):
        group = TaskGroup.from_display('Task', 'Group')
        assert group == TaskGroup(task='Task', group='Group', model='')

    def test_group_of_another_task(self):
        group = TaskGroup.from_display('Task', 'Group', 'Other')
        assert group == TaskGroup(task='Other', group='Group', model='')

    def test_model_is_not_resolved(self):
        """The model is resolved by the MRO build, not by the display ref."""
        assert TaskGroup.from_display('Task', 'Group').model == ''

    @pytest.mark.parametrize('group', ['', 'group', 'Group_1', '1Group', 'Group-1'], ids=[
        'empty', 'lower', 'underscore', 'digit-first', 'dash',
    ])
    def test_invalid_group(self, group):
        with pytest.raises(DefinitionError) as e:
            TaskGroup.from_display('Task', group)
        assert e.value.msg == f'Group name format invalid: "{group}"'
        assert e.value.keys == ['Task', 'displays']
        assert e.value.value == group

    @pytest.mark.parametrize('ref_task', ['other', 'Other_1', '1Other', 'Other-1'], ids=[
        'lower', 'underscore', 'digit-first', 'dash',
    ])
    def test_invalid_ref_task(self, ref_task):
        with pytest.raises(DefinitionError) as e:
            TaskGroup.from_display('Task', 'Group', ref_task)
        assert e.value.msg == f'Task name format invalid: "{ref_task}"'
        assert e.value.keys == ['Task', 'displays']
        assert e.value.value == ref_task
