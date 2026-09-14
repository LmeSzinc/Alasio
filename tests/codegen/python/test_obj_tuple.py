from alasio.codegen.python.gen import CodeGen


class TestObjTuple:
    def test_simple_tuple(self):
        gen = CodeGen()
        with gen.Tuple('my_tuple'):
            gen.Item(1)
            gen.Item('two')

        code = gen.generate_str()
        expected = """\
my_tuple = (
    1,
    'two',
)
"""
        assert code == expected

    def test_empty_tuple(self):
        gen = CodeGen()
        with gen.Tuple('empty'):
            pass
        gen.Tuple()

        code = gen.generate_str()
        expected = """\
empty = ()
()
"""
        assert code == expected

    def test_nested_tuple(self):
        gen = CodeGen()
        with gen.Tuple('outer'):
            with gen.Tuple():
                gen.Comment('inner tuple')
                gen.Item(1.1)

        code = gen.generate_str()
        expected = """\
outer = (
    (
        # inner tuple
        1.1,
    ),
)
"""
        assert code == expected


class TestObjTupleSingleItem:
    """
    A single item tuple must keep the trailing comma, because "(item)" is a
    parenthesized expression instead of a tuple.
    """

    def test_single_item_newline(self):
        gen = CodeGen()
        with gen.Tuple('my_tuple'):
            gen.Item('one')

        code = gen.generate_str()
        expected = """\
my_tuple = (
    'one',
)
"""
        assert code == expected

    def test_single_item_inline(self):
        gen = CodeGen()
        with gen.Tuple('my_tuple').wrap('inline'):
            gen.Item('one')

        code = gen.generate_str()
        assert code == "my_tuple = ('one',)\n"

    def test_single_item_auto(self):
        gen = CodeGen()
        with gen.Tuple('my_tuple').wrap('auto'):
            gen.Item('one')

        code = gen.generate_str()
        assert code == "my_tuple = ('one',)\n"

    def test_single_item_wrap_int(self):
        gen = CodeGen()
        with gen.Tuple('my_tuple').wrap(20):
            gen.Item('one')

        code = gen.generate_str()
        assert code == "my_tuple = ('one',)\n"

    def test_single_item_expand(self):
        gen = CodeGen()
        with gen.Tuple('my_tuple').wrap('expand'):
            gen.Item('one')

        code = gen.generate_str()
        expected = """\
my_tuple = (
    'one',
)
"""
        assert code == expected

    def test_single_item_with_anno(self):
        """The filter datatype in config_dev, annotation and value are both kept."""
        gen = CodeGen()
        with gen.Class('OpsiGeneral').set_inherit('a.GroupBase'):
            with gen.Tuple('AkashiShopFilter').Anno('a.T_TUPLE_STR').wrap():
                gen.Item('ActionPoint')

        code = gen.generate_str()
        expected = """\
class OpsiGeneral(a.GroupBase):
    AkashiShopFilter: a.T_TUPLE_STR = ('ActionPoint',)
"""
        assert code == expected

    def test_single_item_nested_in_list(self):
        gen = CodeGen()
        with gen.List('outer').wrap('auto'):
            with gen.Tuple().wrap('auto'):
                gen.Item('one')

        code = gen.generate_str()
        assert code == "outer = [('one',)]\n"

    def test_single_item_nested_tuple(self):
        gen = CodeGen()
        with gen.Tuple('outer').wrap('auto'):
            with gen.Tuple().wrap('auto'):
                gen.Item('one')

        code = gen.generate_str()
        assert code == "outer = (('one',),)\n"

    def test_single_item_is_literal_evaluable(self):
        """The generated source must evaluate to a tuple, not to a plain value."""
        gen = CodeGen()
        with gen.Tuple('my_tuple').wrap('auto'):
            gen.Item('one')
        with gen.Tuple('my_tuple_nested').wrap('auto'):
            with gen.Tuple().wrap('auto'):
                gen.Item('one')

        code = gen.generate_str()
        namespace = {}
        exec(code, namespace)
        assert namespace['my_tuple'] == ('one',)
        assert isinstance(namespace['my_tuple'], tuple)
        assert namespace['my_tuple_nested'] == (('one',),)
        assert isinstance(namespace['my_tuple_nested'][0], tuple)
