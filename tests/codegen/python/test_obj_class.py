from alasio.codegen.python.gen import CodeGen


class TestObjClass:
    def test_simple_class(self):
        gen = CodeGen()
        with gen.Class('MyClass'):
            gen.Var('x', 10)

        code = gen.generate_str()
        assert code == "class MyClass:\n    x = 10\n"

    def test_empty_class(self):
        gen = CodeGen()
        with gen.Class('Empty'):
            pass

        code = gen.generate_str()
        assert code == "class Empty:\n    pass\n"

    def test_class_inheritance(self):
        gen = CodeGen()
        with gen.Class('Child').set_inherit('Base', metaclass='Singleton'):
            gen.Comment('member')
            gen.Anno('y', 'int')

        code = gen.generate_str()
        expected = """\
class Child(Base, metaclass=Singleton):
    # member
    y: int
"""
        assert code == expected

    def test_class_nesting(self):
        gen = CodeGen()
        with gen.Class('Outer'):
            with gen.Class('Inner'):
                gen.Var('z', 30)

        code = gen.generate_str()
        expected = """\
class Outer:
    class Inner:
        z = 30
"""
        assert code == expected

    def test_class_complex_content(self):
        gen = CodeGen()
        with gen.Class('Complex'):
            gen.MultilineComment('docstring')
            gen.Anno('a', 'int')
            gen.Var('b', 'hello')
            with gen.List('c'):
                gen.Comment('items')
                gen.Item(1)

        code = gen.generate_str()
        expected = """\
class Complex:
    \"\"\"
    docstring
    \"\"\"
    a: int
    b = 'hello'
    c = [
        # items
        1,
    ]
"""
        assert code == expected


class TestObjRawClass:
    def test_basic(self):
        """RawClass produces body without class header."""
        gen = CodeGen()
        gen.Raw('class MyClass:')
        with gen.RawClass():
            gen.Var('x', 1)

        code = gen.generate_str()
        expected = """\
class MyClass:
    x = 1
"""
        assert code == expected

    def test_with_inherit(self):
        """RawClass with set_inherit still skips the header."""
        gen = CodeGen()
        gen.Raw('class User(BaseModel, metaclass=Singleton):')
        with gen.RawClass().set_inherit('BaseModel', metaclass='Singleton'):
            gen.Var('name', 'john')
            gen.Var('age', 0)

        code = gen.generate_str()
        expected = """\
class User(BaseModel, metaclass=Singleton):
    name = 'john'
    age = 0
"""
        assert code == expected

    def test_empty(self):
        """Empty RawClass generates nothing."""
        gen = CodeGen()
        with gen.RawClass():
            pass

        code = gen.generate_str()
        assert code == ''

    def test_nested_in_class(self):
        """RawClass nested inside another class."""
        gen = CodeGen()
        with gen.Class('Outer'):
            gen.Raw('    class Inner:')
            with gen.RawClass():
                gen.Var('y', 2)

        code = gen.generate_str()
        expected = """\
class Outer:
    class Inner:
        y = 2
"""
        assert code == expected

    def test_multiple_items(self):
        """RawClass with multiple items."""
        gen = CodeGen()
        gen.Raw('class MyClass:')
        with gen.RawClass():
            gen.Var('x', 1)
            gen.Var('y', 2)
            gen.Var('z', 3)

        code = gen.generate_str()
        expected = """\
class MyClass:
    x = 1
    y = 2
    z = 3
"""
        assert code == expected

    def test_auto_blank_lines_between_methods(self):
        """RawClass should preserve auto blank lines between nested methods."""
        gen = CodeGen()
        gen.Raw('class MyClass:')
        with gen.RawClass():
            with gen.Def('first'):
                pass
            with gen.Def('second'):
                pass

        code = gen.generate_str()
        expected = """\
class MyClass:
    def first():
        pass

    def second():
        pass
"""
        assert code == expected

    def test_auto_blank_lines_method_and_var(self):
        """RawClass auto blank lines between method and variable."""
        gen = CodeGen()
        gen.Raw('class MyClass:')
        with gen.RawClass():
            gen.Var('x', 1)
            with gen.Def('method'):
                pass

        code = gen.generate_str()
        expected = """\
class MyClass:
    x = 1

    def method():
        pass
"""
        assert code == expected

    def test_with_raw_indent_false_header(self):
        """RawClass with custom header using gen.Raw(indent=False)."""
        gen = CodeGen()
        gen.Raw('class MyClass:', indent=False)
        with gen.RawClass():
            gen.Var('x', 1)

        code = gen.generate_str()
        expected = """\
class MyClass:
    x = 1
"""
        assert code == expected


class TestRawClassDefTopLevel:
    def test_auto_blank_lines_between_raw_class_and_def(self):
        """
        At top level, RawClass and RawDef should get 2 blank lines between them,
        same as Class and Def.
        """
        gen = CodeGen()
        gen.Raw('class First:')
        with gen.RawClass():
            gen.Var('a', 1)
        gen.Raw('def func():')
        with gen.RawDef():
            gen.Var('b', 2)

        ref = CodeGen()
        with ref.Class('First'):
            ref.Var('a', 1)
        with ref.Def('func'):
            ref.Var('b', 2)

        assert gen.generate_str() == ref.generate_str()

    def test_mixed_with_regular_class(self):
        """RawClass and regular Class should interact correctly for blank lines."""
        gen = CodeGen()
        with gen.Class('Existing'):
            gen.Var('x', 1)
        gen.Raw('class Custom:')
        with gen.RawClass():
            gen.Var('y', 2)

        code = gen.generate_str()
        expected = """\
class Existing:
    x = 1


class Custom:
    y = 2
"""
        assert code == expected
