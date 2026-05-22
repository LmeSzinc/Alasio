"""Tests for context managers used by CodeGen and CodeObject."""
from alasio.codegen.python.obj_base import ApplyContextName
from alasio.codegen.python.gen import CodeGen


class TestApplyContextName:
    def test_context_restoration(self):
        gen = CodeGen()
        gen.context_name = 'Initial'

        with ApplyContextName(gen, 'NewContext'):
            assert gen.context_name == 'NewContext'
            with ApplyContextName(gen, 'SubContext'):
                assert gen.context_name == 'SubContext'
            assert gen.context_name == 'NewContext'

        assert gen.context_name == 'Initial'


class TestCodeObjectContext:
    """CodeObject.__enter__/__exit__ changes indent, context, and context_name."""

    def test_class_context_changes_indent_and_context(self):
        gen = CodeGen()
        gen.Var('before', 0)
        with gen.Class('Foo'):
            gen.Var('inside', 1)
        gen.Var('after', 2)
        code = gen.generate_str()
        expected = """\
before = 0


class Foo:
    inside = 1


after = 2
"""
        assert code == expected

    def test_class_context_routes_items_to_class_body(self):
        gen = CodeGen()
        with gen.Class('Foo'):
            gen.Var('x', 1)
            with gen.Class('Bar'):
                gen.Var('y', 2)
        # Both x and y end up in their respective class bodies
        code = gen.generate_str()
        expected = """\


class Foo:
    x = 1

    class Bar:
        y = 2
"""
        assert code == expected

    def test_def_context_changes_indent_and_context(self):
        gen = CodeGen()
        gen.Var('before', 0)
        with gen.Def('run'):
            gen.Var('inside', 1)
        gen.Var('after', 2)
        code = gen.generate_str()
        expected = """\
before = 0


def run():
    inside = 1


after = 2
"""
        assert code == expected

    def test_def_context_routes_items_to_function_body(self):
        gen = CodeGen()
        with gen.Def('run'):
            gen.Var('x', 1)
            gen.Var('y', 2)
        code = gen.generate_str()
        expected = """\
def run():
    x = 1
    y = 2
"""
        assert code == expected

    def test_class_context_restores_indent_after_exit(self):
        gen = CodeGen()
        with gen.Class('Foo'):
            gen.Var('x', 1)
        gen.Var('y', 2)
        code = gen.generate_str()
        expected = """\


class Foo:
    x = 1


y = 2
"""
        assert code == expected

    def test_nested_class_def_contexts(self):
        gen = CodeGen()
        with gen.Class('Outer'):
            gen.Var('a', 1)
            with gen.Def('inner'):
                gen.Var('b', 2)
            gen.Var('c', 3)
        gen.Var('d', 4)
        code = gen.generate_str()
        expected = """\


class Outer:
    a = 1

    def inner():
        b = 2

    c = 3


d = 4
"""
        assert code == expected


class TestCodeGenTab:
    """tab() context manager: adds indentation without changing context."""

    def test_tab_default_indents_one_level(self):
        gen = CodeGen()
        gen.Var('x', 1)
        with gen.tab():
            gen.Var('y', 2)
        code = gen.generate_str()
        expected = """\
x = 1
    y = 2
"""
        assert code == expected

    def test_tab_with_arg_indents_n_levels(self):
        gen = CodeGen()
        gen.Var('x', 1)
        with gen.tab(2):
            gen.Var('y', 2)
        code = gen.generate_str()
        expected = """\
x = 1
        y = 2
"""
        assert code == expected

    def test_tab_restores_indent_after_exit(self):
        gen = CodeGen()
        with gen.tab():
            gen.Var('x', 1)
        gen.Var('y', 2)
        code = gen.generate_str()
        expected = """\
    x = 1
y = 2
"""
        assert code == expected

    def test_tab_does_not_change_context(self):
        """Items inside tab() still go to the current context, not a sub-context."""
        gen = CodeGen()
        gen.Var('outer', 0)
        with gen.tab():
            gen.Var('inner', 1)
            with gen.tab():
                gen.Var('deeper', 2)
        gen.Var('back', 3)
        code = gen.generate_str()
        expected = """\
outer = 0
    inner = 1
        deeper = 2
back = 3
"""
        assert code == expected

    def test_tab_zero_does_nothing(self):
        gen = CodeGen()
        with gen.tab(0):
            gen.Var('x', 1)
        code = gen.generate_str()
        expected = "x = 1\n"
        assert code == expected
