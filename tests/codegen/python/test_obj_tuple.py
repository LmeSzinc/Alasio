from alasio.codegen.python.gen import CodeGenerator


class TestObjTuple:
    def test_simple_tuple(self):
        gen = CodeGenerator()
        with gen.Tuple('my_tuple'):
            gen.Item(1)
            gen.Item('two')

        code = gen.write()
        expected = """\
my_tuple = (
    1,
    'two',
)"""
        assert code == expected

    def test_empty_tuple(self):
        gen = CodeGenerator()
        with gen.Tuple('empty'):
            pass
        gen.Tuple()

        code = gen.write()
        expected = """\
empty = ()
()"""
        assert code == expected

    def test_nested_tuple(self):
        gen = CodeGenerator()
        with gen.Tuple('outer'):
            with gen.Tuple():
                gen.Comment('inner tuple')
                gen.Item(1.1)

        code = gen.write()
        expected = """\
outer = (
    (
        # inner tuple
        1.1,
    ),
)"""
        assert code == expected
