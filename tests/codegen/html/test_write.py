"""
Tests for the write helpers of the generator.
"""

import alasio.codegen.html.write as write_module
from alasio.codegen.html import is_current, write_if_changed
from alasio.testing.filesystem import fs  # noqa: F401


class TestIsCurrent:
    def test_missing(self, fs):
        assert is_current('/a.txt', 'x') is False

    def test_same(self, fs):
        fs.create_file('/a.txt', contents='x')
        assert is_current('/a.txt', 'x') is True

    def test_different(self, fs):
        fs.create_file('/a.txt', contents='y')
        assert is_current('/a.txt', 'x') is False

    def test_empty(self, fs):
        fs.create_file('/a.txt', contents='')
        assert is_current('/a.txt', '') is True

    def test_crlf(self, fs):
        """A file of CRLF line endings holds the same text as LF."""
        fs.create_file('/a.txt', contents='a\r\nb')
        assert is_current('/a.txt', 'a\nb') is True

    def test_crlf_of_different_text(self, fs):
        fs.create_file('/a.txt', contents='a\r\nc')
        assert is_current('/a.txt', 'a\nb') is False

    def test_not_utf8(self, fs):
        """A file of another encoding is not a page of this module."""
        fs.create_file('/a.txt', contents=b'\xff\xfe\x00')
        assert is_current('/a.txt', 'x') is False


class TestWriteIfChanged:
    def test_new(self, fs):
        assert write_if_changed('/a.txt', 'x') is True
        with open('/a.txt', encoding='utf-8') as f:
            assert f.read() == 'x'

    def test_same(self, fs, monkeypatch):
        fs.create_file('/a.txt', contents='x')

        def boom(*args, **kwargs):
            raise AssertionError('the file holds the content, the write must not run')

        monkeypatch.setattr(write_module, 'atomic_write', boom)
        assert write_if_changed('/a.txt', 'x') is False

    def test_changed(self, fs):
        fs.create_file('/a.txt', contents='y')
        assert write_if_changed('/a.txt', 'x') is True
        with open('/a.txt', encoding='utf-8') as f:
            assert f.read() == 'x'

    def test_not_utf8_replaced(self, fs):
        fs.create_file('/a.txt', contents=b'\xff\xfe\x00')
        assert write_if_changed('/a.txt', 'x') is True
        with open('/a.txt', encoding='utf-8') as f:
            assert f.read() == 'x'

    def test_parent_created(self, fs):
        assert write_if_changed('/deep/inside/a.txt', 'x') is True
        with open('/deep/inside/a.txt', encoding='utf-8') as f:
            assert f.read() == 'x'

    def test_empty_content(self, fs):
        assert write_if_changed('/a.txt', '') is True
        with open('/a.txt', encoding='utf-8') as f:
            assert f.read() == ''

    def test_unicode(self, fs):
        assert write_if_changed('/a.txt', '你好\n') is True
        with open('/a.txt', encoding='utf-8') as f:
            assert f.read() == '你好\n'
