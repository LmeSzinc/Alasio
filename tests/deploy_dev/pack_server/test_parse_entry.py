"""
Tests for parse_entry: read pack_update_deps from the source of a mod entry.

The parser reads the module level `entry` of a mod entry and its
pack_update_deps declaration with ast, without executing the source. The
tests cover the static forms the parser accepts, the order of the names and
the source forms it refuses: a pack must never silently lose a dependency
channel because the declaration could not be read.
"""
import pytest

from alasio.deploy_dev.pack_server.parse_entry import parse_pack_update_deps


def parse(source):
    """
    Parse a source of the tests.

    Args:
        source (str | bytes): Source of an entry module

    Returns:
        dict[str, None]: Declared pack_update_deps names
    """
    return parse_pack_update_deps(source)


class TestPackUpdateDepsForms:
    """The static declarations the parser reads."""

    def test_dict(self):
        """A dict literal declares its keys, in the order they are written."""
        source = """\
from alasio.config.entry.const import ModEntryInfo

entry = ModEntryInfo(
    name='m',
    pack_update_deps={'httpx': None, 'ruamel.yaml': None},
)
"""
        result = parse(source)
        assert result == {'httpx': None, 'ruamel.yaml': None}
        assert list(result) == ['httpx', 'ruamel.yaml']

    def test_list(self):
        """A list literal declares its items."""
        source = """\
entry = ModEntryInfo(name='m', pack_update_deps=['httpx', 'starlette'])
"""
        assert parse(source) == {'httpx': None, 'starlette': None}

    def test_tuple(self):
        """A tuple literal declares its items."""
        source = """\
entry = ModEntryInfo(name='m', pack_update_deps=('httpx', 'starlette'))
"""
        assert parse(source) == {'httpx': None, 'starlette': None}

    def test_set(self):
        """A set literal declares its items."""
        source = """\
entry = ModEntryInfo(name='m', pack_update_deps={'httpx', 'starlette'})
"""
        assert parse(source) == {'httpx': None, 'starlette': None}

    def test_dict_fromkeys(self):
        """dict.fromkeys() declares the keys of its iterable."""
        source = """\
entry = ModEntryInfo(name='m', pack_update_deps=dict.fromkeys(['httpx', 'starlette']))
"""
        assert parse(source) == {'httpx': None, 'starlette': None}

    def test_dict_fromkeys_with_value(self):
        """The value of dict.fromkeys() is ignored, only the names matter."""
        source = """\
entry = ModEntryInfo(name='m', pack_update_deps=dict.fromkeys(('httpx',), True))
"""
        assert parse(source) == {'httpx': None}

    def test_dict_fromkeys_of_constant(self):
        """dict.fromkeys() reads a module level constant as its iterable."""
        source = """\
NAMES = ['httpx', 'starlette']
entry = ModEntryInfo(name='m', pack_update_deps=dict.fromkeys(NAMES))
"""
        assert parse(source) == {'httpx': None, 'starlette': None}

    def test_module_constant_dict(self):
        """A module level dict constant is read by name."""
        source = """\
DEPS = {'httpx': None, 'ruamel.yaml': None}
entry = ModEntryInfo(name='m', pack_update_deps=DEPS)
"""
        assert parse(source) == {'httpx': None, 'ruamel.yaml': None}

    def test_module_constant_list(self):
        """A module level list constant is read by name."""
        source = """\
DEPS = ['httpx', 'starlette']
entry = ModEntryInfo(name='m', pack_update_deps=DEPS)
"""
        assert parse(source) == {'httpx': None, 'starlette': None}

    def test_attribute_assignment(self):
        """entry.pack_update_deps may be assigned after the construction."""
        source = """\
entry = ModEntryInfo(name='m')
entry.pack_update_deps = ['httpx', 'starlette']
"""
        assert parse(source) == {'httpx': None, 'starlette': None}

    def test_attribute_overrides_keyword(self):
        """A later attribute assignment replaces the keyword value."""
        source = """\
entry = ModEntryInfo(name='m', pack_update_deps=['httpx'])
entry.pack_update_deps = ['starlette']
"""
        assert parse(source) == {'starlette': None}

    def test_last_entry_wins(self):
        """A later construction of entry replaces the earlier declaration."""
        source = """\
entry = ModEntryInfo(name='m', pack_update_deps=['httpx'])
entry = ModEntryInfo(name='m', pack_update_deps=['starlette'])
"""
        assert parse(source) == {'starlette': None}

    def test_new_entry_resets(self):
        """A construction without pack_update_deps clears the earlier names."""
        source = """\
entry = ModEntryInfo(name='m', pack_update_deps=['httpx'])
entry = ModEntryInfo(name='m')
"""
        assert parse(source) == {}

    def test_no_pack_update_deps(self):
        """An entry without the declaration has no pack updated dependency."""
        source = """\
entry = ModEntryInfo(name='m')
"""
        assert parse(source) == {}

    def test_no_entry(self):
        """A module without an entry has no declaration."""
        source = """\
from alasio.config.entry.const import ModEntryInfo
"""
        assert parse(source) == {}

    def test_none(self):
        """None is an empty declaration."""
        source = """\
entry = ModEntryInfo(name='m', pack_update_deps=None)
"""
        assert parse(source) == {}

    @pytest.mark.parametrize('value', [
        '{}',
        '[]',
        '()',
        'dict.fromkeys([])',
    ])
    def test_empty(self, value):
        """An empty collection declares no name."""
        source = f"entry = ModEntryInfo(name='m', pack_update_deps={value})\n"
        assert parse(source) == {}

    def test_values_are_ignored(self):
        """Only the keys of the declaration are read, a dynamic value is fine."""
        source = """\
entry = ModEntryInfo(name='m', pack_update_deps={'httpx': build_marker()})
"""
        assert parse(source) == {'httpx': None}

    def test_key_constant(self):
        """A dict key may be a module level constant."""
        source = """\
NAME = 'httpx'
entry = ModEntryInfo(name='m', pack_update_deps={NAME: None})
"""
        assert parse(source) == {'httpx': None}

    def test_duplicates_collapse(self):
        """A name that appears several times is declared once."""
        source = """\
entry = ModEntryInfo(name='m', pack_update_deps=['httpx', 'httpx', 'starlette'])
"""
        assert parse(source) == {'httpx': None, 'starlette': None}

    def test_other_fields_are_ignored(self):
        """The other fields and attributes of the entry do not matter."""
        source = """\
entry = ModEntryInfo(name='m', root='mod', pack_update_deps=['httpx'])
entry.root = 'elsewhere'
"""
        assert parse(source) == {'httpx': None}

    def test_conditional_unrelated_attribute(self):
        """A conditional assignment to another entry attribute is not a declaration."""
        source = """\
entry = ModEntryInfo(name='m', pack_update_deps=['httpx'])
if True:
    entry.root = 'elsewhere'
"""
        assert parse(source) == {'httpx': None}

    def test_bytes_source(self):
        """Bytes are parsed with the encoding declaration of the source."""
        source = """\
# -*- coding: utf-8 -*-
# 中文注释
entry = ModEntryInfo(name='m', pack_update_deps=['httpx'])
""".encode('utf-8')
        assert parse(source) == {'httpx': None}

    def test_qualified_entry_class(self):
        """The entry may be constructed through a qualified ModEntryInfo."""
        source = """\
import alasio.config.entry.const as const

entry = const.ModEntryInfo(name='m')
"""
        assert parse(source) == {}


class TestPackUpdateDepsErrors:
    """The source forms the parser refuses."""

    def test_invalid_python(self):
        """A syntax error is reported with the line of the source."""
        with pytest.raises(ValueError, match='invalid Python at line'):
            parse("entry = ModEntryInfo(name='m'\n")

    def test_source_name(self):
        """The name of the source is added to the error message."""
        source = "entry = ModEntryInfo(name='m', pack_update_deps=get_deps())\n"
        with pytest.raises(ValueError, match='module/config/const.py at c3'):
            parse_pack_update_deps(source, source_name='module/config/const.py at c3')

    def test_entry_is_not_a_call(self):
        """The entry must be constructed by a call."""
        with pytest.raises(ValueError, match='entry must be constructed by a call'):
            parse("entry = 1\n")

    def test_entry_is_a_helper_call(self):
        """A helper call that does not name pack_update_deps is not read."""
        with pytest.raises(ValueError, match='does not declare pack_update_deps'):
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
            parse("entry = ModEntryInfo(name='m', pack_update_deps=get_deps())\n")

    def test_unknown_name(self):
        """A name that is not assigned before the declaration is unknown."""
        with pytest.raises(ValueError, match='unknown name'):
            parse("entry = ModEntryInfo(name='m', pack_update_deps=DEPS)\n")

    def test_name_of_dynamic_value(self):
        """A name that holds a dynamic value is not a static declaration."""
        source = """\
DEPS = get_deps()
entry = ModEntryInfo(name='m', pack_update_deps=DEPS)
"""
        with pytest.raises(ValueError, match='is not a static value'):
            parse(source)

    def test_bare_string(self):
        """A bare string is not a collection of names."""
        with pytest.raises(ValueError, match='must be a dict, list, tuple or set'):
            parse("entry = ModEntryInfo(name='m', pack_update_deps='httpx')\n")

    def test_non_string_name(self):
        """Every declared name must be a string."""
        with pytest.raises(ValueError, match='names must be str'):
            parse("entry = ModEntryInfo(name='m', pack_update_deps=[1])\n")

    def test_non_string_key(self):
        """Every key of a dict declaration must be a string."""
        with pytest.raises(ValueError, match='names must be str'):
            parse("entry = ModEntryInfo(name='m', pack_update_deps={1: None})\n")

    def test_dict_unpacking(self):
        """A dict unpacking is not a static declaration of keys."""
        with pytest.raises(ValueError, match='dict unpacking'):
            parse("entry = ModEntryInfo(name='m', pack_update_deps={**OTHER})\n")

    def test_fromkeys_of_string(self):
        """dict.fromkeys() over a bare string is not an accepted collection."""
        with pytest.raises(ValueError, match='requires a dict, list, tuple or set'):
            parse("entry = ModEntryInfo(name='m', pack_update_deps=dict.fromkeys('httpx'))\n")

    def test_fromkeys_arguments(self):
        """dict.fromkeys() takes one or two positional arguments."""
        with pytest.raises(ValueError, match='takes one or two positional arguments'):
            parse(
                "entry = ModEntryInfo(name='m', "
                "pack_update_deps=dict.fromkeys(['httpx'], None, None))\n")

    def test_list_comprehension(self):
        """A comprehension is a dynamic declaration."""
        with pytest.raises(ValueError, match='unsupported expression'):
            parse(
                "entry = ModEntryInfo(name='m', "
                "pack_update_deps=[name for name in ['httpx']])\n")

    def test_dict_union(self):
        """An operation is a dynamic declaration."""
        with pytest.raises(ValueError, match='unsupported expression'):
            parse(
                "entry = ModEntryInfo(name='m', "
                "pack_update_deps={'httpx': None} | {})\n")

    def test_update_call(self):
        """A mutation after the declaration is not read."""
        source = """\
entry = ModEntryInfo(name='m')
entry.pack_update_deps.update({'httpx': None})
"""
        with pytest.raises(ValueError, match='module level assignments'):
            parse(source)

    def test_item_assignment(self):
        """An item assignment after the declaration is not read."""
        source = """\
entry = ModEntryInfo(name='m')
entry.pack_update_deps['httpx'] = None
"""
        with pytest.raises(ValueError, match='item of pack_update_deps'):
            parse(source)

    def test_augmented_assignment(self):
        """An augmented assignment after the declaration is not read."""
        source = """\
entry = ModEntryInfo(name='m')
entry.pack_update_deps |= {'httpx': None}
"""
        with pytest.raises(ValueError, match='augmented assignment'):
            parse(source)

    def test_delete(self):
        """A deletion after the declaration is not read."""
        source = """\
entry = ModEntryInfo(name='m')
del entry.pack_update_deps['httpx']
"""
        with pytest.raises(ValueError, match='deleting pack_update_deps'):
            parse(source)

    def test_conditional_declaration(self):
        """A declaration in a conditional block is not read."""
        source = """\
entry = ModEntryInfo(name='m')
if True:
    entry.pack_update_deps = ['httpx']
"""
        with pytest.raises(ValueError, match='module level assignments'):
            parse(source)

    def test_imported_entry(self):
        """An entry imported from another module cannot be read."""
        with pytest.raises(ValueError, match='module level assignments'):
            parse('from other import entry\n')
