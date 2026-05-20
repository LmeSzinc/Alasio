from alasio.codegen.python.gen import CodeGenerator


class TestObjList:
    def test_simple_list(self):
        gen = CodeGenerator()
        with gen.List('my_list'):
            gen.Item(1)
            gen.Item('two')

        code = gen.write()
        expected = """\
my_list = [
    1,
    'two',
]"""
        assert code == expected

    def test_empty_list(self):
        gen = CodeGenerator()
        with gen.List('empty'):
            pass
        gen.List()

        code = gen.write()
        expected = """\
empty = []
[]"""
        assert code == expected

    def test_nested_list(self):
        gen = CodeGenerator()
        with gen.List('outer'):
            with gen.List():
                gen.Comment('first')
                gen.Item(1)
                gen.Item(2)
            with gen.List():
                gen.MultilineComment('second')
                gen.Item(3)

        code = gen.write()
        expected = """\
outer = [
    [
        # first
        1,
        2,
    ],
    [
        \"\"\"
        second
        \"\"\"
        3,
    ],
]"""
        assert code == expected

    def test_dict_nested_in_list(self):
        gen = CodeGenerator()
        with gen.List('my_list'):
            with gen.Dict(''):
                gen.Var('a', 1)

        code = gen.write()
        expected = """\
my_list = [
    {
        'a': 1,
    },
]"""
        assert code == expected
