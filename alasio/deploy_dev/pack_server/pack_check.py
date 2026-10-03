"""
Read the identity of a pack file of the pack server.

The pack server keeps the packs of an earlier run, see PackRepoGen: a pack is
only built again when its file is missing, when it was encoded with another
pack format, when it packs another version pair, or when the file cannot be
read as a pack at all. Reading the identity of the file is what tells these
cases apart, and the index pack checksum it carries is what latest.pack is
built from when the full pack is kept.

This is the server side of the pack front, it does not decode a pack: only the
header, the version parts of the index section and its trailing checksum are
read, a few hundred KB at most. The client decodes a pack with
alasio/deploy/pack, the server generates and checks the packs, it never
unpacks one.

Usage:
    from alasio.deploy_dev.pack_server.pack_check import PackChecksum

    checksum = PackChecksum.from_file(file)
    checksum.current_version
"""

from msgspec import Struct

from alasio.deploy.pack.decode_base import PackDecodeError
from alasio.ext.algorithm.vint import decode_vint
from alasio.ext.path.atomic import atomic_open

# bytes read first from a pack file: the header, the length vint of the index
# section and the two version parts behind it. A version is a commit sha1, the
# parts are short: a longer one is not the pack of a version and is refused
HEAD_SIZE = 256


class PackChecksum(Struct):
    """
    Identity of a pack file: its format version, its versions, its checksum.

    Attributes:
        pack_version (int): PACK format version, 0~255, the single header byte
            behind b'PACK'
        current_version (str): Version the pack packs, e.g. the commit sha1
        old_version (str): Version the pack updates from, empty in a full pack
        index_checksum (bytes): The 20 bytes checksum digest of the index
            section, the bytes PackEncodeBase.latest_pack() appends to the
            version; the hex form of the same digest is the index_checksum of
            the client decoder
    """

    pack_version: int
    current_version: str
    old_version: str
    index_checksum: bytes

    @classmethod
    def from_file(cls, file):
        """
        Read the identity of a pack file.

        Only the index section is read: the header, the length vint it starts
        with, the version parts and the trailing 20 bytes checksum; the data
        section of a full pack, tens of MB of the real repo, is never read.

        Args:
            file (str): Path of a pack file

        Returns:
            PackChecksum: Identity of the pack

        Raises:
            OSError: If the file cannot be read
            PackDecodeError: If the file is not a pack, or is shorter than
                its index section
        """
        with atomic_open(file, 'rb') as f:
            head = f.read(HEAD_SIZE)
            pack_version, index_end = _read_header(file, head)
            # the index section of a full pack of the real repo is a few
            # hundred KB: read the rest of it, the version parts and the
            # checksum are in it
            data = head + f.read(max(0, index_end - len(head)))
        section = data[5:index_end]
        if len(section) != index_end - 5:
            raise PackDecodeError(
                f'Failed to read the pack: the file ends before its index section: "{file}"')
        current_version, old_version = _read_versions(file, section)
        return cls(
            pack_version=pack_version,
            current_version=current_version,
            old_version=old_version,
            # the checksum is the trailing 20 bytes of the index section
            index_checksum=bytes(section[-20:]),
        )


def _read_header(file, head):
    """
    Read the pack format version and the end of the index section.

    Args:
        file (str): Path of the pack file, for the error message
        head (bytes): Front of the pack file

    Returns:
        tuple[int, int]: (pack format version, byte offset behind the index
            section)

    Raises:
        PackDecodeError: If the file is not a pack, or the length vint of the
            index section is malformed
    """
    if len(head) < 5 or head[:4] != b'PACK':
        raise PackDecodeError(
            f'Failed to read the pack: not a pack file: {head[:4]!r}, "{file}"')
    # the version is one byte in the pack file, an int on the Python side
    pack_version = head[4]
    try:
        length, read = decode_vint(head[5:])
    except ValueError as e:
        raise PackDecodeError(
            f'Failed to read the pack: index section length: {e}, "{file}"') from e
    index_end = 5 + read + length
    if index_end < 25:
        # the index section carries at least its trailing 20 bytes checksum
        raise PackDecodeError(
            f'Failed to read the pack: index section of {index_end - 5} bytes, "{file}"')
    return pack_version, index_end


def _read_versions(file, section):
    """
    Read the two version parts of the index section of a pack.

    The section starts with its own length vint, then the current version and
    the version to update from, each one a length prefixed utf-8 string, see
    PackEncodeBase.iter_packidx_data.

    Args:
        file (str): Path of the pack file, for the error message
        section (bytes | bytearray): Index section of the pack, from its length
            vint to its trailing checksum

    Returns:
        tuple[str, str]: (current version, old version, empty in a full pack)

    Raises:
        PackDecodeError: If a version part is truncated or malformed
    """
    offset = 0
    try:
        _, read = decode_vint(section[offset:])
    except ValueError as e:
        raise PackDecodeError(
            f'Failed to read the pack: index data length: {e}, "{file}"') from e
    offset += read
    versions = []
    for _ in range(2):
        try:
            length, read = decode_vint(section[offset:])
        except ValueError as e:
            raise PackDecodeError(
                f'Failed to read the pack: version length: {e}, "{file}"') from e
        offset += read
        end = offset + length
        if end > len(section):
            raise PackDecodeError(
                f'Failed to read the pack: version part out of range: '
                f'{end} > {len(section)}, "{file}"')
        versions.append(bytes(section[offset:end]).decode('utf-8', errors='replace'))
        offset = end
    return versions[0], versions[1]
