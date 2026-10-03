"""
Tests for the password strength rule (alasio.backend.auth.password).

The rule is a runtime check, not a msgspec field constraint: a
constraint failure would fall back to the field default and drop the
password the user set. The rule is a length check for now, the boundary
is 8 characters, and WEAK_PASSWORD_MESSAGE is the single description
of the rule (startup warning + login page help text).
"""

import pytest

from alasio.backend.auth.password import WEAK_PASSWORD_MESSAGE, is_weak_password


class TestIsWeakPassword:
    @pytest.mark.parametrize('pwd, expected', [
        # empty means "not configured": weak too, the caller tells
        # "no password" and "weak password" apart by checking the raw value
        ('', True),
        # below the boundary
        ('1', True),
        ('1234567', True),
        # the boundary itself is accepted
        ('12345678', False),
        # above the boundary
        ('123456789', False),
        ('a strong passphrase with spaces', False),
        # only the length counts, no character class requirement (yet)
        ('        ', False),
        # unicode counts in characters, not bytes
        ('密码密码密', True),
        ('密码密码密码密码', False),
    ])
    def test_length_rule(self, pwd, expected):
        assert is_weak_password(pwd) is expected

    def test_boundary(self):
        assert is_weak_password('a' * 7)
        assert not is_weak_password('a' * 8)


class TestWeakPasswordMessage:
    def test_message_describes_the_rule(self):
        """The message is the backend reference text of the manual contract
        with the frontend translation t.Auth.ErrDeployPasswordTooWeak
        (frontend/src/i18n/Auth.json): it must name the rule and where the
        user can fix the password. Update the message (and this test)
        together with the rule, then follow up in the frontend JSON."""
        assert 'at least 8 characters' in WEAK_PASSWORD_MESSAGE
        assert 'config/deploy.yaml' in WEAK_PASSWORD_MESSAGE
