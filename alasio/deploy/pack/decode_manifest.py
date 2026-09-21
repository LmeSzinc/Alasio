from hashlib import sha1

from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError, _check_length, _decode
from alasio.deploy.pack.pack_model import RefInfo
from alasio.ext.algorithm.pathlen_coding import decode_prefix_comb, decode_suffix_comb
from alasio.ext.algorithm.vlenint import decode_vlenint
from alasio.ext.cache import cached_property

# header: b'MANI' and the manifest version byte
HEADER_LENGTH = 5
# checksum of the manifest, the trailing digest section
CHECKSUM_LENGTH = 20
# sha1 of a file, the raw 20 bytes digest of the file content
SHA1_LENGTH = 20


class PackDecodeManifest:
    """
    Decode and validate a manifest encoded by PackEncodeManifest.

    A manifest is the flat table of the files of a folder tree: the
    filepath, the size and the sha1 of every file, but no file data. It
    is encoded by PackEncodeManifest in alasio.deploy_dev, so a client can
    tell which files of the tree are missing or outdated, and compare
    the version of the tree it has, without asking the server file by
    file.

    # header
    - b'MANI'
    - MANIFEST version

    # data section
    - filepath
    - size
    - sha1, the 20 bytes digest of the file content
    - checksum of above

    Attributes:
        data (memoryview): Raw manifest bytes.
        manifest_version (bytes): MANIFEST format version byte.
        checksum (str): Checksum of the manifest, the trailing 20 bytes
            digest in hex, the same value validate() verifies.
        files (dict[str, RefInfo]): {filepath: RefInfo} records.
    """

    def __init__(self, data):
        """
        Parse the manifest structure. Use validate() to check the checksum.

        Args:
            data (bytes | bytearray | memoryview): Raw manifest content

        Raises:
            PackDecodeError: If the manifest magic is wrong, or the
                manifest is too short to carry the header and the
                trailing checksum
        """
        if isinstance(data, (bytes, bytearray)):
            data = memoryview(data)
        self.data = data

        # header
        if len(data) < HEADER_LENGTH or data[:4] != b'MANI':
            raise PackDecodeError(
                f'Failed to decode header: not a manifest file: {bytes(data[:4])!r}'
            )
        self.manifest_version = bytes(data[4:5])

        # the data section is everything between the header and the
        # trailing checksum
        if len(data) < HEADER_LENGTH + CHECKSUM_LENGTH:
            raise PackDecodeError(
                f'Failed to decode manifest: truncated: {len(data)} bytes, '
                f'expected at least {HEADER_LENGTH + CHECKSUM_LENGTH}'
            )
        self._data_end = len(data) - CHECKSUM_LENGTH
        self.checksum = bytes(data[self._data_end:]).hex()

    def validate(self):
        """
        Validate the checksum of the manifest.

        The trailing 20 bytes digest covers the header and the whole
        data section, so any corruption of the manifest is detected. The
        records are decoded lazily, accessing files raises
        PackDecodeError on a malformed table.

        Raises:
            PackDecodeError: If the checksum mismatches
        """
        if sha1(self.data[:self._data_end]).digest() != bytes(self.data[self._data_end:]):
            raise PackDecodeError('Failed to validate manifest checksum: checksum mismatch')

    @cached_property
    def files(self) -> "dict[str, RefInfo]":
        """
        Decode the file records of the manifest.

        The filepath section reuses the prefix / suffix encoding of the
        pack index, it is replayed by PackDecodeBase._decode_paths, the
        same decoder the pack index uses. Every decoded path is
        validated with validate_filepath before it is handed out: the
        records tell the client which files to write, a path that cannot
        be written on some platform must not reach the filesystem.

        Returns:
            dict[str, RefInfo]: {filepath: RefInfo}

        Raises:
            PackDecodeError: If the data section is malformed: a section
                is truncated, the value counts mismatch, a path is unsafe
                or duplicated
        """
        data = self.data
        # skip the header
        offset = HEADER_LENGTH

        # filepath, the remaining bytes after the reused prefix and suffix
        prefix_comb, read = _decode('manifest: path prefix comb', decode_vlenint, data[offset:])
        offset += read
        prefix_reuse, path_len = _decode(
            'manifest: path prefix comb', decode_prefix_comb, prefix_comb)
        suffix_comb, read = _decode('manifest: path suffix comb', decode_vlenint, data[offset:])
        offset += read
        # both combs store the number of files, they must agree, the
        # number of files is not stored a third time
        _check_length('manifest: path suffix comb', len(suffix_comb), len(prefix_comb))
        suffix_reuse, suffix_lookback = _decode(
            'manifest: path suffix comb', decode_suffix_comb, suffix_comb)

        # remaining path bytes, empty when every path is fully reused
        count = len(prefix_comb)
        path_end = offset + sum(path_len)
        if path_end > self._data_end:
            raise PackDecodeError(
                f'Failed to decode manifest: path bytes out of range: '
                f'{path_end} > {self._data_end}'
            )
        path_data = data[offset:path_end]
        offset = path_end

        # size
        sizes, read = _decode('manifest: size', decode_vlenint, data[offset:])
        offset += read
        _check_length('manifest: size', len(sizes), count)

        # sha1, the last section of the data section, the raw digest of
        # the file content like the pack index
        sha1_length = self._data_end - offset
        if sha1_length != count * SHA1_LENGTH:
            raise PackDecodeError(
                f'Failed to decode manifest: sha1 out of range: '
                f'{sha1_length} bytes, expected {count * SHA1_LENGTH}'
            )
        sha1s = [
            bytes(data[start:start + SHA1_LENGTH]).hex()
            for start in range(offset, self._data_end, SHA1_LENGTH)
        ]

        paths = PackDecodeBase._decode_paths(
            path_data, prefix_reuse, path_len, suffix_reuse, suffix_lookback)
        records = [
            RefInfo(path=path, size=size, sha1=digest)
            for path, size, digest in zip(paths, sizes, sha1s)
        ]
        # index the records by path, rejecting duplicates
        return PackDecodeBase._to_dict(records, 'manifest')
