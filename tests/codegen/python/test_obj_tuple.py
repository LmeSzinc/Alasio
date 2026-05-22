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
