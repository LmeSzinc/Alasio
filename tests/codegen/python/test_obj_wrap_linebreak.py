from alasio.codegen.python.gen import CodeGen
from alasio.codegen.python.obj_base import GatherItems


# ============================================================================
# GatherItems.get_inline()
# ============================================================================
class TestGetInline:
    """get_inline joins all item_str with spaces, strips trailing comma."""

    def test_basic(self):
        gen = CodeGen()
        with gen.List('l'):
            gen.Item(1)
            gen.Item(2)
        gi = GatherItems(wrap='inline').add(gen.items[0].items)
        assert gi.get_inline() == "1, 2"

    def test_single_item(self):
        gen = CodeGen()
        with gen.List('l'):
            gen.Item('only')
        gi = GatherItems(wrap='inline').add(gen.items[0].items)
        assert gi.get_inline() == "'only'"

    def test_empty(self):
        gi = GatherItems()
        assert gi.get_inline() == ""

    def test_dict_vars(self):
        gen = CodeGen()
        with gen.Dict('d'):
            gen.Var('key', 42)
            gen.Var('other', 'value')
        gi = GatherItems(wrap='inline').add(gen.items[0].items)
        assert gi.get_inline() == "'key': 42, 'other': 'value'"


# ============================================================================
# GatherItems.iter_multiline()
# ============================================================================
class TestIterMultilineLinebreak:
    """iter_multiline yields rows respecting wrap mode and Linebreak."""

    def test_inline_mode(self):
        """wrap='inline' -> single row, same as get_inline()."""
        gen = CodeGen()
        with gen.List('l'):
            gen.Item(1)
            gen.Item(2)
        gi = GatherItems(wrap='inline').add(gen.items[0].items)
        assert list(gi.iter_multiline()) == ["1, 2"]

    def test_newline_mode(self):
        """wrap='newline' -> each item on its own row, no trailing comma."""
        gen = CodeGen()
        with gen.List('l'):
            gen.Item(1)
            gen.Item(2)
        gi = GatherItems(wrap='newline').add(gen.items[0].items)
        assert list(gi.iter_multiline()) == ["1", "2"]

    def test_expand_single_row(self):
        """Items short enough to pack into one row."""
        gen = CodeGen()
        with gen.List('l'):
            gen.Item(1)
            gen.Item(2)
            gen.Item(3)
        gi = GatherItems(wrap='expand').add(gen.items[0].items)
        assert list(gi.iter_multiline()) == ["1, 2, 3,"]

    def test_expand_multi_row(self):
        """Too many items to fit in one row -> multiple rows."""
        gen = CodeGen()
        with gen.List('l'):
            for i in range(1, 10):
                gen.Item(i)
        gi = GatherItems(wrap=15).add(gen.items[0].items)
        assert list(gi.iter_multiline()) == [
            "1, 2, 3, 4,",
            "5, 6, 7, 8,",
            "9,",
        ]

    def test_int_wrap_single_row(self):
        """Int width large enough -> single row preserves trailing comma."""
        gen = CodeGen()
        with gen.List('l'):
            gen.Item(1)
            gen.Item(2)
        gi = GatherItems(wrap=80).add(gen.items[0].items)
        assert list(gi.iter_multiline()) == ["1, 2,"]

    def test_int_wrap_multi_row(self):
        """Int width forces multi-row splitting."""
        gen = CodeGen()
        with gen.List('l'):
            gen.Item('short')
            gen.Item('medium')
            gen.Item('another')
        gi = GatherItems(wrap=20).add(gen.items[0].items)
        # indent_width=4, remain=16. "'short'," (8) + " 'medium'," (9) = 18 > 16
        assert list(gi.iter_multiline()) == ["'short',", "'medium',", "'another',"]

    def test_empty_items(self):
        gi = GatherItems(wrap=40)
        assert list(gi.iter_multiline()) == []

    def test_single_long_item(self):
        """A single item longer than max_width is yielded anyway."""
        gen = CodeGen()
        with gen.List('l'):
            gen.Item("very_long_item")
        gi = GatherItems(wrap=10).add(gen.items[0].items)
        assert list(gi.iter_multiline()) == ["'very_long_item',"]

    def test_linebreak_expand(self):
        """Linebreak splits items into groups; each group independently packed."""
        gen = CodeGen()
        with gen.List('l'):
            gen.Item(1)
            gen.Item(2)
            gen.Item(3)
            gen.Linebreak()
            gen.Item(4)
            gen.Item(5)
        gi = GatherItems(wrap='expand').add(gen.items[0].items)
        assert list(gi.iter_multiline()) == ["1, 2, 3,", "4, 5,"]

    def test_linebreak_int(self):
        """Linebreak with int wrap."""
        gen = CodeGen()
        with gen.List('l'):
            gen.Item(10)
            gen.Item(20)
            gen.Linebreak()
            gen.Item(30)
        gi = GatherItems(wrap=50).add(gen.items[0].items)
        assert list(gi.iter_multiline()) == ["10, 20,", "30,"]

    def test_multiple_linebreaks(self):
        """Multiple Linebreaks create >2 groups."""
        gen = CodeGen()
        with gen.List('l'):
            gen.Item(1)
            gen.Linebreak()
            gen.Item(2)
            gen.Linebreak()
            gen.Item(3)
        gi = GatherItems(wrap='expand').add(gen.items[0].items)
        assert list(gi.iter_multiline()) == ["1,", "2,", "3,"]

    def test_linebreak_at_start(self):
        """Linebreak as first item -> first group is empty (skipped)."""
        gen = CodeGen()
        with gen.List('l'):
            gen.Linebreak()
            gen.Item(1)
        gi = GatherItems(wrap='expand').add(gen.items[0].items)
        assert list(gi.iter_multiline()) == ["1,"]

    def test_linebreak_at_end(self):
        """Trailing Linebreak -> no empty group appended."""
        gen = CodeGen()
        with gen.List('l'):
            gen.Item(1)
            gen.Linebreak()
        gi = GatherItems(wrap='expand').add(gen.items[0].items)
        assert list(gi.iter_multiline()) == ["1,"]

    def test_consecutive_linebreaks(self):
        """Consecutive Linebreaks produce no empty groups."""
        gen = CodeGen()
        with gen.List('l'):
            gen.Item(1)
            gen.Linebreak()
            gen.Linebreak()
            gen.Item(2)
        gi = GatherItems(wrap='expand').add(gen.items[0].items)
        assert list(gi.iter_multiline()) == ["1,", "2,"]

    def test_linebreak_only_items(self):
        """Only Linebreak items -> no groups -> empty."""
        gen = CodeGen()
        with gen.List('l'):
            gen.Linebreak()
            gen.Linebreak()
        gi = GatherItems(wrap='expand').add(gen.items[0].items)
        assert list(gi.iter_multiline()) == []


# ============================================================================
# ClosureWithName.generate() with Linebreak
# ============================================================================
class TestLinebreakInClosure:
    """gen.Linebreak() inside List/Dict with various wrap modes."""

    def test_auto_inline_fits(self):
        """auto: total fits DEFAULT_WIDTH -> inline, Linebreak ignored."""
        gen = CodeGen()
        with gen.List('items').wrap('auto'):
            gen.Item(1)
            gen.Item(2)
            gen.Linebreak()
            gen.Item(3)
        assert gen.generate_str() == "items = [1, 2, 3]\n"

    def test_auto_overflow(self):
        """auto: total exceeds DEFAULT_WIDTH -> groups separated."""
        x = "x" * 55
        y = "y" * 54
        gen = CodeGen()
        with gen.List('items').wrap('auto'):
            gen.Item(x)
            gen.Item(y)
            gen.Linebreak()
            gen.Item('z')
        expected = """\
items = [
    'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx', 'yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy',
    'z',
]
"""
        assert gen.generate_str() == expected

    def test_expand_no_linebreak(self):
        """expand without Linebreak -> all items packed together."""
        gen = CodeGen()
        with gen.List('items').wrap('expand'):
            gen.Item(1)
            gen.Item(2)
            gen.Item(3)
        assert gen.generate_str() == "items = [\n    1, 2, 3,\n]\n"

    def test_expand_with_linebreak(self):
        """expand with Linebreak -> groups on separate lines."""
        gen = CodeGen()
        with gen.Dict('cfg').wrap('expand'):
            gen.Var('a', 1)
            gen.Var('b', 2)
            gen.Linebreak()
            gen.Var('c', 3)
        expected = """\
cfg = {
    'a': 1, 'b': 2,
    'c': 3,
}
"""
        assert gen.generate_str() == expected

    def test_expand_multi_row_linebreak(self):
        """expand with many items + Linebreak -> groups on separate lines."""
        gen = CodeGen()
        with gen.List('data').wrap('expand'):
            for i in range(6):
                gen.Item(i)
            gen.Linebreak()
            for i in range(6, 12):
                gen.Item(i)
        expected = """\
data = [
    0, 1, 2, 3, 4, 5,
    6, 7, 8, 9, 10, 11,
]
"""
        assert gen.generate_str() == expected

    def test_int_wrap(self):
        """int wrap with Linebreak -> groups each on its own line."""
        gen = CodeGen()
        with gen.List('items').wrap(50):
            gen.Item('apple')
            gen.Item('banana')
            gen.Linebreak()
            gen.Item('cherry')
        expected = """\
items = [
    'apple', 'banana',
    'cherry',
]
"""
        assert gen.generate_str() == expected

    def test_int_wrap_overflow_within_group(self):
        """int wrap forces a group to split into multiple rows."""
        gen = CodeGen()
        with gen.List('items').wrap(30):
            gen.Item('alpha_value')
            gen.Item('beta_value')
            gen.Linebreak()
            gen.Item('gamma_value')
        expected = """\
items = [
    'alpha_value',
    'beta_value',
    'gamma_value',
]
"""
        assert gen.generate_str() == expected

    def test_multiple_linebreaks(self):
        """Three groups from two Linebreaks, each on its own row."""
        gen = CodeGen()
        with gen.Dict('cfg').wrap('expand'):
            gen.Var('a', 1)
            gen.Linebreak()
            gen.Var('b', 2)
            gen.Linebreak()
            gen.Var('c', 3)
        expected = """\
cfg = {
    'a': 1,
    'b': 2,
    'c': 3,
}
"""
        assert gen.generate_str() == expected

    def test_newline_ignores_linebreak(self):
        """newline mode doesn't use GatherItems — Linebreak.generate() yields nothing."""
        gen = CodeGen()
        with gen.List('items'):
            gen.Item(1)
            gen.Linebreak()
            gen.Item(2)
        expected = """\
items = [
    1,
    2,
]
"""
        assert gen.generate_str() == expected

    def test_empty(self):
        """Empty closure -> closure_empty."""
        gen = CodeGen()
        with gen.List('empty').wrap('auto'):
            pass
        assert gen.generate_str() == "empty = []\n"

    def test_linebreak_at_root_silent(self):
        """Linebreak at root level generates nothing."""
        gen = CodeGen()
        gen.Linebreak()
        gen.Var('x', 1)
        assert gen.generate_str() == "x = 1\n"

    def test_expand_single_row(self):
        """expand with few items stays as single content row."""
        gen = CodeGen()
        with gen.List('simple').wrap('expand'):
            gen.Item(1)
            gen.Item(2)
        assert gen.generate_str() == "simple = [\n    1, 2,\n]\n"

    def test_expand_multi_row_packing(self):
        """expand with 15 items packs them into one row (all fit within DEFAULT_WIDTH)."""
        gen = CodeGen()
        with gen.List('items').wrap('expand'):
            for i in range(15):
                gen.Item(i)
        expected = """\
items = [
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14,
]
"""
        assert gen.generate_str() == expected

    def test_auto_very_long_single_item(self):
        """auto with one 120-char item -> expand (multi-line)."""
        x = "x" * 120
        gen = CodeGen()
        with gen.List('items').wrap('auto'):
            gen.Item(x)
        expected = f"""\
items = [
    '{x}',
]
"""
        assert gen.generate_str() == expected

    def test_auto_dict_short(self):
        """auto Dict with Linebreak but total short -> inline, Linebreak ignored."""
        gen = CodeGen()
        with gen.Dict('d').wrap('auto'):
            gen.Var('a', 1)
            gen.Var('b', 2)
            gen.Linebreak()
            gen.Var('c', 3)
        assert gen.generate_str() == "d = {'a': 1, 'b': 2, 'c': 3}\n"

    def test_auto_dict_overflow(self):
        """auto Dict that overflows -> groups separated."""
        gen = CodeGen()
        with gen.Dict('d').wrap('auto'):
            gen.Var('x' * 50, 'y' * 50)
            gen.Var('x' * 50, 'y' * 50)
            gen.Linebreak()
            gen.Var('short', 1)
        expected = """\
d = {
    'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx': 'yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy',
    'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx': 'yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy',
    'short': 1,
}
"""
        assert gen.generate_str() == expected

    def test_auto_set(self):
        """auto Set with long items + Linebreak -> groups separated."""
        gen = CodeGen()
        with gen.Set('s').wrap('auto'):
            gen.Item('x' * 55)
            gen.Item('y' * 54)
            gen.Linebreak()
            gen.Item('z')
        expected = """\
s = {
    'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx', 'yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy',
    'z',
}
"""
        assert gen.generate_str() == expected

    def test_auto_tuple(self):
        """auto Tuple with long items + Linebreak -> groups separated."""
        gen = CodeGen()
        with gen.Tuple('t').wrap('auto'):
            gen.Item('x' * 55)
            gen.Item('y' * 55)
            gen.Linebreak()
            gen.Item('z')
        expected = """\
t = (
    'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx',
    'yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy',
    'z',
)
"""
        assert gen.generate_str() == expected


class TestLinebreakInNested:
    """Linebreak in nested contexts."""

    def test_dict_with_linebreak_inside_list(self):
        """Nested Dict with Linebreak — Linebreak skipped by item_str."""
        gen = CodeGen()
        with gen.List('settings').wrap('expand'):
            with gen.Dict('').wrap('auto'):
                gen.Var('key', 'value')
                gen.Linebreak()
                gen.Var('flag', True)
        expected = """\
settings = [
    {'key': 'value', 'flag': True},
]
"""
        assert gen.generate_str() == expected

    def test_list_inside_list_no_linebreak(self):
        """Nested list rendering without Linebreak."""
        gen = CodeGen()
        with gen.List('outer').wrap('expand'):
            with gen.List('').wrap('auto'):
                gen.Item(1)
                gen.Item(2)
        expected = """\
outer = [
    [1, 2],
]
"""
        assert gen.generate_str() == expected
