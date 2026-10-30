"""
Tests of ParseGroups groups_data construction.

ParseGroups reads {nav}.args.yaml through ParseBase.read_args_yaml() and builds
one GroupData per group. Cross-group validation (parents exist, MRO, args merge)
is not part of this module, it lives in the gen_cross MRO build; here only the
per-file structure is checked.
"""

import pytest

from alasio.config_dev.parse.base import DefinitionError, ParseBase, args_yaml_postprocess
from alasio.config_dev.parse.parse_groups import GroupData, ParseGroups
from alasio.ext.path import PathStr
from alasio.ext.path.calc import joinpath
from alasio.testing.filesystem import fs  # noqa: F401
from tests.config_dev.parse.parse_groups.helpers import make_parser


class TestParseGroupsInit:
    def test_file_and_nav_name(self, fs):
        parser = make_parser(fs, 'main')
        assert parser.nav_name == 'main'
        assert parser.file.name == 'main.args.yaml'

    def test_derived_files(self, fs):
        parser = make_parser(fs, 'main')
        root = fs.root_dir.path
        assert parser.tasks_file == joinpath(root, 'module/config/main/main.tasks.yaml')
        assert parser.i18n_file == joinpath(root, 'module/config/main/main_i18n.json')
        assert parser.config_file == joinpath(root, 'module/config/main/main_config.json')
        assert parser.model_file == joinpath(root, 'module/config/main/main_model.py')

    def test_invalid_suffix(self):
        with pytest.raises(DefinitionError) as e:
            ParseGroups(PathStr.new('/project/module/config/main/main.yaml'))
        assert e.value.msg == 'Arg file must endswith ".args.yaml"'

    def test_invalid_nav_name(self):
        with pytest.raises(DefinitionError) as e:
            ParseGroups(PathStr.new('/project/module/config/Main/Main.args.yaml'))
        assert e.value.msg == 'Invalid nav name: "Main"'


class TestGroupsDataEmpty:
    def test_missing_file(self, fs):
        parser = make_parser(fs, 'main')
        assert parser.groups_data == {}

    def test_empty_file(self, fs):
        parser = make_parser(fs, 'main', args_yaml='')
        assert parser.groups_data == {}

    def test_empty_mapping(self, fs):
        parser = make_parser(fs, 'main', args_yaml='{}\n')
        assert parser.groups_data == {}

    def test_malformed_yaml(self, fs):
        """Malformed yaml stops the build instead of silently dropping groups."""
        parser = make_parser(fs, 'main', args_yaml='Group: [\n')
        with pytest.raises(DefinitionError) as e:
            _ = parser.groups_data
        assert e.value.msg.startswith('Invalid yaml: ')
        assert e.value.file == parser.file

    def test_yaml_root_is_list(self, fs):
        parser = make_parser(fs, 'main', args_yaml='- Group\n- Other\n')
        with pytest.raises(DefinitionError) as e:
            _ = parser.groups_data
        assert e.value.msg == 'Expected a mapping at yaml root, got list'
        assert e.value.file == parser.file

    def test_yaml_root_is_scalar(self, fs):
        parser = make_parser(fs, 'main', args_yaml='Group\n')
        with pytest.raises(DefinitionError) as e:
            _ = parser.groups_data
        assert e.value.msg == 'Expected a mapping at yaml root, got str'
        assert e.value.file == parser.file


class TestGroupsData:
    def test_one_group(self, fs):
        parser = make_parser(fs, 'main', args_yaml="""\
Group:
  args:
    Enable: true
    Mode:
      value: normal
      option: [normal, hard]
""")
        data = parser.groups_data
        assert list(data) == ['Group']
        group = data['Group']
        assert isinstance(group, GroupData)
        assert group.name == 'Group'
        assert group.parent == ()
        assert group.dashboard == ''
        assert list(group.args) == ['Enable', 'Mode']
        assert group.args['Enable'].dt == 'checkbox'
        assert group.args['Enable'].value is True
        assert group.args['Mode'].dt == 'select'
        assert group.args['Mode'].value == 'normal'

    def test_multiple_groups_keep_yaml_order(self, fs):
        parser = make_parser(fs, 'main', args_yaml="""\
Beta:
  args:
    B: 1
Alpha:
  args:
    A: 2
""")
        assert list(parser.groups_data) == ['Beta', 'Alpha']

    def test_empty_group_is_kept(self, fs):
        """An empty group is an inforef group, it is kept for the GUI."""
        parser = make_parser(fs, 'main', args_yaml="""\
EmptyGroup:
FullGroup:
  args:
    A: 1
""")
        data = parser.groups_data
        assert list(data) == ['EmptyGroup', 'FullGroup']
        assert data['EmptyGroup'].args == {}
        assert data['FullGroup'].args['A'].value == 1

    def test_parser_back_reference(self, fs):
        parser = make_parser(fs, 'main', args_yaml="""\
Group:
  args:
    A: 1
""")
        assert parser.groups_data['Group'].parser is parser

    def test_override_i18n(self, fs):
        parser = make_parser(fs, 'main', args_yaml="""\
Group:
  override_i18n: true
  args:
    A: 1
""")
        assert parser.groups_data['Group'].override_i18n is True

    def test_parent_str(self, fs):
        parser = make_parser(fs, 'main', args_yaml="""\
Base:
  args:
    A: 1
Variant:
  parent: Base
  args:
    A: 2
""")
        data = parser.groups_data
        assert data['Base'].parent == ()
        assert data['Variant'].parent == ('Base',)

    def test_parent_list(self, fs):
        parser = make_parser(fs, 'main', args_yaml="""\
Variant:
  parent: [Base1, Base2]
""")
        assert parser.groups_data['Variant'].parent == ('Base1', 'Base2')

    def test_override_args_is_input_args(self, fs):
        """
        override_args keeps the args written in the yaml of every group.

        A variant uses them as the override set; a base group keeps them too,
        the MRO build uses override_args to resolve the i18ngroup of variants.
        """
        parser = make_parser(fs, 'main', args_yaml="""\
Base:
  args:
    A: 1
Variant:
  parent: Base
  args:
    A: 2
""")
        data = parser.groups_data
        base = data['Base']
        variant = data['Variant']
        assert base.args == base.override_args
        assert base.override_args is not base.args
        assert variant.args == variant.override_args
        assert variant.override_args['A'].value == 2

    def test_cached_property(self, fs):
        parser = make_parser(fs, 'main', args_yaml="""\
Group:
  args:
    A: 1
""")
        first = parser.groups_data
        second = parser.groups_data
        assert first is second
        assert 'groups_data' in parser.__dict__


class TestGroupsDataError:
    @pytest.mark.parametrize('group_name', ['group', 'Group_1', '1Group', 'Group-1'], ids=[
        'lower', 'underscore', 'digit-first', 'dash',
    ])
    def test_group_name_format(self, fs, group_name):
        parser = make_parser(fs, 'main', args_yaml=f'{group_name}:\n  args:\n    A: 1\n')
        with pytest.raises(DefinitionError) as e:
            _ = parser.groups_data
        assert e.value.msg == f'Group name format invalid: "{group_name}"'
        assert e.value.file == parser.file
        assert e.value.keys == []
        assert e.value.value == group_name

    def test_group_name_must_be_a_str(self, fs):
        """A yaml key like `1:` is an int, it must not reach re.match()."""
        parser = make_parser(fs, 'main', args_yaml='1:\n  args:\n    A: 1\n')
        with pytest.raises(DefinitionError) as e:
            _ = parser.groups_data
        assert e.value.msg == 'Group name format invalid: "1"'
        assert e.value.file == parser.file
        assert e.value.keys == []
        assert e.value.value == 1

    def test_arg_error_gets_file(self, fs):
        parser = make_parser(fs, 'main', args_yaml="""\
Group:
  args:
    Value:
      dt: input
      value: [1, 2]
""")
        with pytest.raises(DefinitionError) as e:
            _ = parser.groups_data
        assert e.value.msg == 'Value of "input-*" datatype must be str/int/float'
        assert e.value.file == parser.file
        assert e.value.keys == ['Group', 'Value']

    def test_unknown_group_field_gets_file(self, fs):
        """A typo in a group field must fail with the source file, not be dropped."""
        parser = make_parser(fs, 'main', args_yaml="""\
Group:
  parrent: Base
  args:
    A: 1
""")
        with pytest.raises(DefinitionError) as e:
            _ = parser.groups_data
        assert e.value.msg == 'ValidationError(Object contains unknown field `parrent`)'
        assert e.value.file == parser.file
        assert e.value.keys == ['Group']

    def test_dashboard_color_error_gets_file(self, fs):
        parser = make_parser(fs, 'main', args_yaml="""\
Group:
  dashboard_color: red
  args:
    A: 1
""")
        with pytest.raises(DefinitionError) as e:
            _ = parser.groups_data
        assert e.value.msg == 'Invalid dashboard_color, expects #RGB #RGBA #RRGGBB #RRGGBBAA'
        assert e.value.file == parser.file
        assert e.value.keys == ['Group', 'dashboard_color']

    def test_non_str_dashboard_color_gets_file(self, fs):
        """A wrong type keeps the color location and the source file."""
        parser = make_parser(fs, 'main', args_yaml="""\
Group:
  dashboard_color: 1
  args:
    A: 1
""")
        with pytest.raises(DefinitionError) as e:
            _ = parser.groups_data
        assert e.value.msg == 'Invalid dashboard_color, expects a str, got int'
        assert e.value.file == parser.file
        assert e.value.keys == ['Group', 'dashboard_color']
        assert e.value.value == 1


class TestGroupsDataPostprocess:
    def test_postprocess_injected_group(self, fs, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        @args_yaml_postprocess('main')
        def add_group(data):
            data['Injected'] = {'args': {'A': 1}}
            return data

        parser = make_parser(fs, 'main', args_yaml="""\
Group:
  args:
    A: 1
""")
        assert list(parser.groups_data) == ['Group', 'Injected']

    def test_protected_nav_skips_postprocess(self, fs, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        @args_yaml_postprocess('alasio')
        def add_group(data):
            data['Injected'] = {'args': {'A': 1}}
            return data

        parser = make_parser(fs, 'alasio', args_yaml="""\
Group:
  args:
    A: 1
""")
        assert list(parser.groups_data) == ['Group']
