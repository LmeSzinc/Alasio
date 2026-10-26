"""
Tests of GroupData construction.

GroupData.from_group_data() is the only entry used by ParseGroups: it validates
the group skeleton, converts every arg through ArgData.from_arg_data() and
normalizes the parent to a tuple. Dashboard population is an explicit step:
build_group_mro() assigns `dashboard` and calls group.populate_dashboard(),
because a msgspec field assignment does not trigger any population hook.

The parser back reference is runtime state, not a field: it lives in
GroupData.__dict__, so a GroupData stays encodable.
"""

import msgspec
import pytest
from msgspec import UNSET

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

    def test_dashboard_is_populated_explicitly(self):
        """Assigning dashboard does not populate args, the call is explicit."""
        group = GroupData(name='Group', args={'Value': ArgData.from_arg_data(0)})
        group.dashboard = 'Progress'
        assert group.args['Value'].ge is UNSET
        group.populate_dashboard()
        assert group.args['Value'].ge == 0

    def test_populate_dashboard_without_dashboard(self):
        group = GroupData(name='Group', args={'Value': ArgData.from_arg_data(0)})
        group.populate_dashboard()
        assert group.args['Value'].ge is UNSET

    def test_parser_is_not_a_field(self):
        """The parser back reference is runtime state, it must not be encoded."""
        assert 'parser' not in GroupData.__struct_fields__


class TestGroupDataSerialization:
    def test_parser_is_kept_out_of_the_encoding(self):
        """
        A group whose parser is set still encodes, and the parser is absent.

        The parser is not a msgspec type, encoding it would fail, so the back
        reference lives in __dict__ instead of being a field.
        """
        group = make_group('Group', """\
parent: [Base]
args:
  Enable: true
""")
        group.parser = object()
        assert msgspec.json.encode(group) == (
            b'{"name":"Group","args":{"Enable":{"dt":"checkbox","value":true}},'
            b'"parent":["Base"],"dashboard_color":"","mro":[],"override_args":{},'
            b'"override_i18n":false,"dashboard":""}'
        )

    def test_round_trip(self):
        """A decoded group equals the source group, the parser is not restored."""
        group = make_group('Group', """\
parent: [Base]
override_i18n: true
args:
  Value: 5
""")
        group.parser = object()
        decoded = msgspec.json.decode(msgspec.json.encode(group), type=GroupData)
        assert decoded == group
        assert decoded.parser is None

    def test_to_builtins(self):
        group = GroupData(name='Group', dashboard_color='#fff')
        group.parser = object()
        assert msgspec.to_builtins(group) == {
            'name': 'Group', 'args': {}, 'parent': (), 'dashboard_color': '#fff',
            'mro': (), 'override_args': {}, 'override_i18n': False, 'dashboard': '',
        }

    def test_parser_is_ignored_on_input(self):
        """`parser` is an internal output, a yaml value is dropped like mro."""
        group = GroupData.from_group_data('Group', {'parser': '/a/main.args.yaml'})
        assert group.parser is None

    def test_parser_assignment(self):
        group = GroupData(name='Group')
        parser = object()
        group.parser = parser
        assert group.parser is parser
        assert group.__dict__ == {'parser': parser}


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

    def test_deprecated_dashboard_input_is_ignored(self):
        """
        The yaml `dashboard:` key is deprecated and dropped.

        A group becomes a dashboard item when its MRO contains a
        "Dashboard{Type}" group, see gen_cross.build_group_mro() and
        tests/config_dev/gen/test_gen_cross_dashboard.py.
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

    @pytest.mark.parametrize('parent', [0, 0.0, False, 1, 1.5, True, {'Base': 1}], ids=[
        'zero', 'float-zero', 'false', 'int', 'float', 'true', 'dict',
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
        """Parent names are validated before msgspec.convert()."""
        with pytest.raises(DefinitionError) as e:
            GroupData.from_group_data('Group', {'parent': ['Base', 1]})
        assert e.value.msg == 'Group parent format invalid: "1"'
        assert e.value.keys == ['Group', 'parent']
        assert e.value.value == 1

    @pytest.mark.parametrize('parent', ['bad-name', 'lower', '1Group'], ids=[
        'dash', 'lower', 'digit-first',
    ])
    def test_parent_name_format(self, parent):
        with pytest.raises(DefinitionError) as e:
            GroupData.from_group_data('Group', {'parent': parent})
        assert e.value.msg == f'Group parent format invalid: "{parent}"'
        assert e.value.keys == ['Group', 'parent']
        assert e.value.value == parent

    def test_parent_duplicate(self):
        with pytest.raises(DefinitionError) as e:
            GroupData.from_group_data('Group', {'parent': ['Base', 'Base']})
        assert e.value.msg == 'Duplicate group parent: "Base"'
        assert e.value.keys == ['Group', 'parent']
        assert e.value.value == ('Base', 'Base')

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

    @pytest.mark.parametrize('color', [
        1, 0, 1.5, True, False, None, [], {},
    ], ids=['int', 'zero', 'float', 'true', 'false', 'none', 'list', 'dict'])
    def test_non_str_color(self, color):
        """A non-str color points at the color, not at the whole group."""
        with pytest.raises(DefinitionError) as e:
            GroupData.from_group_data('Group', {'dashboard_color': color})
        assert e.value.msg == (
            f'Invalid dashboard_color, expects a str, got {type(color).__name__}'
        )
        assert e.value.keys == ['Group', 'dashboard_color']
        assert e.value.value == color


class TestGroupDataUnknownKeys:
    def test_unknown_keys_are_rejected(self):
        """
        A typo such as `parrent` must not silently disable inheritance,
        GroupData forbids unknown fields.
        """
        with pytest.raises(DefinitionError) as e:
            GroupData.from_group_data('Group', {'parrent': 'Base'})
        assert e.value.msg == 'ValidationError(Object contains unknown field `parrent`)'
        assert e.value.keys == ['Group']
        assert e.value.value == {
            'parrent': 'Base', 'args': {}, 'name': 'Group', 'parent': (),
        }

    def test_unknown_arg_field_is_rejected(self):
        with pytest.raises(DefinitionError) as e:
            make_group('Group', """\
args:
  A:
    dt: input
    value: text
    modo: typo
""")
        assert e.value.msg == 'ValidationError(Object contains unknown field `modo`)'
        assert e.value.keys == ['Group', 'A']
