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

from alasio.deploy.config.model import BackendConfig, DeployModel, UpdateConfig, YamlConfigWithPassword
from alasio.ext.file.yamlconfig import build_help_map
from alasio.testing.filesystem import fs  # noqa: F401


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


class TestWeakPassword:
    """
    YamlConfigWithPassword.weak_password: the strength rule of the web ui
    password as a cached flag of the config, so a caller (create_config)
    never holds the plaintext as a variable of its own frame.
    """

    @pytest.mark.parametrize('password, expected', [
        # no password configured
        (None, False),
        ('', False),
        # below / at the 8 character boundary of the rule
        ('1234567', True),
        ('12345678', False),
    ])
    def test_flag(self, fs, password, expected):
        if password is None:
            contents = 'Backend: {}\n'
        else:
            contents = f'Backend:\n  Password: {password!r}\n'
        fs.create_file('/config/deploy.yaml', contents=contents)
        config = YamlConfigWithPassword('/config/deploy.yaml', model=DeployModel)
        assert config.weak_password is expected

    def test_weak_value_is_kept(self, fs):
        """A weak password keeps its configured value: the rule runs at
        runtime, a msgspec field constraint would reset it to the default"""
        fs.create_file('/config/deploy.yaml', contents='Backend:\n  Password: "1234567"\n')
        config = YamlConfigWithPassword('/config/deploy.yaml', model=DeployModel)
        assert config.weak_password is True
        assert config.data.Backend.Password == '1234567'


class TestBackendRepr:
    """The password must not appear in repr(): a traceback prints locals"""

    def test_repr_masks_password(self):
        backend = BackendConfig(Password='secret-password')
        assert repr(backend) == (
            "BackendConfig(Host='0.0.0.0', Port=22267, Password='********', "
            "WebuiSSLKey=None, WebuiSSLCert=None)"
        )

    def test_nested_repr_masks_password(self):
        """The whole model repr is masked through the nested BackendConfig"""
        model = DeployModel(Backend=BackendConfig(Password='secret-password'))
        text = repr(model)
        assert 'secret-password' not in text
        assert "Password='********'" in text
