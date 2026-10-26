"""
dashboard="Progress": a progress in percent, e.g. 86.54%.

DashboardProgress provides a float Value with Value >= 0. The frontend renders
the value as a percentage.
"""

from tests.config_dev.parse.parse_groups.helpers import YAML_DASHBOARD_BASE, parse_dashboard_groups


class TestDashboardProgress:
    def test_type_comes_from_parent(self):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
Progress:
  parent: DashboardProgress
  dashboard_color: "#abcdef"
""")
        progress = groups['Progress']
        assert progress.parent == ('DashboardProgress',)
        assert progress.mro == ('Progress', 'DashboardProgress', 'DashboardBase')
        assert progress.dashboard == 'Progress'
        assert progress.dashboard_color == '#abcdef'

    def test_inherits_float_value_and_time(self):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
Progress:
  parent: DashboardProgress
""")
        progress = groups['Progress']
        assert list(progress.args) == ['Time', 'Value']
        assert progress.args['Time'].dt == 'datetime'
        value = progress.args['Value']
        assert value.dt == 'input-float'
        assert value.value == 0.0
        assert value.ge == 0

    def test_override_value(self):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
Progress:
  parent: DashboardProgress
  args:
    Value:
      value: 68.35
      range: 0~100
""")
        value = groups['Progress'].args['Value']
        assert value.dt == 'input-float'
        assert value.value == 68.35
        assert value.ge == 0
        assert value.le == 100
