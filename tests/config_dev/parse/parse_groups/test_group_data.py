"""
Tests of GroupData construction.

GroupData.from_group_data() is the only entry used by ParseGroups: it validates
the group skeleton, converts every arg through ArgData.from_arg_data() and
normalizes the parent to a tuple. The dashboard branches of __post_init__ are
covered by the test_dashboard_*.py files, because `dashboard` is an internal
field that from_group_data() ignores.
"""

import pytest

from alasio.config_dev.parse.base import DefinitionError
from alasio.config_dev.parse.parse_args import ArgData
from alasio.config_dev.parse.parse_groups import GroupData
from tests.config_dev.parse.parse_groups.helpers import make_group


class TestGroupDataDefaults:
    def test_defaults(self):
        group = GroupData(name='Group')
        assert group.name == 'Group'
        assert group.args == {}
        assert group.parent == ()
        assert group.dashboard_color == ''
        assert group.parser is None
        assert group.mro == ()
        assert group.override_args == {}
        assert group.override_i18n is False
        assert group.dashboard == ''

    def test_assign_dashboard_after_init_does_not_run_post_init(self):
        """
        __post_init__ runs on construction only.

        The MRO build assigns `dashboard` to an existing object, so the
        dashboard validation can not depend on this assignment.
        """
        group = GroupData(name='Group')
        group.dashboard = 'progress'
        assert group.dashboard == 'progress'
        assert group.args == {}

    def test_invalid_dashboard_datatype(self):
        with pytest.raises(DefinitionError) as e:
            GroupData(name='Group', dashboard='invalid')
        assert e.value.msg == 'Invalid dashboard datatype: "invalid"'
        assert e.value.keys == ['Group', 'dashboard']
        assert e.value.value == 'invalid'


class TestGroupDataFromGroupData:
    def test_empty_dict(self):
        group = GroupData.from_group_data('Group', {})
        assert group.name == 'Group'
        assert group.args == {}
        assert group.parent == ()

    def test_none(self):
        group = GroupData.from_group_data('Group', None)
        assert group.name == 'Group'
        assert group.args == {}

    @pytest.mark.parametrize('data', [[], 0, 0.0, False, ''], ids=[
        'empty-list', 'zero', 'float', 'false', 'empty-str',
    ])
    def test_non_dict_data(self, data):
        """A falsy non-dict is not an empty group."""
        with pytest.raises(DefinitionError) as e:
            GroupData.from_group_data('Group', data)
        assert e.value.msg == 'Group data must be a dict'
        assert e.value.keys == ['Group']
        assert e.value.value == data

    def test_non_dict_data_truthy(self):
        with pytest.raises(DefinitionError) as e:
            GroupData.from_group_data('Group', 'Group')
        assert e.value.msg == 'Group data must be a dict'
        assert e.value.keys == ['Group']
        assert e.value.value == 'Group'

    def test_name_is_taken_from_the_argument(self):
        group = GroupData.from_group_data('Group', {'name': 'Other'})
        assert group.name == 'Group'

    def test_input_data_is_not_mutated(self):
        """from_group_data works on a copy, the yaml data can be reused."""
        data = {'args': {'A': 1}, 'parent': 'Base', 'dashboard': 'progress'}
        GroupData.from_group_data('Group', data)
        assert data == {'args': {'A': 1}, 'parent': 'Base', 'dashboard': 'progress'}

    def test_internal_fields_are_ignored(self):
        """mro / override_args / dashboard are output fields, not input."""
        group = GroupData.from_group_data('Group', {
            'mro': ['X'],
            'override_args': {'A': 1},
            'dashboard': 'value',
        })
        assert group.mro == ()
        assert group.override_args == {}
        assert group.dashboard == ''

    def test_dashboard_input_is_ignored(self):
        """
        The yaml `dashboard:` key is dropped.

        Dashboard groups are detected from the "Dashboard{Type}" group name in
        the MRO build, see doc/2026-10-26_parse-groups-test-findings.md.
        """
        group = make_group('Group', """\
dashboard: progress
args:
  Value: 0
""")
        assert group.dashboard == ''
        assert list(group.args) == ['Value']
        assert group.args['Value'].dt == 'input-int'

    def test_override_i18n(self):
        group = make_group('Group', """\
override_i18n: true
args:
  A: 1
""")
        assert group.override_i18n is True

    def test_parser_default_is_none(self):
        group = make_group('Group', """\
args:
  A: 1
""")
        assert group.parser is None


class TestGroupDataArgs:
    def test_args_are_converted(self):
        group = make_group('Group', """\
args:
  Enable: true
  Mode:
    value: normal
    option: [normal, hard]
  Count: 128
""")
        assert list(group.args) == ['Enable', 'Mode', 'Count']
        assert all(isinstance(arg, ArgData) for arg in group.args.values())
        assert group.args['Enable'].dt == 'checkbox'
        assert group.args['Enable'].value is True
        assert group.args['Mode'].dt == 'select'
        assert group.args['Mode'].value == 'normal'
        assert group.args['Count'].dt == 'input-int'
        assert group.args['Count'].value == 128

    def test_args_order_is_kept(self):
        group = make_group('Group', """\
args:
  B: 1
  A: 2
""")
        assert list(group.args) == ['B', 'A']

    @pytest.mark.parametrize('value', [[], 0, '', 1, True], ids=[
        'empty-list', 'zero', 'empty-str', 'int', 'true',
    ])
    def test_args_must_be_a_dict(self, value):
        with pytest.raises(DefinitionError) as e:
            GroupData.from_group_data('Group', {'args': value})
        assert e.value.msg == 'Group args must be a dict'
        assert e.value.keys == ['Group', 'args']
        assert e.value.value == {'args': value}

    @pytest.mark.parametrize('arg_name', ['group', 'Arg_1', '1Arg', 'Arg-1', ''], ids=[
        'lower', 'underscore', 'digit-first', 'dash', 'empty',
    ])
    def test_arg_name_format(self, arg_name):
        with pytest.raises(DefinitionError) as e:
            GroupData.from_group_data('Group', {'args': {arg_name: 1}})
        assert e.value.msg == f'Arg name format invalid: "{arg_name}"'
        assert e.value.keys == ['Group']
        assert e.value.value == arg_name

    def test_arg_name_must_be_a_str(self):
        """A yaml key like `1:` is an int, it must not reach re.match()."""
        with pytest.raises(DefinitionError) as e:
            GroupData.from_group_data('Group', {'args': {1: 0}})
        assert e.value.msg == 'Arg name format invalid: "1"'
        assert e.value.keys == ['Group']
        assert e.value.value == 1

    def test_arg_error_gets_group_and_arg_keys(self):
        with pytest.raises(DefinitionError) as e:
            make_group('Group', """\
args:
  Value:
    dt: input
    value: [1, 2]
""")
        assert e.value.msg == 'Value of "input-*" datatype must be str/int/float'
        assert e.value.keys == ['Group', 'Value']
        assert e.value.value == {'dt': 'input', 'value': [1, 2]}
        assert e.value.file == ''


class TestGroupDataParent:
    def test_parent_str(self):
        group = make_group('Group', """\
parent: Base
args:
  A: 1
""")
        assert group.parent == ('Base',)

    def test_parent_list(self):
        group = make_group('Group', """\
parent: [Base1, Base2]
args:
  A: 1
""")
        assert group.parent == ('Base1', 'Base2')

    def test_parent_tuple(self):
        group = GroupData.from_group_data('Group', {'parent': ('Base1', 'Base2')})
        assert group.parent == ('Base1', 'Base2')

    @pytest.mark.parametrize('parent', [None, '', [], ()], ids=[
        'none', 'empty-str', 'empty-list', 'empty-tuple',
    ])
    def test_no_parent(self, parent):
        group = GroupData.from_group_data('Group', {'parent': parent})
        assert group.parent == ()

    @pytest.mark.parametrize('parent', [1, 1.5, True, {'Base': 1}], ids=[
        'int', 'float', 'true', 'dict',
    ])
    def test_parent_invalid_scalar(self, parent):
        with pytest.raises(DefinitionError) as e:
            GroupData.from_group_data('Group', {'parent': parent})
        assert e.value.msg == (
            f'Invalid group parent, expected str or list[str], got {parent}'
        )
        assert e.value.keys == ['Group', 'parent']
        assert e.value.value == parent

    def test_parent_invalid_element(self):
        """A list parent is normalized to a tuple before msgspec validates it."""
        with pytest.raises(DefinitionError) as e:
            GroupData.from_group_data('Group', {'parent': ['Base', 1]})
        assert e.value.msg == 'ValidationError(Expected `str`, got `int` - at `$.parent[1]`)'
        assert e.value.keys == ['Group']
        assert e.value.value == {
            'parent': ('Base', 1), 'args': {}, 'name': 'Group',
        }

    def test_parent_cannot_include_self(self):
        with pytest.raises(DefinitionError) as e:
            GroupData.from_group_data('Group', {'parent': ['Group']})
        assert e.value.msg == 'Group parent cannot include group itself'
        assert e.value.keys == ['Group', 'parent']
        assert e.value.value == ('Group',)

    def test_parent_cannot_include_self_among_others(self):
        with pytest.raises(DefinitionError):
            GroupData.from_group_data('Group', {'parent': ['Base', 'Group']})


class TestGroupDataDashboardColor:
    @pytest.mark.parametrize('color', [
        '#fff', '#FFF', '#ffff', '#ffffff', '#ffffffff', '#AbCdEf',
    ])
    def test_valid_color(self, color):
        group = GroupData.from_group_data('Group', {'dashboard_color': color})
        assert group.dashboard_color == color

    @pytest.mark.parametrize('color', [
        'red', '#ff', '#fffff', '#fffffff', '#ggg', ' #fff', '#fff ',
    ])
    def test_invalid_color(self, color):
        with pytest.raises(DefinitionError) as e:
            GroupData.from_group_data('Group', {'dashboard_color': color})
        assert e.value.msg == 'Invalid dashboard_color, expects #RGB #RGBA #RRGGBB #RRGGBBAA'
        assert e.value.keys == ['Group', 'dashboard_color']
        assert e.value.value == color

    def test_empty_color(self):
        group = GroupData.from_group_data('Group', {'dashboard_color': ''})
        assert group.dashboard_color == ''

    def test_non_str_color(self):
        """msgspec rejects a non-str before the color validator sees it."""
        with pytest.raises(DefinitionError) as e:
            GroupData.from_group_data('Group', {'dashboard_color': 1})
        assert e.value.msg == 'ValidationError(Expected `str`, got `int` - at `$.dashboard_color`)'
        assert e.value.keys == ['Group']
        assert e.value.value == {
            'dashboard_color': 1, 'args': {}, 'name': 'Group', 'parent': (),
        }


class TestGroupDataUnknownKeys:
    def test_unknown_keys_are_ignored(self):
        """
        Known limitation: msgspec ignores unknown fields by default, so a typo
        such as `parrent` is silently dropped instead of being reported.
        """
        group = GroupData.from_group_data('Group', {'parrent': 'Base'})
        assert group.parent == ()
