"""
Tests for probe_mirrors: the normalization of the mirror input forms (the
three forms of Mirrors.from_input()), the Mirrors lookups and the digest
fingerprint of the structure.

No http is involved, the module is pure data.
"""
import pytest

from alasio.deploy.httpclient.probe_mirrors import (
    AllMirrorsFailedError, Mirrors, RangeUnsupportedError, populate_mirrors
)


class TestPopulateMirrors:
    """The three input forms, normalized into the full structure."""

    def test_str_is_a_single_mirror(self):
        """A str is one mirror named 'default'."""
        assert populate_mirrors('http://only/') == {
            'default': {
                'default': 'http://only',
            },
        }

    def test_flat_dict_entries_are_their_own_group(self):
        """In the flat form the key is the mirror name of a group of
        its own."""
        assert populate_mirrors({
            'cn': 'http://cn',
            'global': 'http://global',
        }) == {
            'cn': {
                'cn': 'http://cn',
            },
            'global': {
                'global': 'http://global',
            },
        }

    def test_full_structure_is_kept(self):
        """The full structure passes through, the order is kept."""
        mirrors = {
            'cn': {
                '123pan': 'http://pan',
                'tencent-cos': 'http://cos',
            },
            'global': {
                'global': 'http://global',
            },
        }
        populated = populate_mirrors(mirrors)
        assert populated == mirrors
        assert list(populated) == ['cn', 'global']
        assert list(populated['cn']) == ['123pan', 'tencent-cos']

    def test_mixed_forms(self):
        """The flat and the nested form may mix per group."""
        assert populate_mirrors({
            'cn': {
                '123pan': 'http://pan',
            },
            'global': 'http://global',
        }) == {
            'cn': {
                '123pan': 'http://pan',
            },
            'global': {
                'global': 'http://global',
            },
        }

    @pytest.mark.parametrize('mirrors, error', [
        ({}, ValueError),
        ({'cn': {}}, ValueError),
        ([], TypeError),
        ({'cn': 123}, TypeError),
        ({1: {'a': 'http://a'}}, TypeError),
    ])
    def test_invalid_input(self, mirrors, error):
        """An empty input or a malformed structure is rejected."""
        with pytest.raises(error):
            populate_mirrors(mirrors)

    @pytest.mark.parametrize('name', ['', 'a/b', 'a b', '..', 'x' * 65])
    def test_invalid_name(self, name):
        """A malformed mirror name is rejected."""
        with pytest.raises(ValueError):
            populate_mirrors({
                'cn': {
                    name: 'http://pan',
                },
            })

    def test_duplicate_name_across_groups(self):
        """A mirror name must be unique across the groups."""
        with pytest.raises(ValueError, match='Duplicate mirror name'):
            populate_mirrors({
                'cn': {
                    '123pan': 'http://a',
                },
                'global': {
                    '123pan': 'http://b',
                },
            })

    @pytest.mark.parametrize('url', [
        '', 'ftp://h', 'http://', 'http:///', 'h', 'http:/h', 'https://', '://x',
    ])
    def test_invalid_url(self, url):
        """A url that is not an http/https url is rejected."""
        with pytest.raises(ValueError):
            populate_mirrors({
                'cn': {
                    'a': url,
                },
            })


class TestMirrors:
    """Mirrors.from_input() and the shared lookups."""

    def test_from_input(self):
        """The three forms are accepted, a Mirrors is returned as-is."""
        assert Mirrors.from_input('http://only/').groups == {'default': {'default': 'http://only'}}
        mirrors = Mirrors.from_input({'cn': 'http://cn', 'global': 'http://global'})
        assert Mirrors.from_input(mirrors) is mirrors
        assert mirrors.url_of('cn') == 'http://cn'
        assert mirrors.url_of('nope') == ''

    def test_single(self):
        """A single mirror name is resolved, a multi one is not."""
        assert Mirrors.from_input('http://only').single == 'default'
        assert Mirrors.from_input({'cn': {'a': 'http://a'}}).single == 'a'
        assert Mirrors.from_input({'cn': 'http://cn', 'global': 'http://global'}).single == ''

    def test_digest_covers_the_structure(self):
        """The fingerprint is of the group boundaries and the names in
        their order; the urls and the group names are not part of it."""
        a = Mirrors({'cn': {'a': 'http://1', 'b': 'http://2'}, 'global': {'g': 'http://3'}})
        b = Mirrors({'cn': {'a': 'http://1', 'b': 'http://2'}, 'global': {'g': 'http://3'}})
        # a member reorder changes the priority order
        c = Mirrors({'cn': {'b': 'http://2', 'a': 'http://1'}, 'global': {'g': 'http://3'}})
        # a url change keeps the identity of the mirrors
        d = Mirrors({'cn': {'a': 'http://9', 'b': 'http://2'}, 'global': {'g': 'http://3'}})
        # a group name is a label only
        e = Mirrors({'cn-main': {'a': 'http://1', 'b': 'http://2'}, 'global': {'g': 'http://3'}})
        # moving a member to another group changes the semantics
        f = Mirrors({'cn': {'a': 'http://1'}, 'global': {'g': 'http://3', 'b': 'http://2'}})
        assert a.digest == b.digest
        assert a.digest == d.digest
        assert a.digest == e.digest
        assert a.digest != c.digest
        assert a.digest != f.digest

    def test_invalid_mirrors_are_rejected(self):
        """The input validation of populate_mirrors() applies."""
        with pytest.raises(ValueError):
            Mirrors({})
        with pytest.raises(TypeError):
            Mirrors(['http://a'])
        with pytest.raises(ValueError):
            Mirrors({'cn': {'a b': 'http://a'}})
        with pytest.raises(ValueError, match='Duplicate mirror name'):
            Mirrors({'cn': {'a': 'http://a'}, 'global': {'a': 'http://b'}})


class TestErrors:
    """The exceptions of the module."""

    def test_all_mirrors_failed_is_an_exception(self):
        assert issubclass(AllMirrorsFailedError, Exception)
        assert str(AllMirrorsFailedError('a: down; b: gone')) == 'a: down; b: gone'

    def test_range_unsupported_is_an_exception(self):
        assert issubclass(RangeUnsupportedError, Exception)
