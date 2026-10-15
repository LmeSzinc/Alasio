"""
Tests for the mod entry fields of ModEntryInfo (alasio/config/entry/const.py):
the update source of a mod (mirrors), in the input forms of
Mirrors.from_input (see doc/2026-10-05_mod-update-backend-integration.md
§9), and the dependency fields (requirements_file and pack_update_deps).
"""
import pytest

from alasio.config.entry.const import ModEntryInfo
from alasio.deploy.httpclient.probe_mirrors import Mirrors


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


class TestModEntryInfoDependencies:
    """The dependency fields of a mod entry."""

    def test_defaults(self):
        """By default a mod reads requirements.txt and updates nothing by pack."""
        entry = ModEntryInfo(name='m')
        assert entry.requirements_file == 'requirements.txt'
        assert entry.pack_update_deps == {}

    def test_declared(self):
        """The declared names and the requirements path are kept."""
        entry = ModEntryInfo(
            name='m',
            requirements_file='deploy/AidLux/0.92/requirements.txt',
            pack_update_deps={'httpx': None, 'ruamel.yaml': None},
        )
        assert entry.requirements_file == 'deploy/AidLux/0.92/requirements.txt'
        assert list(entry.pack_update_deps) == ['httpx', 'ruamel.yaml']

    def test_copy_keeps_the_dependencies(self):
        """ModEntryInfo.copy() (deepcopy) keeps the declared pack updated names."""
        entry = ModEntryInfo(name='m', pack_update_deps={'httpx': None})

        other = entry.copy()

        assert other.pack_update_deps == entry.pack_update_deps
        assert other.pack_update_deps is not entry.pack_update_deps
