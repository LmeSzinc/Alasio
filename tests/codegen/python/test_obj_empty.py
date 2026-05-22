from alasio.codegen.python.gen import CodeGen


class TestObjEmpty:
    def test_auto_blank_lines_top_level(self):
        gen = CodeGen()
        gen.Import('os')
        gen.FromImport('typing').Import('List')

        with gen.Class('MyClass'):
            gen.Var('x', 1)

        with gen.Def('my_func'):
            gen.Pass()

        code = gen.generate_str()
        # 2 lines after imports, 2 lines between class and func
        expected = """\
import os
from typing import List


class MyClass:
    x = 1


def my_func():
    pass
"""
        assert code == expected

    def test_auto_blank_lines_inside_class(self):
        gen = CodeGen()
        with gen.Class('MyClass'):
            gen.Var('x', 1)
            with gen.Def('__init__'):
                gen.Pass()
            with gen.Def('run'):
                gen.Pass()

        code = gen.generate_str()
        # 1 line before methods
        expected = """\
class MyClass:
    x = 1

    def __init__():
        pass

    def run():
        pass
"""
        assert code == expected

    def test_manual_empty_lines(self):
        gen = CodeGen()
        gen.Import('os')
        gen.Empty(1)  # manual 1 line
        with gen.Class('MyClass'):
            gen.Pass()

        code = gen.generate_str()
        # Should have only 1 line, not 2
        expected = """\
import os


class MyClass:
    pass
"""
        assert code == expected

    def test_mixed_content_blank_lines(self):
        gen = CodeGen()
        gen.Import('os')
        gen.Var('GLOBAL_VAR', 100)

        with gen.Def('top_func'):
            gen.Pass()

        code = gen.generate_str()
        # Var after import: 1 line.
        # But Def after Var at top level: 2 lines.
        expected = """\
import os

GLOBAL_VAR = 100


def top_func():
    pass
"""
        assert code == expected


class TestNewlineEnding:
    """newline_ending() ensures exactly one trailing blank line."""

    def test_no_trailing_empty_adds_one(self):
        gen = CodeGen()
        gen.Var('x', 1)
        gen.newline_ending()
        code = gen.generate_str()
        expected = """\
x = 1
"""
        assert code == expected

    def test_already_one_trailing_empty_skips(self):
        gen = CodeGen()
        gen.Var('x', 1)
        gen.Empty(1)
        gen.newline_ending()
        code = gen.generate_str()
        expected = """\
x = 1
"""
        assert code == expected

    def test_multiple_trailing_empties_reduced_to_one(self):
        gen = CodeGen()
        gen.Var('x', 1)
        gen.Empty(3)
        gen.newline_ending()
        code = gen.generate_str()
        expected = """\
x = 1
"""
        assert code == expected

    def test_called_twice_is_idempotent(self):
        gen = CodeGen()
        gen.Var('x', 1)
        gen.newline_ending()
        code1 = gen.generate_str()
        gen.newline_ending()
        code2 = gen.generate_str()
        assert code1 == code2

    def test_works_without_code(self):
        """With no items, newline_ending is a no-op and generates empty string."""
        gen = CodeGen()
        gen.newline_ending()
        code = gen.generate_str()
        assert code == ''

    def test_does_not_affect_leading_content(self):
        gen = CodeGen()
        gen.Var('a', 1)
        gen.Empty(2)
        gen.Var('b', 2)
        gen.newline_ending()
        code = gen.generate_str()
        expected = """\
a = 1


b = 2
"""
        assert code == expected

    def test_trailing_comment_preserved(self):
        """Empty line goes after the trailing comment."""
        gen = CodeGen()
        gen.Var('x', 1)
        gen.Comment('end')
        gen.newline_ending()
        code = gen.generate_str()
        expected = """\
x = 1
# end
"""
        assert code == expected

    def test_recursive_trailing_empty_in_last_object(self):
        """
        Trailing Empty inside the last nested Class/Def is cleaned to avoid
        double trailing newlines (the config_generated.py scenario).
        """
        gen = CodeGen()
        with gen.Class('MyClass').set_inherit('Base'):
            gen.Comment('First group')
            gen.Var('x', 1)
            gen.Empty()  # spacer between groups
            gen.Comment('Second group')
            gen.Var('y', 2)
            gen.Empty()  # trailing Empty item inside class
        code = gen.generate_str()
        # Should have exactly 1 trailing newline, not 2
        assert code == 'class MyClass(Base):\n    # First group\n    x = 1\n\n    # Second group\n    y = 2\n'
        # Verify structure: only the root Empty(1) at the very end
        lines = code.split('\n')
        assert lines[-1] == ''
        assert lines[-2] == '    y = 2'

    def test_only_last_item_trailing_cleaned_not_siblings(self):
        """
        Only the last non-Empty ClosureObject's chain is cleaned recursively.
        Sibling items' trailing empties are preserved.
        """
        gen = CodeGen()
        with gen.Class('A'):
            gen.Var('a', 1)
            gen.Empty()  # trailing Empty in A (non-last, should be kept)
        with gen.Class('B'):
            gen.Var('b', 2)
            gen.Empty()  # trailing Empty in B (last, should be cleaned)
        gen.newline_ending()
        code = gen.generate_str()
        # Class A still has its trailing Empty (one blank line after a = 1)
        # Class B's trailing Empty is cleaned (last item in the chain)
        # Auto blank lines: 2 blanks between top-level classes
        # Total blank lines between a = 1 and class B: 1 (A's trailing) + 2 (auto) = 3
        expected = """\
class A:
    a = 1



class B:
    b = 2
"""
        assert code == expected

    def test_nested_class_trailing_empty_cleaned(self):
        """
        Trailing Empty in a nested Class at the end of a parent Class
        is cleaned (recursive chain).
        """
        gen = CodeGen()
        with gen.Class('Outer'):
            gen.Var('x', 1)
            with gen.Class('Inner'):
                gen.Var('y', 2)
                gen.Empty()  # trailing Empty inside Inner
            gen.Empty()  # trailing Empty inside Outer
        gen.newline_ending()
        code = gen.generate_str()
        expected = """\
class Outer:
    x = 1

    class Inner:
        y = 2
"""
        assert code == expected
