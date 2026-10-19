"""
Tests for alasio.ext.file.tomlfile.

Covers the adaptive parser lookup (get_tomllib) and the parsing helper
(toml_loads).
"""
import sys

import pytest

from alasio.ext.file.tomlfile import get_tomllib, toml_loads


class TestGetTomllib:
    """Test cases for the get_tomllib function"""

    def test_prefers_stdlib_on_311(self):
        """tomllib is the standard library module of Python 3.11+"""
        if sys.version_info < (3, 11):
            pytest.skip('tomllib is the standard library module of Python 3.11+')
        import tomllib
        assert get_tomllib() is tomllib

    def test_fallback_to_tomli(self, monkeypatch):
        """tomli is the backport used when tomllib is not available"""
        tomli = pytest.importorskip('tomli')
        monkeypatch.setitem(sys.modules, 'tomllib', None)
        assert get_tomllib() is tomli

    def test_neither_installed_raises(self, monkeypatch):
        monkeypatch.setitem(sys.modules, 'tomllib', None)
        monkeypatch.setitem(sys.modules, 'tomli', None)
        with pytest.raises(ImportError, match='Dependency `tomli` is required'):
            get_tomllib()

    def test_returns_parser_with_loads(self):
        assert callable(get_tomllib().loads)


class TestTomlLoads:
    """Test cases for the toml_loads function"""

    @pytest.mark.parametrize("data", [
        '[project]\nname = "alasio"\n',
        b'[project]\nname = "alasio"\n',
        bytearray(b'[project]\nname = "alasio"\n'),
    ])
    def test_input_types(self, data):
        """A document is parsed from str, bytes and bytearray"""
        assert toml_loads(data) == {'project': {'name': 'alasio'}}

    def test_nested(self):
        assert toml_loads("""\
[project]
name = "alasio"
dependencies = ["msgspec", "starlette"]

[project.optional-dependencies]
image = ["numpy", "opencv-python-headless"]

[[tool.poetry.source]]
name = "pypi"
priority = "primary"
""") == {
            'project': {
                'name': 'alasio',
                'dependencies': ['msgspec', 'starlette'],
                'optional-dependencies': {
                    'image': ['numpy', 'opencv-python-headless'],
                },
            },
            'tool': {
                'poetry': {
                    'source': [{'name': 'pypi', 'priority': 'primary'}],
                },
            },
        }

    def test_unicode_bytes(self):
        """Bytes are decoded as UTF-8, as the TOML specification requires"""
        assert toml_loads('[project]\ndescription = "测试"\n'.encode('utf-8')) == {
            'project': {'description': '测试'},
        }

    def test_empty_document(self):
        assert toml_loads('') == {}

    @pytest.mark.parametrize("data", [
        '[project',
        b'[project',
        'name = "alasio"\nname = "other"\n',
    ])
    def test_invalid_document_raises_value_error(self, data):
        """TOMLDecodeError is a ValueError, callers decide how to handle it"""
        with pytest.raises(ValueError):
            toml_loads(data)

    def test_invalid_utf8_raises(self):
        with pytest.raises(UnicodeDecodeError):
            toml_loads(b'[project]\nname = "\xff"\n')
