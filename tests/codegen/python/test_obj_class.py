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
