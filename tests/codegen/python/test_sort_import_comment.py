from alasio.codegen.python.gen import CodeGen


class TestSortImportCommentBefore:
    """Comment before the first import should be preserved before imports."""

    def test_comment_before_imports_preserved(self):
        """Comment before the first import is kept before imports."""
        gen = CodeGen()
        gen.Comment('stdlib imports')
        gen.Import('os')
        gen.Import('json')
        gen.Var('x', 1)
        gen.sort_import()
        code = gen.generate_str()
        expected = """\
# stdlib imports
import json
import os

x = 1
"""
        assert code == expected


class TestSortImportCommentBetween:
    """Comment between imports should be moved after the import block."""

    def test_comment_between_imports_moved_after(self):
        """Comment between imports is moved after the import block (2 blank lines before it)."""
        gen = CodeGen()
        gen.Import('os')
        gen.Comment('third-party imports')
        gen.Import('pytest')
        gen.Var('x', 1)
        gen.sort_import()
        code = gen.generate_str()
        expected = """\
import os

import pytest

# third-party imports
x = 1
"""
        assert code == expected


class TestSortImportCommentAfter:
    """Comment after the last import (before code) should be moved after the import block."""

    def test_comment_after_imports_moved_after(self):
        """Comment after the last import (before code) is moved after the import block (2 blank lines before it)."""
        gen = CodeGen()
        gen.Import('os')
        gen.Import('json')
        gen.Comment('end of imports')
        gen.Var('x', 1)
        gen.sort_import()
        code = gen.generate_str()
        expected = """\
import json
import os

# end of imports
x = 1
"""
        assert code == expected


class TestSortImportCommentEdgeCases:
    """Edge cases for sort_import with comments."""

    def test_only_comment_and_empty_in_header_then_code(self):
        gen = CodeGen()
        gen.Comment('some comment')
        gen.Empty(1)
        gen.Var('x', 1)
        gen.sort_import()
        code = gen.generate_str()
        expected = """\
# some comment

x = 1
"""
        assert code == expected


class TestSortImportCommentIdempotent:
    """Second sort must be a no-op when comments are involved."""

    def test_sort_twice_noop_with_pre_import_comment(self):
        """Second sort leaves pre-import comments untouched."""
        gen = CodeGen()
        gen.Comment('stdlib')
        gen.Import('os')
        gen.Import('json')
        gen.Var('x', 1)
        gen.sort_import()
        code1 = gen.generate_str()
        gen.sort_import()
        code2 = gen.generate_str()
        assert code1 == code2

    def test_sort_twice_noop_with_comment_between_imports(self):
        """Second sort leaves comment-between-imports stable (already moved after)."""
        gen = CodeGen()
        gen.Import('os')
        gen.Comment('third-party imports')
        gen.Import('pytest')
        gen.Var('x', 1)
        gen.sort_import()
        code1 = gen.generate_str()
        gen.sort_import()
        code2 = gen.generate_str()
        assert code1 == code2

    def test_sort_twice_noop_with_post_import_comment(self):
        """Second sort leaves post-import comments in place (already after imports)."""
        gen = CodeGen()
        gen.Import('os')
        gen.Import('pytest')
        gen.Comment('third-party')
        gen.Var('x', 1)
        gen.sort_import()
        code1 = gen.generate_str()
        gen.sort_import()
        code2 = gen.generate_str()
        assert code1 == code2

    def test_sort_twice_noop_with_both_comments(self):
        """Second sort is no-op with a mix of pre- and post-import comments."""
        gen = CodeGen()
        gen.Comment('top')
        gen.Import('pytest')
        gen.Import('os')
        gen.Comment('inline')
        gen.Import('alasio')
        gen.Import('json')
        gen.Var('x', 1)
        gen.sort_import()
        code1 = gen.generate_str()
        gen.sort_import()
        code2 = gen.generate_str()
        assert code1 == code2


class TestSortImportCommentThenCode:
    """Scenario: items are Import -> Comment, insert 1 empty line between them."""

    def test_import_then_comment_one_empty(self):
        """Single import followed by comment gets 1 blank line between them."""
        gen = CodeGen()
        gen.Import('os')
        # 1 empty line
        gen.Comment('done with imports')
        # 0~1 empty lines are allow here in PEP8
        gen.Var('x', 1)
        gen.sort_import()
        code = gen.generate_str()
        expected = """\
import os

# done with imports
x = 1
"""
        assert code == expected

    def test_multiple_imports_then_comment_one_empty(self):
        """Multiple imports followed by comment — 1 blank after sorted imports, before comment."""
        gen = CodeGen()
        gen.Import('os')
        gen.Import('json')
        # 1 empty line
        gen.Comment('done with imports')
        # 0~1 empty lines are allow here in PEP8
        gen.Var('x', 1)
        gen.sort_import()
        code = gen.generate_str()
        expected = """\
import json
import os

# done with imports
x = 1
"""
        assert code == expected

    def test_import_then_comment_idempotent(self):
        """Second sort is no-op for Import -> Comment -> Code."""
        gen = CodeGen()
        gen.Import('os')
        gen.Comment('done')
        gen.Var('x', 1)
        gen.sort_import()
        code1 = gen.generate_str()
        gen.sort_import()
        code2 = gen.generate_str()
        assert code1 == code2


class TestSortImportThenClassDef:
    """Scenario: items are Import -> Class/Def, insert 2 empty lines between them."""

    def test_import_then_class_two_empties(self):
        """Single import followed by top-level Class gets 2 blank lines between them."""
        gen = CodeGen()
        gen.Import('os')
        # 2 empty lines
        with gen.Class('Foo'):
            gen.Var('x', 1)
        gen.sort_import()
        code = gen.generate_str()
        expected = """\
import os


class Foo:
    x = 1
"""
        assert code == expected

    def test_import_then_def_two_empties(self):
        """Single import followed by top-level Def gets 2 blank lines between them."""
        gen = CodeGen()
        gen.Import('os')
        # 2 empty lines
        with gen.Def('foo'):
            gen.Var('x', 1)
        gen.sort_import()
        code = gen.generate_str()
        expected = """\
import os


def foo():
    x = 1
"""
        assert code == expected

    def test_multiple_imports_then_class_two_empties(self):
        """Multiple imports followed by Class — 2 blank lines after sorted imports, before class."""
        gen = CodeGen()
        gen.Import('os')
        gen.Import('json')
        # 2 empty lines
        with gen.Class('Foo'):
            gen.Var('x', 1)
        gen.sort_import()
        code = gen.generate_str()
        expected = """\
import json
import os


class Foo:
    x = 1
"""
        assert code == expected

    def test_import_then_class_idempotent(self):
        """Second sort is no-op for Import -> Class."""
        gen = CodeGen()
        gen.Import('os')
        with gen.Class('Foo'):
            gen.Var('x', 1)
        gen.sort_import()
        code1 = gen.generate_str()
        gen.sort_import()
        code2 = gen.generate_str()
        assert code1 == code2

    def test_import_then_def_idempotent(self):
        """Second sort is no-op for Import -> Def."""
        gen = CodeGen()
        gen.Import('os')
        with gen.Def('foo'):
            gen.Var('x', 1)
        gen.sort_import()
        code1 = gen.generate_str()
        gen.sort_import()
        code2 = gen.generate_str()
        assert code1 == code2


class TestSortImportCommentEmptyThenOthers:
    """Scenario: Import -> Comment -> Empty -> Class/Def.
    Insert 1 empty between Import and Comment, keeping the empty after comment."""

    def test_import_comment_empty_then_class(self):
        """Import -> Comment -> Empty -> Class: 1 blank between Import and Comment, empty after comment preserved."""
        gen = CodeGen()
        gen.Import('os')
        # 2 empty lines, because it's before class
        gen.Comment('some comment')
        # 0~2 empty lines are allow here in PEP8
        gen.Empty()
        with gen.Class('Foo'):
            gen.Var('x', 1)
        gen.sort_import()
        code = gen.generate_str()
        expected = """\
import os


# some comment

class Foo:
    x = 1
"""
        assert code == expected

    def test_import_comment_empty_then_def(self):
        """Import -> Comment -> Empty -> Def: same spacing pattern."""
        gen = CodeGen()
        gen.Import('os')
        # 2 empty lines, because it's before class
        gen.Comment('some comment')
        # 0~2 empty lines are allow here in PEP8
        gen.Empty()
        with gen.Def('foo'):
            gen.Var('x', 1)
        gen.sort_import()
        code = gen.generate_str()
        expected = """\
import os


# some comment

def foo():
    x = 1
"""
        assert code == expected

    def test_import_comment_empty_then_class_idempotent(self):
        """Second sort is no-op for Import -> Comment -> Empty -> Class."""
        gen = CodeGen()
        gen.Import('os')
        gen.Comment('some comment')
        gen.Empty(1)
        with gen.Class('Foo'):
            gen.Var('x', 1)
        gen.sort_import()
        code1 = gen.generate_str()
        gen.sort_import()
        code2 = gen.generate_str()
        assert code1 == code2
