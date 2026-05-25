from alasio.codegen.python.gen import CodeGen


class TestListWrap:
    def test_wrap_false_inline(self):
        gen = CodeGen()
        with gen.List('items').wrap('inline'):
            gen.Item(1)
            gen.Item(2)
            gen.Item(3)
        code = gen.generate_str()
        assert code == "items = [1, 2, 3]\n"

    def test_wrap_always_explicit(self):
        gen = CodeGen()
        with gen.List('items').wrap('newline'):
            gen.Item(1)
            gen.Item(2)
        code = gen.generate_str()
        expected = """\
items = [
    1,
    2,
]
"""
        assert code == expected

    def test_wrap_always_default(self):
        # Default _wrap is 'always' when using with gen.List
        gen = CodeGen()
        with gen.List('items'):
            gen.Item(1)
            gen.Item(2)
        code = gen.generate_str()
        expected = """\
items = [
    1,
    2,
]
"""
        assert code == expected

    def test_wrap_int_single_line(self):
        # Width large enough, everything fits on one line
        # iter_multiline adds trailing comma even on single row
        gen = CodeGen()
        with gen.List('items').wrap(80):
            gen.Item(1)
            gen.Item(2)
        code = gen.generate_str()
        assert code == "items = [1, 2]\n"

    def test_wrap_int_multiline(self):
        # Width small enough to force wrapping
        gen = CodeGen()
        with gen.List('items').wrap(20):
            gen.Item('short')
            gen.Item('medium')
            gen.Item('another')
        code = gen.generate_str()
        expected = """\
items = [
    'short',
    'medium',
    'another',
]
"""
        assert code == expected

    def test_wrap_true_defaults_to_120(self):
        # wrap('auto') is equivalent to wrap(120)
        gen = CodeGen()
        with gen.List('items').wrap('auto'):
            gen.Item('short')
            gen.Item('items')
            gen.Item('fit_in_line')
        code = gen.generate_str()
        # All fit within 120, so inline
        assert code == "items = ['short', 'items', 'fit_in_line']\n"

    def test_empty_list_wrap_false(self):
        gen = CodeGen()
        with gen.List('empty').wrap('inline'):
            pass
        code = gen.generate_str()
        assert code == "empty = []\n"

    def test_nested_list_inner_wrap_false(self):
        # Inner list with wrap('inline') stays inline inside outer always-wrap
        gen = CodeGen()
        with gen.List('outer'):
            with gen.List().wrap('inline'):
                gen.Item(1)
                gen.Item(2)
        code = gen.generate_str()
        expected = """\
outer = [
    [1, 2],
]
"""
        assert code == expected

    def test_wrap_expand_single_row(self):
        """All items fit on one line inside expanded brackets."""
        gen = CodeGen()
        with gen.List('items').wrap('expand'):
            gen.Item(1)
            gen.Item(2)
            gen.Item(3)
        code = gen.generate_str()
        assert code == "items = [\n    1, 2, 3,\n]\n"

    def test_wrap_expand_multi_row(self):
        """Too many items force wrapping inside expanded brackets."""
        gen = CodeGen()
        with gen.List('items').wrap('expand'):
            for i in range(15):
                gen.Item(i)
        code = gen.generate_str()
        expected = """\
items = [
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14,
]
"""
        assert code == expected


class TestDictWrap:
    def test_wrap_false_inline(self):
        gen = CodeGen()
        with gen.Dict('items').wrap('inline'):
            gen.Var('a', 1)
            gen.Var('b', 2)
        code = gen.generate_str()
        assert code == "items = {'a': 1, 'b': 2}\n"

    def test_wrap_always_default(self):
        gen = CodeGen()
        with gen.Dict('items'):
            gen.Var('a', 1)
            gen.Var('b', 2)
        code = gen.generate_str()
        expected = """\
items = {
    'a': 1,
    'b': 2,
}
"""
        assert code == expected

    def test_wrap_int_multiline(self):
        gen = CodeGen()
        with gen.Dict('items').wrap(20):
            gen.Var('key1', 'value1')
            gen.Var('key2', 'value2')
        code = gen.generate_str()
        expected = """\
items = {
    'key1': 'value1',
    'key2': 'value2',
}
"""
        assert code == expected

    def test_empty_dict_wrap_false(self):
        gen = CodeGen()
        with gen.Dict('empty').wrap('inline'):
            pass
        code = gen.generate_str()
        assert code == "empty = {}\n"

    def test_single_item_dict_wrap_false(self):
        gen = CodeGen()
        with gen.Dict('single').wrap('inline'):
            gen.Var('key', 42)
        code = gen.generate_str()
        assert code == "single = {'key': 42}\n"

    def test_wrap_expand(self):
        gen = CodeGen()
        with gen.Dict('d').wrap('expand'):
            gen.Var('a', 1)
            gen.Var('b', 2)
            gen.Var('keyword', 'desc')
        code = gen.generate_str()
        expected = """\
d = {
    'a': 1, 'b': 2, 'keyword': 'desc',
}
"""
        assert code == expected


class TestTupleWrap:
    def test_wrap_false_inline(self):
        gen = CodeGen()
        with gen.Tuple('items').wrap('inline'):
            gen.Item(1)
            gen.Item(2)
        code = gen.generate_str()
        assert code == "items = (1, 2)\n"

    def test_wrap_always_default(self):
        gen = CodeGen()
        with gen.Tuple('items'):
            gen.Item(1)
            gen.Item(2)
        code = gen.generate_str()
        expected = """\
items = (
    1,
    2,
)
"""
        assert code == expected

    def test_wrap_int_multiline(self):
        gen = CodeGen()
        with gen.Tuple('items').wrap(20):
            gen.Item('first')
            gen.Item('second')
        code = gen.generate_str()
        expected = """\
items = (
    'first',
    'second',
)
"""
        assert code == expected

    def test_empty_tuple_wrap_false(self):
        gen = CodeGen()
        with gen.Tuple('empty').wrap('inline'):
            pass
        code = gen.generate_str()
        assert code == "empty = ()\n"

    def test_wrap_expand(self):
        gen = CodeGen()
        with gen.Tuple('t').wrap('expand'):
            gen.Item(1)
            gen.Item(2)
        code = gen.generate_str()
        expected = """\
t = (
    1, 2,
)
"""
        assert code == expected


class TestSetWrap:
    def test_wrap_false_inline(self):
        gen = CodeGen()
        with gen.Set('items').wrap('inline'):
            gen.Item(1)
            gen.Item(2)
        code = gen.generate_str()
        assert code == "items = {1, 2}\n"

    def test_wrap_always_default(self):
        gen = CodeGen()
        with gen.Set('items'):
            gen.Item(1)
            gen.Item(2)
        code = gen.generate_str()
        expected = """\
items = {
    1,
    2,
}
"""
        assert code == expected

    def test_wrap_int_multiline(self):
        gen = CodeGen()
        with gen.Set('items').wrap(20):
            gen.Item('first')
            gen.Item('second')
        code = gen.generate_str()
        expected = """\
items = {
    'first',
    'second',
}
"""
        assert code == expected

    def test_empty_set_wrap_false(self):
        gen = CodeGen()
        with gen.Set('empty_set').wrap('inline'):
            pass
        code = gen.generate_str()
        assert code == "empty_set = set()\n"

    def test_wrap_expand(self):
        gen = CodeGen()
        with gen.Set('s').wrap('expand'):
            gen.Item(1)
            gen.Item(2)
        code = gen.generate_str()
        expected = """\
s = {
    1, 2,
}
"""
        assert code == expected


class TestWrapMixedCollections:
    def test_list_of_dicts_with_wrap_false(self):
        gen = CodeGen()
        with gen.List('data').wrap('inline'):
            with gen.Dict('').wrap('inline'):
                gen.Var('a', 1)
            with gen.Dict('').wrap('inline'):
                gen.Var('b', 2)
        code = gen.generate_str()
        assert code == "data = [{'a': 1}, {'b': 2}]\n"

    def test_dict_of_lists_with_wrap_always(self):
        gen = CodeGen()
        with gen.Dict('data'):
            with gen.List('key_a').wrap('inline'):
                gen.Item(1)
                gen.Item(2)
        code = gen.generate_str()
        expected = """\
data = {
    'key_a': [1, 2],
}
"""
        assert code == expected

    def test_override_default_always_with_false(self):
        # Default 'always' should be overrideable by wrap('inline')
        gen = CodeGen()
        with gen.List('items').wrap('inline'):
            gen.Item(1)
        code = gen.generate_str()
        assert code == "items = [1]\n"

    def test_gatheritems_inline_with_dict_vars(self):
        # Verify GatherItems.get_inline() handles Var trailing comma correctly
        from alasio.codegen.python.obj_base import GatherItems
        gen = CodeGen()
        with gen.Dict('my_dict'):
            gen.Var('key', 42)
            gen.Var('other', 'value')
        # Access the Var items inside the Dict
        # Var.item_str includes trailing comma from line_ending,
        # but _strip_trailing_comma should remove it before joining
        gather = GatherItems()
        gather.add(gen.items[0].items)
        result = gather.get_inline()
        assert result == "'key': 42, 'other': 'value'"

    def test_expand_inside_newline(self):
        """Expand inside newline-wrapped outer collection."""
        gen = CodeGen()
        with gen.Dict('data'):
            with gen.List('numbers').wrap('expand'):
                gen.Item(1)
                gen.Item(2)
        code = gen.generate_str()
        expected = """\
data = {
    'numbers': [
        1, 2,
    ],
}
"""
        assert code == expected


class TestAutoWrap:
    """Tests for wrap('auto') -- decide inline vs expand based on GatherItems.DEFAULT_WIDTH.
    All expected outputs use multiline strings to verify the entire generated code."""

    def test_auto_short_stays_inline(self):
        """Auto mode: items that fit within DEFAULT_WIDTH stay inline."""
        gen = CodeGen()
        with gen.List('items').wrap('auto'):
            gen.Item(1)
            gen.Item(2)
        code = gen.generate_str()
        expected = """\
items = [1, 2]
"""
        assert code == expected

    def test_auto_long_becomes_expand(self):
        """Auto mode: items exceeding DEFAULT_WIDTH switch to expand."""
        x = "x" * 55
        y = "y" * 54
        gen = CodeGen()
        with gen.List('items').wrap('auto'):
            gen.Item(x)
            gen.Item(y)
        code = gen.generate_str()
        expected = f"""\
items = [
    '{x}', '{y}',
]
"""
        assert code == expected

    def test_auto_empty_list(self):
        """Auto mode with no items produces empty brackets."""
        gen = CodeGen()
        with gen.List('empty').wrap('auto'):
            pass
        code = gen.generate_str()
        expected = """\
empty = []
"""
        assert code == expected

    def test_auto_dict_short_stays_inline(self):
        """Auto mode: short Dict items stay inline."""
        gen = CodeGen()
        with gen.Dict('d').wrap('auto'):
            gen.Var('a', 1)
            gen.Var('b', 2)
        code = gen.generate_str()
        expected = """\
d = {'a': 1, 'b': 2}
"""
        assert code == expected

    def test_auto_dict_long_becomes_expand(self):
        """Auto mode: Dict items exceeding DEFAULT_WIDTH switch to expand."""
        gen = CodeGen()
        k = "k" * 50
        v = "v" * 60
        with gen.Dict('d').wrap('auto'):
            gen.Var(k, v)
            gen.Var(k, v)
        code = gen.generate_str()
        expected = f"""\
d = {{
    '{k}': '{v}',
    '{k}': '{v}',
}}
"""
        assert code == expected

    def test_auto_tuple_long_becomes_expand(self):
        """Auto mode: Tuple items exceeding DEFAULT_WIDTH switch to expand."""
        x = "x" * 55
        y = "y" * 55
        gen = CodeGen()
        with gen.Tuple('t').wrap('auto'):
            gen.Item(x)
            gen.Item(y)
        code = gen.generate_str()
        expected = f"""\
t = (
    '{x}',
    '{y}',
)
"""
        assert code == expected

    def test_auto_set_long_becomes_expand(self):
        """Auto mode: Set items exceeding DEFAULT_WIDTH switch to expand."""
        x = "x" * 55
        y = "y" * 54
        gen = CodeGen()
        with gen.Set('s').wrap('auto'):
            gen.Item(x)
            gen.Item(y)
        code = gen.generate_str()
        expected = f"""\
s = {{
    '{x}', '{y}',
}}
"""
        assert code == expected

    def test_auto_list_very_long_single_item(self):
        """Auto mode: single item longer than DEFAULT_WIDTH switches to expand."""
        x = "x" * 120
        gen = CodeGen()
        with gen.List('items').wrap('auto'):
            gen.Item(x)
        code = gen.generate_str()
        expected = f"""\
items = [
    '{x}',
]
"""
        assert code == expected

    def test_auto_nested_inline_outer_default(self):
        """Auto-wrap inside default (newline) outer."""
        gen = CodeGen()
        with gen.List('outer'):
            with gen.List('').wrap('auto'):
                gen.Item(1)
                gen.Item(2)
        code = gen.generate_str()
        expected = """\
outer = [
    [1, 2],
]
"""
        assert code == expected
