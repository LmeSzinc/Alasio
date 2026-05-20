from alasio.codegen.python.gen import CodeGenerator


class TestObjClass:
    def test_simple_class(self):
        gen = CodeGenerator()
        with gen.Class('MyClass'):
            gen.Var('x', 10)

        code = gen.write()
        assert code == "class MyClass:\n    x = 10"

    def test_empty_class(self):
        gen = CodeGenerator()
        with gen.Class('Empty'):
            pass

        code = gen.write()
        assert code == "class Empty:\n    pass"

    def test_class_inheritance(self):
        gen = CodeGenerator()
        with gen.Class('Child').set_inherit('Base', metaclass='Singleton'):
            gen.Comment('member')
            gen.Anno('y', 'int')

        code = gen.write()
        expected = """\
class Child(Base, metaclass=Singleton):
    # member
    y: int"""
        assert code == expected

    def test_class_nesting(self):
        gen = CodeGenerator()
        with gen.Class('Outer'):
            with gen.Class('Inner'):
                gen.Var('z', 30)

        code = gen.write()
        expected = """\
class Outer:
    class Inner:
        z = 30"""
        assert code == expected

    def test_class_complex_content(self):
        gen = CodeGenerator()
        with gen.Class('Complex'):
            gen.MultilineComment('docstring')
            gen.Anno('a', 'int')
            gen.Var('b', 'hello')
            with gen.List('c'):
                gen.Comment('items')
                gen.Item(1)

        code = gen.write()
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
    ]"""
        assert code == expected
