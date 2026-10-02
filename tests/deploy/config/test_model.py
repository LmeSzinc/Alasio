"""
Tests for the deploy config model (alasio/deploy/config/model.py)

The model is validated by YamlConfig (alasio/ext/file/yamlconfig.py): a value
that fails the msgspec annotation (type / pattern) is reported in the log and
falls back to the field default, so an invalid config file can never crash
the backend.
"""
import msgspec
import pytest
from msgspec.msgpack import encode as msgpack_encode
from msgspecerror import load_msgpack_with_default

from alasio.deploy.config.model import UpdateConfig
from alasio.ext.file.yamlconfig import build_help_map


def load(value):
    """
    Validate a raw AutoRestartTime value the way the config file is validated

    Args:
        value: Raw value of the AutoRestartTime field

    Returns:
        tuple[str | None, list]: (validated value, validation errors)
    """
    obj, errors = load_msgpack_with_default(msgpack_encode({'AutoRestartTime': value}), UpdateConfig)
    return obj.AutoRestartTime, errors


class TestAutoRestartTime:
    """AutoRestartTime: scheduled restart time of the daily restart"""

    @pytest.mark.parametrize("value", ['03:50', '3:50', '00:00', '23:59', '0:00'])
    def test_valid_time(self, value):
        """A "HH:MM" time within 00:00~23:59 is accepted as is"""
        assert msgspec.convert({'AutoRestartTime': value}, UpdateConfig).AutoRestartTime == value

    def test_null_disables(self):
        """null is not a validation error: it disables the scheduled restart"""
        assert load(None) == (None, [])

    def test_default_enables_the_restart(self):
        """The default is the documented 03:50"""
        assert UpdateConfig().AutoRestartTime == '03:50'

    @pytest.mark.parametrize("value", ['24:00', '23:60', '03:5', '0350', 'abc', ''])
    def test_pattern_rejects_malformed_time(self, value):
        """A time that is not HH:MM fails the field pattern"""
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert({'AutoRestartTime': value}, UpdateConfig)

    @pytest.mark.parametrize("value", [
        '24:00',           # hour out of range
        '23:60',           # minute out of range
        '03:5',            # minute without the second digit
        '0350',            # separator missing
        '03:50:00',        # seconds are not part of the form
        'weekday1-04:00',  # a weekday restriction is not a daily time
        '',
        830,               # yaml reads an unquoted 13:50 as this integer
    ])
    def test_invalid_value_falls_back_to_the_default(self, value):
        """An invalid value is reported and the default is kept"""
        value_out, errors = load(value)
        assert value_out == '03:50'
        assert errors

    def test_help_is_found_by_the_config_writer(self):
        """The help lines survive the Optional annotation (YamlConfig writes them)"""
        help_text = build_help_map(UpdateConfig)[('AutoRestartTime',)]
        assert 'Scheduled restart time' in help_text
