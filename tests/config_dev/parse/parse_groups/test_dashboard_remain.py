"""
dashboard="Remain": a remaining count, e.g. 1 / 3.

Remain reuses the Amount data structure: the concrete group overrides Value
with a range, and the store model resets Value to the maximum when the record
expires.
"""

from tests.config_dev.parse.parse_groups.helpers import YAML_DASHBOARD_BASE, parse_dashboard_groups


class TestDashboardRemain:
    def test_type_comes_from_parent(self):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
HardCampaign:
  parent: DashboardRemain
  dashboard_color: "#00ff00"
""")
        group = groups['HardCampaign']
        assert group.parent == ('DashboardRemain',)
        assert group.mro == ('HardCampaign', 'DashboardRemain', 'DashboardAmount', 'DashboardBase')
        assert group.dashboard == 'Remain'
        assert group.dashboard_color == '#00ff00'

    def test_inherits_time_and_value(self):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
HardCampaign:
  parent: DashboardRemain
  args:
    Value:
      value: 3
      range: 0~3
""")
        group = groups['HardCampaign']
        assert list(group.args) == ['Time', 'Value']
        assert group.args['Time'].dt == 'datetime'
        assert group.args['Value'].value == 3
        assert group.args['Value'].ge == 0
        assert group.args['Value'].le == 3
