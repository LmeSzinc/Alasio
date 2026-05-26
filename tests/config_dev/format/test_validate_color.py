import pytest

from alasio.config_dev.format.validate_color import (
    RE_DASHBOARD_COLOR,
    validate_dashboard_color,
)
from alasio.config_dev.parse.base import DefinitionError


class TestReDashboardColor:
    """Test RE_DASHBOARD_COLOR regex"""

    @pytest.mark.parametrize('color', [
        # #RGB
        '#FFF', '#000', '#abc', '#A1B',
        # #RGBA
        '#FFFF', '#0000', '#abcd', '#A1B2',
        # #RRGGBB
        '#FFFFFF', '#000000', '#aabbcc', '#123456', '#A1B2C3',
        # #RRGGBBAA
        '#FFFFFFFF', '#00000000', '#aabbccdd', '#12345678',
    ])
    def test_valid_colors(self, color):
        assert RE_DASHBOARD_COLOR.match(color)

    @pytest.mark.parametrize('color', [
        # No hash prefix
        'FFF', 'FFFFFF',
        # Wrong length
        '#FF', '#FFFFF', '#FFFFFFFFF',
        # Non-hex characters
        '#GGG', '#XYZXYZ',
        # Empty / bare hash
        '', '#',
    ])
    def test_invalid_colors(self, color):
        assert not RE_DASHBOARD_COLOR.match(color)

    @pytest.mark.parametrize('color', [
        '#ABC', '#abc', '#AaBbCc', '#aAbBcC12',
    ])
    def test_case_insensitive(self, color):
        assert RE_DASHBOARD_COLOR.match(color)


class TestValidateDashboardColor:
    """Test validate_dashboard_color function"""

    @pytest.mark.parametrize('color', [
        '',
        '#FFF', '#000', '#abc', '#A1B',
        '#FFFF', '#0000', '#abcd', '#A1B2',
        '#FFFFFF', '#000000', '#aabbcc', '#123456', '#A1B2C3',
        '#FFFFFFFF', '#00000000', '#aabbccdd', '#12345678',
    ])
    def test_valid(self, color):
        assert validate_dashboard_color(color) is None

    @pytest.mark.parametrize('color', [
        'FFF',
        'FFFFFF',
        '123456',
        '#FF',
        '#FFFFF',
        '#FFFFFFFFF',
        '#GGG',
        '#XYZXYZ',
        '#',
    ])
    def test_invalid(self, color):
        with pytest.raises(DefinitionError):
            validate_dashboard_color(color)

    def test_error_message(self):
        with pytest.raises(DefinitionError) as exc:
            validate_dashboard_color('FFF')
        assert 'Invalid dashboard_color' in str(exc.value)

    @pytest.mark.parametrize('color,group,expected_keys', [
        ('GGG', 'MyGroup', ['MyGroup', 'dashboard_color']),
        ('GGG', None, ['dashboard_color']),
    ])
    def test_error_keys(self, color, group, expected_keys):
        kwargs = dict(group=group) if group else {}
        with pytest.raises(DefinitionError) as exc:
            validate_dashboard_color(color, **kwargs)
        assert exc.value.keys == expected_keys

    def test_error_value_preserved(self):
        with pytest.raises(DefinitionError) as exc:
            validate_dashboard_color('#ZZZ')
        assert exc.value.value == '#ZZZ'
