"""
dashboard="planner": a multi-step plan, e.g. 56.27% <3.6d.

A planner group gets generated "Progress" and "Eta" args at the top. Every
custom "xxxValue" must be paired with "xxxTotal" and vice versa.
"""

import pytest
from msgspec import UNSET

from alasio.config.alasio.group_base import DEFAULT_TIME
from alasio.config_dev.parse.base import DefinitionError
from tests.config_dev.parse.parse_groups.helpers import make_dashboard_group


class TestDashboardPlannerArgs:
    def test_adds_progress_and_eta_at_top(self):
        group = make_dashboard_group('planner', {
            'GreenValue': 0,
            'GreenTotal': 0,
        })
        assert list(group.args) == ['Progress', 'Eta', 'GreenValue', 'GreenTotal', 'Time']
        progress = group.args['Progress']
        assert progress.dt == 'input-int'
        assert progress.value == 0
        assert progress.ge == 0
        eta = group.args['Eta']
        assert eta.dt == 'input'
        assert eta.value == ''
        assert eta.get_anno() == 'str'
        assert group.args['Time'].value == DEFAULT_TIME

    def test_empty_args(self):
        group = make_dashboard_group('planner')
        assert list(group.args) == ['Progress', 'Eta', 'Time']

    def test_custom_eta_is_rejected(self):
        with pytest.raises(DefinitionError) as e:
            make_dashboard_group('planner', {'Eta': ''})
        assert e.value.msg == 'Dashboard datatype "planner" cannot have custom arg "Eta"'
        assert e.value.keys == ['Group', 'args', 'Eta']
        assert e.value.value.value == ''

    def test_custom_progress_is_rejected(self):
        with pytest.raises(DefinitionError) as e:
            make_dashboard_group('planner', {'Progress': 0})
        assert e.value.msg == 'Dashboard datatype "planner" cannot have custom arg "Progress"'
        assert e.value.keys == ['Group', 'args', 'Progress']
        assert e.value.value.value == 0

    def test_custom_time_is_rejected(self):
        with pytest.raises(DefinitionError) as e:
            make_dashboard_group('planner', {
                'Time': '2026-01-01T00:00:00Z',
            })
        assert e.value.msg == 'Dashboard group cannot have custom arg "Time"'
        assert e.value.keys == ['Group', 'args', 'Time']

    def test_extra_args_are_allowed(self):
        group = make_dashboard_group('planner', {
            'GreenValue': 0,
            'GreenTotal': 0,
            'Name': 'x',
        })
        assert group.args['Name'].dt == 'input'
        assert group.args['Name'].ge is UNSET


class TestDashboardPlannerPairs:
    @pytest.mark.parametrize('args', [
        {'Value': 0, 'Total': 0},
        {'GreenValue': 0, 'GreenTotal': 0},
        {'Value': 0, 'Total': 0, 'GreenValue': 0, 'GreenTotal': 0},
    ], ids=['value-total', 'green', 'two-pairs'])
    def test_paired_args(self, args):
        group = make_dashboard_group('planner', args)
        for arg in group.args.values():
            if arg.dt in ('input-int', 'input-float'):
                assert arg.ge == 0
        assert group.args['Time'].dt == 'datetime'

    @pytest.mark.parametrize('args, arg_name, pair_name', [
        ({'Value': 0}, 'Value', 'Total'),
        ({'Total': 0}, 'Total', 'Value'),
        ({'GreenValue': 0}, 'GreenValue', 'GreenTotal'),
        ({'GreenTotal': 0}, 'GreenTotal', 'GreenValue'),
    ], ids=['value', 'total', 'green-value', 'green-total'])
    def test_unpaired_arg(self, args, arg_name, pair_name):
        with pytest.raises(DefinitionError) as e:
            make_dashboard_group('planner', args)
        assert e.value.msg == (
            f'Dashboard planner arg "{arg_name}" is not paired with "{pair_name}"'
        )
        assert e.value.keys == ['Group', 'args', arg_name]
        assert e.value.value == pair_name

    def test_pairing_is_case_sensitive(self):
        with pytest.raises(DefinitionError) as e:
            make_dashboard_group('planner', {'Value': 0, 'total': 0})
        assert e.value.msg == 'Dashboard planner arg "Value" is not paired with "Total"'
