"""
Tests for ModEntryInfo.mirrors (alasio/config/entry/const.py): the update
source of a mod, in the input forms of Mirrors.from_input (see
doc/2026-10-05_mod-update-backend-integration.md §9).
"""
import pytest

from alasio.config.entry.const import ModEntryInfo
from alasio.deploy.httpclient.probe import Mirrors


class TestModEntryInfoMirrors:
    """The mirrors field of a mod entry."""

    def test_default_is_empty(self):
        """A mod without a declared update source is unmanaged."""
        entry = ModEntryInfo(name='m')
        assert entry.mirrors == ''

    @pytest.mark.parametrize('mirrors, names', [
        # a single mirror: only one candidate, no probe
        ('https://only.example/alas', {'default'}),
        # name to url: each mirror is a group of its own
        (
            {'cn': 'https://cn.example/alas', 'global': 'https://global.example/alas'},
            {'cn', 'global'},
        ),
        # group to mirrors: the members of a group are tried in order
        (
            {'cn': {'123pan': 'https://123pan.example', 'self-host': 'https://self.example'},
             'global': {'global': 'https://global.example'}},
            {'123pan', 'self-host', 'global'},
        ),
        # the flat and the nested form mixed per group
        (
            {'cn': {'123pan': 'https://123pan.example'}, 'global': 'https://global.example'},
            {'123pan', 'global'},
        ),
    ])
    def test_accepts_the_mirror_input_forms(self, mirrors, names):
        """The field carries the input and Mirrors.from_input parses it."""
        entry = ModEntryInfo(name='m', mirrors=mirrors)

        assert entry.mirrors == mirrors
        parsed = Mirrors.from_input(entry.mirrors)
        assert set(parsed.urls) == names

    def test_copy_keeps_the_mirrors(self):
        """ModEntryInfo.copy() (deepcopy) keeps the update source."""
        entry = ModEntryInfo(name='m', mirrors={'cn': {'123pan': 'https://123pan.example'}})

        other = entry.copy()

        assert other.mirrors == entry.mirrors
        assert other.mirrors is not entry.mirrors
