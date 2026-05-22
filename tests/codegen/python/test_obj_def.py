from alasio.codegen.python.gen import CodeGen


class TestObjDef:
    def test_simple_def(self):
        gen = CodeGen()
        with gen.Def('hello').set_args('name'):
            gen.Comment('say hello')
            gen.Var('msg', 'hello')

        code = gen.generate_str()
        expected = """\
def hello(name):
    # say hello
    msg = 'hello'
"""
        assert code == expected

    def test_empty_def(self):
        gen = CodeGen()
        with gen.Def('nop').set_args('self'):
            pass

        code = gen.generate_str()
        expected = """\
def nop(self):
    pass
"""
        assert code == expected

    def test_def_with_kwargs(self):
        gen = CodeGen()
        with gen.Def('search').set_args('query', timeout=10, retry=True):
            gen.MultilineComment('search something')
            gen.Var('found', [])

        code = gen.generate_str()
        expected = """\
def search(query, timeout=10, retry=True):
    \"\"\"
    search something
    \"\"\"
    found = []
"""
        assert code == expected
