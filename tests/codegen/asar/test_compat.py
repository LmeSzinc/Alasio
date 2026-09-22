"""
Cross version compatibility tests.

The reader accepts the archives of both reference versions, including the
shapes only one of them writes (the integrity block of an empty file, the
extra empty block 3.4.1 writes for a content of exactly one block size).
"""
import hashlib
import os

from alasio.codegen.asar import AsarArchive
from alasio.codegen.asar.format import BLOCK_SIZE
from alasio.testing.filesystem import fs  # noqa: F401
from tests.codegen.asar import fixture


def sha256(data):
    """
    Hash a content.

    Args:
        data (bytes): Content

    Returns:
        str: SHA256 hex digest
    """
    return hashlib.sha256(data).hexdigest()


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
