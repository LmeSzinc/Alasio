from alasio.codegen.python.gen import CodeGen


class TestSortImportBasic:
    """Test basic PEP8 sort_import behavior."""

    def test_empty_no_imports(self):
        gen = CodeGen()
        gen.Var('x', 1)
        gen.sort_import()
        code = gen.generate_str()
        assert code == "x = 1\n"

    def test_only_stdlib(self):
        gen = CodeGen()
        gen.Import('os')
        gen.Import('json')
        gen.sort_import()
        code = gen.generate_str()
        expected = """\
import json
import os
"""
        assert code == expected

    def test_basic_pep8_order(self):
        gen = CodeGen()
        gen.Import('pytest')     # third-party
        gen.Import('os')         # stdlib
        gen.Import('alasio')     # local project
        gen.Import('json')       # stdlib
        gen.sort_import()
        code = gen.generate_str()
        expected = """\
import json
import os

import pytest

import alasio
"""
        assert code == expected

    def test_sort_preserves_code_after_imports(self):
        gen = CodeGen()
        gen.Import('os')
        gen.Import('alasio')
        gen.Var('x', 42)
        gen.sort_import()
        code = gen.generate_str()
        # Auto blank lines: 1 blank line between import block and top-level var
        expected = """\
import os

import alasio

x = 42
"""
        assert code == expected

    def test_sort_with_class_after(self):
        gen = CodeGen()
        gen.Import('os')
        gen.Import('alasio')
        gen.Class('MyClass')
        gen.sort_import()
        code = gen.generate_str()
        # Auto blank lines: 2 blank lines before top-level class
        expected = """\
import os

import alasio


class MyClass:
    pass
"""
        assert code == expected

    def test_no_blank_lines_with_only_stdlib(self):
        gen = CodeGen()
        gen.Import('json')
        gen.Import('os')
        gen.Import('sys')
        gen.sort_import()
        code = gen.generate_str()
        expected = """\
import json
import os
import sys
"""
        assert code == expected

    def test_no_blank_lines_with_only_third_party(self):
        gen = CodeGen()
        gen.Import('pytest')
        gen.FromImport('pytest').Import('mark')
        gen.sort_import()
        code = gen.generate_str()
        expected = """\
import pytest
from pytest import mark
"""
        assert code == expected


class TestSortImportDuplicates:
    """Duplicate imports should be preserved, not merged."""

    def test_duplicate_imports_deduped_by_registry(self):
        # gen.Import() deduplicates via _import_registry, returning the
        # same object. sort_import itself does not merge.
        gen = CodeGen()
        gen.Import('os')
        gen.Import('os')  # returns same object (registry dedup)
        gen.Import('json')
        gen.sort_import()
        code = gen.generate_str()
        expected = """\
import json
import os
"""
        assert code == expected

    def test_duplicate_from_import_preserved(self):
        gen = CodeGen()
        gen.FromImport('typing').Import('List')
        gen.FromImport('typing').Import('List')  # duplicate
        gen.sort_import()
        code = gen.generate_str()
        expected = """\
from typing import List
from typing import List
"""
        assert code == expected


class TestSortImportMixed:
    """Mixed Import and FromImport sorting."""

    def test_import_and_fromimport_sorted_together(self):
        gen = CodeGen()
        gen.FromImport('os').Import('path')       # stdlib
        gen.Import('json')                         # stdlib
        gen.Import('os')                           # stdlib
        gen.sort_import()
        code = gen.generate_str()
        # Alphabetical by module: json < os
        expected = """\
import json
from os import path
import os
"""
        assert code == expected

    def test_mixed_all_groups(self):
        gen = CodeGen()
        gen.Import('pytest')                       # third-party
        gen.FromImport('typing').Import('List')    # stdlib
        gen.Import('json')                         # stdlib
        gen.Import('alasio')                       # local
        gen.sort_import()
        code = gen.generate_str()
        expected = """\
import json
from typing import List

import pytest

import alasio
"""
        assert code == expected


class TestSortImportHeaderBoundary:
    """Test that header detection stops at first code object."""

    def test_comment_before_imports_lost(self):
        gen = CodeGen()
        gen.Comment('stdlib imports')
        gen.Import('os')
        gen.Import('json')
        gen.Var('x', 1)
        gen.sort_import()
        code = gen.generate_str()
        expected = """\
import json
import os

x = 1
"""
        assert code == expected

    def test_var_ends_header(self):
        gen = CodeGen()
        gen.Import('os')
        gen.Var('x', 1)          # code starts here
        gen.Import('json')       # this is after code, NOT in header
        gen.sort_import()
        code = gen.generate_str()
        expected = """\
import os

x = 1
import json
"""
        assert code == expected

    def test_class_ends_header(self):
        gen = CodeGen()
        gen.Import('os')
        gen.Class('Foo')
        gen.Import('alasio')
        gen.sort_import()
        code = gen.generate_str()
        expected = """\
import os


class Foo:
    pass


import alasio
"""
        assert code == expected

    def test_empty_between_imports_in_header(self):
        gen = CodeGen()
        gen.Import('os')
        gen.Empty(1)
        gen.Import('json')
        gen.sort_import()
        code = gen.generate_str()
        expected = """\
import json
import os
"""
        assert code == expected


class TestSortImportLazyImports:
    """Lazy imports should be preserved through sorting."""

    def test_lazy_imports_in_sorted_output(self):
        gen = CodeGen()
        gen.Import('typing').as_('t').lazy()
        gen.Import('os')
        gen.Import('json')
        gen.sort_import()
        code = gen.generate_str()
        expected = """\
import json
import os
"""
        assert code == expected

    def test_used_lazy_import_in_sorted_output(self):
        gen = CodeGen()
        imp_t = gen.Import('typing').as_('t').lazy()
        imp_t.use()
        gen.Import('os')
        gen.Import('json')
        gen.sort_import()
        code = gen.generate_str()
        expected = """\
import json
import os
import typing as t
"""
        assert code == expected

    def test_from_import_multi_line_after_sort(self):
        gen = CodeGen()
        with gen.FromImport('typing'):
            gen.Import('Dict')
            gen.Import('List')
            gen.Import('Any')
        gen.sort_import()
        code = gen.generate_str()
        expected = """\
from typing import (
    Any,
    Dict,
    List,
)
"""
        assert code == expected


class TestSortImportEdgeCases:
    """Edge cases for sort_import."""

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

    def test_sort_is_idempotent(self):
        gen = CodeGen()
        gen.Import('pytest')
        gen.Import('os')
        gen.Import('alasio')
        gen.Import('json')
        gen.sort_import()
        code1 = gen.generate_str()
        gen.sort_import()
        code2 = gen.generate_str()
        assert code1 == code2

    def test_sort_returns_self(self):
        gen = CodeGen()
        gen.Import('os')
        result = gen.sort_import()
        assert result is gen
