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

    def test_auto_blank_lines_around_def(self):
        """1 blank line before and after a Def inside a Class."""
        gen = CodeGen()
        with gen.Class('MyClass'):
            gen.Var('x', 1)
            with gen.Def('method_a'):
                pass
            gen.Var('y', 2)
            with gen.Def('method_b'):
                pass
            gen.Var('z', 3)

        code = gen.generate_str()
        expected = """\
class MyClass:
    x = 1

    def method_a():
        pass

    y = 2

    def method_b():
        pass

    z = 3
"""
        assert code == expected

    def test_auto_blank_lines_def_adjacent(self):
        """1 blank line between two adjacent Defs inside a Class."""
        gen = CodeGen()
        with gen.Class('MyClass'):
            with gen.Def('first'):
                pass
            with gen.Def('second'):
                pass
            with gen.Def('third'):
                pass

        code = gen.generate_str()
        expected = """\
class MyClass:
    def first():
        pass

    def second():
        pass

    def third():
        pass
"""
        assert code == expected

    def test_auto_blank_lines_class_inside_class(self):
        """1 blank line before and after a nested Class inside a Class."""
        gen = CodeGen()
        with gen.Class('Outer'):
            gen.Var('x', 1)
            with gen.Class('Inner'):
                gen.Var('y', 2)
            gen.Var('z', 3)

        code = gen.generate_str()
        expected = """\
class Outer:
    x = 1

    class Inner:
        y = 2

    z = 3
"""
        assert code == expected

    def test_auto_blank_lines_mixed_class_def(self):
        """1 blank line around mixed Class and Def inside a Class."""
        gen = CodeGen()
        with gen.Class('Outer'):
            gen.Var('x', 1)
            with gen.Class('Inner'):
                pass
            with gen.Def('method'):
                pass
            gen.Var('y', 2)

        code = gen.generate_str()
        expected = """\
class Outer:
    x = 1

    class Inner:
        pass

    def method():
        pass

    y = 2
"""
        assert code == expected


class TestObjRawClass:
    def test_basic(self):
        """RawClass with header inside, produces body."""
        gen = CodeGen()
        with gen.RawClass():
            gen.Raw('class MyClass:', indent=False)
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
        with gen.RawClass().set_inherit('BaseModel', metaclass='Singleton'):
            gen.Raw('class User(BaseModel, metaclass=Singleton):', indent=False)
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
            with gen.RawClass():
                gen.Raw('    class Inner:', indent=False)
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
        with gen.RawClass():
            gen.Raw('class MyClass:', indent=False)
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
        with gen.RawClass():
            gen.Raw('class MyClass:', indent=False)
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
        with gen.RawClass():
            gen.Raw('class MyClass:', indent=False)
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

    def test_auto_blank_lines_around_def(self):
        """1 blank line before and after a Def inside RawClass."""
        gen = CodeGen()
        with gen.RawClass():
            gen.Raw('class MyClass:', indent=False)
            gen.Var('x', 1)
            with gen.Def('method_a'):
                pass
            gen.Var('y', 2)
            with gen.Def('method_b'):
                pass
            gen.Var('z', 3)

        code = gen.generate_str()
        expected = """\
class MyClass:
    x = 1

    def method_a():
        pass

    y = 2

    def method_b():
        pass

    z = 3
"""
        assert code == expected

    def test_auto_blank_lines_def_adjacent(self):
        """1 blank line between adjacent Defs inside RawClass."""
        gen = CodeGen()
        with gen.RawClass():
            gen.Raw('class MyClass:', indent=False)
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


class TestRawClassDefTopLevel:
    def test_auto_blank_lines_between_raw_class_and_def(self):
        """
        At top level, RawClass and RawDef should get 2 blank lines between them,
        same as Class and Def.
        """
        gen = CodeGen()
        with gen.RawClass():
            gen.Raw('class First:', indent=False)
            gen.Var('a', 1)
        with gen.RawDef():
            gen.Raw('def func():', indent=False)
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
        with gen.RawClass():
            gen.Raw('class Custom:', indent=False)
            gen.Var('y', 2)

        code = gen.generate_str()
        expected = """\
class Existing:
    x = 1


class Custom:
    y = 2
"""
        assert code == expected
