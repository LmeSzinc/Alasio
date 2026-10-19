import gzip
import io
import os
import tarfile
import zipfile

import pytest

from alasio.deploy_dev.wheel.archive import FILE_MODE, ZIP_MIN_MTIME, GzipFileExtend, ZipFileExtend, zip_date_time
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_bytes
from alasio.testing.filesystem import fs  # noqa: F401

# 2021-01-01 00:00:00 UTC
MTIME = 1609459200
ZIP_FILE = '/dist/demo-1.0-py3-none-any.whl'
TAR_FILE = '/dist/demo-1.0.tar.gz'


def build_zip(mtime=MTIME):
    """
    Build a zip archive from the members under test.

    Args:
        mtime (int): Unix timestamp in UTC+0

    Returns:
        bytes: Content of the archive
    """
    with ZipFileExtend(ZIP_FILE, mtime) as zf:
        zf.write_member('demo/core.py', b'a = 1\n')
        zf.write_member('demo/empty.py', b'')
    return atomic_read_bytes(ZIP_FILE)


def build_tar(mtime=MTIME):
    """
    Build a gzip compressed tar archive from the members under test.

    Args:
        mtime (int): Unix timestamp in UTC+0

    Returns:
        bytes: Content of the archive
    """
    with GzipFileExtend(TAR_FILE, mtime) as gz:
        gz.write_member('demo-1.0/demo/core.py', b'a = 1\n')
        gz.write_member('demo-1.0/demo/empty.py', b'')
    return atomic_read_bytes(TAR_FILE)


class TestZipDateTime:
    @pytest.mark.parametrize('mtime, expected', [
        # the MS-DOS date of the zip format starts at 1980-01-01
        (0, (1980, 1, 1, 0, 0, 0)),
        (ZIP_MIN_MTIME - 1, (1980, 1, 1, 0, 0, 0)),
        (ZIP_MIN_MTIME, (1980, 1, 1, 0, 0, 0)),
        (1609459200, (2021, 1, 1, 0, 0, 0)),
        # the odd second is kept, the zip writer truncates it on write
        (1609459201, (2021, 1, 1, 0, 0, 1)),
        (1609459202, (2021, 1, 1, 0, 0, 2)),
    ])
    def test_zip_date_time(self, mtime, expected):
        """The timestamp is converted in UTC and clamped to the DOS epoch."""
        assert zip_date_time(mtime) == expected


class TestZipFileExtend:
    def test_content(self, fs):
        """Members are readable and carry their content."""
        data = build_zip()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert zf.testzip() is None
            assert zf.namelist() == ['demo/core.py', 'demo/empty.py']
            assert zf.read('demo/core.py') == b'a = 1\n'
            assert zf.read('demo/empty.py') == b''

    def test_member_metadata(self, fs):
        """Every member has mode 0o644, the commit time and deflate."""
        data = build_zip(MTIME + 1)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                # the odd second is truncated to the 2 second resolution
                assert info.date_time == (2021, 1, 1, 0, 0, 0)
                assert info.external_attr == FILE_MODE << 16
                assert info.create_system == 3
                assert info.compress_type == zipfile.ZIP_DEFLATED

    def test_compressed(self, fs):
        """Members are deflated, not stored."""
        content = b'a' * 4096
        with ZipFileExtend(ZIP_FILE, MTIME) as zf:
            zf.write_member('demo/core.py', content)
        with zipfile.ZipFile(io.BytesIO(atomic_read_bytes(ZIP_FILE))) as zf:
            info = zf.getinfo('demo/core.py')
            assert info.file_size == len(content)
            assert info.compress_size < len(content)
            assert zf.read('demo/core.py') == content

    def test_reproducible(self, fs):
        """The same inputs build the same bytes."""
        assert build_zip() == build_zip()

    def test_timestamp_changes_bytes(self, fs):
        """The timestamp of the member is the given one, not the build time."""
        assert build_zip(MTIME) != build_zip(MTIME + 2)

    def test_atomic_replace(self, fs):
        """The artifact replaces the target and leaves no tmp file."""
        build_zip()
        assert sorted(os.listdir('/dist')) == ['demo-1.0-py3-none-any.whl']

    def test_lazy_open(self, fs):
        """The archive is opened by the first member, an unused context writes nothing."""
        with ZipFileExtend(ZIP_FILE, MTIME) as zf:
            assert 'zipfile' not in zf.__dict__
        assert not os.path.exists('/dist')

    def test_existing_folder_is_not_created(self, fs, monkeypatch):
        """A build into an existing folder does no extra IO on the folder."""
        fs.create_dir('/dist')

        def makedirs(self, *args, **kwargs):
            raise AssertionError('makedirs must not be called')

        monkeypatch.setattr(PathStr, 'makedirs', makedirs)
        build_zip()
        assert sorted(os.listdir('/dist')) == ['demo-1.0-py3-none-any.whl']

    def test_failure_keeps_target(self, fs):
        """A failed build removes the tmp file and keeps the target."""
        fs.create_file(ZIP_FILE, contents=b'old')
        with pytest.raises(RuntimeError):
            with ZipFileExtend(ZIP_FILE, MTIME) as zf:
                zf.write_member('demo/core.py', b'a = 1\n')
                raise RuntimeError('build failed')
        assert atomic_read_bytes(ZIP_FILE) == b'old'
        assert sorted(os.listdir('/dist')) == ['demo-1.0-py3-none-any.whl']

    def test_failure_without_target(self, fs):
        """A failed build of a new artifact leaves no file."""
        with pytest.raises(RuntimeError):
            with ZipFileExtend(ZIP_FILE, MTIME) as zf:
                zf.write_member('demo/core.py', b'a = 1\n')
                raise RuntimeError('build failed')
        assert os.listdir('/dist') == []

    def test_open_failure_removes_tmp(self, fs, monkeypatch):
        """A tmp file created by a failed open is removed."""
        def open_zip(file, mode='w'):
            fs.create_file(file, contents=b'')
            raise OSError('cannot open the archive')

        monkeypatch.setattr(zipfile, 'ZipFile', open_zip)
        with pytest.raises(OSError):
            with ZipFileExtend(ZIP_FILE, MTIME) as zf:
                zf.write_member('demo/core.py', b'a = 1\n')
        assert os.listdir('/dist') == []


class TestGzipFileExtend:
    def test_content(self, fs):
        """Members are readable and carry their content."""
        data = build_tar()
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tar:
            assert tar.getnames() == ['demo-1.0/demo/core.py', 'demo-1.0/demo/empty.py']
            assert tar.extractfile('demo-1.0/demo/core.py').read() == b'a = 1\n'
            assert tar.extractfile('demo-1.0/demo/empty.py').read() == b''

    def test_member_metadata(self, fs):
        """Every member has mode 0o644 and the commit time, no owner."""
        data = build_tar()
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tar:
            for info in tar.getmembers():
                assert info.mtime == MTIME
                assert info.mode == FILE_MODE
                # the build must not record the user that runs it
                assert info.uid == 0
                assert info.gid == 0
                assert info.uname == ''
                assert info.gname == ''
                assert info.type == tarfile.REGTYPE

    def test_gzip_header(self, fs):
        """The gzip header carries the commit time and no file name."""
        data = build_tar()
        # magic, deflate, FNAME flag unset
        assert data[:4] == b'\x1f\x8b\x08\x00'
        assert int.from_bytes(data[4:8], 'little') == MTIME

    def test_reproducible(self, fs):
        """The same inputs build the same bytes."""
        assert build_tar() == build_tar()

    def test_timestamp_changes_bytes(self, fs):
        """The timestamp of the member is the given one, not the build time."""
        assert build_tar(MTIME) != build_tar(MTIME + 1)

    def test_atomic_replace(self, fs):
        """The artifact replaces the target and leaves no tmp file."""
        build_tar()
        assert sorted(os.listdir('/dist')) == ['demo-1.0.tar.gz']

    def test_lazy_open(self, fs):
        """The stream is opened by the first member, an unused context writes nothing."""
        with GzipFileExtend(TAR_FILE, MTIME) as gz:
            assert 'tar' not in gz.__dict__
        assert not os.path.exists('/dist')

    def test_existing_folder_is_not_created(self, fs, monkeypatch):
        """A build into an existing folder does no extra IO on the folder."""
        fs.create_dir('/dist')

        def makedirs(self, *args, **kwargs):
            raise AssertionError('makedirs must not be called')

        monkeypatch.setattr(PathStr, 'makedirs', makedirs)
        build_tar()
        assert sorted(os.listdir('/dist')) == ['demo-1.0.tar.gz']

    def test_failure_keeps_target(self, fs):
        """A failed build removes the tmp file and keeps the target."""
        fs.create_file(TAR_FILE, contents=b'old')
        with pytest.raises(RuntimeError):
            with GzipFileExtend(TAR_FILE, MTIME) as gz:
                gz.write_member('demo-1.0/demo/core.py', b'a = 1\n')
                raise RuntimeError('build failed')
        assert atomic_read_bytes(TAR_FILE) == b'old'
        assert sorted(os.listdir('/dist')) == ['demo-1.0.tar.gz']

    def test_failure_without_target(self, fs):
        """A failed build of a new artifact leaves no file."""
        with pytest.raises(RuntimeError):
            with GzipFileExtend(TAR_FILE, MTIME) as gz:
                gz.write_member('demo-1.0/demo/core.py', b'a = 1\n')
                raise RuntimeError('build failed')
        assert os.listdir('/dist') == []

    def test_open_failure_removes_tmp(self, fs, monkeypatch):
        """A tmp file created by a failed open is removed."""
        def open_gzip(*args, **kwargs):
            raise OSError('cannot open the archive')

        monkeypatch.setattr(gzip, 'GzipFile', open_gzip)
        with pytest.raises(OSError):
            with GzipFileExtend(TAR_FILE, MTIME) as gz:
                gz.write_member('demo-1.0/demo/core.py', b'a = 1\n')
        assert os.listdir('/dist') == []
