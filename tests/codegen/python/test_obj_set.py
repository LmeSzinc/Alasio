from alasio.codegen.python.gen import CodeGenerator


class TestObjSet:
    def test_simple_set(self):
        gen = CodeGenerator()
        with gen.Set('my_set'):
            gen.Item(1)
            gen.Item('two')

        code = gen.write()
        expected = """\
my_set = {
    1,
    'two',
}
"""
        # Note: Set in codeobj uses { } always if name is provided and items exist.
        assert code == expected

    def test_empty_set(self):
        gen = CodeGenerator()
        with gen.Set('empty_set'):
            pass
        gen.Set()

        code = gen.write()
        expected = """\
empty_set = set()
set()
"""
        assert code == expected

    def test_nested_set(self):
        gen = CodeGenerator()
        with gen.Set('outer'):
            with gen.Set():
                gen.MultilineComment('inner set')
                gen.Item(10)

        code = gen.write()
        expected = """\
outer = {
    {
        \"\"\"
        inner set
        \"\"\"
        10,
    },
}
"""
        assert code == expected
