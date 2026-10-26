"""
dashboard="Total": a value with a total, e.g. 8000 / 14000.

The total is the `le` constraint of Value: the concrete group must override
Value with a range so the frontend can display value / total.
"""

from msgspec import UNSET

from tests.config_dev.parse.parse_groups.helpers import YAML_DASHBOARD_BASE, parse_dashboard_groups


class TestDashboardTotal:
    def test_type_comes_from_parent(self):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
Oil:
  parent: DashboardTotal
  dashboard_color: "#7f7f7f"
""")
        oil = groups['Oil']
        assert oil.parent == ('DashboardTotal',)
        assert oil.mro == ('Oil', 'DashboardTotal', 'DashboardAmount', 'DashboardBase')
        assert oil.dashboard == 'Total'
        assert oil.dashboard_color == '#7f7f7f'

    def test_inherits_time_and_value(self):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
Oil:
  parent: DashboardTotal
""")
        oil = groups['Oil']
        assert list(oil.args) == ['Time', 'Value']
        assert oil.args['Time'].dt == 'datetime'
        assert oil.args['Value'].dt == 'input-int'
        assert oil.args['Value'].ge == 0
        assert oil.args['Value'].le is UNSET

    def test_value_range_sets_the_total(self):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
Oil:
  parent: DashboardTotal
  args:
    Value:
      value: 37
      range: 0~25000
""")
        value = groups['Oil'].args['Value']
        assert value.value == 37
        assert value.ge == 0
        assert value.le == 25000

    def test_custom_args_are_kept(self):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
Oil:
  parent: DashboardTotal
  args:
    Note: hello
""")
        oil = groups['Oil']
        assert list(oil.args) == ['Time', 'Value', 'Note']
        assert oil.args['Note'].value == 'hello'
