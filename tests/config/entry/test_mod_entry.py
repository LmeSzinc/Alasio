"""
Tests for the mod entry of a mod (alasio/config/entry/const.py):

- ``ModEntryModel``, the declaration itself: the update source of a mod
  (mirrors), in the input forms of Mirrors.from_input (see
  doc/2026-10-05_mod-update-backend-integration.md §9), the source of the
  distributions installed with pip (pypi_mirrors and its default), the
  indexes the update packs provide (update_provide_pypi_mirrors and its
  default), and the dependency fields (requirements_file and
  update_pack_deps);
- ``ModEntry``, the handle: the location of a declaration and the
  declaration it reads.
"""
from copy import deepcopy

import pytest

from alasio.base.filter import parse_filter
from alasio.config.entry.mod_entry import (
    DEFAULT_PYPI_MIRRORS, DEFAULT_UPDATE_PROVIDE_PYPI_MIRRORS, ModEntry, ModEntryModel
)
from alasio.deploy.httpclient.probe_mirrors import Mirrors
from alasio.ext.path import PathStr
from alasio.testing.filesystem import fs  # noqa: F401


def entry_of(model, root=''):
    """
    Wrap a declaration in an entry of a location.

    Args:
        model (ModEntryModel): Declaration
        root (str): Root folder of the mod. Defaults to ''

    Returns:
        ModEntry:
    """
    return ModEntry(root=root, data=model)


class TestModEntryModelMirrors:
    """The mirrors field of a declaration."""

    def test_default_is_empty(self):
        """A mod without a declared update source is unmanaged."""
        assert ModEntryModel(name='m').mirrors == ''

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
        model = ModEntryModel(name='m', mirrors=mirrors)

        assert model.mirrors == mirrors
        parsed = Mirrors.from_input(model.mirrors)
        assert set(parsed.urls) == names

    def test_copy_keeps_the_mirrors(self):
        """copy() (deepcopy) keeps the update source."""
        entry = entry_of(ModEntryModel(name='m', mirrors={'cn': {'123pan': 'https://123pan.example'}}))

        other = entry.copy()

        assert other.data.mirrors == entry.data.mirrors
        assert other.data.mirrors is not entry.data.mirrors


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


class TestModEntryModelPypiMirrors:
    """The pypi_mirrors field of a declaration and the record name of it."""

    def test_default(self):
        """A mod that declares nothing uses the default mirrors, as a copy of
        its own: an edit of one entry never reaches the default or another
        entry."""
        first = ModEntryModel(name='a')
        second = ModEntryModel(name='b')

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
        model = ModEntryModel(name='m', pypi_mirrors=mirrors)

        assert model.pypi_mirrors == mirrors
        assert Mirrors.from_input(model.pypi_mirrors).urls == urls

    def test_record_name_of_the_default(self):
        """The mods that keep the default share one record: the name of the
        default is ''."""
        assert entry_of(ModEntryModel(name='a')).pypi_record_name == ''
        assert entry_of(ModEntryModel(name='b', pypi_mirrors=deepcopy(DEFAULT_PYPI_MIRRORS))).pypi_record_name == ''

    def test_record_name_of_an_own_source(self):
        """A mod with a source of its own keeps a record of its own, keyed by
        the mod name: its selection never overwrites the shared one."""
        first = entry_of(ModEntryModel(name='a', pypi_mirrors={'cn': {'self': 'https://self.example/simple'}}))
        second = entry_of(ModEntryModel(name='b', pypi_mirrors={'cn': {'self': 'https://self.example/simple'}}))

        assert first.pypi_record_name == 'a'
        assert second.pypi_record_name == 'b'

    def test_record_name_compares_the_digest(self):
        """The compare is on the digest of the mirror structure: the names
        and their order make the structure, the urls do not."""
        # another member order is another structure: the mod keeps a record
        # of its own
        reordered = deepcopy(DEFAULT_PYPI_MIRRORS)
        reordered['cn'] = dict(reversed(reordered['cn'].items()))
        assert entry_of(ModEntryModel(name='m', pypi_mirrors=reordered)).pypi_record_name == 'm'

        # the urls are read from the entry at every use and are not part of
        # the digest: the same names share the record of the default
        fetched = deepcopy(DEFAULT_PYPI_MIRRORS)
        fetched['cn']['aliyun'] = 'https://mirrors.example.com/pypi/simple'
        assert entry_of(ModEntryModel(name='m', pypi_mirrors=fetched)).pypi_record_name == ''

    def test_record_name_is_cached(self):
        """The name is a cached property: the entry is created with its
        mirrors, a later edit of them never changes the name."""
        entry = entry_of(ModEntryModel(name='m', pypi_mirrors={'cn': {'self': 'https://self.example/simple'}}))
        assert entry.pypi_record_name == 'm'

        entry.data.pypi_mirrors = deepcopy(DEFAULT_PYPI_MIRRORS)

        assert entry.pypi_record_name == 'm'

    def test_copy_keeps_the_pypi_mirrors(self):
        """copy() (deepcopy) keeps the declared pypi mirrors."""
        entry = entry_of(ModEntryModel(name='m', pypi_mirrors={'cn': {'self': 'https://self.example/simple'}}))

        other = entry.copy()

        assert other.data.pypi_mirrors == entry.data.pypi_mirrors
        assert other.data.pypi_mirrors is not entry.data.pypi_mirrors


class TestModEntryModelUpdateProvidePypiMirrors:
    """The update_provide_pypi_mirrors field of a declaration."""

    def test_default(self):
        """A mod that declares nothing provides the py38deps index."""
        assert DEFAULT_UPDATE_PROVIDE_PYPI_MIRRORS == (
            'https://py38deps.github.io/py38deps/simple/',)
        assert ModEntryModel(name='m').update_provide_pypi_mirrors == \
            list(DEFAULT_UPDATE_PROVIDE_PYPI_MIRRORS)

    def test_declared(self):
        """A mod keeps the indexes it declares, in the declared order."""
        model = ModEntryModel(
            name='m',
            update_provide_pypi_mirrors=['https://a.example/simple', 'https://b.example/simple'],
        )
        assert model.update_provide_pypi_mirrors == [
            'https://a.example/simple', 'https://b.example/simple']

    def test_empty(self):
        """A mod that provides no index declares an empty list."""
        assert ModEntryModel(name='m', update_provide_pypi_mirrors=[]).update_provide_pypi_mirrors == []

    def test_copy_keeps_the_provided_indexes(self):
        """copy() (deepcopy) keeps the declared indexes."""
        entry = entry_of(ModEntryModel(name='m', update_provide_pypi_mirrors=['https://a.example/simple']))

        other = entry.copy()

        assert other.data.update_provide_pypi_mirrors == entry.data.update_provide_pypi_mirrors


class TestModEntryModelDependencies:
    """The dependency fields of a declaration."""

    def test_defaults(self):
        """By default a mod reads requirements.txt and updates nothing by pack."""
        model = ModEntryModel(name='m')
        assert model.requirements_file == 'requirements.txt'
        assert model.update_pack_deps == []

    def test_declared(self):
        """The declared names and the requirements path are kept."""
        model = ModEntryModel(
            name='m',
            requirements_file='deploy/AidLux/0.92/requirements.txt',
            update_pack_deps=['httpx', 'ruamel.yaml'],
        )
        assert model.requirements_file == 'deploy/AidLux/0.92/requirements.txt'
        assert model.update_pack_deps == ['httpx', 'ruamel.yaml']

    def test_copy_keeps_the_dependencies(self):
        """copy() (deepcopy) keeps the declared pack updated names."""
        entry = entry_of(ModEntryModel(name='m', update_pack_deps=['httpx']))

        other = entry.copy()

        assert other.data.update_pack_deps == entry.data.update_pack_deps


class TestModEntryModelSchedulerPriority:
    """The scheduler priority of a declaration."""

    def test_default(self):
        """A mod that declares no order runs the library default order."""
        model = ModEntryModel(name='m')

        assert list(parse_filter(model.scheduler_priority)) == ['RestartDevice', 'RestartGame']

    def test_declared(self):
        """The declared DSL is kept as it is written, parse_filter reads it."""
        model = ModEntryModel(name='m', scheduler_priority='A > B > C')

        assert model.scheduler_priority == 'A > B > C'
        assert list(parse_filter(model.scheduler_priority)) == ['A', 'B', 'C']


class TestModEntryModelAssetLang:
    """The asset languages of an entry."""

    def test_iter_asset_lang(self):
        """The shared assets are tried first, then the declared languages."""
        entry = ModEntry(data=ModEntryModel(name='m', asset_lang=['cn', 'jp']))

        assert list(entry.iter_asset_lang()) == ['', 'cn', 'jp']


class TestModEntryLayout:
    """The location of an entry, one of the two layouts the framework knows."""

    def test_default(self):
        """A mod is module/config, the only layout the walk up looks for."""
        assert ModEntry().path_config == 'module/config'

    def test_alasio(self):
        """The library keeps its own config inside the package."""
        assert ModEntry.alasio.path_config == 'alasio/config'

    def test_unknown_layout(self):
        """A layout no reader looks for is refused at construction."""
        with pytest.raises(ValueError, match='path_config must be'):
            ModEntry(path_config='module/configs')

    def test_root_is_absolute(self, fs):
        """A relative root is resolved against the working folder, once."""
        fs.create_dir('/work/mod')
        fs.chdir('/work')

        root = PathStr.new(ModEntry('mod').root)

        assert root.name == 'mod'
        assert root.isdir()

    def test_empty_root(self):
        """An entry without a location keeps an empty root."""
        assert ModEntry().root == ''

    def test_copy_keeps_the_layout(self):
        """copy() (deepcopy) keeps the location of the entry."""
        entry = ModEntry.alasio

        assert entry.copy().path_config == 'alasio/config'


class TestModEntryData:
    """The declaration an entry answers."""

    def test_given_data(self):
        """A caller that holds a declaration does not touch the filesystem."""
        model = ModEntryModel(name='m')

        assert ModEntry(data=model).data is model

    def test_read_from_the_location(self, fs):
        """The declaration is read from the config folder of the root."""
        fs.create_file('/mod/module/config/_index/mod_entry.yaml', contents='name: m\n')

        entry = ModEntry('/mod')

        assert entry.data.name == 'm'

    def test_data_is_cached(self, fs):
        """The file is read once, a later edit is not seen."""
        fs.create_file('/mod/module/config/_index/mod_entry.yaml', contents='name: m\n')
        entry = ModEntry('/mod')
        assert entry.data.name == 'm'

        with open('/mod/module/config/_index/mod_entry.yaml', 'w', encoding='utf-8') as f:
            f.write('name: changed\n')

        assert entry.data.name == 'm'

    def test_missing_declaration(self, fs):
        """A folder that declares no entry answers no data."""
        fs.create_dir('/mod')

        with pytest.raises(FileNotFoundError):
            _ = ModEntry('/mod').data

    def test_alasio_is_a_singleton(self):
        """The entry of the library is created once."""
        assert ModEntry.alasio is ModEntry.alasio
        assert ModEntry.alasio.data.name == 'alasio'


class TestModEntryExist:
    """The readiness of a mod, the generated index of its config."""

    def test_missing(self, fs):
        """A mod without the generated index is not ready."""
        fs.create_file('/mod/module/config/_index/mod_entry.yaml', contents='name: m\n')

        assert ModEntry('/mod').exist() is False

    def test_generated(self, fs):
        """A mod with the generated index is ready."""
        fs.create_file('/mod/module/config/_index/config.index.json', contents='{}')

        assert ModEntry('/mod').exist() is True
