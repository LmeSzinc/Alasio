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
