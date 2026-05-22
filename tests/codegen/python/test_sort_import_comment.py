"""Tests for sort_import mixed with comments."""
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
