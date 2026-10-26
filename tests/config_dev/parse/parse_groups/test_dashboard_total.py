"""
dashboard="total": a value with a total, e.g. 2437 / 3000.

A total group must have both "Value" and "Total". GroupData adds the inherited
"Time" arg and populates `ge=0` on every unconstrained numeric arg.
"""

from datetime import datetime, timezone

import pytest
from msgspec import UNSET

from alasio.config.alasio.group_base import DEFAULT_TIME
from alasio.config_dev.parse.base import DefinitionError
from tests.config_dev.parse.parse_groups.helpers import make_dashboard_group


class TestDashboardTotal:
    @pytest.mark.parametrize('args, missing', [
        ({}, ['Value', 'Total']),
        ({'Value': 0}, ['Total']),
        ({'Total': 0}, ['Value']),
    ], ids=['none', 'missing-total', 'missing-value'])
    def test_requires_value_and_total(self, args, missing):
        with pytest.raises(DefinitionError) as e:
            make_dashboard_group('total', args)
        assert e.value.msg == (
            f'Dashboard datatype "total" must have args "Value" and "Total", '
            f'missing {missing}'
        )
        assert e.value.keys == ['Group', 'args']
        assert 'Time' in e.value.value

    def test_adds_time(self):
        group = make_dashboard_group('total', {'Value': 0, 'Total': 100})
        assert list(group.args) == ['Value', 'Total', 'Time']
        time = group.args['Time']
        assert time.dt == 'datetime'
        assert time.value == DEFAULT_TIME

    def test_populate_ge0(self):
        group = make_dashboard_group('total', {'Value': 37, 'Total': 100})
        assert group.args['Value'].ge == 0
        assert group.args['Total'].ge == 0
        assert group.args['Value'].value == 37
        assert group.args['Total'].value == 100

    def test_existing_constraints_are_kept(self):
        group = make_dashboard_group('total', {
            'Value': {'dt': 'input-int', 'value': 1, 'range': 'x>1'},
            'Total': {'dt': 'input-int', 'value': 3, 'range': 'x>=2'},
        })
        assert group.args['Value'].gt == 1
        assert group.args['Value'].ge is UNSET
        assert group.args['Total'].ge == 2

    def test_non_numeric_args_do_not_get_ge0(self):
        group = make_dashboard_group('total', {
            'Value': {'dt': 'static', 'value': 'a'},
            'Total': {'dt': 'static', 'value': 'b'},
        })
        assert group.args['Value'].ge is UNSET
        assert group.args['Total'].ge is UNSET

    def test_extra_args_are_allowed(self):
        group = make_dashboard_group('total', {
            'Value': 0,
            'Total': 0,
            'Name': 'x',
        })
        assert group.args['Name'].dt == 'input'
        assert group.args['Name'].ge is UNSET

    def test_custom_time_is_rejected(self):
        with pytest.raises(DefinitionError) as e:
            make_dashboard_group('total', {
                'Value': 0,
                'Total': 0,
                'Time': datetime(2026, 1, 1, tzinfo=timezone.utc),
            })
        assert e.value.msg == 'Dashboard group cannot have custom arg "Time"'
        assert e.value.keys == ['Group', 'args', 'Time']

    def test_variant_only_overrides_are_rejected(self):
        """
        A variant only carries its override args, so direct construction can
        not satisfy the total datatype. The MRO build assigns `dashboard` after
        merging the parent args and does not re-run __post_init__, so this
        branch is not used by the real pipeline.
        """
        with pytest.raises(DefinitionError) as e:
            make_dashboard_group('total', {'Value': 0}, name='Variant', parent=('Base',))
        assert 'missing' in e.value.msg
