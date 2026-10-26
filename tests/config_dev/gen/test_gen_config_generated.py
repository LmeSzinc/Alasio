"""
Tests of the generated group_export module.

group_export.py is a pure re-export module: every import is part of the public
API of ``alasio.config.alasio.group_export``. The generator must emit a
file-level ruff directive, otherwise formatting the generated file deletes the
imports as unused (F401).
"""

from alasio.codegen.python import CodeGen
from alasio.config_dev.gen_index import IndexGenerator
from alasio.ext import env


class TestGroupExport:
    def test_file_level_ruff_noqa(self, monkeypatch):
        """The generated file starts with the directive before its imports."""
        captured = {}

        def fake_write(gen, file='', gitadd=None, skip_same=True):
            captured['code'] = gen.generate_str()
            return True

        monkeypatch.setattr(CodeGen, 'write', fake_write)
        generator = object.__new__(IndexGenerator)
        generator.path_config = env.ALASIO_ROOT.joinpath('alasio/config')
        generator.dict_nav_config = {}
        generator.generate_group_export()

        code = captured['code']
        assert code.startswith('# ruff: noqa: F401\n')
        # The directive protects the imports, it does not replace them
        assert 'from alasio.config.alasio.store_model import (' in code
        assert 'DashboardPlanner' in code
