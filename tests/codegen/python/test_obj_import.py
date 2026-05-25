from alasio.codegen.python.gen import CodeGen


class TestObjImport:
    def test_simple_import(self):
        gen = CodeGen()
        gen.Import('typing')
        gen.Import('os').as_('std_os')

        code = gen.generate_str()
        expected = """\
import os as std_os
import typing
"""
        assert code == expected

    def test_from_import(self):
        gen = CodeGen()
        gen.FromImport('pydantic').Import('BaseModel')

        code = gen.generate_str()
        assert code == "from pydantic import BaseModel\n"

    def test_multi_from_import_sorted(self):
        gen = CodeGen()
        with gen.FromImport('typing'):
            gen.Import('List')
            gen.Import('Dict').as_('TDict')
            gen.Import('Any')

        code = gen.generate_str()
        # Should be sorted: Any, Dict as TDict, List
        expected = """\
from typing import (
    Any,
    Dict as TDict,
    List,
)
"""
        assert code == expected

    def test_raw_content(self):
        gen = CodeGen()
        with gen.Class('MyClass'):
            gen.Raw("""
            def __init__(self):
                self.x = 1
            """)

        code = gen.generate_str()
        expected = """\
class MyClass:
    def __init__(self):
        self.x = 1
"""
        assert code == expected

    def test_lazy_import(self):
        gen = CodeGen()
        imp_typing = gen.Import('typing').lazy()
        gen.Import('os').lazy()

        # None should be generated yet
        assert gen.generate_str() == ""

        # Use typing
        imp_typing.use()
        assert gen.generate_str() == "import typing\n"

        # Use os via gen name
        gen.use_import('os')
        code = gen.generate_str()
        expected = """\
import os
import typing
"""
        assert code == expected

    def test_lazy_import_with_alias(self):
        gen = CodeGen()
        gen.Import('typing').as_('t').lazy()
        
        # Use alias
        gen.use_import('t')
        assert gen.generate_str() == "import typing as t\n"

    def test_lazy_from_import(self):
        gen = CodeGen()
        with gen.FromImport('typing') as f:
            f.Import('List').lazy()
            f.Import('Dict')

        # Only Dict should be generated
        assert gen.generate_str() == "from typing import Dict\n"

        # Use List
        gen.use_import('List')
        expected = """\
from typing import (
    Dict,
    List,
)
"""
        assert gen.generate_str() == expected


class TestFromImportWrap:
    """Tests for FromImport.wrap() behavior."""

    def test_from_import_wrap_inline(self):
        """FromImport.wrap('inline') renders multiple items inline."""
        gen = CodeGen()
        with gen.FromImport('typing').wrap('inline'):
            gen.Import('List')
            gen.Import('Dict')
        code = gen.generate_str()
        expected = """\
from typing import Dict, List
"""
        assert code == expected

    def test_from_import_wrap_auto_short(self):
        """FromImport.wrap() with short items stays inline (auto)."""
        gen = CodeGen()
        with gen.FromImport('typing').wrap():
            gen.Import('List')
            gen.Import('Dict')
        code = gen.generate_str()
        # "from typing import Dict, List" is short enough (<120) -> inline
        expected = """\
from typing import Dict, List
"""
        assert code == expected

    def test_from_import_wrap_auto_long(self):
        """FromImport.wrap() with long items uses newline (auto)."""
        gen = CodeGen()
        long_name1 = "x" * 55
        long_name2 = "y" * 55
        with gen.FromImport('typing').wrap():
            gen.Import(long_name1)
            gen.Import(long_name2)
        code = gen.generate_str()
        # "from typing import xxx...xxx, yyy...yyy" > 120 chars -> newline
        expected = f"""\
from typing import (
    {long_name1},
    {long_name2},
)
"""
        assert code == expected

    def test_from_import_wrap_default_newline(self):
        """FromImport without .wrap() defaults to newline for multiple items."""
        gen = CodeGen()
        with gen.FromImport('typing'):
            gen.Import('List')
            gen.Import('Dict')
        code = gen.generate_str()
        expected = """\
from typing import (
    Dict,
    List,
)
"""
        assert code == expected

    def test_from_import_wrap_inline_single_item(self):
        """FromImport.wrap('inline') with single item."""
        gen = CodeGen()
        with gen.FromImport('typing').wrap('inline'):
            gen.Import('List')
        code = gen.generate_str()
        assert code == "from typing import List\n"

    def test_from_import_wrap_auto_single_item(self):
        """FromImport.wrap() with single item stays inline."""
        gen = CodeGen()
        with gen.FromImport('typing').wrap():
            gen.Import('List')
        code = gen.generate_str()
        assert code == "from typing import List\n"