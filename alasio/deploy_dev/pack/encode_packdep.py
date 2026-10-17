import msgspec

from alasio.deploy.pack.packdep import PACKDEP_MAGIC, PACKDEP_VERSION, PackDepObj, validate_packdep_name
from alasio.ext.path.validate import validate_filename


def encode_packdep(entries):
    """
    Encode the dependency snapshot of a repo pack.

    The output is the header (PACKDEP_MAGIC + one byte PACKDEP_VERSION)
    followed by a msgpack array of PackDepObj. The entries are sorted by
    name, so the bytes only depend on the set of entries, not on the order
    of the mapping the caller built. The file itself is authenticated by the
    repo index pack, it carries no checksum of its own.

    Args:
        entries (dict[str, tuple[str, bytes]]): {PEP 503 name: (version,
            20 bytes index checksum)} of the dependency channels published
            for the repo version

    Returns:
        bytes: Content of .pack/packdep.pack

    Raises:
        ValueError: If a name or a version is not a safe path component, or
            a checksum is not 20 bytes
    """
    rows = []
    for name in sorted(entries):
        version, checksum = entries[name]
        validate_packdep_name(name)
        validate_filename(version)
        if not isinstance(checksum, (bytes, bytearray, memoryview)):
            raise ValueError(
                f'Index checksum of {name!r} is not bytes: {type(checksum).__name__}')
        checksum = bytes(checksum)
        if len(checksum) != 20:
            raise ValueError(
                f'Index checksum of {name!r} is {len(checksum)} bytes, expected 20')
        rows.append(PackDepObj(name=name, version=version, index_checksum=checksum))
    return PACKDEP_MAGIC + bytes((PACKDEP_VERSION,)) + msgspec.msgpack.encode(rows)
