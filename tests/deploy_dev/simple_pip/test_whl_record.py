import pytest

from alasio.deploy_dev.simple_pip import RecordManager, sha256_checksum
from alasio.deploy_dev.simple_pip.whl_record import RecordEntry
from alasio.testing.filesystem import fs  # noqa: F401


class TestSha256Checksum:
    @pytest.mark.parametrize("content, expected", [
        (b'', '47DEQpj8HBSa-_TImW-5JCeuQeRkm5NMpJWZG3hSuFU'),
        (b'abc', 'ungWv48Bz-pBQUDeXa4iI7ADYaOWF3qctBD_YfIAFa0'),
        # The checksum pip records for the INSTALLER file it writes
        (b'pip\n', 'zuuue4knoyJ-UwPPXg8fezS7VCrXJQrAP7zeNuwvFQg'),
        ('中文'.encode('utf-8'), 'cnJtiBj2kwZs62mvo2Qhi2kuYuqSs4V4I2N4D0dSnCE'),
    ])
    def test_sha256_checksum(self, content, expected):
        """The checksum is the urlsafe base64 of the sha256 digest, without padding."""
        result = sha256_checksum(content)
        assert result == expected
        # Url safe without padding, so the checksum is usable in a RECORD
        assert '=' not in result
        assert '+' not in result
        assert '/' not in result


class TestRecordEntry:
    def test_default_fields(self):
        """The checksum and the size of an entry are empty by default, no hash recorded."""
        entry = RecordEntry('demo/__init__.py')
        assert entry.path == 'demo/__init__.py'
        assert entry.sha256 == ''
        assert entry.size == ''
        assert repr(entry) == '<RecordEntry demo/__init__.py,,>'


class TestLoadBytes:
    @pytest.mark.parametrize("content, expected", [
        # A RECORD written by pip, three columns, the RECORD itself has no hash
        (b'demo/__init__.py,sha256=abc,6\n'
         b'demo-1.0.dist-info/RECORD,,\n',
         [('demo/__init__.py', 'sha256=abc', '6'),
          ('demo-1.0.dist-info/RECORD', '', '')]),
        # Windows line endings
        (b'demo/__init__.py,sha256=abc,6\r\n'
         b'demo/core.py,sha256=def,7\r\n',
         [('demo/__init__.py', 'sha256=abc', '6'),
          ('demo/core.py', 'sha256=def', '7')]),
        # A row with the path only, written by some tools
        (b'demo-1.0.dist-info/RECORD\n',
         [('demo-1.0.dist-info/RECORD', '', '')]),
        # Extra columns are ignored, pip warns about them and keeps the row
        (b'demo/__init__.py,sha256=abc,6,extra\n',
         [('demo/__init__.py', 'sha256=abc', '6')]),
        # The backslashes of a RECORD written on Windows are normalized
        (b'win32\\lib\\afxres.py,sha256=abc,1\n',
         [('win32/lib/afxres.py', 'sha256=abc', '1')]),
        # A quoted path holding a comma
        (b'"demo/a,b.py",sha256=abc,1\n',
         [('demo/a,b.py', 'sha256=abc', '1')]),
        # Empty content and blank lines
        (b'', []),
        (b'\n\n', []),
        # Duplicated paths, the last row wins
        (b'a.py,sha256=x,1\na.py,sha256=y,2\n',
         [('a.py', 'sha256=y', '2')]),
    ])
    def test_load(self, content, expected):
        """The RECORD of any tool is read back, path, checksum and size are kept."""
        record = RecordManager()
        record.load_bytes(content)
        assert [(entry.path, entry.sha256, entry.size) for entry in record.entries.values()] == expected
        # The key of an entry is the path of the entry
        assert list(record.entries) == [row[0] for row in expected]

    def test_load_non_utf8(self):
        """PEP 376 requires the RECORD to be UTF-8, anything else is rejected."""
        record = RecordManager()
        with pytest.raises(UnicodeDecodeError):
            record.load_bytes(b'demo/\xff.py,sha256=abc,1\n')

    def test_load_replaces_entries(self):
        """Loading a RECORD replaces the entries of the manager."""
        record = RecordManager()
        record.add_content('demo/old.py', b'a = 1\n')
        record.load_bytes(b'demo/new.py,sha256=abc,1\n')
        assert list(record.entries) == ['demo/new.py']


class TestDumpBytes:
    def test_dump_exact_content(self):
        """The RECORD is a csv file with "\n" line endings, a row per file."""
        record = RecordManager()
        record.add_content('demo/__init__.py', b'a = 1\n')
        record.add_content('demo-1.0.dist-info/RECORD', None)
        assert record.dump_bytes() == b"""\
demo/__init__.py,sha256=y3i9ihf3t1H-DUZjNm3LwlcgQDPvfd1ksfKWlXO1suI,6
demo-1.0.dist-info/RECORD,,
"""

    def test_dump_empty(self):
        """A RECORD without an entry is an empty file."""
        assert RecordManager().dump_bytes() == b''

    def test_dump_order(self):
        """The rows are sorted by the components of the path, a directory before the files named after it.

        A plain sort of the raw strings puts the deeper path in the middle,
        "/" is 0x2f, before the digits and the letters but after "-" and ".".
        """
        record = RecordManager()
        for path in ['demo/aaaname.py', 'demo/aaa-z.py', 'demo/aaa/name.py', 'demo/bbbname.py']:
            record.add_content(path, b'x')
        assert record.dump_bytes() == b"""\
demo/aaa/name.py,sha256=LXEWQrcmsEQBYnyp-6wy9chTD7GQPMTbAiWHF5IaSIE,1
demo/aaa-z.py,sha256=LXEWQrcmsEQBYnyp-6wy9chTD7GQPMTbAiWHF5IaSIE,1
demo/aaaname.py,sha256=LXEWQrcmsEQBYnyp-6wy9chTD7GQPMTbAiWHF5IaSIE,1
demo/bbbname.py,sha256=LXEWQrcmsEQBYnyp-6wy9chTD7GQPMTbAiWHF5IaSIE,1
"""

    def test_dump_order_tree(self):
        """The files of a directory stay together, the files outside of site-packages first."""
        record = RecordManager()
        for path in [
            'alasio/testing/base.py',
            'alasio/testing/__pycache__/base.cpython-38.pyc',
            'alasio/testing/filesystem/base.py',
            'alasio-0.1.0.dist-info/RECORD',
            '../../Scripts/flask.exe',
            'alasio.py',
        ]:
            record.add_content(path, b'x')
        assert record.dump_bytes() == b"""\
../../Scripts/flask.exe,sha256=LXEWQrcmsEQBYnyp-6wy9chTD7GQPMTbAiWHF5IaSIE,1
alasio/testing/__pycache__/base.cpython-38.pyc,sha256=LXEWQrcmsEQBYnyp-6wy9chTD7GQPMTbAiWHF5IaSIE,1
alasio/testing/base.py,sha256=LXEWQrcmsEQBYnyp-6wy9chTD7GQPMTbAiWHF5IaSIE,1
alasio/testing/filesystem/base.py,sha256=LXEWQrcmsEQBYnyp-6wy9chTD7GQPMTbAiWHF5IaSIE,1
alasio-0.1.0.dist-info/RECORD,sha256=LXEWQrcmsEQBYnyp-6wy9chTD7GQPMTbAiWHF5IaSIE,1
alasio.py,sha256=LXEWQrcmsEQBYnyp-6wy9chTD7GQPMTbAiWHF5IaSIE,1
"""

    def test_dump_reproducible(self):
        """The bytes of a RECORD do not depend on the order the entries were added."""
        first = RecordManager()
        first.add_content('demo/b.py', b'x')
        first.add_content('demo/a.py', b'x')
        second = RecordManager()
        second.add_content('demo/a.py', b'x')
        second.add_content('demo/b.py', b'x')
        assert first.dump_bytes() == second.dump_bytes()
        assert first.dump_bytes() == b"""\
demo/a.py,sha256=LXEWQrcmsEQBYnyp-6wy9chTD7GQPMTbAiWHF5IaSIE,1
demo/b.py,sha256=LXEWQrcmsEQBYnyp-6wy9chTD7GQPMTbAiWHF5IaSIE,1
"""

    def test_dump_quotes_a_path_holding_a_comma(self):
        """A path holding a comma is quoted, the row can be read back."""
        record = RecordManager()
        record.add_content('demo/a,b.py', b'x')
        dumped = record.dump_bytes()
        assert dumped == b'"demo/a,b.py",sha256=LXEWQrcmsEQBYnyp-6wy9chTD7GQPMTbAiWHF5IaSIE,1\n'
        loaded = RecordManager()
        loaded.load_bytes(dumped)
        assert list(loaded.entries) == ['demo/a,b.py']

    def test_dump_load_round_trip(self):
        """Dumping and loading a RECORD keeps the entries."""
        record = RecordManager()
        record.add_content('demo/__init__.py', b'a = 1\n')
        record.add_content('../../Scripts/demo-tool', b'x')
        record.add_content('demo-1.0.dist-info/RECORD', None)
        loaded = RecordManager()
        loaded.load_bytes(record.dump_bytes())
        assert list(loaded.entries) == list(record.entries)
        assert [(entry.path, entry.sha256, entry.size) for entry in loaded.entries.values()] == [
            (entry.path, entry.sha256, entry.size) for entry in record.entries.values()
        ]


class TestAddContent:
    def test_add_content(self):
        """The checksum and the size are computed from the content."""
        record = RecordManager()
        record.add_content('demo/__init__.py', b'a = 1\n')
        entry = record.entries['demo/__init__.py']
        assert (entry.path, entry.sha256, entry.size) == (
            'demo/__init__.py', 'sha256=y3i9ihf3t1H-DUZjNm3LwlcgQDPvfd1ksfKWlXO1suI', '6')

    def test_add_content_no_data(self):
        """A file without checksum, e.g. a .pyc generated at install time, keeps empty fields."""
        record = RecordManager()
        record.add_content('demo/__pycache__/core.cpython-38.pyc', None)
        entry = record.entries['demo/__pycache__/core.cpython-38.pyc']
        assert (entry.sha256, entry.size) == ('', '')

    def test_add_content_empty_file(self):
        """An empty file is recorded with the checksum of the empty content."""
        record = RecordManager()
        record.add_content('demo/empty.py', b'')
        entry = record.entries['demo/empty.py']
        assert (entry.sha256, entry.size) == ('sha256=47DEQpj8HBSa-_TImW-5JCeuQeRkm5NMpJWZG3hSuFU', '0')

    def test_add_content_normalizes_path(self):
        """The path is stored posix style, like the rows of a RECORD."""
        record = RecordManager()
        record.add_content('demo\\__init__.py', b'')
        assert list(record.entries) == ['demo/__init__.py']

    def test_add_content_overwrites(self):
        """Adding the same path twice keeps the last content."""
        record = RecordManager()
        record.add_content('demo/a.py', b'a = 1\n')
        record.add_content('demo/a.py', b'a = 2\n')
        assert list(record.entries) == ['demo/a.py']
        entry = record.entries['demo/a.py']
        assert (entry.sha256, entry.size) == ('sha256=' + sha256_checksum(b'a = 2\n'), '6')


class TestAddFile:
    def test_add_file(self, fs):
        """The checksum and the size are computed from the file on disk."""
        fs.create_file('/env/Lib/site-packages/demo/__init__.py', contents=b'a = 1\n')
        record = RecordManager()
        record.add_file('demo/__init__.py', '/env/Lib/site-packages/demo/__init__.py')
        entry = record.entries['demo/__init__.py']
        assert (entry.path, entry.sha256, entry.size) == (
            'demo/__init__.py', 'sha256=y3i9ihf3t1H-DUZjNm3LwlcgQDPvfd1ksfKWlXO1suI', '6')

    def test_add_file_missing(self, fs):
        """A missing file raises, an entry without checksum would hide it."""
        record = RecordManager()
        with pytest.raises(FileNotFoundError):
            record.add_file('demo/__init__.py', '/env/Lib/site-packages/demo/__init__.py')
        assert record.entries == {}


class TestIterPyFiles:
    def test_iter_py_files(self):
        """Only the .py entries are compiled at install time."""
        record = RecordManager()
        record.add_content('demo/__init__.py', b'')
        record.add_content('demo/data.json', b'')
        record.add_content('../../Scripts/demo-tool.py', b'')
        record.add_content('demo-1.0.dist-info/RECORD', None)
        assert [entry.path for entry in record.iter_py_files()] == [
            'demo/__init__.py', '../../Scripts/demo-tool.py']

    def test_iter_py_files_empty(self):
        assert list(RecordManager().iter_py_files()) == []
