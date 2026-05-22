from alasio.codegen.python.gen import CodeGen


class TestObjDict:
    def test_simple_dict(self):
        gen = CodeGen()
        with gen.Dict('my_dict'):
            gen.Var('a', 1)
            gen.Var('b', 'two')
            gen.Var(3, 'c')

        code = gen.generate_str()
        expected = """\
my_dict = {
    'a': 1,
    'b': 'two',
    3: 'c',
}
"""
        assert code == expected

    def test_empty_dict(self):
        gen = CodeGen()
        with gen.Dict('empty'):
            pass
        gen.Dict('')

        code = gen.generate_str()
        expected = """\
empty = {}
{}
"""
        assert code == expected

    def test_nested_dict(self):
        gen = CodeGen()
        with gen.Dict('outer'):
            with gen.Dict('inner'):
                gen.Comment('val part')
                gen.Var('key', 'val')

        code = gen.generate_str()
        expected = """\
outer = {
    'inner': {
        # val part
        'key': 'val',
    },
}
"""
        assert code == expected

    def test_list_nested_in_dict(self):
        gen = CodeGen()
        with gen.Dict('my_dict'):
            with gen.List('nested_list'):
                gen.MultilineComment('inner list')
                gen.Item(10)

        code = gen.generate_str()
        expected = """\
my_dict = {
    'nested_list': [
        \"\"\"
        inner list
        \"\"\"
        10,
    ],
}
"""
        assert code == expected
