"""
The args a dashboard item renders must stay visible.

A hidden arg is left out of {nav}_config.json, and the GUI view is built from
that file, so a hidden `Value` would leave the dashboard item showing a value it
never receives. populate_dashboard() rejects that instead of letting the item
fall back to an invented number. Only the args the item is made of are guarded:
Time / ServerUpdate and custom args of the group may be hidden.
"""

import pytest

from alasio.config_dev.parse.base import DefinitionError
from tests.config_dev.parse.parse_groups.helpers import YAML_DASHBOARD_BASE, parse_dashboard_groups


class TestDashboardHiddenDisplayArg:
    @pytest.mark.parametrize('group_yaml, arg_name, dashboard_type', [
        ("""\
WeeklyPoints:
  parent: DashboardSoftTotal
  args:
    Value:
      value: 0
      hide: true
      range: 0~25000
""", 'Value', 'SoftTotal'),
        ("""\
SimulatedUniverse:
  parent: DashboardDynamicTotal
  args:
    Value:
      value: 0
      hide: true
    Total:
      value: 0
""", 'Value', 'DynamicTotal'),
        ("""\
SimulatedUniverse:
  parent: DashboardDynamicTotal
  args:
    Total:
      value: 0
      hide: true
""", 'Total', 'DynamicTotal'),
        ("""\
Gems:
  parent: DashboardAmount
  args:
    Value:
      value: 0
      hide: true
""", 'Value', 'Amount'),
        ("""\
Campaign:
  parent: DashboardProgress
  args:
    Value:
      value: 0.0
      hide: true
""", 'Value', 'Progress'),
        ("""\
Planner:
  parent: DashboardPlanner
  args:
    Progress:
      value: 0
      hide: true
""", 'Progress', 'Planner'),
        ("""\
Planner:
  parent: DashboardPlanner
  args:
    Eta:
      value: ""
      hide: true
""", 'Eta', 'Planner'),
    ], ids=['soft-total-value', 'dynamic-total-value', 'dynamic-total-total',
            'amount-value', 'progress-value', 'planner-progress', 'planner-eta'])
    def test_hidden_display_arg_is_rejected(self, group_yaml, arg_name, dashboard_type):
        groups_text = YAML_DASHBOARD_BASE + group_yaml
        group_name = group_yaml.partition(':')[0]
        with pytest.raises(DefinitionError) as e:
            parse_dashboard_groups(groups_text)
        assert e.value.msg == (
            f'Dashboard arg "{arg_name}" must not be hidden, the {dashboard_type} item renders it')
        assert e.value.keys == [group_name, arg_name]

    @pytest.mark.parametrize('group_yaml', [
        # The record time is not read by the item's value line
        """\
Gems:
  parent: DashboardAmount
  args:
    Time:
      value: 2020-01-01T00:00:00Z
      hide: true
""",
        # A custom arg of the group is not rendered by the item either
        """\
Gems:
  parent: DashboardAmount
  args:
    Custom:
      value: 0
      hide: true
""",
        # Planner custom Value/Total pairs are rendered by the config card only,
        # the item itself shows Progress and Eta
        """\
Planner:
  parent: DashboardPlanner
  args:
    GreenValue: 0
    GreenTotal:
      value: 0
      hide: true
""",
    ], ids=['time', 'custom-arg', 'planner-pair'])
    def test_hidden_non_display_arg_is_allowed(self, group_yaml):
        group_name = group_yaml.partition(':')[0]
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + group_yaml)
        assert groups[group_name].dashboard
