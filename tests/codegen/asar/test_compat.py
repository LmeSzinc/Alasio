"""
Cross version compatibility tests.

Two things are asserted here:

1. the archives electron-builder produced for the desktop client match the
   source tree they were built from (skipped when there is no release build)
2. the reader accepts the archives of both reference versions, including the
   shapes only one of them writes (the integrity block of an empty file, the
   extra empty block 3.4.1 writes for a content of exactly one block size)
"""
import hashlib
import os

import pytest

from alasio.codegen.asar import AsarArchive
from alasio.codegen.asar.format import BLOCK_SIZE
from alasio.codegen.asar.model import KIND_FILE
from alasio.testing.filesystem import fs  # noqa: F401
from tests.codegen.asar import fixture

WEBAPP = os.path.join('webapp')
ARCHIVE = os.path.join(WEBAPP, 'release', 'app.asar')


def sha256(data):
    """
    Hash a content.

    Args:
        data (bytes): Content

    Returns:
        str: SHA256 hex digest
    """
    return hashlib.sha256(data).hexdigest()


def tree_hashes(root):
    """
    Hash every file of a directory tree.

    Works on the real filesystem and on the in-memory fake filesystem of
    ``alasio.testing.filesystem``: it only uses os.walk() and open().

    Args:
        root (str): Root directory

    Returns:
        dict: {relative POSIX path: SHA256}
    """
    hashes = {}
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            with open(path, 'rb') as f:
                content = f.read()
            relative = os.path.relpath(path, root).replace(os.sep, '/')
            hashes[relative] = sha256(content)
    return hashes


class TestRealProduct:
    """
    The archive of the desktop client, built by electron-builder with
    @electron/asar 3.4.1. Skipped when the release build is not present.
    """

    def archive_or_skip(self):
        """
        Load the release archive.

        Returns:
            AsarArchive: The archive, to be used as a context manager
        """
        if not os.path.isfile(ARCHIVE):
            pytest.skip(f'{ARCHIVE} is not built')
        return AsarArchive(ARCHIVE)

    def test_entries_match_the_source_tree(self):
        """Every entry of the archive is the file vite and the packer wrote."""
        with self.archive_or_skip() as archive:
            # The archive holds the content of `webapp/dist` plus `package.json`,
            # both relative to the root of the desktop client
            expected = {
                f'dist/{path}': digest
                for path, digest in tree_hashes(os.path.join(WEBAPP, 'dist')).items()
            }
            with open(os.path.join(WEBAPP, 'package.json'), 'rb') as f:
                expected['package.json'] = sha256(f.read())
            actual = {
                path: sha256(bytes(archive.read_file(path)))
                for path, info in archive.iter_entries() if info.kind == KIND_FILE
            }
        assert actual == expected
        assert len(actual) == 28

    def test_directories(self):
        """The archive has no empty directory, but it stores the ones it needs."""
        with self.archive_or_skip() as archive:
            dirs = sorted(path for path, info in archive.iter_entries() if info.kind != KIND_FILE)
        assert dirs == [
            'dist', 'dist/main', 'dist/preload', 'dist/renderer',
            'dist/renderer/_app', 'dist/renderer/_app/immutable',
            'dist/renderer/_app/immutable/assets',
            'dist/renderer/_app/immutable/chunks',
            'dist/renderer/_app/immutable/entry',
            'dist/renderer/_app/immutable/nodes',
        ]

    def test_offsets_are_back_to_back(self):
        """The entries are stored one after the other, without padding."""
        with self.archive_or_skip() as archive:
            # The offsets follow the order of the archive, which is the order the
            # file system gave the packer, not the canonical order of this module
            files = sorted(
                ((path, info) for path, info in archive.iter_entries() if info.kind == KIND_FILE),
                key=lambda entry: entry[1].offset,
            )
            offset = 0
            for path, info in files:
                assert info.offset == offset, path
                offset += info.size
            assert offset == os.path.getsize(ARCHIVE) - archive.data_offset

    def test_validate(self):
        """The structure and every content hash of the real archive are valid."""
        with self.archive_or_skip() as archive:
            archive.validate(verify_content=True)

    def test_streaming_extraction(self, fs):
        """Extracting the whole archive gives the content of every entry."""
        # The release archive is read at import time: this test runs under the
        # in-memory filesystem, which serves every path from memory and never
        # touches the real disk (see fixture.release_archive_bytes).
        original = fixture.release_archive_bytes()
        if original is None:
            pytest.skip(f'{fixture.RELEASE_ARCHIVE} is not built')
        fs.create_file('/app.asar', contents=original)
        with AsarArchive('/app.asar') as archive:
            expected = {
                path: sha256(bytes(archive.read_file(path)))
                for path, info in archive.iter_entries() if info.kind == KIND_FILE
            }
            archive.extract_all('/out', verify=True)
        # the extracted tree, read back through the fake filesystem
        assert tree_hashes('/out') == expected


class TestVersionDifferences:
    def test_empty_file_block(self, fs):
        """Both versions write a single empty block for an empty file."""
        fs.create_file('/packthis.asar', contents=fixture.packthis_430())
        with AsarArchive('/packthis.asar') as archive:
            blocks = archive.entry('emptyfile.txt').integrity.blocks
        assert blocks == [sha256(b'')]

    def test_extra_block_of_341(self, fs):
        """A 3.4.1 archive of a one block content stores an extra empty block."""
        content = b'a' * BLOCK_SIZE
        header = {
            'files': {
                'big.bin': {
                    'size': BLOCK_SIZE,
                    'offset': '0',
                    'integrity': {
                        'algorithm': 'SHA256',
                        'hash': sha256(content),
                        'blockSize': BLOCK_SIZE,
                        'blocks': [sha256(content), sha256(b'')],
                    },
                },
            },
        }
        fs.create_file('/legacy.asar', contents=fixture.make_archive(header, content))
        with AsarArchive('/legacy.asar') as archive:
            assert archive.entry('big.bin').integrity.blocks == [sha256(content), sha256(b'')]
            # The reader accepts it, the hashes are never checked block by block
            archive.validate(verify_content=True)

    def test_blocks_are_not_compared(self, fs):
        """Atom does not compare the blocks either, only the whole file hash."""
        content = b'hello'
        header = {
            'files': {
                'a.txt': {
                    'size': 5,
                    'offset': '0',
                    'integrity': {
                        'algorithm': 'SHA256',
                        'hash': sha256(content),
                        'blockSize': BLOCK_SIZE,
                        'blocks': [sha256(b'other')],
                    },
                },
            },
        }
        fs.create_file('/odd.asar', contents=fixture.make_archive(header, content))
        with AsarArchive('/odd.asar') as archive:
            archive.validate(verify_content=True)

    def test_deduplicated_offsets(self, fs):
        """A 4.3.0 archive shares the content of identical files."""
        shared = b'same'
        header = {
            'files': {
                'a.txt': {
                    'size': 4, 'offset': '0',
                    'integrity': {
                        'algorithm': 'SHA256', 'hash': sha256(shared),
                        'blockSize': BLOCK_SIZE, 'blocks': [sha256(shared)],
                    },
                },
                'b.txt': {
                    'size': 4, 'offset': '0',
                    'integrity': {
                        'algorithm': 'SHA256', 'hash': sha256(shared),
                        'blockSize': BLOCK_SIZE, 'blocks': [sha256(shared)],
                    },
                },
            },
        }
        fs.create_file('/dedup.asar', contents=fixture.make_archive(header, shared))
        with AsarArchive('/dedup.asar') as archive:
            assert archive.entry('a.txt').offset == archive.entry('b.txt').offset == 0
            archive.validate(verify_content=True)
            # The data area is only 4 bytes long, the entries overlap
            assert os.path.getsize('/dedup.asar') - archive.data_offset == 4
