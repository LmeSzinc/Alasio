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


class TestObjRawDef:
    def test_basic(self):
        """RawDef produces body without def header."""
        gen = CodeGen()
        gen.Raw('def run(self, timeout=10):')
        with gen.RawDef():
            gen.Var('name', 'john')

        code = gen.generate_str()
        expected = """\
def run(self, timeout=10):
    name = 'john'
"""
        assert code == expected

    def test_with_args(self):
        """RawDef with set_args still skips the header."""
        gen = CodeGen()
        gen.Raw('def run(self, timeout=10):')
        with gen.RawDef().set_args('self', timeout=10):
            gen.Var('name', 'john')

        code = gen.generate_str()
        expected = """\
def run(self, timeout=10):
    name = 'john'
"""
        assert code == expected

    def test_empty(self):
        """Empty RawDef generates nothing."""
        gen = CodeGen()
        with gen.RawDef():
            pass

        code = gen.generate_str()
        assert code == ''

    def test_nested_in_class(self):
        """RawDef nested inside a class."""
        gen = CodeGen()
        with gen.Class('MyClass'):
            gen.Raw('    def method(self):')
            with gen.RawDef():
                gen.Var('x', 1)

        code = gen.generate_str()
        expected = """\
class MyClass:
    def method(self):
        x = 1
"""
        assert code == expected

    def test_multiple_items(self):
        """RawDef with multiple statements."""
        gen = CodeGen()
        gen.Raw('def func():')
        with gen.RawDef():
            gen.Var('x', 1)
            gen.Var('y', 2)

        code = gen.generate_str()
        expected = """\
def func():
    x = 1
    y = 2
"""
        assert code == expected

    def test_auto_blank_lines(self):
        """RawDef auto blank lines between nested elements."""
        gen = CodeGen()
        gen.Raw('def func():')
        with gen.RawDef():
            gen.Var('x', 1)
            with gen.If('x > 0'):
                gen.Var('result', 'positive')
            gen.Var('y', 2)

        code = gen.generate_str()
        expected = """\
def func():
    x = 1
    if x > 0:
        result = 'positive'
    y = 2
"""
        assert code == expected

    def test_with_raw_indent_false_header(self):
        """RawDef with custom header using gen.Raw(indent=False)."""
        gen = CodeGen()
        gen.Raw('def func():', indent=False)
        with gen.RawDef():
            gen.Var('x', 1)

        code = gen.generate_str()
        expected = """\
def func():
    x = 1
"""
        assert code == expected
