"""
dashboard="value": a single value, e.g. 61549961.

A value group must have arg "Value". GroupData adds the inherited "Time" arg
and populates `ge=0` on every unconstrained numeric arg.
"""

from datetime import datetime, timezone

import pytest
from msgspec import UNSET

from alasio.config.alasio.group_base import DEFAULT_TIME
from alasio.config_dev.parse.base import DefinitionError
from alasio.config_dev.parse.parse_args import ArgData
from alasio.config_dev.parse.parse_groups import GroupData
from tests.config_dev.parse.parse_groups.helpers import make_dashboard_group


class TestDashboardValue:
    def test_requires_value(self):
        with pytest.raises(DefinitionError) as e:
            make_dashboard_group('value')
        assert e.value.msg == 'Dashboard datatype "value" must have arg "Value"'
        assert e.value.keys == ['Group', 'args']
        # Time is added before the datatype validation
        assert list(e.value.value) == ['Time']

    def test_adds_time(self):
        group = make_dashboard_group('value', {'Value': 0})
        assert list(group.args) == ['Value', 'Time']
        time = group.args['Time']
        assert time.dt == 'datetime'
        assert time.value == DEFAULT_TIME
        assert time.get_anno() == 'a.T_DATETIME'

    def test_keeps_value(self):
        group = make_dashboard_group('value', {'Value': 12345})
        arg = group.args['Value']
        assert arg.dt == 'input-int'
        assert arg.value == 12345
        assert arg.ge == 0

    def test_float_value(self):
        group = make_dashboard_group('value', {'Value': 0.5})
        arg = group.args['Value']
        assert arg.dt == 'input-float'
        assert arg.value == 0.5
        assert arg.ge == 0

    def test_custom_time_is_rejected(self):
        with pytest.raises(DefinitionError) as e:
            make_dashboard_group('value', {
                'Value': 0,
                'Time': datetime(2026, 1, 1, tzinfo=timezone.utc),
            })
        assert e.value.msg == 'Dashboard group cannot have custom arg "Time"'
        assert e.value.keys == ['Group', 'args', 'Time']
        assert e.value.value.dt == 'datetime'

    def test_extra_args_are_allowed(self):
        group = make_dashboard_group('value', {
            'Value': 0,
            'Note': 'hello',
        })
        assert group.args['Value'].ge == 0
        assert group.args['Note'].dt == 'input'
        assert group.args['Note'].value == 'hello'
        assert group.args['Note'].ge is UNSET


class TestDashboardValueGe:
    @pytest.mark.parametrize('arg_def, expected_ge', [
        (0, 0),
        (0.5, 0),
        ({'dt': 'input-int', 'value': 0, 'range': 'x>0'}, UNSET),
        ({'dt': 'input-int', 'value': 0, 'range': 'x>=2'}, 2),
        ({'dt': 'input-int', 'value': 0, 'range': '0~10'}, 0),
        ({'dt': 'input-int', 'value': 0, 'range': 'x<=-1'}, UNSET),
        ({'dt': 'input-int', 'value': 0, 'range': 'x<0'}, UNSET),
        ({'dt': 'input-int', 'value': 0, 'range': 'x<5'}, 0),
        ({'dt': 'select', 'value': 'a', 'option': ['a', 'b']}, UNSET),
        ({'dt': 'checkbox', 'value': True}, UNSET),
    ], ids=[
        'int', 'float', 'gt', 'ge', 'range', 'le-negative', 'lt-zero',
        'lt-positive', 'select', 'checkbox',
    ])
    def test_populate_ge0(self, arg_def, expected_ge):
        group = make_dashboard_group('value', {'Value': 0, 'Arg': arg_def})
        arg = group.args['Arg']
        if expected_ge is UNSET:
            assert arg.ge is UNSET
        else:
            assert arg.ge == expected_ge

    def test_existing_gt_is_kept(self):
        group = make_dashboard_group('value', {
            'Value': 0,
            'Arg': {'dt': 'input-int', 'value': 1, 'range': 'x>1'},
        })
        arg = group.args['Arg']
        assert arg.gt == 1
        assert arg.ge is UNSET

    def test_le_and_lt_are_kept(self):
        group = make_dashboard_group('value', {
            'Value': 0,
            'Arg': {'dt': 'input-int', 'value': 1, 'range': '0~10'},
        })
        arg = group.args['Arg']
        assert arg.ge == 0
        assert arg.le == 10

    def test_all_numeric_args_get_ge0(self):
        group = make_dashboard_group('value', {
            'Value': 0,
            'Count': 10,
            'Ratio': 0.5,
            'Name': 'x',
        })
        assert group.args['Value'].ge == 0
        assert group.args['Count'].ge == 0
        assert group.args['Ratio'].ge == 0
        assert group.args['Name'].ge is UNSET

    def test_none_arg_is_skipped(self):
        """A broken arg is not created by from_group_data, but guard anyway."""
        group = GroupData(name='Group', dashboard='value', args={
            'Value': ArgData.from_arg_data(0),
            'Broken': None,
        })
        assert group.args['Broken'] is None
        assert group.args['Value'].ge == 0
