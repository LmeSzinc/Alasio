"""
dashboard="progress": a progress bar, e.g. 68.35%.

A progress group must have arg "Value" with an int/float default. The default
value is kept as-is, the datatype is forced to input-float so the frontend can
edit a percentage.
"""

import pytest
from msgspec import UNSET

from alasio.config.alasio.group_base import DEFAULT_TIME
from alasio.config_dev.parse.base import DefinitionError
from tests.config_dev.parse.parse_groups.helpers import make_dashboard_group


class TestDashboardProgress:
    def test_requires_value(self):
        with pytest.raises(DefinitionError) as e:
            make_dashboard_group('progress')
        assert e.value.msg == 'Dashboard datatype "progress" must have arg "Value"'
        assert e.value.keys == ['Group', 'args']

    def test_int_value(self):
        group = make_dashboard_group('progress', {'Value': 0})
        arg = group.args['Value']
        assert arg.dt == 'input-float'
        assert arg.value == 0
        assert arg.ge == 0

    def test_float_value(self):
        group = make_dashboard_group('progress', {'Value': 0.5})
        arg = group.args['Value']
        assert arg.dt == 'input-float'
        assert arg.value == 0.5
        assert arg.ge == 0

    def test_forces_input_float(self):
        group = make_dashboard_group('progress', {
            'Value': {'dt': 'input-int', 'value': 10},
        })
        arg = group.args['Value']
        assert arg.dt == 'input-float'
        assert arg.value == 10

    def test_adds_time(self):
        group = make_dashboard_group('progress', {'Value': 0})
        assert list(group.args) == ['Value', 'Time']
        assert group.args['Time'].dt == 'datetime'
        assert group.args['Time'].value == DEFAULT_TIME

    @pytest.mark.parametrize('value', ['A', ''], ids=['text', 'empty'])
    def test_str_value_is_rejected(self, value):
        with pytest.raises(DefinitionError) as e:
            make_dashboard_group('progress', {'Value': value})
        assert e.value.msg == (
            f'Dashboard datatype "progress" must have default value in int or float, '
            f'got "{value}"'
        )
        assert e.value.keys == ['Group', 'args', 'Value']
        assert e.value.value == value

    def test_bool_value_is_rejected(self):
        """bool is a subclass of int, but it is not a progress value."""
        with pytest.raises(DefinitionError) as e:
            make_dashboard_group('progress', {'Value': True})
        assert e.value.msg == (
            'Dashboard datatype "progress" must have default value in int or float, '
            'got "True"'
        )
        assert e.value.keys == ['Group', 'args', 'Value']
        assert e.value.value is True

    def test_select_value_is_rejected(self):
        with pytest.raises(DefinitionError) as e:
            make_dashboard_group('progress', {
                'Value': {'dt': 'select', 'value': 'a', 'option': ['a', 'b']},
            })
        assert e.value.msg == (
            'Dashboard datatype "progress" must have default value in int or float, '
            'got "a"'
        )

    def test_existing_constraints_are_kept(self):
        group = make_dashboard_group('progress', {
            'Value': {'dt': 'input-float', 'value': 1.5, 'range': 'x>1'},
        })
        arg = group.args['Value']
        assert arg.dt == 'input-float'
        assert arg.gt == 1
        assert arg.ge is UNSET

    def test_le_negative_does_not_get_ge0(self):
        group = make_dashboard_group('progress', {
            'Value': {'dt': 'input-float', 'value': 1.5, 'range': 'x<=-1'},
        })
        arg = group.args['Value']
        assert arg.le == -1
        assert arg.ge is UNSET

    def test_custom_time_is_rejected(self):
        with pytest.raises(DefinitionError) as e:
            make_dashboard_group('progress', {
                'Value': 0,
                'Time': '2026-01-01T00:00:00Z',
            })
        assert e.value.msg == 'Dashboard group cannot have custom arg "Time"'
        assert e.value.keys == ['Group', 'args', 'Time']
