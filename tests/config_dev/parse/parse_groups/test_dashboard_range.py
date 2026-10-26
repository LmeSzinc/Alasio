"""
Dashboard numeric args: the automatic ge=0 and the final range validation.

populate_dashboard() adds ge=0 to a numeric arg only when the author wrote no
range constraint at all, then validates the default value against the final
range. The range rules themselves live in validate_range_default(), these tests
cover the dashboard call site: what disables the automatic ge=0, what the
validation rejects, and where the error points at.
"""

import pytest
from msgspec import UNSET

from alasio.config_dev.parse.base import DefinitionError
from alasio.testing.filesystem import fs  # noqa: F401
from tests.config_dev.parse.parse_groups.helpers import (
    YAML_DASHBOARD_BASE, build_dashboard_mro, make_parser, parse_dashboard_groups
)


class TestAutoGe0:
    """ge=0 is a default, an explicit range is the author's decision."""

    def test_no_constraint_gets_ge0(self):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
Gems:
  parent: DashboardAmount
  args:
    Custom: 5
""")
        assert groups['Gems'].args['Custom'].ge == 0

    @pytest.mark.parametrize('arg_yaml, expected', [
        ('value: 5\n      range: 1~15', {'ge': 1, 'le': 15}),
        ('value: 5\n      range: x>1', {'gt': 1}),
        ('value: -1\n      range: x<=-1', {'le': -1}),
        ('value: 3\n      le: 10', {'le': 10}),
        ('value: 3\n      lt: 10', {'lt': 10}),
        ('value: 3\n      ge: 2', {'ge': 2}),
        ('value: 3\n      gt: 2', {'gt': 2}),
    ], ids=['ge-le', 'gt', 'le-negative', 'le', 'lt', 'ge', 'gt-only'])
    def test_explicit_constraint_disables_ge0(self, arg_yaml, expected):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + f"""\
Gems:
  parent: DashboardAmount
  args:
    Value:
      {arg_yaml}
""")
        value = groups['Gems'].args['Value']
        for key, bound in expected.items():
            assert getattr(value, key) == bound
        if 'ge' not in expected and 'gt' not in expected:
            assert value.ge is UNSET
            assert value.gt is UNSET

    def test_inherited_value_without_constraint_gets_ge0(self):
        """The ge=0 contract applies to the merged args of the group."""
        groups = parse_dashboard_groups("""\
DashboardBase:
  args:
    Time: 2020-01-01T00:00:00Z
DashboardAmount:
  parent: DashboardBase
  args:
    Value: 0
Gems:
  parent: DashboardAmount
""")
        assert groups['Gems'].args['Value'].ge == 0

    def test_non_numeric_args_are_untouched(self):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
Planner:
  parent: DashboardPlanner
""")
        planner = groups['Planner']
        assert planner.args['Time'].ge is UNSET
        assert planner.args['Eta'].ge is UNSET


class TestDashboardDefaultValue:
    def test_negative_inherited_value_is_rejected(self):
        """The automatic ge=0 must be satisfiable by the default value."""
        with pytest.raises(DefinitionError) as e:
            parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
Gems:
  parent: DashboardAmount
  args:
    Value: -5
""")
        assert e.value.msg == 'Default value -5 violates ge=0'
        assert e.value.file == ''
        assert e.value.keys == ['Gems', 'Value']

    @pytest.mark.parametrize('arg_yaml, message', [
        ('value: 3\n      range: x>=5', 'Default value 3 violates ge=5'),
        ('value: 0\n      range: x>0', 'Default value 0 violates gt=0'),
        ('value: 99\n      range: 0~10', 'Default value 99 violates le=10'),
        ('value: 10\n      range: x<10', 'Default value 10 violates lt=10'),
        # same boundary on both constraints of a side, the exclusive one wins
        ('value: 5\n      ge: 5\n      gt: 5', 'Default value 5 violates gt=5'),
        ('value: 5\n      le: 5\n      lt: 5', 'Default value 5 violates lt=5'),
        ('value: 0.5\n      dt: input-float\n      range: 1~2', 'Default value 0.5 violates ge=1'),
    ], ids=['ge', 'gt', 'le', 'lt', 'ge-gt-tie', 'le-lt-tie', 'float'])
    def test_value_out_of_range_is_rejected(self, arg_yaml, message):
        with pytest.raises(DefinitionError) as e:
            parse_dashboard_groups(YAML_DASHBOARD_BASE + f"""\
Gems:
  parent: DashboardAmount
  args:
    Value:
      {arg_yaml}
""")
        assert e.value.msg == message
        assert e.value.keys == ['Gems', 'Value']

    def test_planner_custom_value_is_rejected(self):
        """Every numeric arg is validated, not only Value."""
        with pytest.raises(DefinitionError) as e:
            parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
Planner:
  parent: DashboardPlanner
  args:
    GreenValue: -1
    GreenTotal: 0
""")
        assert e.value.msg == 'Default value -1 violates ge=0'
        assert e.value.keys == ['Planner', 'GreenValue']


class TestDashboardEmptyRange:
    @pytest.mark.parametrize('arg_yaml, message', [
        ('value: 0\n      ge: 5\n      le: -1', 'Range is empty: ge=5 and le=-1'),
        ('value: 0\n      gt: 5\n      lt: 5', 'Range is empty: gt=5 and lt=5'),
        ('value: 0\n      range: x>=5\n      le: -1', 'Range is empty: ge=5 and le=-1'),
        ('value: 0\n      range: x>5\n      le: 5', 'Range is empty: gt=5 and le=5'),
        ('value: 5\n      ge: 5\n      lt: 5', 'Range is empty: ge=5 and lt=5'),
    ], ids=['ge-le', 'gt-lt', 'range-and-le', 'gt-and-le', 'ge-and-lt'])
    def test_empty_range_is_rejected(self, arg_yaml, message):
        """
        Bounds that exclude each other are reported as an empty range, not as
        an out of range default value.
        """
        with pytest.raises(DefinitionError) as e:
            parse_dashboard_groups(YAML_DASHBOARD_BASE + f"""\
Gems:
  parent: DashboardAmount
  args:
    Value:
      {arg_yaml}
""")
        assert e.value.msg == message
        assert e.value.keys == ['Gems', 'Value']


class TestDashboardRangeErrorLocation:
    def test_auto_ge0_error_gets_file(self, fs):
        """The error of the args populated after the MRO build names the file."""
        parser = make_parser(fs, 'main', args_yaml=YAML_DASHBOARD_BASE + """\
Gems:
  parent: DashboardAmount
  args:
    Value: -5
""")
        with pytest.raises(DefinitionError) as e:
            build_dashboard_mro(parser.groups_data)
        assert e.value.msg == 'Default value -5 violates ge=0'
        assert e.value.file == parser.file
        assert e.value.keys == ['Gems', 'Value']
