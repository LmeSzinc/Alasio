"""
Tests of TaskDefinition and TaskData.from_task_definition().

TaskDefinition is the input schema of one task of {nav}.tasks.yaml: msgspec
checks the structure, the value types and the unknown fields. TaskData is the
runtime form: it validates names, resolves the implied group models and builds
the display cards. The parser back reference is runtime state, not a field, so
a TaskData stays encodable.
"""

import msgspec
import pytest

from alasio.config_dev.parse.base import DefinitionError
from alasio.config_dev.parse.parse_tasks import CardItem, TaskData, TaskDefinition, TaskGroup, TaskGroupRef
from tests.config_dev.parse.parse_tasks.helpers import make_definition, make_task


def definition(**kwargs):
    """
    Build a TaskDefinition from field values.

    Args:
        **kwargs: Field values of the definition

    Returns:
        TaskDefinition: Decoded definition
    """
    return msgspec.convert(kwargs, type=TaskDefinition)


class TestTaskDefinition:
    def test_fields(self):
        assert TaskDefinition.__struct_fields__ == ('groups', 'displays', 'global_bind')

    def test_defaults(self):
        obj = definition()
        assert obj.groups == {}
        assert obj.displays == 'flat'
        assert obj.global_bind is False

    def test_groups_forms(self):
        obj = definition(groups={
            'Base': None,
            'Variant': 'Other',
            'Cross': {'task': 'OtherTask'},
            'Empty': {},
        })
        assert obj.groups == {
            'Base': None,
            'Variant': 'Other',
            'Cross': TaskGroupRef(task='OtherTask'),
            'Empty': TaskGroupRef(),
        }

    def test_groups_null(self):
        """`groups:` with no value is an empty mapping, not an error."""
        assert definition(groups=None).groups is None

    @pytest.mark.parametrize('groups', [[], '', 0, False, 'Group', ['Group']], ids=[
        'list', 'empty-str', 'zero', 'false', 'str', 'list-of-group',
    ])
    def test_groups_must_be_a_mapping(self, groups):
        with pytest.raises(msgspec.ValidationError):
            definition(groups=groups)

    @pytest.mark.parametrize('value', [0, 1, 1.5, True, False, [], ['Group']], ids=[
        'zero', 'int', 'float', 'true', 'false', 'empty-list', 'list',
    ])
    def test_group_value_types(self, value):
        with pytest.raises(msgspec.ValidationError):
            definition(groups={'Group': value})

    def test_group_key_must_be_a_str(self):
        """A yaml key like `1:` is an int, it must not become a group name."""
        with pytest.raises(msgspec.ValidationError):
            definition(groups={1: None})

    def test_displays_short_forms(self):
        for displays in ('flat', 'group', 'none', '', None):
            obj = definition(displays=displays)
            assert obj.displays == displays

    def test_displays_cards(self):
        obj = definition(displays=[
            [{'info': 'Fleet'}, 'Fleet1', 'Fleet2'],
            [{'task': 'Commission', 'group': 'Preset'}],
            ['Scheduler'],
        ])
        assert obj.displays == [
            [CardItem(info='Fleet'), 'Fleet1', 'Fleet2'],
            [CardItem(group='Preset', task='Commission')],
            ['Scheduler'],
        ]

    @pytest.mark.parametrize('displays', [0, 1, False, True, 1.5, {}], ids=[
        'zero', 'int', 'false', 'true', 'float', 'dict',
    ])
    def test_displays_types(self, displays):
        with pytest.raises(msgspec.ValidationError):
            definition(displays=displays)

    def test_card_must_be_a_list(self):
        """A card is a list of items, a bare group name is not a card."""
        with pytest.raises(msgspec.ValidationError):
            definition(displays=['Group'])

    @pytest.mark.parametrize('item', [1, None, ['Group'], True], ids=[
        'int', 'null', 'list', 'bool',
    ])
    def test_card_item_types(self, item):
        with pytest.raises(msgspec.ValidationError):
            definition(displays=[[item]])

    @pytest.mark.parametrize('item', [
        {'info': 1}, {'group': 1}, {'task': 1}, {'info': None}, {'group': None},
    ], ids=['info-int', 'group-int', 'task-int', 'info-null', 'group-null'])
    def test_card_item_field_types(self, item):
        with pytest.raises(msgspec.ValidationError):
            definition(displays=[[item]])

    @pytest.mark.parametrize('data', [
        {'grops': {}},
        {'groups': {'Group': {'model': 'Base'}}},
        {'groups': {'Group': {'group': 'Group'}}},
        {'displays': [[{'info': 'Fleet', 'groups': 'Group'}]]},
        {'displays': [[{'group': 'Group', 'bogus': 1}]]},
    ], ids=['task', 'group-ref', 'group-ref-group', 'card-groups', 'card-bogus'])
    def test_unknown_fields(self, data):
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert(data, type=TaskDefinition)

    @pytest.mark.parametrize('value', ['true', 'false', 0, 1, None, [], {}], ids=[
        'true-str', 'false-str', 'zero', 'one', 'null', 'list', 'dict',
    ])
    def test_global_bind_must_be_a_bool(self, value):
        with pytest.raises(msgspec.ValidationError):
            definition(global_bind=value)


class TestTaskDataBasic:
    def test_minimal(self):
        task = TaskData.from_task_definition('Task', definition())
        assert task == TaskData(task='Task')
        assert task.groups == {}
        assert task.displays == {}
        assert task.global_bind is False

    def test_task_name_is_coerced_to_str(self):
        """ParseTasks validates the yaml key before calling this method."""
        assert TaskData.from_task_definition(1, definition()).task == '1'

    def test_default_collections_are_not_shared(self):
        task1 = TaskData.from_task_definition('Task1', definition())
        task2 = TaskData.from_task_definition('Task2', definition())
        task1.groups['Group'] = TaskGroup(task='Task1', group='Group', model='Group')
        assert task2.groups == {}
        assert task2.displays == {}

    def test_definition_is_not_mutated(self):
        obj = make_definition("""\
groups:
  Base:
  Variant: Other
displays:
  - [{info: Fleet}, Base]
global_bind: true
""")
        snapshot = msgspec.to_builtins(obj)
        TaskData.from_task_definition('Task', obj)
        assert msgspec.to_builtins(obj) == snapshot


class TestGroups:
    def test_groups_default_to_empty(self):
        assert TaskData.from_task_definition('Task', definition()).groups == {}

    def test_empty_groups(self):
        assert TaskData.from_task_definition('Task', definition(groups={})).groups == {}

    def test_null_groups_is_empty(self):
        """`groups:` with no value is an empty mapping."""
        assert TaskData.from_task_definition('Task', definition(groups=None)).groups == {}

    @pytest.mark.parametrize('group_name', ['group', 'Group_1', '1Group', 'Group-1'], ids=[
        'lower', 'underscore', 'digit-first', 'dash',
    ])
    def test_group_name_format(self, group_name):
        with pytest.raises(DefinitionError) as e:
            TaskData.from_task_definition('Task', definition(groups={group_name: None}))
        assert e.value.msg == f'Group name format invalid: "{group_name}"'
        assert e.value.keys == ['Task', 'groups']
        assert e.value.value == group_name

    def test_groups_keep_yaml_order(self):
        task = make_task("""\
groups:
  Beta:
  Alpha:
""")
        assert list(task.groups) == ['Beta', 'Alpha']

    def test_group_references(self):
        task = make_task("""\
groups:
  Base:
  Variant: Other
  Cross:
    task: OtherTask
""")
        assert task.groups['Base'] == TaskGroup(task='Task', group='Base', model='Base')
        assert task.groups['Variant'] == TaskGroup(task='Task', group='Variant', model='Other')
        assert task.groups['Cross'] == TaskGroup(task='OtherTask', group='Cross', model='')

    def test_cross_task_model_is_not_resolved(self):
        task = TaskData.from_task_definition(
            'Task', definition(groups={'Group': {'task': 'Other'}}))
        assert task.groups['Group'].model == ''

    def test_dict_reference_to_itself_is_basic(self):
        """
        The MRO build looks up the same reference again for a self reference,
        so the model has to be the group name here.
        """
        task = TaskData.from_task_definition(
            'Task', definition(groups={'Group': {'task': 'Task'}}))
        assert task.groups['Group'] == TaskGroup(task='Task', group='Group', model='Group')

    def test_empty_dict_reference_is_basic(self):
        task = TaskData.from_task_definition('Task', definition(groups={'Group': {}}))
        assert task.groups['Group'] == TaskGroup(task='Task', group='Group', model='Group')

    def test_model_format(self):
        with pytest.raises(DefinitionError) as e:
            TaskData.from_task_definition('Task', definition(groups={'Group': 'base'}))
        assert e.value.msg == 'Group model format invalid: "base"'
        assert e.value.keys == ['Task', 'groups', 'Group']
        assert e.value.value == 'base'

    def test_ref_task_format(self):
        with pytest.raises(DefinitionError) as e:
            TaskData.from_task_definition(
                'Task', definition(groups={'Group': {'task': 'other'}}))
        assert e.value.msg == 'Task name format invalid: "other"'
        assert e.value.keys == ['Task', 'groups', 'Group']
        assert e.value.value == 'other'


class TestDisplays:
    def test_default_is_flat(self):
        task = make_task("""\
groups:
  Scheduler:
  Group:
""")
        assert list(task.displays) == ['card-Task-Group']
        card = task.displays['card-Task-Group']
        assert card.raw_info == 'Group'
        assert list(card.groups) == ['Scheduler', 'Group']

    def test_explicit_flat(self):
        task = make_task("""\
groups:
  Group1:
  Group2:
displays: flat
""")
        assert list(task.displays) == ['card-Task-Group1']
        assert list(task.displays['card-Task-Group1'].groups) == ['Group1', 'Group2']

    def test_flat_without_groups(self):
        assert TaskData.from_task_definition('Task', definition()).displays == {}
        assert TaskData.from_task_definition('Task', definition(groups=None)).displays == {}

    def test_group_short_form(self):
        task = make_task("""\
groups:
  Scheduler:
  Group:
displays: group
""")
        assert list(task.displays) == ['card-Task-Scheduler', 'card-Task-Group']
        assert task.displays['card-Task-Scheduler'].raw_info == 'Scheduler'
        assert list(task.displays['card-Task-Scheduler'].groups) == ['Scheduler']
        assert task.displays['card-Task-Group'].raw_info == 'Group'
        assert list(task.displays['card-Task-Group'].groups) == ['Group']

    def test_group_short_form_without_groups(self):
        task = TaskData.from_task_definition('Task', definition(displays='group'))
        assert task.displays == {}

    @pytest.mark.parametrize('displays', [None, 'none', 'None', ''], ids=[
        'null', 'none', 'capital-none', 'empty-str',
    ])
    def test_empty_display_forms(self, displays):
        task = TaskData.from_task_definition(
            'Task', definition(groups={'Group': None}, displays=displays))
        assert task.displays == {}

    @pytest.mark.parametrize('displays', ['Flat', 'groups', 'flat ', 'cards', 'list'], ids=[
        'capital-flat', 'plural', 'trailing-space', 'cards', 'list',
    ])
    def test_invalid_short_form(self, displays):
        obj = definition().groups
        # the schema accepts any str, the meaning is checked here
        task_definition = msgspec.convert(
            {'groups': obj, 'displays': displays}, type=TaskDefinition)
        with pytest.raises(DefinitionError) as e:
            TaskData.from_task_definition('Task', task_definition)
        assert e.value.msg == 'display should be "flat" or "group" or "null" or a list of cards'
        assert e.value.keys == ['Task', 'displays']
        assert e.value.value == displays

    def test_list_of_cards(self):
        task = make_task("""\
groups:
  Group1:
  Group2:
  Group3:
displays:
  - [Group1]
  - [Group2, Group3]
  - [{info: Fleet}, Group1, Group2]
""")
        assert list(task.displays) == [
            'card-Task-Group1',
            'card-Task-Group2',
            'card-Task-Fleet',
        ]
        assert list(task.displays['card-Task-Group1'].groups) == ['Group1']
        assert list(task.displays['card-Task-Group2'].groups) == ['Group2', 'Group3']
        assert list(task.displays['card-Task-Fleet'].groups) == ['Group1', 'Group2']

    def test_empty_list(self):
        assert TaskData.from_task_definition('Task', definition(displays=[])).displays == {}

    def test_duplicate_cards_get_an_index(self):
        task = make_task("""\
groups:
  Group:
displays:
  - [Group]
  - [Group]
""")
        assert list(task.displays) == ['card-Task-Group', 'card-Task-Group2']

    def test_info_only_card(self):
        task = make_task("""\
displays:
  - [{info: Fleet}]
""")
        assert list(task.displays) == ['card-Task-Fleet']
        assert task.displays['card-Task-Fleet'].groups == {}

    def test_cross_task_display(self):
        task = make_task("""\
displays:
  - [{task: Other, group: Group}]
""")
        assert list(task.displays) == ['card-Task-Group']
        assert task.displays['card-Task-Group'].groups['Group'].task == 'Other'

    def test_display_group_is_not_bound_to_the_task(self):
        """
        Cross-file checks (does the group exist, does the model exist) belong
        to the MRO build, this parser only builds the reference.
        """
        task = make_task("""\
groups:
  Group1:
displays:
  - [Group2]
""")
        assert task.displays['card-Task-Group2'].groups['Group2'].group == 'Group2'

    def test_empty_card(self):
        with pytest.raises(DefinitionError) as e:
            TaskData.from_task_definition('Task', definition(displays=[[]]))
        assert e.value.msg == 'Display card must have at least one group or an info'
        assert e.value.keys == ['Task', 'displays']
        assert e.value.value == []

    def test_duplicate_group_in_card(self):
        with pytest.raises(DefinitionError) as e:
            TaskData.from_task_definition('Task', definition(displays=[['Group', 'Group']]))
        assert e.value.msg == 'Duplicate group to display: "Group"'
        assert e.value.keys == ['Task', 'displays']
        assert e.value.value == TaskGroup(task='Task', group='Group', model='')


class TestGlobalBind:
    def test_default_is_false(self):
        assert TaskData.from_task_definition('Task', definition()).global_bind is False

    def test_true(self):
        assert TaskData.from_task_definition(
            'Task', definition(global_bind=True)).global_bind is True

    def test_false(self):
        assert TaskData.from_task_definition(
            'Task', definition(global_bind=False)).global_bind is False

    def test_yaml_boolean(self):
        task = make_task("""\
global_bind: yes
""")
        assert task.global_bind is True


class TestHasScheduler:
    def test_no_groups(self):
        assert TaskData.from_task_definition('Task', definition()).has_scheduler is False

    def test_no_scheduler(self):
        task = make_task("""\
groups:
  Group:
  Group2:
""")
        assert task.has_scheduler is False

    def test_scheduler(self):
        task = make_task("""\
groups:
  Scheduler:
""")
        assert task.has_scheduler is True

    def test_scheduler_variant(self):
        task = make_task("""\
groups:
  Scheduler: SchedulerStatic
""")
        assert task.has_scheduler is True

    def test_scheduler_prefixed_group_name(self):
        task = make_task("""\
groups:
  SchedulerUedit:
""")
        assert task.has_scheduler is True

    def test_scheduler_from_another_task(self):
        task = make_task("""\
groups:
  Scheduler:
    task: Other
""")
        with pytest.raises(DefinitionError) as e:
            _ = task.has_scheduler
        assert e.value.msg == 'Task should not reference scheduler of another task'
        assert e.value.keys == ['Task', 'groups']
        assert e.value.value == 'Scheduler'

    def test_global_bind_with_scheduler(self):
        task = make_task("""\
global_bind: true
groups:
  Scheduler:
""")
        with pytest.raises(DefinitionError) as e:
            _ = task.has_scheduler
        assert e.value.msg == 'Global bind task should not have group "Scheduler"'
        assert e.value.keys == ['Task', 'groups']
        assert e.value.value == 'Scheduler'

    def test_cross_task_group_is_not_scheduler(self):
        task = make_task("""\
groups:
  Group:
    task: Other
""")
        assert task.has_scheduler is False

    def test_display_only_scheduler_is_not_bound(self):
        """
        has_scheduler describes the bound groups of the task. A card that
        displays another task's Scheduler does not make this task schedulable.
        """
        task = make_task("""\
groups:
  Group:
displays:
  - [Scheduler]
""")
        assert task.has_scheduler is False

    def test_cached_property(self):
        task = make_task("""\
groups:
  Group:
""")
        assert task.has_scheduler is False
        task.groups['Scheduler'] = TaskGroup(task='Task', group='Scheduler', model='Scheduler')
        assert task.has_scheduler is False
        assert 'has_scheduler' in task.__dict__


class TestTaskDataSerialization:
    def test_parser_is_not_a_field(self):
        """The parser back reference is runtime state, it must not be encoded."""
        assert 'parser' not in TaskData.__struct_fields__
        assert TaskData.from_task_definition('Task', definition()).parser is None

    def test_parser_is_kept_out_of_the_encoding(self):
        """
        A task whose parser is set still encodes, and the parser is absent.

        The parser is not a msgspec type, encoding it would fail, so the back
        reference lives in __dict__ instead of being a field.
        """
        task = TaskData.from_task_definition('Task', definition(groups={'Group': None}))
        task.parser = object()
        assert msgspec.json.encode(task) == (
            b'{"task":"Task",'
            b'"groups":{"Group":{"task":"Task","group":"Group","model":"Group"}},'
            b'"displays":{"card-Task-Group":{"task":"Task","raw_info":"Group",'
            b'"groups":{"Group":{"task":"Task","group":"Group","model":"Group"}},"info":""}},'
            b'"global_bind":false}'
        )

    def test_round_trip(self):
        """A decoded task equals the source task, the parser is not restored."""
        task = TaskData.from_task_definition('Task', definition(
            groups={'Base': None, 'Variant': 'Other'},
            displays='group',
            global_bind=True,
        ))
        task.parser = object()
        decoded = msgspec.json.decode(msgspec.json.encode(task), type=TaskData)
        assert decoded == task
        assert decoded.parser is None

    def test_to_builtins(self):
        task = TaskData.from_task_definition('Task', definition(global_bind=True))
        task.parser = object()
        assert msgspec.to_builtins(task) == {
            'task': 'Task', 'groups': {}, 'displays': {}, 'global_bind': True,
        }

    def test_parser_assignment(self):
        task = TaskData.from_task_definition('Task', definition())
        parser = object()
        task.parser = parser
        assert task.parser is parser
        assert task.__dict__ == {'parser': parser}
