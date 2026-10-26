"""
dashboard="Amount": a single value, e.g. 8654.

The type is not declared by a `dashboard:` key. A group becomes an Amount
dashboard item by inheriting DashboardAmount, which provides the Time arg and
the Value >= 0 constraint.
"""

import pytest
from msgspec import UNSET

from tests.config_dev.parse.parse_groups.helpers import YAML_DASHBOARD_BASE, parse_dashboard_groups


class TestDashboardAmount:
    def test_type_comes_from_parent(self):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
Gems:
  parent: DashboardAmount
  dashboard_color: "#eb8efe"
""")
        gems = groups['Gems']
        assert gems.parent == ('DashboardAmount',)
        assert gems.mro == ('Gems', 'DashboardAmount', 'DashboardBase')
        assert gems.dashboard == 'Amount'
        assert gems.dashboard_color == '#eb8efe'

    def test_inherits_time_and_value(self):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
Gems:
  parent: DashboardAmount
""")
        gems = groups['Gems']
        assert list(gems.args) == ['Time', 'Value']
        assert gems.args['Time'].dt == 'datetime'
        assert gems.args['Time'].value.year == 2020
        assert gems.args['Value'].dt == 'input-int'
        assert gems.args['Value'].value == 0
        assert gems.args['Value'].ge == 0
        assert gems.args['Value'].le is UNSET

    def test_override_value(self):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
Gems:
  parent: DashboardAmount
  args:
    Value:
      value: 100
      range: 0~999
""")
        value = groups['Gems'].args['Value']
        assert value.value == 100
        assert value.ge == 0
        assert value.le == 999

    def test_override_value_without_range_gets_ge0(self):
        """The dashboard ge=0 contract also applies to an overridden Value."""
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
Gems:
  parent: DashboardAmount
  args:
    Value: 100
""")
        value = groups['Gems'].args['Value']
        assert value.value == 100
        assert value.ge == 0

    @pytest.mark.parametrize('yaml_arg, expected_ge', [
        ('0', 0),
        ('0.5', 0),
        # any explicit constraint disables the automatic ge=0
        ("{dt: input-int, value: 3, range: 'x>=2'}", 2),
        ("{dt: input-int, value: 1, range: 'x>0'}", UNSET),
        ("{dt: input-int, value: 0, range: 'x<=10'}", UNSET),
        ("{dt: input-int, value: -1, range: 'x<=-1'}", UNSET),
        ("{dt: input-int, value: -1, range: 'x<0'}", UNSET),
        ("{dt: input-int, value: 0, range: 'x<5'}", UNSET),
        ('{dt: input-int, value: 3, le: 10}', UNSET),
        ('{dt: select, value: a, option: [a, b]}', UNSET),
        ('{dt: checkbox, value: true}', UNSET),
    ], ids=[
        'int', 'float', 'ge', 'gt', 'le-positive', 'le-negative', 'lt-zero', 'lt-positive',
        'explicit-le', 'select', 'checkbox',
    ])
    def test_populate_ge0_on_custom_args(self, yaml_arg, expected_ge):
        """
        A numeric arg without any range constraint gets ge=0.

        An arg with an explicit constraint keeps exactly what the author wrote,
        the dashboard contract does not add a bound next to it.
        """
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + f"""\
Gems:
  parent: DashboardAmount
  args:
    Arg: {yaml_arg}
""")
        arg = groups['Gems'].args['Arg']
        if expected_ge is UNSET:
            assert arg.ge is UNSET
        else:
            assert arg.ge == expected_ge

    def test_custom_args_are_kept(self):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
Gems:
  parent: DashboardAmount
  args:
    Note: hello
""")
        gems = groups['Gems']
        assert list(gems.args) == ['Time', 'Value', 'Note']
        assert gems.args['Note'].dt == 'input'
        assert gems.args['Note'].value == 'hello'
