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

    def test_auto_blank_lines_around_def_inside_def(self):
        """1 blank line before and after a nested Def inside a Def."""
        gen = CodeGen()
        with gen.Def('outer'):
            gen.Var('x', 1)
            with gen.Def('inner'):
                pass
            gen.Var('y', 2)

        code = gen.generate_str()
        expected = """\
def outer():
    x = 1

    def inner():
        pass

    y = 2
"""
        assert code == expected

    def test_auto_blank_lines_nested_def_adjacent(self):
        """1 blank line between two adjacent nested Defs inside a Def."""
        gen = CodeGen()
        with gen.Def('outer'):
            with gen.Def('first'):
                pass
            with gen.Def('second'):
                pass

        code = gen.generate_str()
        expected = """\
def outer():
    def first():
        pass

    def second():
        pass
"""
        assert code == expected


class TestObjRawDef:
    def test_basic(self):
        """RawDef with header inside, produces body."""
        gen = CodeGen()
        with gen.RawDef():
            gen.Raw('def run(self, timeout=10):', indent=False)
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
        with gen.RawDef().set_args('self', timeout=10):
            gen.Raw('def run(self, timeout=10):', indent=False)
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
            with gen.RawDef():
                gen.Raw('    def method(self):', indent=False)
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
        with gen.RawDef():
            gen.Raw('def func():', indent=False)
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
        with gen.RawDef():
            gen.Raw('def func():', indent=False)
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

    def test_auto_blank_lines_around_def(self):
        """1 blank line before and after a Def inside RawDef."""
        gen = CodeGen()
        with gen.RawDef():
            gen.Raw('def outer():', indent=False)
            gen.Var('x', 1)
            with gen.Def('inner'):
                pass
            gen.Var('y', 2)

        code = gen.generate_str()
        expected = """\
def outer():
    x = 1

    def inner():
        pass

    y = 2
"""
        assert code == expected

    def test_auto_blank_lines_def_adjacent(self):
        """1 blank line between adjacent Defs inside RawDef."""
        gen = CodeGen()
        with gen.RawDef():
            gen.Raw('def outer():', indent=False)
            with gen.Def('first'):
                pass
            with gen.Def('second'):
                pass

        code = gen.generate_str()
        expected = """\
def outer():
    def first():
        pass

    def second():
        pass
"""
        assert code == expected

    def test_raw_def_inside_class(self):
        """RawDef inside a Class (header inside RawDef context)."""
        gen = CodeGen()
        with gen.Class('MyClass'):
            gen.Var('x', 1)
            with gen.RawDef():
                gen.Raw('    def method(self):', indent=False)
                gen.Var('y', 2)
            gen.Var('z', 3)

        code = gen.generate_str()
        expected = """\
class MyClass:
    x = 1

    def method(self):
        y = 2

    z = 3
"""
        assert code == expected

    def test_raw_def_inside_def(self):
        """RawDef inside a Def (header inside RawDef context)."""
        gen = CodeGen()
        with gen.Def('outer'):
            gen.Var('x', 1)
            with gen.RawDef():
                gen.Raw('    def inner():', indent=False)
                gen.Var('y', 2)
            gen.Var('z', 3)

        code = gen.generate_str()
        expected = """\
def outer():
    x = 1

    def inner():
        y = 2

    z = 3
"""
        assert code == expected

    def test_raw_def_inside_raw_class(self):
        """RawDef inside RawClass (header inside RawDef context)."""
        gen = CodeGen()
        with gen.RawClass():
            gen.Raw('class MyClass:', indent=False)
            gen.Var('x', 1)
            with gen.RawDef():
                gen.Raw('    def method(self):', indent=False)
                gen.Var('y', 2)
            gen.Var('z', 3)

        code = gen.generate_str()
        expected = """\
class MyClass:
    x = 1

    def method(self):
        y = 2

    z = 3
"""
        assert code == expected
