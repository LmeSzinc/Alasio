from alasio.codegen.python.gen import CodeGenerator


class TestObjDef:
    def test_simple_def(self):
        gen = CodeGenerator()
        with gen.Def('hello').set_args('name'):
            gen.Comment('say hello')
            gen.Var('msg', 'hello')

        code = gen.write()
        expected = """\
def hello(name):
    # say hello
    msg = 'hello'
"""
        assert code == expected

    def test_empty_def(self):
        gen = CodeGenerator()
        with gen.Def('nop').set_args('self'):
            pass

        code = gen.write()
        expected = """\
def nop(self):
    pass
"""
        assert code == expected

    def test_def_with_kwargs(self):
        gen = CodeGenerator()
        with gen.Def('search').set_args('query', timeout=10, retry=True):
            gen.MultilineComment('search something')
            gen.Var('found', [])

        code = gen.write()
        expected = """\
def search(query, timeout=10, retry=True):
    \"\"\"
    search something
    \"\"\"
    found = []
"""
        assert code == expected
