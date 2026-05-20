from alasio.codegen.python.gen import CodeGenerator


class TestObjImport:
    def test_simple_import(self):
        gen = CodeGenerator()
        gen.Import('typing')
        gen.Import('os').as_('std_os')

        code = gen.write()
        expected = """\
import typing
import os as std_os"""
        assert code == expected

    def test_from_import(self):
        gen = CodeGenerator()
        gen.FromImport('pydantic').Import('BaseModel')

        code = gen.write()
        assert code == "from pydantic import BaseModel"

    def test_multi_from_import_sorted(self):
        gen = CodeGenerator()
        with gen.FromImport('typing'):
            gen.Import('List')
            gen.Import('Dict').as_('TDict')
            gen.Import('Any')

        code = gen.write()
        # Should be sorted: Any, Dict as TDict, List
        expected = """\
from typing import (
    Any,
    Dict as TDict,
    List,
)"""
        assert code == expected

    def test_raw_content(self):
        gen = CodeGenerator()
        with gen.Class('MyClass'):
            gen.Raw("""
            def __init__(self):
                self.x = 1
            """)

        code = gen.write()
        expected = """\
class MyClass:
    def __init__(self):
        self.x = 1"""
        assert code == expected

    def test_lazy_import(self):
        gen = CodeGenerator()
        imp_typing = gen.Import('typing').lazy()
        gen.Import('os').lazy()

        # None should be generated yet
        assert gen.write() == ""

        # Use typing
        imp_typing.use()
        assert gen.write() == "import typing"

        # Use os via gen name
        gen.use_import('os')
        code = gen.write()
        expected = """\
import typing
import os"""
        assert code == expected

    def test_lazy_from_import(self):
        gen = CodeGenerator()
        with gen.FromImport('typing') as f:
            f.Import('List').lazy()
            f.Import('Dict')

        # Only Dict should be generated
        assert gen.write() == "from typing import Dict"

        # Use List
        gen.use_import('List')
        expected = """\
from typing import (
    Dict,
    List,
)"""
        assert gen.write() == expected
