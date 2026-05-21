from alasio.codegen.python.gen import CodeGenerator


class TestListWrap:
    def test_wrap_false_inline(self):
        gen = CodeGenerator()
        with gen.List('items').wrap(False):
            gen.Item(1)
            gen.Item(2)
            gen.Item(3)
        code = gen.write()
        assert code == "items = [1, 2, 3,]\n"

    def test_wrap_always_explicit(self):
        gen = CodeGenerator()
        with gen.List('items').wrap('always'):
            gen.Item(1)
            gen.Item(2)
        code = gen.write()
        expected = """\
items = [
    1,
    2,
]
"""
        assert code == expected

    def test_wrap_always_default(self):
        # Default _wrap is 'always' when using with gen.List
        gen = CodeGenerator()
        with gen.List('items'):
            gen.Item(1)
            gen.Item(2)
        code = gen.write()
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
        gen = CodeGenerator()
        with gen.List('items').wrap(80):
            gen.Item(1)
            gen.Item(2)
        code = gen.write()
        assert code == "items = [1, 2,]\n"

    def test_wrap_int_multiline(self):
        # Width small enough to force wrapping
        gen = CodeGenerator()
        with gen.List('items').wrap(20):
            gen.Item('short')
            gen.Item('medium')
            gen.Item('another')
        code = gen.write()
        expected = """\
items = [
    'short',
    'medium',
    'another',
]
"""
        assert code == expected

    def test_wrap_true_defaults_to_120(self):
        # wrap(True) is equivalent to wrap(120)
        gen = CodeGenerator()
        with gen.List('items').wrap(True):
            gen.Item('short')
            gen.Item('items')
            gen.Item('fit_in_line')
        code = gen.write()
        # All fit within 120, so inline
        assert code == "items = ['short', 'items', 'fit_in_line',]\n"

    def test_empty_list_wrap_false(self):
        gen = CodeGenerator()
        with gen.List('empty').wrap(False):
            pass
        code = gen.write()
        assert code == "empty = []\n"

    def test_nested_list_inner_wrap_false(self):
        # Inner list with wrap(False) stays inline inside outer always-wrap
        gen = CodeGenerator()
        with gen.List('outer'):
            with gen.List().wrap(False):
                gen.Item(1)
                gen.Item(2)
        code = gen.write()
        expected = """\
outer = [
    [1, 2,],
]
"""
        assert code == expected


class TestDictWrap:
    def test_wrap_false_inline(self):
        gen = CodeGenerator()
        with gen.Dict('items').wrap(False):
            gen.Var('a', 1)
            gen.Var('b', 2)
        code = gen.write()
        assert code == "items = {'a': 1, 'b': 2,}\n"

    def test_wrap_always_default(self):
        gen = CodeGenerator()
        with gen.Dict('items'):
            gen.Var('a', 1)
            gen.Var('b', 2)
        code = gen.write()
        expected = """\
items = {
    'a': 1,
    'b': 2,
}
"""
        assert code == expected

    def test_wrap_int_multiline(self):
        gen = CodeGenerator()
        with gen.Dict('items').wrap(20):
            gen.Var('key1', 'value1')
            gen.Var('key2', 'value2')
        code = gen.write()
        expected = """\
items = {
    'key1': 'value1',
    'key2': 'value2',
}
"""
        assert code == expected

    def test_empty_dict_wrap_false(self):
        gen = CodeGenerator()
        with gen.Dict('empty').wrap(False):
            pass
        code = gen.write()
        assert code == "empty = {}\n"

    def test_single_item_dict_wrap_false(self):
        gen = CodeGenerator()
        with gen.Dict('single').wrap(False):
            gen.Var('key', 42)
        code = gen.write()
        assert code == "single = {'key': 42,}\n"


class TestTupleWrap:
    def test_wrap_false_inline(self):
        gen = CodeGenerator()
        with gen.Tuple('items').wrap(False):
            gen.Item(1)
            gen.Item(2)
        code = gen.write()
        assert code == "items = (1, 2,)\n"

    def test_wrap_always_default(self):
        gen = CodeGenerator()
        with gen.Tuple('items'):
            gen.Item(1)
            gen.Item(2)
        code = gen.write()
        expected = """\
items = (
    1,
    2,
)
"""
        assert code == expected

    def test_wrap_int_multiline(self):
        gen = CodeGenerator()
        with gen.Tuple('items').wrap(20):
            gen.Item('first')
            gen.Item('second')
        code = gen.write()
        expected = """\
items = (
    'first',
    'second',
)
"""
        assert code == expected

    def test_empty_tuple_wrap_false(self):
        gen = CodeGenerator()
        with gen.Tuple('empty').wrap(False):
            pass
        code = gen.write()
        assert code == "empty = ()\n"


class TestSetWrap:
    def test_wrap_false_inline(self):
        gen = CodeGenerator()
        with gen.Set('items').wrap(False):
            gen.Item(1)
            gen.Item(2)
        code = gen.write()
        assert code == "items = {1, 2,}\n"

    def test_wrap_always_default(self):
        gen = CodeGenerator()
        with gen.Set('items'):
            gen.Item(1)
            gen.Item(2)
        code = gen.write()
        expected = """\
items = {
    1,
    2,
}
"""
        assert code == expected

    def test_wrap_int_multiline(self):
        gen = CodeGenerator()
        with gen.Set('items').wrap(20):
            gen.Item('first')
            gen.Item('second')
        code = gen.write()
        expected = """\
items = {
    'first',
    'second',
}
"""
        assert code == expected

    def test_empty_set_wrap_false(self):
        gen = CodeGenerator()
        with gen.Set('empty_set').wrap(False):
            pass
        code = gen.write()
        assert code == "empty_set = set()\n"


class TestWrapMixedCollections:
    def test_list_of_dicts_with_wrap_false(self):
        gen = CodeGenerator()
        with gen.List('data').wrap(False):
            with gen.Dict('').wrap(False):
                gen.Var('a', 1)
            with gen.Dict('').wrap(False):
                gen.Var('b', 2)
        code = gen.write()
        assert code == "data = [{'a': 1,}, {'b': 2,},]\n"

    def test_dict_of_lists_with_wrap_always(self):
        gen = CodeGenerator()
        with gen.Dict('data'):
            with gen.List('key_a').wrap(False):
                gen.Item(1)
                gen.Item(2)
        code = gen.write()
        expected = """\
data = {
    'key_a': [1, 2,],
}
"""
        assert code == expected

    def test_override_default_always_with_false(self):
        # Default 'always' should be overrideable by wrap(False)
        gen = CodeGenerator()
        with gen.List('items').wrap(False):
            gen.Item(1)
        code = gen.write()
        assert code == "items = [1,]\n"

    def test_gatheritems_inline_with_dict_vars(self):
        # Verify GatherItems.get_inline() handles Var trailing comma correctly
        from alasio.codegen.python.base import GatherItems
        gen = CodeGenerator()
        with gen.Dict('my_dict'):
            gen.Var('key', 42)
            gen.Var('other', 'value')
        # Access the Var items inside the Dict
        # Var.item_str includes trailing comma from line_ending,
        # but _strip_trailing_comma should remove it before joining
        gather = GatherItems()
        gather.add(gen.items[0].items)
        result = gather.get_inline()
        assert result == "'key': 42, 'other': 'value',"
