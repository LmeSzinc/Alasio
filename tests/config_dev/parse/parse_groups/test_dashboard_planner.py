"""
dashboard="Planner": a progress with an ETA, e.g. 56.27% > 3.6d.

DashboardPlanner provides the Progress and Eta args. The concrete group adds
its own Value/Total pairs when it needs them.
"""

from tests.config_dev.parse.parse_groups.helpers import YAML_DASHBOARD_BASE, parse_dashboard_groups


class TestDashboardPlanner:
    def test_type_comes_from_parent(self):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
Planner:
  parent: DashboardPlanner
  dashboard_color: "#fedcba"
""")
        planner = groups['Planner']
        assert planner.parent == ('DashboardPlanner',)
        assert planner.mro == ('Planner', 'DashboardPlanner', 'DashboardBase')
        assert planner.dashboard == 'Planner'
        assert planner.dashboard_color == '#fedcba'

    def test_inherits_progress_eta_and_time(self):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
Planner:
  parent: DashboardPlanner
""")
        planner = groups['Planner']
        assert list(planner.args) == ['Time', 'Progress', 'Eta']
        assert planner.args['Time'].dt == 'datetime'
        assert planner.args['Progress'].dt == 'input-int'
        assert planner.args['Progress'].value == 0
        assert planner.args['Progress'].ge == 0
        assert planner.args['Eta'].dt == 'input'
        assert planner.args['Eta'].value == ''

    def test_custom_value_total_pairs(self):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
Planner:
  parent: DashboardPlanner
  args:
    GreenValue: 0
    GreenTotal: 0
""")
        planner = groups['Planner']
        assert list(planner.args) == ['Time', 'Progress', 'Eta', 'GreenValue', 'GreenTotal']
        assert planner.args['GreenValue'].ge == 0
        assert planner.args['GreenTotal'].ge == 0

    def test_progress_and_eta_can_be_overridden(self):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
Planner:
  parent: DashboardPlanner
  args:
    Progress:
      value: 56
      range: 0~100
    Eta: 3.6d
""")
        planner = groups['Planner']
        assert planner.args['Progress'].value == 56
        assert planner.args['Progress'].le == 100
        assert planner.args['Eta'].value == '3.6d'
