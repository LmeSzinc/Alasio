"""
Tests for the PEP 440 version port (packaging/version.py).

The expectations are the normalized strings and the order of packaging itself,
the cases cover the segments a version of an index page or a pin carries:
epoch, release, pre, post, dev and local.
"""
import pytest

from alasio.deploy.pip_download._utils import canonicalize_version
from alasio.deploy.pip_download._version import InvalidVersion, Version, parse


class TestVersionParse:
    """The version string is parsed into its normalized form."""

    @pytest.mark.parametrize('text, expected', [
        ('1.0', '1.0'),
        ('v1.2', '1.2'),
        ('1.0.0', '1.0.0'),
        ('1!2.0', '1!2.0'),
        ('1.0a1', '1.0a1'),
        ('1.0alpha1', '1.0a1'),
        ('1.0-alpha.1', '1.0a1'),
        ('1.0b2', '1.0b2'),
        ('1.0c1', '1.0rc1'),
        ('1.0pre1', '1.0rc1'),
        ('1.0preview1', '1.0rc1'),
        ('1.0.post1', '1.0.post1'),
        ('1.0-1', '1.0.post1'),
        ('1.0.rev1', '1.0.post1'),
        ('1.0.r1', '1.0.post1'),
        ('1.0.dev1', '1.0.dev1'),
        ('1.0a1.dev2', '1.0a1.dev2'),
        ('1.0+local', '1.0+local'),
        ('1.0+abc.1', '1.0+abc.1'),
        ('2.0.0+ubuntu-1', '2.0.0+ubuntu.1'),
        ('  1.0  ', '1.0'),
    ])
    def test_normalized(self, text, expected):
        assert str(Version(text)) == expected
        assert str(parse(text)) == expected

    @pytest.mark.parametrize('text', [
        '',
        'not-a-version',
        '1.0+',
        '1..0',
        '1.0!',
        '-1.0',
        None,
    ])
    def test_invalid(self, text):
        with pytest.raises(InvalidVersion):
            Version(text)

    def test_repr(self):
        assert repr(Version('1.0')) == "<Version('1.0')>"

    def test_parts(self):
        version = Version('1!2.0.1rc2.post3.dev4+local.5')
        assert version.epoch == 1
        assert version.release == (2, 0, 1)
        assert version.pre == ('rc', 2)
        assert version.post == 3
        assert version.dev == 4
        assert version.local == 'local.5'
        assert version.public == '1!2.0.1rc2.post3.dev4'
        assert version.base_version == '1!2.0.1'
        assert version.is_prerelease
        assert version.is_postrelease

    def test_stable(self):
        version = Version('2.1.0')
        assert not version.is_prerelease
        assert not version.is_postrelease
        assert version.epoch == 0
        assert version.pre is None
        assert version.post is None
        assert version.dev is None
        assert version.local is None


class TestVersionCompare:
    """The comparisons follow PEP 440."""

    @pytest.mark.parametrize('left, right', [
        ('1.0', '1.0.0'),
        ('1.0', '1.0.0.0'),
        ('1.0', 'v1.0'),
        ('1.0.post', '1.0.post0'),
        ('1.0.dev', '1.0.dev0'),
        ('1.0-1', '1.0.post1'),
        ('1.0c1', '1.0rc1'),
        ('1.0+abc.1', '1.0+abc.1'),
        ('0!1.0', '1.0'),
    ])
    def test_equal(self, left, right):
        assert Version(left) == Version(right)
        assert hash(Version(left)) == hash(Version(right))
        assert not Version(left) != Version(right)

    @pytest.mark.parametrize('left, right', [
        ('1.0.dev1', '1.0a1'),
        ('1.0a1', '1.0b1'),
        ('1.0b1', '1.0rc1'),
        ('1.0rc1', '1.0'),
        ('1.0', '1.0.post1'),
        ('1.0', '1.1'),
        ('1.9', '1.10'),
        ('1.0', '1!0.1'),
        ('1.0+abc', '1.0+abd'),
        ('1.0+abc', '1.0+1'),
    ])
    def test_order(self, left, right):
        assert Version(left) < Version(right)
        assert Version(right) > Version(left)
        assert Version(left) <= Version(right)
        assert Version(right) >= Version(left)
        assert Version(left) != Version(right)

    def test_compare_with_other_type(self):
        assert Version('1.0') != '1.0'
        with pytest.raises(TypeError):
            Version('1.0') < '1.0'


class TestCanonicalizeVersion:
    """The canonical form strips the trailing zeros of the release segment."""

    @pytest.mark.parametrize('text, expected', [
        ('1.0.1', '1.0.1'),
        ('1.0.0', '1'),
        ('1.4.0.0.0', '1.4'),
        ('0.0', '0'),
        ('1.0.0rc1', '1rc1'),
        ('1.0.0+local', '1+local'),
        ('1!1.0.0', '1!1'),
        ('not-a-version', 'not-a-version'),
    ])
    def test_canonical(self, text, expected):
        assert canonicalize_version(text) == expected

    def test_strip_trailing_zero_off(self):
        assert canonicalize_version('1.0.0', strip_trailing_zero=False) == '1.0.0'

    def test_version_input(self):
        assert canonicalize_version(Version('1.0.0')) == '1'
