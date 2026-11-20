"""
dashboard="SoftTotal": a value over a soft total, both under a hard total.

The hard total is the range of Value, e.g. "0~25000", the soft total is the
`Total` arg the game reports at runtime. Value may pass the soft total, so
populate_dashboard() only fills `Total` from the hard total of Value: there is
no pairing rule like the Value <= Total of DynamicTotal.
"""

import pytest
from msgspec import UNSET

from alasio.config_dev.parse.base import DefinitionError
from tests.config_dev.parse.parse_groups.helpers import YAML_DASHBOARD_BASE, parse_dashboard_groups


class TestDashboardSoftTotal:
    def test_type_comes_from_parent(self):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
WeeklyPoints:
  parent: DashboardSoftTotal
  dashboard_color: "#123456"
  args:
    Value:
      value: 0
      range: 0~25000
""")
        group = groups['WeeklyPoints']
        assert group.parent == ('DashboardSoftTotal',)
        assert group.mro == (
            'WeeklyPoints', 'DashboardSoftTotal', 'DashboardAmount', 'DashboardBase',
        )
        assert group.dashboard == 'SoftTotal'
        assert group.dashboard_color == '#123456'

    def test_total_is_filled_from_hard_total(self):
        """The author writes the hard total once, as the range of Value."""
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
WeeklyPoints:
  parent: DashboardSoftTotal
  args:
    Value:
      value: 0
      range: 0~25000
""")
        args = groups['WeeklyPoints'].args
        assert list(args) == ['Time', 'Value', 'Total']
        assert args['Value'].ge == 0
        assert args['Value'].le == 25000
        assert args['Total'].ge == 0
        assert args['Total'].le == 25000

    def test_total_keeps_a_tighter_upper_bound(self):
        """A stricter bound of Total is the author's decision."""
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
WeeklyPoints:
  parent: DashboardSoftTotal
  args:
    Value:
      value: 0
      range: 0~25000
    Total:
      value: 0
      range: 0~20000
""")
        assert groups['WeeklyPoints'].args['Total'].le == 20000

    def test_total_above_hard_total_is_rejected(self):
        """Total and Value share the hard total, Total may not pass it."""
        with pytest.raises(DefinitionError) as e:
            parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
WeeklyPoints:
  parent: DashboardSoftTotal
  args:
    Value:
      value: 0
      range: 0~25000
    Total:
      value: 0
      range: 0~30000
""")
        assert e.value.msg == 'Dashboard SoftTotal arg "Total" must not exceed the hard total of "Value" (le=25000)'
        assert e.value.keys == ['WeeklyPoints', 'args', 'Total']
        assert e.value.value == 30000

    @pytest.mark.parametrize('arg_yaml', [
        'Value: 0',
        'Value:\n      value: 0\n      range: x<25000',
        'Value:\n      value: 0\n      ge: 0',
    ], ids=['no-range', 'lt-only', 'ge-only'])
    def test_value_without_hard_total_is_rejected(self, arg_yaml):
        """A soft total needs a hard total to fall back to."""
        with pytest.raises(DefinitionError) as e:
            parse_dashboard_groups(YAML_DASHBOARD_BASE + f"""\
WeeklyPoints:
  parent: DashboardSoftTotal
  args:
    {arg_yaml}
""")
        assert e.value.msg == (
            'Dashboard SoftTotal group requires an upper bound (le) on "Value", '
            'e.g. range: "0~25000"')
        assert e.value.keys == ['WeeklyPoints', 'args', 'Value']

    def test_inherited_value_without_hard_total_is_rejected(self):
        """The inherited Value of DashboardAmount has no upper bound."""
        with pytest.raises(DefinitionError) as e:
            parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
WeeklyPoints:
  parent: DashboardSoftTotal
""")
        assert e.value.msg == (
            'Dashboard SoftTotal group requires an upper bound (le) on "Value", '
            'e.g. range: "0~25000"')
        assert e.value.keys == ['WeeklyPoints', 'args', 'Value']

    def test_each_group_keeps_its_own_hard_total(self):
        """The inherited Total is copied, the base group is shared."""
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
Oil:
  parent: DashboardSoftTotal
  args:
    Value:
      value: 0
      range: 0~25000

Coin:
  parent: DashboardSoftTotal
  args:
    Value:
      value: 0
      range: 0~20000
""")
        assert groups['Oil'].args['Total'].le == 25000
        assert groups['Coin'].args['Total'].le == 20000
        # the shared base group is not rewritten by a concrete group
        assert groups['DashboardSoftTotal'].args['Total'].le is UNSET

    def test_variant_recomputes_the_inherited_total(self):
        """A group inheriting a SoftTotal group bounds Total by its own Value."""
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
Oil:
  parent: DashboardSoftTotal
  args:
    Value:
      value: 0
      range: 0~25000

OilSmall:
  parent: Oil
  args:
    Value:
      value: 0
      range: 0~10000
""")
        assert groups['OilSmall'].args['Value'].le == 10000
        assert groups['OilSmall'].args['Total'].le == 10000
        assert groups['Oil'].args['Total'].le == 25000

    def test_base_group_takes_no_hard_total(self):
        """A base group declares the fields only, no concrete hard total."""
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE)
        base = groups['DashboardSoftTotal']
        assert base.dashboard == 'SoftTotal'
        assert base.args['Value'].le is UNSET
        assert base.args['Total'].ge == 0
        assert base.args['Total'].le is UNSET
