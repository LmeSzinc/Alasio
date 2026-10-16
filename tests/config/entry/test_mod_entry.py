"""
Tests for the mod entry fields of ModEntryInfo (alasio/config/entry/const.py):
the update source of a mod (mirrors), in the input forms of
Mirrors.from_input (see doc/2026-10-05_mod-update-backend-integration.md
§9), the source of the distributions installed with pip (pypi_mirrors and
its default), and the dependency fields (requirements_file and
pack_update_deps).
"""
from copy import deepcopy

import pytest

from alasio.config.entry.const import DEFAULT_PYPI_MIRRORS, ModEntryInfo
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


class TestPypiMirrorsDefault:
    """The default pypi mirrors of the mods (DEFAULT_PYPI_MIRRORS)."""

    def test_content(self):
        """The cn group (tsinghua, aliyun, tencent, in the preference order)
        and the official PyPI group."""
        assert list(DEFAULT_PYPI_MIRRORS) == ['cn', 'global']
        assert list(DEFAULT_PYPI_MIRRORS['cn']) == ['tsinghua', 'aliyun', 'tencent']
        assert list(DEFAULT_PYPI_MIRRORS['global']) == ['pypi']
        assert DEFAULT_PYPI_MIRRORS == {
            'cn': {
                'tsinghua': 'https://pypi.tuna.tsinghua.edu.cn/simple',
                'aliyun': 'https://mirrors.aliyun.com/pypi/simple',
                'tencent': 'https://mirrors.cloud.tencent.com/pypi/simple',
            },
            'global': {
                'pypi': 'https://pypi.org/simple',
            },
        }

    def test_parses_as_mirrors(self):
        """The default is a usable mirror structure: two groups, unique names
        and http urls."""
        mirrors = Mirrors.from_input(DEFAULT_PYPI_MIRRORS)

        assert list(mirrors.groups) == ['cn', 'global']
        assert list(mirrors.groups['cn']) == ['tsinghua', 'aliyun', 'tencent']
        assert list(mirrors.groups['global']) == ['pypi']
        assert mirrors.urls == {
            'tsinghua': 'https://pypi.tuna.tsinghua.edu.cn/simple',
            'aliyun': 'https://mirrors.aliyun.com/pypi/simple',
            'tencent': 'https://mirrors.cloud.tencent.com/pypi/simple',
            'pypi': 'https://pypi.org/simple',
        }
        assert mirrors.single == ''


class TestModEntryInfoPypiMirrors:
    """The pypi_mirrors field of a mod entry and the record name of it."""

    def test_default(self):
        """A mod that declares nothing uses the default mirrors, as a copy of
        its own: an edit of one entry never reaches the default or another
        entry."""
        first = ModEntryInfo(name='a')
        second = ModEntryInfo(name='b')

        assert first.pypi_mirrors == DEFAULT_PYPI_MIRRORS
        assert first.pypi_mirrors is not DEFAULT_PYPI_MIRRORS

        first.pypi_mirrors['cn'].pop('tsinghua')

        assert 'tsinghua' in second.pypi_mirrors['cn']
        assert 'tsinghua' in DEFAULT_PYPI_MIRRORS['cn']

    @pytest.mark.parametrize('mirrors, urls', [
        # a single url: the whole source is one private index
        (
            'https://pack.example.com/pypi/simple',
            {'default': 'https://pack.example.com/pypi/simple'},
        ),
        # the nested form: a private index next to the official PyPI
        (
            {'private': {'example': 'https://pack.example.com/pypi/simple'},
             'global': {'pypi': 'https://pypi.org/simple'}},
            {'example': 'https://pack.example.com/pypi/simple', 'pypi': 'https://pypi.org/simple'},
        ),
    ])
    def test_override(self, mirrors, urls):
        """A mod with a source of its own keeps the declared mirrors."""
        entry = ModEntryInfo(name='m', pypi_mirrors=mirrors)

        assert entry.pypi_mirrors == mirrors
        assert Mirrors.from_input(entry.pypi_mirrors).urls == urls

    def test_record_name_of_the_default(self):
        """The mods that keep the default share one record: the name of the
        default is ''."""
        assert ModEntryInfo(name='a').pypi_record_name == ''
        assert ModEntryInfo(name='b', pypi_mirrors=deepcopy(DEFAULT_PYPI_MIRRORS)).pypi_record_name == ''

    def test_record_name_of_an_own_source(self):
        """A mod with a source of its own keeps a record of its own, keyed by
        the mod name: its selection never overwrites the shared one."""
        first = ModEntryInfo(name='a', pypi_mirrors={'cn': {'self': 'https://self.example/simple'}})
        second = ModEntryInfo(name='b', pypi_mirrors={'cn': {'self': 'https://self.example/simple'}})

        assert first.pypi_record_name == 'a'
        assert second.pypi_record_name == 'b'

    def test_record_name_compares_the_digest(self):
        """The compare is on the digest of the mirror structure: the names
        and their order make the structure, the urls do not."""
        # another member order is another structure: the mod keeps a record
        # of its own
        reordered = deepcopy(DEFAULT_PYPI_MIRRORS)
        reordered['cn'] = dict(reversed(reordered['cn'].items()))
        assert ModEntryInfo(name='m', pypi_mirrors=reordered).pypi_record_name == 'm'

        # the urls are read from the entry at every use and are not part of
        # the digest: the same names share the record of the default
        fetched = deepcopy(DEFAULT_PYPI_MIRRORS)
        fetched['cn']['aliyun'] = 'https://mirrors.example.com/pypi/simple'
        assert ModEntryInfo(name='m', pypi_mirrors=fetched).pypi_record_name == ''

    def test_record_name_is_cached(self):
        """The name is a cached property: the entry is created with its
        mirrors, a later edit of them never changes the name."""
        entry = ModEntryInfo(name='m', pypi_mirrors={'cn': {'self': 'https://self.example/simple'}})
        assert entry.pypi_record_name == 'm'

        entry.pypi_mirrors = deepcopy(DEFAULT_PYPI_MIRRORS)

        assert entry.pypi_record_name == 'm'

    def test_copy_keeps_the_pypi_mirrors(self):
        """ModEntryInfo.copy() (deepcopy) keeps the declared pypi mirrors."""
        entry = ModEntryInfo(name='m', pypi_mirrors={'cn': {'self': 'https://self.example/simple'}})

        other = entry.copy()

        assert other.pypi_mirrors == entry.pypi_mirrors
        assert other.pypi_mirrors is not entry.pypi_mirrors


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
