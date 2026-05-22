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
