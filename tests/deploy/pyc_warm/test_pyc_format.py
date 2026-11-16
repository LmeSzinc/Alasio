"""
The header of a pyc file as the running interpreter writes it, PEP 3147.

The header is parsed by hand in alasio/deploy/pyc_warm/fresh.py, these
tests pin the layout to the interpreter that runs them. The version
matrix is not written here: the python-tests job of the CI runs the
tests on 3 operating systems and python 3.8 to 3.14, every job checks
its own interpreter.
"""
import os
import sys
from importlib._bootstrap_external import _code_to_timestamp_pyc

from alasio.deploy.pyc_warm.fresh import HEADER_SIZE, parse_header, stale_sources
from alasio.deploy.simple_pip.pyc import pyc_path
from alasio.testing.filesystem import fs  # noqa: F401

# Path and content of the source of the tests
SOURCE = '/pkg/core.py'
CONTENT = b'VALUE = 42\n'
# A magic number python never writes: the low two bytes are the version
FOREIGN_MAGIC = b'\x00\x00\x0d\x0a'


def compile_pyc(fs, source=SOURCE):
    """
    Write the pyc file the running interpreter would write for a source.

    The bytes come from the packer of CPython rather than from
    py_compile(): py_compile writes the pyc file with the low level
    file functions of importlib, which the fake filesystem serves only
    through the mock of os.open().

    Args:
        fs (FakeFilesystem): The fake filesystem
        source (str): Path of the source file

    Returns:
        tuple[bytes, os.stat_result]: The bytecode of the source and
            its stat
    """
    fs.create_file(source, contents=CONTENT)
    st = os.stat(source)
    code = compile(CONTENT, source, 'exec')
    # The packer answers a bytearray, the fake filesystem holds bytes
    body = bytes(_code_to_timestamp_pyc(code, st.st_mtime, st.st_size))
    return body, st


def replace_source(fs, content, mtime):
    """
    Replace the source file of the tests with an other revision.

    Args:
        fs (FakeFilesystem): The fake filesystem
        content (bytes): Content of the new revision
        mtime (float): Modification time of the new revision

    Returns:
        os.stat_result: Stat of the new revision
    """
    fs.remove(SOURCE)
    fs.create_file(SOURCE, contents=content)
    os.utime(SOURCE, (mtime, mtime))
    return os.stat(SOURCE)


class TestPycFormat:
    """
    Tests for the layout of a pyc header, against the running interpreter.
    """

    def test_our_parser_agrees_with_this_interpreter(self, fs):
        """A pyc file written by this interpreter warms its source."""
        body, st = compile_pyc(fs)
        fs.create_file(pyc_path(SOURCE), contents=body)
        assert parse_header(body) == (int(st.st_mtime), st.st_size)
        assert stale_sources([SOURCE]) == []

    def test_header_shape(self, fs):
        """The header is 16 bytes: magic, flags, mtime and size."""
        body, st = compile_pyc(fs)
        assert len(body) > HEADER_SIZE
        # A timestamp based pyc file without the check source flag
        assert body[4:8] == b'\x00\x00\x00\x00'
        assert int.from_bytes(body[8:12], 'little') == int(st.st_mtime)
        assert int.from_bytes(body[12:16], 'little') == st.st_size

    def test_pyc_name_holds_the_cache_tag(self, fs):
        """The pyc file of a source is named for this interpreter."""
        assert sys.implementation.cache_tag in pyc_path(SOURCE)

    def test_foreign_magic_is_rejected(self, fs):
        """A pyc file with the magic of an other version never warms a source."""
        body, st = compile_pyc(fs)
        foreign = FOREIGN_MAGIC + body[4:]
        fs.create_file(pyc_path(SOURCE), contents=foreign)
        # The source is the one the pyc file was written for, so only the
        # magic number can tell the two apart
        assert parse_header(body) == (int(st.st_mtime), st.st_size)
        assert parse_header(foreign) is None
        assert stale_sources([SOURCE]) == [SOURCE]

    def test_stale_source_is_rejected(self, fs):
        """An edited source is stale, its pyc file dates from the former one."""
        body, st = compile_pyc(fs)
        fs.create_file(pyc_path(SOURCE), contents=body)
        with open(SOURCE, 'ab') as f:
            f.write(b'VALUE = 43\n')
        assert stale_sources([SOURCE]) == [SOURCE]

    def test_header_decides_when_the_pyc_file_is_newer(self, fs):
        """A pyc file newer than its source is checked by its header."""
        body, st = compile_pyc(fs)
        fs.create_file(pyc_path(SOURCE), contents=body)
        # An other revision of the source with an other length, dated
        # before the pyc file: the mtime alone would keep the pyc file
        shorter = b'VALUE = 4\n'
        replace_source(fs, shorter, st.st_mtime - 1)
        assert stale_sources([SOURCE]) == [SOURCE]
