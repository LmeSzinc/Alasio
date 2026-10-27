"""
Tests for parse_entry: read the declarations from the source of a mod entry.

The parser reads the module level `entry` of a mod entry, its update_pack_deps,
pypi_mirrors and update_provide_pypi_mirrors declarations, with ast, without
executing the source: the value node of every key is extracted, evaluated, and
validated against ModEntryInfo. The tests cover the static forms the parser
accepts, the order of the names, and the source forms it refuses: a pack must
never silently lose a dependency channel, or fetch from a mirror the entry did
not declare, because the declaration could not be read.
"""
import pytest

from alasio.config.entry.const import (
    DEFAULT_PYPI_MIRRORS, DEFAULT_UPDATE_PROVIDE_PYPI_MIRRORS, DICT_MOD_ENTRY as LIBRARY_MOD_ENTRY
)
from alasio.deploy_dev.pack_server import parse_entry
from alasio.deploy_dev.pack_server.parse_entry import ENTRY_FILE, PARSED_KEYS, PackRepoEntry, parse_mod_entry
from alasio.git.mock.mock_repo import MockGitRepo


def parse(source):
    """
    Parse a source of the tests.

    Args:
        source (str | bytes): Source of an entry module

    Returns:
        tuple[str, ...]: Declared update_pack_deps names
    """
    return parse_mod_entry(source).update_pack_deps


def parse_mirrors(source):
    """
    Parse the pypi_mirrors declaration of a source of the tests.

    Args:
        source (str | bytes): Source of an entry module

    Returns:
        str | dict: The declared mirror input
    """
    return parse_mod_entry(source).pypi_mirrors


def parse_provide(source):
    """
    Parse the update_provide_pypi_mirrors declaration of a source of the tests.

    Args:
        source (str | bytes): Source of an entry module

    Returns:
        tuple[str, ...]: The declared provided index urls
    """
    return parse_mod_entry(source).update_provide_pypi_mirrors


class TestUpdatePackDepsForms:
    """The static declarations the parser reads."""

    def test_dict(self):
        """A dict literal declares its keys, in the order they are written."""
        source = """\
from alasio.config.entry.const import ModEntryInfo

entry = ModEntryInfo(
    name='m',
    update_pack_deps={'httpx': None, 'ruamel.yaml': None},
)
"""
        result = parse(source)
        assert result == ('httpx', 'ruamel.yaml')

    def test_list(self):
        """A list literal declares its items."""
        source = """\
entry = ModEntryInfo(name='m', update_pack_deps=['httpx', 'starlette'])
"""
        assert parse(source) == ('httpx', 'starlette')

    def test_tuple(self):
        """A tuple literal declares its items."""
        source = """\
entry = ModEntryInfo(name='m', update_pack_deps=('httpx', 'starlette'))
"""
        assert parse(source) == ('httpx', 'starlette')

    def test_set(self):
        """A set literal declares its items."""
        source = """\
entry = ModEntryInfo(name='m', update_pack_deps={'httpx', 'starlette'})
"""
        assert parse(source) == ('httpx', 'starlette')

    def test_dict_fromkeys(self):
        """dict.fromkeys() declares the keys of its iterable."""
        source = """\
entry = ModEntryInfo(name='m', update_pack_deps=dict.fromkeys(['httpx', 'starlette']))
"""
        assert parse(source) == ('httpx', 'starlette')

    def test_dict_fromkeys_with_value(self):
        """The value of dict.fromkeys() is ignored, only the names matter."""
        source = """\
entry = ModEntryInfo(name='m', update_pack_deps=dict.fromkeys(('httpx',), True))
"""
        assert parse(source) == ('httpx',)

    def test_dict_fromkeys_of_constant(self):
        """dict.fromkeys() reads a module level constant as its iterable."""
        source = """\
NAMES = ['httpx', 'starlette']
entry = ModEntryInfo(name='m', update_pack_deps=dict.fromkeys(NAMES))
"""
        assert parse(source) == ('httpx', 'starlette')

    def test_module_constant_dict(self):
        """A module level dict constant is read by name."""
        source = """\
DEPS = {'httpx': None, 'ruamel.yaml': None}
entry = ModEntryInfo(name='m', update_pack_deps=DEPS)
"""
        assert parse(source) == ('httpx', 'ruamel.yaml')

    def test_module_constant_list(self):
        """A module level list constant is read by name."""
        source = """\
DEPS = ['httpx', 'starlette']
entry = ModEntryInfo(name='m', update_pack_deps=DEPS)
"""
        assert parse(source) == ('httpx', 'starlette')

    def test_attribute_assignment(self):
        """entry.update_pack_deps may be assigned after the construction."""
        source = """\
entry = ModEntryInfo(name='m')
entry.update_pack_deps = ['httpx', 'starlette']
"""
        assert parse(source) == ('httpx', 'starlette')

    def test_attribute_overrides_keyword(self):
        """A later attribute assignment replaces the keyword value."""
        source = """\
entry = ModEntryInfo(name='m', update_pack_deps=['httpx'])
entry.update_pack_deps = ['starlette']
"""
        assert parse(source) == ('starlette',)

    def test_last_entry_wins(self):
        """A later construction of entry replaces the earlier declaration."""
        source = """\
entry = ModEntryInfo(name='m', update_pack_deps=['httpx'])
entry = ModEntryInfo(name='m', update_pack_deps=['starlette'])
"""
        assert parse(source) == ('starlette',)

    def test_new_entry_resets(self):
        """A construction without update_pack_deps clears the earlier names."""
        source = """\
entry = ModEntryInfo(name='m', update_pack_deps=['httpx'])
entry = ModEntryInfo(name='m')
"""
        assert parse(source) == ()

    def test_no_update_pack_deps(self):
        """An entry without the declaration has no pack updated dependency."""
        source = """\
entry = ModEntryInfo(name='m')
"""
        assert parse(source) == ()

    def test_no_entry(self):
        """A module without an entry has no declaration."""
        source = """\
from alasio.config.entry.const import ModEntryInfo
"""
        assert parse(source) == ()

    def test_none(self):
        """None is an empty declaration."""
        source = """\
entry = ModEntryInfo(name='m', update_pack_deps=None)
"""
        assert parse(source) == ()

    @pytest.mark.parametrize('value', [
        '{}',
        '[]',
        '()',
        'dict.fromkeys([])',
    ])
    def test_empty(self, value):
        """An empty collection declares no name."""
        source = f"entry = ModEntryInfo(name='m', update_pack_deps={value})\n"
        assert parse(source) == ()

    def test_values_are_ignored(self):
        """Only the keys of the declaration are read, a dynamic value is fine."""
        source = """\
entry = ModEntryInfo(name='m', update_pack_deps={'httpx': build_marker()})
"""
        assert parse(source) == ('httpx',)

    def test_key_constant(self):
        """A dict key may be a module level constant."""
        source = """\
NAME = 'httpx'
entry = ModEntryInfo(name='m', update_pack_deps={NAME: None})
"""
        assert parse(source) == ('httpx',)

    def test_duplicates_collapse(self):
        """A name that appears several times is declared once."""
        source = """\
entry = ModEntryInfo(name='m', update_pack_deps=['httpx', 'httpx', 'starlette'])
"""
        assert parse(source) == ('httpx', 'starlette')

    def test_other_fields_are_ignored(self):
        """The other fields and attributes of the entry do not matter."""
        source = """\
entry = ModEntryInfo(name='m', root='mod', update_pack_deps=['httpx'])
entry.root = 'elsewhere'
"""
        assert parse(source) == ('httpx',)

    def test_conditional_unrelated_attribute(self):
        """A conditional assignment to another entry attribute is not a declaration."""
        source = """\
entry = ModEntryInfo(name='m', update_pack_deps=['httpx'])
if True:
    entry.root = 'elsewhere'
"""
        assert parse(source) == ('httpx',)

    def test_bytes_source(self):
        """Bytes are parsed with the encoding declaration of the source."""
        source = """\
# -*- coding: utf-8 -*-
# 中文注释
entry = ModEntryInfo(name='m', update_pack_deps=['httpx'])
""".encode('utf-8')
        assert parse(source) == ('httpx',)

    def test_qualified_entry_class(self):
        """The entry may be constructed through a qualified ModEntryInfo."""
        source = """\
import alasio.config.entry.const as const

entry = const.ModEntryInfo(name='m')
"""
        assert parse(source) == ()


class TestDictModEntry:
    """The library default entry, looked up by key."""

    def test_default_entry(self):
        """A lookup without an assignment keeps the library entry's value."""
        source = """\
from alasio.config.entry.const import DICT_MOD_ENTRY

entry = DICT_MOD_ENTRY['alas']
"""
        assert parse(source) == tuple(LIBRARY_MOD_ENTRY['alas'].update_pack_deps)

    def test_attribute_assignment(self):
        """An attribute assignment replaces the library entry's value."""
        source = """\
from alasio.config.entry.const import DICT_MOD_ENTRY

entry = DICT_MOD_ENTRY['alas']
entry.update_pack_deps = ['httpx', 'starlette']
"""
        assert parse(source) == ('httpx', 'starlette')

    def test_alias_import(self):
        """The lookup works through an alias of DICT_MOD_ENTRY."""
        source = """\
from alasio.config.entry.const import DICT_MOD_ENTRY as DE

entry = DE['alas']
entry.update_pack_deps = ('httpx',)
"""
        assert parse(source) == ('httpx',)

    def test_module_alias_import(self):
        """The lookup works through a module imported as an alias."""
        source = """\
import alasio.config.entry.const as const

entry = const.DICT_MOD_ENTRY['alas']
entry.update_pack_deps = ('httpx',)
"""
        assert parse(source) == ('httpx',)

    def test_constant_key(self):
        """The key of the lookup may be a module level constant."""
        source = """\
from alasio.config.entry.const import DICT_MOD_ENTRY

NAME = 'alas'
entry = DICT_MOD_ENTRY[NAME]
entry.update_pack_deps = ('httpx',)
"""
        assert parse(source) == ('httpx',)

    def test_real_entry_shape(self):
        """The real repo shape: the lookup, entry.root and the declaration."""
        source = """\
from alasio.config.entry.const import DICT_MOD_ENTRY, ConfigConst as ConfigConst_
from alasio.ext.path import PathStr

entry = DICT_MOD_ENTRY['alas']
entry.root = PathStr.new(__file__).uppath(3)
entry.update_pack_deps = ['httpx', 'starlette']
"""
        assert parse(source) == ('httpx', 'starlette')

    def test_later_construction_resets(self):
        """A later ModEntryInfo construction resets the lookup value."""
        source = """\
from alasio.config.entry.const import DICT_MOD_ENTRY

entry = DICT_MOD_ENTRY['alas']
entry.update_pack_deps = ['httpx']
entry = ModEntryInfo(name='m')
"""
        assert parse(source) == ()

    def test_later_lookup_resets(self):
        """A later lookup resets the value of an earlier construction."""
        source = """\
from alasio.config.entry.const import DICT_MOD_ENTRY

entry = ModEntryInfo(name='m', update_pack_deps=['httpx'])
entry = DICT_MOD_ENTRY['alas']
"""
        assert parse(source) == tuple(LIBRARY_MOD_ENTRY['alas'].update_pack_deps)

    def test_unknown_key(self):
        """A key that the library dict does not hold is refused."""
        source = """\
from alasio.config.entry.const import DICT_MOD_ENTRY

entry = DICT_MOD_ENTRY['does_not_exist']
"""
        with pytest.raises(ValueError, match='unknown DICT_MOD_ENTRY key'):
            parse(source)

    def test_missing_import(self):
        """A bare DICT_MOD_ENTRY is not resolved without its import."""
        source = "entry = DICT_MOD_ENTRY['alas']\n"
        with pytest.raises(ValueError, match='DICT_MOD_ENTRY lookup'):
            parse(source)

    def test_other_import(self):
        """A DICT_MOD_ENTRY imported from another module is not the library."""
        source = """\
from other import DICT_MOD_ENTRY

entry = DICT_MOD_ENTRY['alas']
"""
        with pytest.raises(ValueError, match='DICT_MOD_ENTRY lookup'):
            parse(source)

    def test_other_subscript(self):
        """A subscript of another object is not an entry lookup."""
        source = "entry = OTHER['alas']\n"
        with pytest.raises(ValueError, match='DICT_MOD_ENTRY lookup'):
            parse(source)

    def test_dynamic_key(self):
        """The key of the lookup must be a static value."""
        source = """\
from alasio.config.entry.const import DICT_MOD_ENTRY

NAME = get_name()
entry = DICT_MOD_ENTRY[NAME]
"""
        with pytest.raises(ValueError, match='is not a static value'):
            parse(source)


class TestPackRepoEntry:
    """Read the entry from the git blob of a commit."""

    @staticmethod
    def make_repo(entries):
        """
        Build a mock repo with an entry source per commit.

        Args:
            entries (dict[str, str | None]): {commit: entry source}, a None
                value leaves the commit without an entry file

        Returns:
            MockGitRepo: Repo, the head of master is the last commit
        """
        repo = MockGitRepo()
        parent = None
        for index, (commit, source) in enumerate(entries.items()):
            if source is not None:
                repo.register_file(commit, ENTRY_FILE, source.encode('utf-8'))
            repo.register_commit(
                commit, parents=[parent] if parent else None,
                author_name='Author', author_time=index, message=f'commit {commit}')
            parent = commit
        repo.register_branch('master', parent)
        repo.register_head(parent)
        return repo

    def test_missing(self):
        """A commit without an entry file declares no dependency."""
        repo = self.make_repo({'c1': None})
        assert PackRepoEntry(repo, 'c1').update_pack_deps == ()

    def test_read(self):
        """The declaration is read from the entry blob of the commit."""
        source = "entry = ModEntryInfo(name='m', update_pack_deps=['httpx'])\n"
        repo = self.make_repo({'c1': source})
        assert PackRepoEntry(repo, 'c1').update_pack_deps == ('httpx',)

    def test_dict_mod_entry(self):
        """The library default entry is read from the entry blob."""
        source = """\
from alasio.config.entry.const import DICT_MOD_ENTRY

entry = DICT_MOD_ENTRY['alas']
entry.update_pack_deps = ['httpx']
"""
        repo = self.make_repo({'c1': source})
        assert PackRepoEntry(repo, 'c1').update_pack_deps == ('httpx',)

    def test_pypi_mirrors(self):
        """The mirror declaration is read from the same entry blob."""
        source = "entry = ModEntryInfo(name='m', pypi_mirrors='https://a.example/simple')\n"
        repo = self.make_repo({'c1': source})
        assert PackRepoEntry(repo, 'c1').pypi_mirrors == 'https://a.example/simple'

    def test_pypi_mirrors_default(self):
        """An entry without the declaration gives the library default."""
        source = "entry = ModEntryInfo(name='m', update_pack_deps=('httpx',))\n"
        repo = self.make_repo({'c1': source})
        assert PackRepoEntry(repo, 'c1').pypi_mirrors == DEFAULT_PYPI_MIRRORS

    def test_pypi_mirrors_missing_entry(self):
        """A commit without an entry gives the library default too."""
        repo = self.make_repo({'c1': None})
        assert PackRepoEntry(repo, 'c1').pypi_mirrors == DEFAULT_PYPI_MIRRORS

    def test_pypi_mirrors_default_not_shared(self):
        """The default of a reader is a copy: the library default is never edited."""
        repo = self.make_repo({'c1': None})
        value = PackRepoEntry(repo, 'c1').pypi_mirrors
        value['cn']['tsinghua'] = 'https://edited.example/simple'
        assert DEFAULT_PYPI_MIRRORS['cn']['tsinghua'] != 'https://edited.example/simple'

    def test_update_provide_pypi_mirrors(self):
        """The provided index declaration is read from the entry blob."""
        source = (
            "entry = ModEntryInfo(name='m', "
            "update_provide_pypi_mirrors=('https://a.example/simple',))\n")
        repo = self.make_repo({'c1': source})
        assert PackRepoEntry(repo, 'c1').update_provide_pypi_mirrors == (
            'https://a.example/simple',)

    def test_update_provide_pypi_mirrors_default(self):
        """An entry without the declaration provides the library default."""
        source = "entry = ModEntryInfo(name='m', update_pack_deps=('httpx',))\n"
        repo = self.make_repo({'c1': source})
        assert PackRepoEntry(repo, 'c1').update_provide_pypi_mirrors == \
            DEFAULT_UPDATE_PROVIDE_PYPI_MIRRORS

    def test_update_provide_pypi_mirrors_missing_entry(self):
        """A commit without an entry provides the library default too."""
        repo = self.make_repo({'c1': None})
        assert PackRepoEntry(repo, 'c1').update_provide_pypi_mirrors == \
            DEFAULT_UPDATE_PROVIDE_PYPI_MIRRORS

    def test_readers_share_one_parse(self, monkeypatch):
        """The three readers of the entry share one parse, the mod_entry."""
        source = "entry = ModEntryInfo(name='m', update_pack_deps=('httpx',))\n"
        repo = self.make_repo({'c1': source})
        calls = []
        real = parse_entry.parse_mod_entry

        def counting(source, source_name=''):
            calls.append(source_name)
            return real(source, source_name=source_name)

        monkeypatch.setattr(parse_entry, 'parse_mod_entry', counting)
        entry = PackRepoEntry(repo, 'c1')
        assert entry.update_pack_deps == ('httpx',)
        assert entry.pypi_mirrors == DEFAULT_PYPI_MIRRORS
        assert entry.update_provide_pypi_mirrors == DEFAULT_UPDATE_PROVIDE_PYPI_MIRRORS
        assert len(calls) == 1

    def test_mod_entry_is_cached(self, monkeypatch):
        """The parsed entry is a cached property, the readers reuse it."""
        source = "entry = ModEntryInfo(name='m', update_pack_deps=('httpx',))\n"
        repo = self.make_repo({'c1': source})
        entry = PackRepoEntry(repo, 'c1')
        assert entry.mod_entry is entry.mod_entry

    def test_commit(self):
        """The entry reads the commit it is bound to, not the head."""
        first = "entry = ModEntryInfo(name='m', update_pack_deps=('httpx',))\n"
        second = "entry = ModEntryInfo(name='m', update_pack_deps=('starlette',))\n"
        repo = self.make_repo({'c1': first, 'c2': second})
        assert PackRepoEntry(repo, 'c1').update_pack_deps == ('httpx',)
        assert PackRepoEntry(repo, 'c2').update_pack_deps == ('starlette',)

    def test_error_names_the_commit(self):
        """A broken entry reports the file and the commit."""
        source = "entry = ModEntryInfo(name='m', update_pack_deps=get_deps())\n"
        repo = self.make_repo({'c1': source})
        with pytest.raises(ValueError, match='module/config/const.py at commit c1'):
            PackRepoEntry(repo, 'c1').update_pack_deps

    def test_symlink(self):
        """An entry path that is a symlink is refused, its blob is a path."""

        class FakeFile:
            sha1 = 'x'
            mode = b'120000'

        class FakeRepo:
            dict_object = {'c1': object()}

            def get_file(self, commit, path):
                return FakeFile()

            def cat(self, sha1):
                raise AssertionError('the symlink blob must not be read')

        with pytest.raises(ValueError, match='is a symlink'):
            PackRepoEntry(FakeRepo(), 'c1').update_pack_deps

    def test_repo_is_read_lazily(self):
        """A repo that was not read is read before the entry is looked up."""

        class LazyRepo:
            def __init__(self):
                self.read = False

            def read_lazy(self):
                self.read = True
                self.dict_object = {}
                return self

            def get_file(self, commit, path):
                assert self.read
                return None

        repo = LazyRepo()
        assert PackRepoEntry(repo, 'c1').update_pack_deps == ()
        assert repo.read is True


class TestUpdatePackDepsErrors:
    """The source forms the parser refuses."""

    def test_invalid_python(self):
        """A syntax error is reported with the line of the source."""
        with pytest.raises(ValueError, match='invalid Python at line'):
            parse("entry = ModEntryInfo(name='m'\n")

    def test_source_name(self):
        """The name of the source is added to the error message."""
        source = "entry = ModEntryInfo(name='m', update_pack_deps=get_deps())\n"
        with pytest.raises(ValueError, match='module/config/const.py at c3'):
            parse_mod_entry(source, source_name='module/config/const.py at c3')

    def test_entry_is_not_a_call(self):
        """The entry must be constructed by a call."""
        with pytest.raises(ValueError, match='entry must be constructed by a call'):
            parse("entry = 1\n")

    def test_entry_is_a_helper_call(self):
        """A helper call that does not name update_pack_deps is not read."""
        with pytest.raises(ValueError, match='does not declare update_pack_deps'):
            parse('entry = build_entry()\n')

    def test_entry_kwargs(self):
        """A **kwargs of the entry construction may hide the declaration."""
        with pytest.raises(ValueError, match='kwargs in the entry construction'):
            parse("entry = ModEntryInfo(name='m', **kwargs)\n")

    def test_annotation_without_value(self):
        """A bare annotation does not construct an entry."""
        with pytest.raises(ValueError, match='entry has no value'):
            parse('entry: object\n')

    def test_dynamic_value(self):
        """A function call is not a static declaration."""
        with pytest.raises(ValueError, match='unsupported call'):
            parse("entry = ModEntryInfo(name='m', update_pack_deps=get_deps())\n")

    def test_unknown_name(self):
        """A name that is not assigned before the declaration is unknown."""
        with pytest.raises(ValueError, match='unknown name'):
            parse("entry = ModEntryInfo(name='m', update_pack_deps=DEPS)\n")

    def test_name_of_dynamic_value(self):
        """A name that holds a dynamic value is not a static declaration."""
        source = """\
DEPS = get_deps()
entry = ModEntryInfo(name='m', update_pack_deps=DEPS)
"""
        with pytest.raises(ValueError, match='is not a static value'):
            parse(source)

    def test_bare_string(self):
        """A bare string is not a collection of names: the model refuses it."""
        with pytest.raises(ValueError, match='invalid entry value'):
            parse("entry = ModEntryInfo(name='m', update_pack_deps='httpx')\n")

    def test_non_string_name(self):
        """Every declared name must be a string: the model refuses it."""
        with pytest.raises(ValueError, match='invalid entry value'):
            parse("entry = ModEntryInfo(name='m', update_pack_deps=[1])\n")

    def test_non_string_key(self):
        """Every key of a dict declaration must be a string: the model refuses it."""
        with pytest.raises(ValueError, match='invalid entry value'):
            parse("entry = ModEntryInfo(name='m', update_pack_deps={1: None})\n")

    def test_dict_unpacking(self):
        """A dict unpacking is not a static declaration of keys."""
        with pytest.raises(ValueError, match='dict unpacking'):
            parse("entry = ModEntryInfo(name='m', update_pack_deps={**OTHER})\n")

    def test_fromkeys_of_string(self):
        """dict.fromkeys() over a bare string is not an accepted collection."""
        with pytest.raises(ValueError, match='requires a dict, list, tuple or set'):
            parse("entry = ModEntryInfo(name='m', update_pack_deps=dict.fromkeys('httpx'))\n")

    def test_fromkeys_arguments(self):
        """dict.fromkeys() takes one or two positional arguments."""
        with pytest.raises(ValueError, match='takes one or two positional arguments'):
            parse(
                "entry = ModEntryInfo(name='m', "
                "update_pack_deps=dict.fromkeys(['httpx'], None, None))\n")

    def test_list_comprehension(self):
        """A comprehension is a dynamic declaration."""
        with pytest.raises(ValueError, match='unsupported expression'):
            parse(
                "entry = ModEntryInfo(name='m', "
                "update_pack_deps=[name for name in ['httpx']])\n")

    def test_dict_union(self):
        """An operation is a dynamic declaration."""
        with pytest.raises(ValueError, match='unsupported expression'):
            parse(
                "entry = ModEntryInfo(name='m', "
                "update_pack_deps={'httpx': None} | {})\n")

    def test_update_call(self):
        """A mutation after the declaration is not read."""
        source = """\
entry = ModEntryInfo(name='m')
entry.update_pack_deps.update({'httpx': None})
"""
        with pytest.raises(ValueError, match='module level assignments'):
            parse(source)

    def test_item_assignment(self):
        """An item assignment after the declaration is not read."""
        source = """\
entry = ModEntryInfo(name='m')
entry.update_pack_deps['httpx'] = None
"""
        with pytest.raises(ValueError, match='item or an attribute of update_pack_deps'):
            parse(source)

    def test_augmented_assignment(self):
        """An augmented assignment after the declaration is not read."""
        source = """\
entry = ModEntryInfo(name='m')
entry.update_pack_deps |= {'httpx': None}
"""
        with pytest.raises(ValueError, match='augmented assignment'):
            parse(source)

    def test_delete(self):
        """A deletion after the declaration is not read."""
        source = """\
entry = ModEntryInfo(name='m')
del entry.update_pack_deps['httpx']
"""
        with pytest.raises(ValueError, match='deleting update_pack_deps'):
            parse(source)

    def test_conditional_declaration(self):
        """A declaration in a conditional block is not read."""
        source = """\
entry = ModEntryInfo(name='m')
if True:
    entry.update_pack_deps = ['httpx']
"""
        with pytest.raises(ValueError, match='module level assignments'):
            parse(source)

    def test_imported_entry(self):
        """An entry imported from another module cannot be read."""
        with pytest.raises(ValueError, match='module level assignments'):
            parse('from other import entry\n')


class TestPypiMirrorsForms:
    """The static mirror declarations the parser reads."""

    def test_missing(self):
        """An entry without the declaration keeps the default of ModEntryInfo."""
        assert parse_mirrors("entry = ModEntryInfo(name='m')\n") == DEFAULT_PYPI_MIRRORS

    def test_no_entry(self):
        """A module without an entry keeps the default of ModEntryInfo."""
        assert parse_mirrors('x = 1\n') == DEFAULT_PYPI_MIRRORS

    def test_keyword(self):
        """A single url is read from the keyword of the construction."""
        source = "entry = ModEntryInfo(name='m', pypi_mirrors='https://a.example/simple')\n"
        assert parse_mirrors(source) == 'https://a.example/simple'

    def test_dict(self):
        """A dict literal is read with its values, nested and flat mixed."""
        source = """\
entry = ModEntryInfo(
    name='m',
    pypi_mirrors={
        'cn': {'aliyun': 'https://a.example/simple'},
        'global': 'https://b.example/simple',
    },
)
"""
        assert parse_mirrors(source) == {
            'cn': {'aliyun': 'https://a.example/simple'},
            'global': 'https://b.example/simple',
        }

    def test_attribute(self):
        """An attribute assignment after the construction replaces the value."""
        source = """\
entry = ModEntryInfo(name='m', pypi_mirrors='https://a.example/simple')
entry.pypi_mirrors = {'cn': 'https://b.example/simple'}
"""
        assert parse_mirrors(source) == {'cn': 'https://b.example/simple'}

    def test_new_entry_resets(self):
        """A later construction without the declaration resets it to the default."""
        source = """\
entry = ModEntryInfo(name='m', pypi_mirrors='https://a.example/simple')
entry = ModEntryInfo(name='m')
"""
        assert parse_mirrors(source) == DEFAULT_PYPI_MIRRORS

    def test_module_constant(self):
        """A module level name that holds a structure is resolved."""
        source = """\
MIRRORS = {'cn': {'self': 'https://a.example/simple'}}
entry = ModEntryInfo(name='m', pypi_mirrors=MIRRORS)
"""
        assert parse_mirrors(source) == {'cn': {'self': 'https://a.example/simple'}}

    def test_nested_constant(self):
        """A nested structure may be built from module level names."""
        source = """\
CN = {'self': 'https://a.example/simple'}
entry = ModEntryInfo(name='m', pypi_mirrors={'cn': CN})
"""
        assert parse_mirrors(source) == {'cn': {'self': 'https://a.example/simple'}}

    def test_default_imported(self):
        """The library default is resolved to the default structure."""
        source = """\
from alasio.config.entry.const import DEFAULT_PYPI_MIRRORS

entry = ModEntryInfo(name='m', pypi_mirrors=DEFAULT_PYPI_MIRRORS)
"""
        assert parse_mirrors(source) == DEFAULT_PYPI_MIRRORS

    def test_default_imported_as(self):
        """The default is resolved through the alias of the import."""
        source = """\
from alasio.config.entry.const import DEFAULT_PYPI_MIRRORS as DEFAULTS

entry = ModEntryInfo(name='m', pypi_mirrors=DEFAULTS)
"""
        assert parse_mirrors(source) == DEFAULT_PYPI_MIRRORS

    def test_default_module_alias(self):
        """The default is resolved through the alias of the module import."""
        source = """\
import alasio.config.entry.const as const

entry = ModEntryInfo(name='m', pypi_mirrors=const.DEFAULT_PYPI_MIRRORS)
"""
        assert parse_mirrors(source) == DEFAULT_PYPI_MIRRORS

    def test_deepcopy(self):
        """deepcopy() over the default is read."""
        source = """\
from copy import deepcopy
from alasio.config.entry.const import DEFAULT_PYPI_MIRRORS

entry = ModEntryInfo(name='m', pypi_mirrors=deepcopy(DEFAULT_PYPI_MIRRORS))
"""
        assert parse_mirrors(source) == DEFAULT_PYPI_MIRRORS

    def test_copy_method(self):
        """The .copy() of a structure is read."""
        source = """\
MIRRORS = {'cn': 'https://a.example/simple'}
entry = ModEntryInfo(name='m', pypi_mirrors=MIRRORS.copy())
"""
        assert parse_mirrors(source) == {'cn': 'https://a.example/simple'}

    def test_default_not_shared(self):
        """The default structure of an entry is a copy of the library one."""
        source = """\
from alasio.config.entry.const import DEFAULT_PYPI_MIRRORS

entry = ModEntryInfo(name='m', pypi_mirrors=DEFAULT_PYPI_MIRRORS)
"""
        value = parse_mirrors(source)
        value['cn']['tsinghua'] = 'https://edited.example/simple'
        assert DEFAULT_PYPI_MIRRORS['cn']['tsinghua'] != 'https://edited.example/simple'

    def test_dict_mod_entry(self):
        """The default entry of the library gives its own structure."""
        source = """\
from alasio.config.entry.const import DICT_MOD_ENTRY

entry = DICT_MOD_ENTRY['alas']
"""
        assert parse_mirrors(source) == LIBRARY_MOD_ENTRY['alas'].pypi_mirrors

    def test_both_declarations(self):
        """The two declarations of one source are read together."""
        source = """\
entry = ModEntryInfo(
    name='m',
    update_pack_deps=('httpx',),
    pypi_mirrors='https://a.example/simple',
)
"""
        assert parse(source) == ('httpx',)
        assert parse_mirrors(source) == 'https://a.example/simple'


class TestPypiMirrorsErrors:
    """The mirror declarations the parser refuses."""

    def test_dynamic_value(self):
        """A computed value cannot be read."""
        with pytest.raises(ValueError, match='pypi_mirrors: unsupported call'):
            parse_mirrors("entry = ModEntryInfo(name='m', pypi_mirrors=get_mirrors())\n")

    def test_unknown_name(self):
        """A name the module never assigns cannot be read."""
        with pytest.raises(ValueError, match='unknown name "MIRRORS"'):
            parse_mirrors("entry = ModEntryInfo(name='m', pypi_mirrors=MIRRORS)\n")

    def test_dynamic_constant(self):
        """A name of a dynamic value cannot be read."""
        source = """\
MIRRORS = get_mirrors()
entry = ModEntryInfo(name='m', pypi_mirrors=MIRRORS)
"""
        with pytest.raises(ValueError, match='not a static value'):
            parse_mirrors(source)

    def test_not_a_structure(self):
        """A value that is neither a str nor a dict is refused."""
        with pytest.raises(ValueError, match='invalid entry value'):
            parse_mirrors("entry = ModEntryInfo(name='m', pypi_mirrors=123)\n")

    def test_bad_group(self):
        """A group of a value that is neither a str nor a dict is refused."""
        with pytest.raises(ValueError, match='invalid entry value'):
            parse_mirrors("entry = ModEntryInfo(name='m', pypi_mirrors={'cn': 1})\n")

    def test_bad_url(self):
        """A member url that is not a str is refused."""
        with pytest.raises(ValueError, match='invalid entry value'):
            parse_mirrors("entry = ModEntryInfo(name='m', pypi_mirrors={'cn': {'a': 1}})\n")

    def test_item_assignment(self):
        """An item assignment after the declaration is not read."""
        source = """\
entry = ModEntryInfo(name='m')
entry.pypi_mirrors['cn'] = 'https://a.example/simple'
"""
        with pytest.raises(ValueError, match='item or an attribute of pypi_mirrors'):
            parse_mirrors(source)

    def test_nested_item_assignment(self):
        """A nested item assignment after the declaration is not read."""
        source = """\
entry = ModEntryInfo(name='m')
entry.pypi_mirrors['cn']['self'] = 'https://a.example/simple'
"""
        with pytest.raises(ValueError, match='item or an attribute of pypi_mirrors'):
            parse_mirrors(source)

    def test_update_call(self):
        """A mutation after the declaration is not read."""
        source = """\
entry = ModEntryInfo(name='m')
entry.pypi_mirrors.update({'cn': 'https://a.example/simple'})
"""
        with pytest.raises(ValueError, match='module level assignments'):
            parse_mirrors(source)

    def test_augmented_assignment(self):
        """An augmented assignment after the declaration is not read."""
        source = """\
entry = ModEntryInfo(name='m')
entry.pypi_mirrors |= {'cn': 'https://a.example/simple'}
"""
        with pytest.raises(ValueError, match='augmented assignment'):
            parse_mirrors(source)

    def test_delete(self):
        """A deletion after the declaration is not read."""
        source = """\
entry = ModEntryInfo(name='m')
del entry.pypi_mirrors['cn']
"""
        with pytest.raises(ValueError, match='deleting pypi_mirrors'):
            parse_mirrors(source)

    def test_conditional_declaration(self):
        """A declaration in a conditional block is not read."""
        source = """\
entry = ModEntryInfo(name='m')
if True:
    entry.pypi_mirrors = 'https://a.example/simple'
"""
        with pytest.raises(ValueError, match='module level assignments'):
            parse_mirrors(source)

    def test_broken_mirror_fails_the_names(self):
        """The declarations are read in one pass: a broken mirror fails all of them."""
        with pytest.raises(ValueError, match='pypi_mirrors'):
            parse(
                "entry = ModEntryInfo(name='m', update_pack_deps=('httpx',), "
                "pypi_mirrors=get_mirrors())\n")


class TestUpdateProvidePypiMirrorsForms:
    """The static provided index declarations the parser reads."""

    def test_default(self):
        """An entry that declares nothing provides the py38deps index."""
        assert parse_provide("entry = ModEntryInfo(name='m')\n") == DEFAULT_UPDATE_PROVIDE_PYPI_MIRRORS

    def test_no_entry(self):
        """A module without an entry keeps the default of ModEntryInfo."""
        assert parse_provide('x = 1\n') == DEFAULT_UPDATE_PROVIDE_PYPI_MIRRORS

    def test_tuple(self):
        """A tuple literal declares its urls in order."""
        source = (
            "entry = ModEntryInfo(name='m', "
            "update_provide_pypi_mirrors=('https://a.example/simple', "
            "'https://b.example/simple'))\n")
        assert parse_provide(source) == ('https://a.example/simple', 'https://b.example/simple')

    def test_list(self):
        """A list literal is read as a tuple."""
        source = (
            "entry = ModEntryInfo(name='m', "
            "update_provide_pypi_mirrors=['https://a.example/simple'])\n")
        assert parse_provide(source) == ('https://a.example/simple',)

    def test_set(self):
        """A set literal is an accepted declaration too."""
        source = (
            "entry = ModEntryInfo(name='m', "
            "update_provide_pypi_mirrors={'https://a.example/simple'})\n")
        assert parse_provide(source) == ('https://a.example/simple',)

    def test_duplicates_collapse(self):
        """A repeated url is provided once, in the order it is written."""
        source = (
            "entry = ModEntryInfo(name='m', update_provide_pypi_mirrors=("
            "'https://a.example/simple', 'https://b.example/simple', "
            "'https://a.example/simple'))\n")
        assert parse_provide(source) == ('https://a.example/simple', 'https://b.example/simple')

    def test_module_constant(self):
        """A module level name that holds the urls is resolved."""
        source = """\
PROVIDE = ('https://a.example/simple',)
entry = ModEntryInfo(name='m', update_provide_pypi_mirrors=PROVIDE)
"""
        assert parse_provide(source) == ('https://a.example/simple',)

    def test_attribute(self):
        """An attribute assignment after the construction replaces the value."""
        source = """\
entry = ModEntryInfo(name='m', update_provide_pypi_mirrors=('https://a.example/simple',))
entry.update_provide_pypi_mirrors = ('https://b.example/simple',)
"""
        assert parse_provide(source) == ('https://b.example/simple',)

    def test_new_entry_resets(self):
        """A later construction without the declaration resets it to the default."""
        source = """\
entry = ModEntryInfo(name='m', update_provide_pypi_mirrors=('https://a.example/simple',))
entry = ModEntryInfo(name='m')
"""
        assert parse_provide(source) == DEFAULT_UPDATE_PROVIDE_PYPI_MIRRORS

    def test_dict_mod_entry(self):
        """The library default entry gives its own declaration."""
        source = """\
from alasio.config.entry.const import DICT_MOD_ENTRY

entry = DICT_MOD_ENTRY['alas']
"""
        expected = LIBRARY_MOD_ENTRY['alas'].update_provide_pypi_mirrors
        assert parse_provide(source) == expected

    def test_dict_mod_entry_attribute(self):
        """An attribute assignment on a library entry replaces its declaration."""
        source = """\
from alasio.config.entry.const import DICT_MOD_ENTRY

entry = DICT_MOD_ENTRY['alas']
entry.update_provide_pypi_mirrors = ('https://a.example/simple',)
"""
        assert parse_provide(source) == ('https://a.example/simple',)


class TestUpdateProvidePypiMirrorsErrors:
    """The provided index declarations the parser refuses."""

    def test_not_a_url(self):
        """A declaration that is not an http(s) url is refused."""
        with pytest.raises(ValueError, match='must be http\\(s\\) urls'):
            parse_provide(
                "entry = ModEntryInfo(name='m', update_provide_pypi_mirrors=('ftp://a',))\n")

    def test_not_a_base_url(self):
        """A bare host without a scheme is refused."""
        with pytest.raises(ValueError, match='must be http\\(s\\) urls'):
            parse_provide(
                "entry = ModEntryInfo(name='m', "
                "update_provide_pypi_mirrors=('mirror.example/simple',))\n")

    def test_bare_string(self):
        """One url needs a collection: the model refuses a bare string."""
        with pytest.raises(ValueError, match='invalid entry value'):
            parse_provide(
                "entry = ModEntryInfo(name='m', "
                "update_provide_pypi_mirrors='https://a.example/simple')\n")

    def test_non_string_item(self):
        """Every provided index must be a string."""
        with pytest.raises(ValueError, match='invalid entry value'):
            parse_provide(
                "entry = ModEntryInfo(name='m', update_provide_pypi_mirrors=(1,))\n")

    def test_dict(self):
        """A dict is not a declaration of urls: the model refuses it."""
        with pytest.raises(ValueError, match='invalid entry value'):
            parse_provide(
                "entry = ModEntryInfo(name='m', "
                "update_provide_pypi_mirrors={'https://a.example/simple': None})\n")

    def test_dynamic_value(self):
        """A computed value cannot be read."""
        with pytest.raises(ValueError, match='update_provide_pypi_mirrors: unsupported call'):
            parse_provide(
                "entry = ModEntryInfo(name='m', update_provide_pypi_mirrors=get_mirrors())\n")

    def test_unknown_name(self):
        """A name the module never assigns cannot be read."""
        with pytest.raises(ValueError, match='unknown name "PROVIDE"'):
            parse_provide(
                "entry = ModEntryInfo(name='m', update_provide_pypi_mirrors=PROVIDE)\n")

    def test_item_assignment(self):
        """An item assignment after the declaration is not read."""
        source = """\
entry = ModEntryInfo(name='m')
entry.update_provide_pypi_mirrors[0] = 'https://a.example/simple'
"""
        with pytest.raises(ValueError, match='item or an attribute of update_provide_pypi_mirrors'):
            parse_provide(source)

    def test_update_call(self):
        """A mutation after the declaration is not read."""
        source = """\
entry = ModEntryInfo(name='m')
entry.update_provide_pypi_mirrors.append('https://a.example/simple')
"""
        with pytest.raises(ValueError, match='module level assignments'):
            parse_provide(source)

    def test_augmented_assignment(self):
        """An augmented assignment after the declaration is not read."""
        source = """\
entry = ModEntryInfo(name='m')
entry.update_provide_pypi_mirrors += ('https://a.example/simple',)
"""
        with pytest.raises(ValueError, match='augmented assignment'):
            parse_provide(source)

    def test_delete(self):
        """A deletion after the declaration is not read."""
        source = """\
entry = ModEntryInfo(name='m')
del entry.update_provide_pypi_mirrors
"""
        with pytest.raises(ValueError, match='deleting update_provide_pypi_mirrors'):
            parse_provide(source)

    def test_conditional_declaration(self):
        """A declaration in a conditional block is not read."""
        source = """\
entry = ModEntryInfo(name='m')
if True:
    entry.update_provide_pypi_mirrors = ('https://a.example/simple',)
"""
        with pytest.raises(ValueError, match='module level assignments'):
            parse_provide(source)


class TestParseModEntry:
    """The three step parse: extract the nodes, evaluate them, validate."""

    def test_keys(self):
        """The pack server reads exactly three module level keys."""
        assert PARSED_KEYS == (
            'update_pack_deps', 'pypi_mirrors', 'update_provide_pypi_mirrors')

    def test_one_pass(self):
        """Every declaration of the source is read by one parse."""
        source = """\
entry = ModEntryInfo(
    name='m',
    update_pack_deps=('httpx',),
    pypi_mirrors={'cn': {'self': 'https://pypi.example/simple'}},
    update_provide_pypi_mirrors=('https://py38deps.example/simple',),
)
"""
        entry = parse_mod_entry(source)
        assert entry.update_pack_deps == ('httpx',)
        assert entry.pypi_mirrors == {'cn': {'self': 'https://pypi.example/simple'}}
        assert entry.update_provide_pypi_mirrors == ('https://py38deps.example/simple',)

    def test_model_defaults(self):
        """The keys an entry omits keep the defaults of ModEntryInfo."""
        entry = parse_mod_entry("entry = ModEntryInfo(name='m')\n")
        assert entry.update_pack_deps == ()
        assert entry.pypi_mirrors == DEFAULT_PYPI_MIRRORS
        assert entry.update_provide_pypi_mirrors == DEFAULT_UPDATE_PROVIDE_PYPI_MIRRORS

    def test_default_is_not_shared(self):
        """The parsed default mirrors are a copy of the library default."""
        entry = parse_mod_entry("entry = ModEntryInfo(name='m')\n")
        entry.pypi_mirrors['cn']['tsinghua'] = 'https://edited.example/simple'
        assert DEFAULT_PYPI_MIRRORS['cn']['tsinghua'] != 'https://edited.example/simple'

    def test_model_validates_every_key(self):
        """A value of another type is refused, and the message names the key."""
        with pytest.raises(ValueError, match=r'\$\.update_pack_deps'):
            parse_mod_entry("entry = ModEntryInfo(name='m', update_pack_deps=1)\n")
        with pytest.raises(ValueError, match=r'\$\.pypi_mirrors'):
            parse_mod_entry("entry = ModEntryInfo(name='m', pypi_mirrors=1)\n")
        with pytest.raises(ValueError, match=r'\$\.update_provide_pypi_mirrors'):
            parse_mod_entry(
                "entry = ModEntryInfo(name='m', update_provide_pypi_mirrors=1)\n")

    def test_mixed_mirror_structure(self):
        """The flat and the nested form mix in one pypi_mirrors structure."""
        source = (
            "entry = ModEntryInfo(name='m', "
            "pypi_mirrors={'cn': {'self': 'https://a.example/simple'}, "
            "'global': 'https://b.example/simple'})\n")
        assert parse_mod_entry(source).pypi_mirrors == {
            'cn': {'self': 'https://a.example/simple'},
            'global': 'https://b.example/simple',
        }

    def test_helper_call_needs_a_declaration(self):
        """A helper call that declares none of the keys is refused."""
        with pytest.raises(ValueError, match='does not declare update_pack_deps'):
            parse_mod_entry("entry = make_entry('m')\n")
