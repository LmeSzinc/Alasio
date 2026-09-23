"""
Helpers of the simple_pip tests.

Every test module imports the fixture of the in-memory filesystem
explicitly, the fixture is never registered in a conftest.py:

    from alasio.testing.filesystem import fs  # noqa: F401

Wheels and distributions are built on the (fake) filesystem, the functions
here are imported by the tests the way tests/deploy_dev/pack does it:

    from conftest import build_wheel, create_dist, site_packages
"""
import base64
import hashlib
import py_compile
import zipfile

import pytest

from alasio.deploy_dev.simple_pip import DistInfo
from alasio.ext.path.iter import iter_files, iter_folders
from alasio.logger.writer import LogWriter


def sha256_record(content):
    """
    Compute the RECORD checksum of a content.

    The checksum is computed with hashlib, independently from the code
    under test.

    Args:
        content (bytes): Content of the file

    Returns:
        str: "sha256=<checksum>"
    """
    digest = hashlib.sha256(content).digest()
    checksum = base64.urlsafe_b64encode(digest).decode('latin1').rstrip('=')
    return f'sha256={checksum}'


def build_wheel(
    path, files=None, name='demo', version='1.0', dist_info=True, record=True, executable=(),
):
    """
    Build a wheel on the (fake) filesystem.

    Args:
        path (str): Path of the .whl file to write
        files (dict[str, bytes]): Files of the wheel, path relative to the
            wheel root. The files of the .dist-info are generated unless
            they are given. Defaults to None.
        name (str): Name of the distribution. Defaults to 'demo'.
        version (str): Version of the distribution. Defaults to '1.0'.
        dist_info (bool): False to build a wheel without .dist-info
        record (bool): False to build a wheel without the RECORD of the
            .dist-info
        executable (Collection[str]): Members to build with the executable
            permission, like a script of .data/scripts

    Returns:
        str: Path of the wheel
    """
    members = dict(files or {})
    folder = f'{name}-{version}.dist-info'
    if dist_info:
        metadata = f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n\n'
        members.setdefault(f'{folder}/METADATA', metadata.encode('utf-8'))
        members.setdefault(f'{folder}/WHEEL', b'Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\n')
        members.setdefault(f'{folder}/top_level.txt', f'{name}\n'.encode('utf-8'))
        if record:
            rows = [
                f'{member},{sha256_record(content)},{len(content)}\n'
                for member, content in members.items()
            ]
            rows.append(f'{folder}/RECORD,,\n')
            members[f'{folder}/RECORD'] = ''.join(rows).encode('utf-8')

    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for member, content in members.items():
            if member in executable:
                info = zipfile.ZipInfo(member)
                # A regular file with the executable permission set
                info.external_attr = 0o100755 << 16
                zf.writestr(info, content)
            else:
                zf.writestr(member, content)
    return path


def create_dist(fake, files, record=None, name='demo', version='1.0', site=None):
    """
    Create a distribution on the (fake) filesystem.

    Args:
        fake (FakeFilesystem): Active fake filesystem
        files (dict[str, bytes | str]): Files of the distribution, path
            relative to site-packages, e.g. "demo/core.py" or
            "../../Scripts/demo-tool" for a file installed outside of it
        record (list[str | tuple[str, str, str]] | None): Rows of the RECORD
            file, either a path, or a (path, sha256, size) tuple. Defaults
            to None, the rows of files and of METADATA are written with the
            checksum of the content. [] writes a RECORD without any row,
            False writes no RECORD at all.
        name (str): Name of the distribution. Defaults to 'demo'.
        version (str): Version of the distribution. Defaults to '1.0'.
        site (str): Path of site-packages. Defaults to the fake environment.

    Returns:
        DistInfo: The distribution
    """
    site = site or site_packages(fake)
    folder = f'{name}-{version}.dist-info'
    dist_files = dict(files)
    if not any(path.endswith('/METADATA') for path in dist_files):
        dist_files[f'{folder}/METADATA'] = b'Metadata-Version: 2.1\n'

    if record is None:
        record = [
            (path, sha256_record(content if isinstance(content, bytes) else content.encode('utf-8')),
             str(len(content)))
            for path, content in dist_files.items()
        ]
        record.append((f'{folder}/RECORD', '', ''))

    for path, content in dist_files.items():
        fake.create_file(f'{site}/{path}', contents=content)
    if record is not False:
        lines = [row if isinstance(row, str) else ','.join(row) for row in record]
        content = ''.join(f'{line}\n' for line in lines)
        fake.create_file(f'{site}/{folder}/RECORD', contents=content.encode('utf-8'))

    return DistInfo(f'{site}/{folder}')


def abs_path(fake, path):
    """
    Absolute path of a path of the fake filesystem.

    The fake filesystem resolves a path without drive against the drive of
    the working directory on Windows, e.g. "/env/x" gives "E:/env/x".

    Args:
        fake (FakeFilesystem): Active fake filesystem
        path (str): Path like "/env/Lib/site-packages"

    Returns:
        str: Absolute posix path, with the drive of the fake filesystem on Windows
    """
    root = fake.root_dir.path.rstrip('/')
    return f'{root}/{path.lstrip("/")}'


def site_packages(fake):
    """
    Path of the site-packages directory of the fake environment.

    Args:
        fake (FakeFilesystem): Active fake filesystem

    Returns:
        str: Absolute posix path of site-packages
    """
    return abs_path(fake, '/env/Lib/site-packages')


def list_files(root):
    """
    List the files of a directory, relative to it.

    os.walk() is not supported by the fake filesystem, iter_files() of the
    project is used instead.

    Args:
        root (str): Directory to walk

    Returns:
        list[str]: Sorted relative posix paths of the files
    """
    root = root.rstrip('/')
    return sorted(path[len(root) + 1:] for path in iter_files(root, recursive=True))


def list_folders(root):
    """
    List the directories of a directory, relative to it.

    Args:
        root (str): Directory to walk

    Returns:
        list[str]: Sorted relative posix paths of the directories
    """
    root = root.rstrip('/')
    return sorted(path[len(root) + 1:] for path in iter_folders(root, recursive=True))


@pytest.fixture(autouse=True)
def mute_log_file():
    """
    Keep the tests from opening log files in the project log directory.

    Every log line of the test process (pytest's module name is "__main__")
    would create log/{date}__main__.txt in the repository. The in-memory
    filesystem fixture redirects the writes already, this also covers the
    tests that do not use it.

    The log assertions keep working: the tests capture with
    logger.mock_capture_writer(), which swaps the writer in front of the
    (muted) file target.
    """
    LogWriter().mute(fd=True)
    yield
    # Drop whatever fd the test left cached (real or fake) before unmuting
    LogWriter().close_fd()


@pytest.fixture
def pyc_compile(monkeypatch):
    """
    Patch py_compile.compile() into a stub writing a dummy .pyc file.

    py_compile reads the source of a file with the builtin open() captured
    by the tokenize module, so a source of the fake filesystem can not be
    read by it. The stub keeps the path computation of the installation
    under test, only the bytes of the .pyc are not real ones.
    """
    def compile_stub(py_file, cfile=None, dfile=None, **kwargs):
        with open(cfile, 'wb') as f:
            f.write(b'\x00' * 16)
        return cfile

    monkeypatch.setattr(py_compile, 'compile', compile_stub)
