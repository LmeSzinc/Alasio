import re
from typing import List

import msgspec
from msgspec import Struct

from alasio.deploy.pack.decode_base import PackDecodeError
from alasio.ext.path.validate import validate_filename

# the path of the generated dependency snapshot of a repo tree, a file of the
# pack area like .pack/history.pack
PACKDEP_PATH = '.pack/packdep.pack'
# the magic of the file: a metadata file, not a pack (no index / data section)
PACKDEP_MAGIC = b'PDEP'
# format version of the file, one byte behind the magic, 0~255
PACKDEP_VERSION = 0
# a PEP 503 normalized distribution name: lowercase letters, digits and single
# hyphens between them, the dist-key of the pack channel and the ledger
_PEP503_NORMALIZED = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*\Z')


def validate_packdep_name(name):
    """
    Validate a dependency name of the file.

    The name is the PEP 503 normalized distribution name, the dist-key of the
    pack channel and the ledger folder: only lowercase letters, digits and
    single hyphens between them. A name that is not normalized would never
    match the name the client derives from its requirements file, so it is
    rejected instead of being written into the tree.

    Args:
        name (str): Name to validate

    Raises:
        ValueError: If the name is not a safe path component, or is not PEP 503
            normalized
    """
    validate_filename(name)
    if not _PEP503_NORMALIZED.fullmatch(name):
        raise ValueError(f'Packdep name is not PEP 503 normalized: {name!r}')


class PackDepObj(Struct, array_like=True):
    """
    One dependency snapshot of .pack/packdep.pack.

    The record is the semantic content of the latest.pack of one dependency
    channel: the version the channel published and the index checksum of its
    full pack. It is encoded as a msgpack array, the field order is
    (name, version, index_checksum).

    Attributes:
        name (str): PEP 503 normalized distribution name, the packdep folder
            name and the ledger key of the distribution
        version (str): Display version published in latest.pack
        index_checksum (bytes): The 20 bytes index checksum of the full pack
            of the version
    """
    # PEP 503 normalized distribution name
    name: str
    # display version published in latest.pack
    version: str
    # 20 bytes index checksum of the dependency full pack
    index_checksum: bytes


def decode_packdep(data):
    """
    Decode and validate .pack/packdep.pack.

    The file is a header (PACKDEP_MAGIC + one byte version) followed by a
    msgpack array of PackDepObj. Every entry is validated before it is
    returned: a malformed entry makes the whole file unusable (a partial
    trust is harder to reason about than falling back to the server).

    The file itself is authenticated by the record of the repo index pack
    (.pack/packdep.pack, size + sha1); this function only decodes the bytes,
    it does not prove where they came from.

    Args:
        data (bytes | bytearray | memoryview): Content of the file

    Returns:
        dict[str, PackDepObj]: {name: entry}, in the order of the file

    Raises:
        PackDecodeError: If the header, the format version, the msgpack
            payload or an entry is invalid
    """
    if isinstance(data, (bytearray, memoryview)):
        data = bytes(data)
    if len(data) < 5:
        raise PackDecodeError(
            f'Failed to decode packdep: {len(data)} bytes, expected the 5 bytes header')
    if data[:4] != PACKDEP_MAGIC:
        raise PackDecodeError(
            f'Failed to decode packdep: not a packdep file: {data[:4]!r}')
    version = data[4]
    if version != PACKDEP_VERSION:
        raise PackDecodeError(
            f'Failed to decode packdep: unsupported format version {version}, '
            f'expected {PACKDEP_VERSION}')

    try:
        entries = msgspec.msgpack.decode(data[5:], type=List[PackDepObj])
    except (msgspec.DecodeError, ValueError) as e:
        raise PackDecodeError(f'Failed to decode packdep entries: {e}') from e

    out = {}
    for entry in entries:
        try:
            validate_packdep_name(entry.name)
            validate_filename(entry.version)
        except ValueError as e:
            raise PackDecodeError(
                f'Failed to decode packdep entry: {e}, name={entry.name!r}, '
                f'version={entry.version!r}') from e
        if len(entry.index_checksum) != 20:
            raise PackDecodeError(
                f'Failed to decode packdep entry {entry.name!r}: '
                f'index checksum of {len(entry.index_checksum)} bytes, expected 20')
        if entry.name in out:
            raise PackDecodeError(
                f'Failed to decode packdep: duplicate dependency {entry.name!r}')
        out[entry.name] = entry
    return out
