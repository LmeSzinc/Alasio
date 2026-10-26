"""
dashboard="DynamicTotal": a value with a dynamic total, e.g. 8000 / 14000.

Unlike Total, the total is a separate `Total` arg instead of the `le`
constraint of Value. The store model keeps Value <= Total at runtime.
"""

from tests.config_dev.parse.parse_groups.helpers import YAML_DASHBOARD_BASE, parse_dashboard_groups


class TestDashboardDynamicTotal:
    def test_type_comes_from_parent(self):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
SimulatedUniverse:
  parent: DashboardDynamicTotal
  dashboard_color: "#123456"
""")
        group = groups['SimulatedUniverse']
        assert group.parent == ('DashboardDynamicTotal',)
        assert group.mro == (
            'SimulatedUniverse', 'DashboardDynamicTotal', 'DashboardAmount', 'DashboardBase',
        )
        assert group.dashboard == 'DynamicTotal'
        assert group.dashboard_color == '#123456'

    def test_inherits_value_and_total(self):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
SimulatedUniverse:
  parent: DashboardDynamicTotal
""")
        group = groups['SimulatedUniverse']
        assert list(group.args) == ['Time', 'Value', 'Total']
        assert group.args['Value'].dt == 'input-int'
        assert group.args['Value'].ge == 0
        assert group.args['Total'].dt == 'input-int'
        assert group.args['Total'].ge == 0

    def test_override_value_and_total(self):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
SimulatedUniverse:
  parent: DashboardDynamicTotal
  args:
    Value:
      value: 3600
      range: 0~14000
    Total:
      value: 8000
      range: 0~14000
""")
        args = groups['SimulatedUniverse'].args
        assert args['Value'].value == 3600
        assert args['Value'].le == 14000
        assert args['Total'].value == 8000
        assert args['Total'].le == 14000
