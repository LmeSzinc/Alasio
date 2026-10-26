"""
dashboard="Planner": a progress with an ETA, e.g. 56.27% > 3.6d.

DashboardPlanner provides the Progress and Eta args. The concrete group adds
its own Value/Total pairs when it needs them.
"""

import pytest

from alasio.config_dev.parse.base import DefinitionError
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

    def test_unprefixed_value_total_pair_is_allowed(self):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
Planner:
  parent: DashboardPlanner
  args:
    Value: 8
    Total: 10
""")
        planner = groups['Planner']
        assert list(planner.args) == ['Time', 'Progress', 'Eta', 'Value', 'Total']
        assert planner.args['Value'].value == 8
        assert planner.args['Total'].value == 10
        assert planner.args['Value'].ge == 0
        assert planner.args['Total'].ge == 0

    def test_custom_pair_definition_order_is_preserved(self):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
Planner:
  parent: DashboardPlanner
  args:
    GreenValue: 33
    GreenTotal: 33
    BlueValue: 22
    BlueTotal: 44
""")
        planner = groups['Planner']
        assert list(planner.args) == [
            'Time', 'Progress', 'Eta', 'GreenValue', 'GreenTotal', 'BlueValue', 'BlueTotal',
        ]

    @pytest.mark.parametrize('args_yaml, arg_name, message', [
        (
            '    GreenValue: 33\n',
            'GreenValue',
            'Dashboard planner arg "GreenValue" is not paired with "GreenTotal"',
        ),
        (
            '    GreenTotal: 33\n',
            'GreenTotal',
            'Dashboard planner arg "GreenTotal" is not paired with "GreenValue"',
        ),
        (
            '    GreenTotal: 33\n    GreenValue: 33\n',
            'GreenTotal',
            'Dashboard planner arg "GreenTotal" is not paired with "GreenValue"',
        ),
        (
            '    GreenValue: 33\n    BlueValue: 22\n',
            'GreenValue',
            'Dashboard planner arg "GreenValue" must be followed by "GreenTotal", got "BlueValue"',
        ),
        (
            '    GreenValue: 33\n    BlueTotal: 44\n    BlueValue: 22\n    GreenTotal: 33\n',
            'GreenValue',
            'Dashboard planner arg "GreenValue" must be followed by "GreenTotal", got "BlueTotal"',
        ),
        (
            '    GreenValue: 33\n    Extra: 1\n',
            'GreenValue',
            'Dashboard planner arg "GreenValue" must be followed by "GreenTotal", got "Extra"',
        ),
    ], ids=[
        'missing-total', 'missing-value', 'total-before-value', 'two-values', 'interleaved', 'arg-between-pair',
    ])
    def test_invalid_pairs_are_rejected(self, args_yaml, arg_name, message):
        with pytest.raises(DefinitionError) as e:
            parse_dashboard_groups(YAML_DASHBOARD_BASE + f"""\
Planner:
  parent: DashboardPlanner
  args:
{args_yaml}""")
        assert e.value.msg == message
        assert e.value.keys == ['Planner', 'args', arg_name]

    def test_planner_without_value_total_is_allowed(self):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
Planner:
  parent: DashboardPlanner
  args:
    Custom: hidden
""")
        planner = groups['Planner']
        assert list(planner.args) == ['Time', 'Progress', 'Eta', 'Custom']
        assert planner.args['Custom'].dt == 'input'
        assert planner.args['Custom'].value == 'hidden'

    def test_non_pair_arg_after_pair_is_allowed(self):
        groups = parse_dashboard_groups(YAML_DASHBOARD_BASE + """\
Planner:
  parent: DashboardPlanner
  args:
    GreenValue: 8
    GreenTotal: 10
    Custom: hidden
""")
        planner = groups['Planner']
        assert list(planner.args) == [
            'Time', 'Progress', 'Eta', 'GreenValue', 'GreenTotal', 'Custom',
        ]

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
