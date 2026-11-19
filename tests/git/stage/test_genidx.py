"""
Tests of `GenIdx.pack_to_idx`, the builder of a pack index.

The packs are written entry by entry here instead of by git: what these tests
are about is the shape of the delta chains, and only a pack built by hand can
hold a ref-delta whose base is an ofs-delta, a base that comes later in the
file, or a base that the pack does not hold at all.

Every self-contained pack is compared with the idx `git index-pack` writes for
it, byte for byte, and read back with `IdxFile`, the reader the rest of the
project uses. A thin pack is refused by default, and the refusal names the base
it needs.
"""
import hashlib
import secrets
import struct
import zlib

import pytest

from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.git.file.exception import PackBroken, PackNotSelfContained
from alasio.git.file.idx import IdxFile
from alasio.git.obj.obj import GitObject
from alasio.git.stage.genidx import GenIdx
from alasio.testing import git_server

# The packs are small and every test writes its own folder, so the file is
# safe to run beside the other marked files
pytestmark = pytest.mark.parallel

BLOB = 3
OFS_DELTA = 6
REF_DELTA = 7


def blob_sha1(content):
    """
    The sha1 git gives a blob.

    Args:
        content (bytes): Content of the blob

    Returns:
        str: Hex sha1
    """
    header = b'blob ' + str(len(content)).encode() + b'\x00'
    return hashlib.sha1(header + content).hexdigest()


def encode_obj_header(objtype, size):
    """
    The type and size header of one pack entry.

    Args:
        objtype (int): Type of the entry
        size (int): Length of the content the entry carries

    Returns:
        bytes: The header, one to ten bytes
    """
    out = bytearray()
    byte = (objtype << 4) | (size & 0x0F)
    size >>= 4
    while size:
        out.append(byte | 0x80)
        byte = size & 0x7F
        size >>= 7
    out.append(byte)
    return bytes(out)


def encode_ofs_offset(offset):
    """
    The backwards distance of an ofs-delta, in the varint git uses for it.

    Each byte but the last carries a bias of one, so that a one byte distance
    is always smaller than a two byte one.

    Args:
        offset (int): Bytes between the base entry and the delta entry

    Returns:
        bytes: The encoded distance
    """
    out = bytearray((offset & 0x7F,))
    offset >>= 7
    while offset:
        offset -= 1
        out.append(0x80 | (offset & 0x7F))
        offset >>= 7
    out.reverse()
    return bytes(out)


def encode_size(size):
    """
    The seven bit little endian length of a delta header.

    Args:
        size (int): Length of the content

    Returns:
        bytes: The encoded length
    """
    out = bytearray()
    while True:
        byte = size & 0x7F
        size >>= 7
        if size:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def make_delta(source, target):
    """
    Delta instructions that rebuild one content from another.

    The common prefix is copied and the rest is inserted, which expresses any
    pair of contents and keeps the instructions readable in a failure.

    Args:
        source (bytes): Content the delta is applied to
        target (bytes): Content the delta produces

    Returns:
        bytes: The delta
    """
    common = 0
    limit = min(len(source), len(target))
    while common < limit and source[common] == target[common]:
        common += 1

    out = bytearray(encode_size(len(source)))
    out += encode_size(len(target))
    if common:
        # a copy instruction: the header says which of the offset and size
        # bytes are present, and the bytes follow it in that order
        header = 0x80 | 0x01
        sizes = bytearray()
        if common & 0xFF:
            header |= 0x10
            sizes.append(common & 0xFF)
        if common >> 8:
            header |= 0x20
            sizes.append((common >> 8) & 0xFF)
        if common >> 16:
            header |= 0x40
            sizes.append((common >> 16) & 0xFF)
        out.append(header)
        out.append(0)
        out += sizes
    rest = target[common:]
    while rest:
        chunk, rest = rest[:0x7F], rest[0x7F:]
        out.append(len(chunk))
        out += chunk
    return bytes(out)


class PackBuilder:
    """
    A version 2 pack written entry by entry.

    The add methods answer the offset of the entry they wrote, so a delta can
    point at another entry without the test doing arithmetic on the layout.

    Usage:
        pack = PackBuilder()
        first = pack.add_full(BLOB, b'content')
        pack.add_ofs_delta(first, delta)
        data = pack.build()
    """

    # The pack header: magic, version 2 and the object count, which is only
    # known at build(), so the offsets of the entries start after it
    HEADER_SIZE = 12

    def __init__(self):
        self._entries = bytearray()
        self._count = 0

    def _append(self, entry):
        """
        Args:
            entry (bytes): One complete entry

        Returns:
            int: Offset of the entry in the pack
        """
        offset = self.HEADER_SIZE + len(self._entries)
        self._entries += entry
        self._count += 1
        return offset

    def add_full(self, objtype, content):
        """
        Add a full object, its content compressed as a whole.

        Args:
            objtype (int): Type of the object
            content (bytes): Content of the object

        Returns:
            int: Offset of the entry in the pack
        """
        return self._append(encode_obj_header(objtype, len(content)) + zlib.compress(content))

    def add_ofs_delta(self, base_offset, delta):
        """
        Add an ofs-delta, the base of which is earlier in the pack.

        Args:
            base_offset (int): Offset of the base entry
            delta (bytes): Delta instructions

        Returns:
            int: Offset of the entry in the pack
        """
        offset = self.HEADER_SIZE + len(self._entries)
        entry = (encode_obj_header(OFS_DELTA, len(delta))
                 + encode_ofs_offset(offset - base_offset)
                 + zlib.compress(delta))
        return self._append(entry)

    def add_ref_delta(self, base_sha1, delta):
        """
        Add a ref-delta, the base of which is named by sha1 and may be
        anywhere in the pack, or outside it.

        Args:
            base_sha1 (str): Hex sha1 of the base
            delta (bytes): Delta instructions

        Returns:
            int: Offset of the entry in the pack
        """
        entry = (encode_obj_header(REF_DELTA, len(delta))
                 + bytes.fromhex(base_sha1)
                 + zlib.compress(delta))
        return self._append(entry)

    def build(self):
        """
        Returns:
            bytes: The pack, from its header to its trailing checksum
        """
        header = b'PACK' + struct.pack('>II', 2, self._count)
        data = header + bytes(self._entries)
        return data + hashlib.sha1(data).digest()


class StubGenIdx(GenIdx):
    """
    A GenIdx whose object store is a fixed mapping of content.

    A base outside the pack is what a thin pack needs, and a real repository
    would hold it; the stub keeps a test off a repository fixture.
    """

    def __init__(self, objects):
        """
        Args:
            objects (dict[str, bytes]): {hex sha1: content of the blob}
        """
        super().__init__('')
        self._objects = objects

    def cat(self, sha1):
        """
        Args:
            sha1 (str): Hex sha1 of the object

        Returns:
            GitObject: The blob

        Raises:
            KeyError: If the stub does not hold it
        """
        content = self._objects[sha1]
        return GitObject(type=BLOB, size=len(content), data=memoryview(content))


@pytest.fixture
def folder():
    """
    A folder of the test, removed afterwards.

    Yields:
        PathStr: The folder
    """
    root = PathStr.new(env.ALASIO_TEMP).joinpath(f'git_genidx_{secrets.token_hex(8)}')
    root.makedirs(exist_ok=True)
    try:
        yield root
    finally:
        git_server.remove_folder(root)


def git_index_pack(folder, pack_data, name='git'):
    """
    The idx git itself writes for a pack.

    Args:
        folder (PathStr): Folder of the test
        pack_data (bytes): The pack
        name (str): Base name of the two files. Defaults to 'git'.

    Returns:
        bytes: The idx file
    """
    pack_file = folder.joinpath(f'{name}.pack')
    idx_file = folder.joinpath(f'{name}.idx')
    pack_file.atomic_write(pack_data)
    git_server.git('index-pack', '-o', idx_file, pack_file)
    return idx_file.atomic_read_bytes()


def read_idx(folder, pack_data, idx_data, name='check'):
    """
    Read an idx back with the reader of the project.

    `IdxFile.idx_read()` checks the header, the tables and the trailing sha1,
    so a returned mapping is an idx a client can use.

    Args:
        folder (PathStr): Folder of the test
        pack_data (bytes): The pack the idx belongs to
        idx_data (bytes): The idx to read
        name (str): Base name of the two files. Defaults to 'check'.

    Returns:
        dict[str, tuple[int, int]]: {hex sha1: (start, end)} of every entry
    """
    pack_file = folder.joinpath(f'{name}.pack')
    idx_file = folder.joinpath(f'{name}.idx')
    pack_file.atomic_write(pack_data)
    idx_file.atomic_write(idx_data)
    reader = IdxFile(pack_file)
    reader.idx_read()
    return reader.dict_offset


class TestPackToIdxWhenFullObjects:
    def test_a_pack_without_deltas(self, folder):
        """A pack of full objects is indexed as it stands."""
        first = b'first content\n' * 20
        second = b'second content\n' * 20

        pack = PackBuilder()
        pack.add_full(BLOB, first)
        pack.add_full(BLOB, second)
        data = pack.build()

        idx = GenIdx(folder).pack_to_idx(data)

        assert idx == git_index_pack(folder, data)
        assert set(read_idx(folder, data, idx)) == {blob_sha1(first), blob_sha1(second)}

    def test_an_empty_pack(self, folder):
        """
        A pack of no object is still an idx of no entry.

        The idx is compared with the one git writes rather than read back:
        `IdxFile` refuses an empty sha1 table, a pack of no object being a
        shape no client sends.
        """
        data = PackBuilder().build()

        idx = GenIdx(folder).pack_to_idx(data)

        assert idx == git_index_pack(folder, data)
        # magic, fanout, and the two checksums, with no table between them
        assert len(idx) == 8 + 1024 + 20 + 20


class TestPackToIdxWhenOfsDelta:
    def test_a_chain_of_ofs_deltas(self, folder):
        """A chain of ofs-deltas resolves backwards through the pack."""
        first = b'a' * 200
        second = first + b'b'
        third = second + b'c'

        pack = PackBuilder()
        offset_first = pack.add_full(BLOB, first)
        offset_second = pack.add_ofs_delta(offset_first, make_delta(first, second))
        pack.add_ofs_delta(offset_second, make_delta(second, third))
        data = pack.build()

        idx = GenIdx(folder).pack_to_idx(data)

        assert idx == git_index_pack(folder, data)
        assert set(read_idx(folder, data, idx)) == {blob_sha1(first), blob_sha1(second), blob_sha1(third)}


class TestPackToIdxWhenRefDelta:
    def test_a_ref_delta_whose_base_is_in_the_pack(self, folder):
        """
        The base of a ref-delta is looked up in the pack itself.

        This is the shape `git repack` writes, and a reader that only holds the
        bytes of the pack has to index it without a repository behind.
        """
        base = b'content of the base\n' * 20
        target = base + b'appended\n'

        pack = PackBuilder()
        pack.add_full(BLOB, base)
        pack.add_ref_delta(blob_sha1(base), make_delta(base, target))
        data = pack.build()

        idx = GenIdx(folder).pack_to_idx(data)

        assert idx == git_index_pack(folder, data)
        assert set(read_idx(folder, data, idx)) == {blob_sha1(base), blob_sha1(target)}

    def test_a_ref_delta_before_its_base(self, folder):
        """A ref-delta may name a base that the pack holds later."""
        base = b'content of a later base\n' * 20
        target = base + b'appended\n'

        pack = PackBuilder()
        pack.add_ref_delta(blob_sha1(base), make_delta(base, target))
        pack.add_full(BLOB, base)
        data = pack.build()

        idx = GenIdx(folder).pack_to_idx(data)

        assert idx == git_index_pack(folder, data)
        assert set(read_idx(folder, data, idx)) == {blob_sha1(base), blob_sha1(target)}

    def test_a_ref_delta_on_a_delta_that_comes_later(self, folder):
        """
        A chain that needs more than one pass.

        The first entry names the second one, so neither can be resolved while
        the parse walks the pack, and the entry that the second one names is
        itself a delta on a full object: the chain is only complete after two
        rounds.
        """
        base = b'base content\n' * 20
        middle = base + b'middle\n'
        top = middle + b'top\n'

        pack = PackBuilder()
        pack.add_ref_delta(blob_sha1(middle), make_delta(middle, top))
        pack.add_ref_delta(blob_sha1(base), make_delta(base, middle))
        pack.add_full(BLOB, base)
        data = pack.build()

        idx = GenIdx(folder).pack_to_idx(data)

        assert idx == git_index_pack(folder, data)
        assert set(read_idx(folder, data, idx)) == {blob_sha1(base), blob_sha1(middle), blob_sha1(top)}


class TestPackToIdxWhenMixedChain:
    def test_an_ofs_delta_on_a_ref_delta(self, folder):
        """
        An ofs-delta whose base is a ref-delta.

        The old pipeline's packs hold this shape, and an ofs-delta is only
        resolvable once the ref-delta it points at is resolved: the deferral of
        one kind of delta has to cascade into the other kind.
        """
        base = b'base content\n' * 20
        middle = base + b'middle\n'
        top = middle + b'top\n'

        pack = PackBuilder()
        offset_middle = pack.add_ref_delta(blob_sha1(base), make_delta(base, middle))
        pack.add_ofs_delta(offset_middle, make_delta(middle, top))
        pack.add_full(BLOB, base)
        data = pack.build()

        idx = GenIdx(folder).pack_to_idx(data)

        assert idx == git_index_pack(folder, data)
        assert set(read_idx(folder, data, idx)) == {blob_sha1(base), blob_sha1(middle), blob_sha1(top)}


class TestPackToIdxWhenThin:
    def test_a_base_outside_the_pack_is_refused(self, folder):
        """A pack that names a base it does not hold is a thin pack."""
        base = b'content the pack does not hold\n' * 20
        target = base + b'appended\n'

        pack = PackBuilder()
        pack.add_ref_delta(blob_sha1(base), make_delta(base, target))
        data = pack.build()

        with pytest.raises(PackNotSelfContained):
            GenIdx(folder).pack_to_idx(data)

    def test_the_refusal_names_the_missing_base(self, folder):
        """The message says which base the pack needs, and where."""
        base = b'content the pack does not hold\n' * 20
        target = base + b'appended\n'

        pack = PackBuilder()
        pack.add_ref_delta(blob_sha1(base), make_delta(base, target))
        data = pack.build()

        with pytest.raises(PackNotSelfContained) as caught:
            GenIdx(folder).pack_to_idx(data)

        assert blob_sha1(base) in str(caught.value)
        assert 'offset=12' in str(caught.value)

    def test_allow_external_reads_the_base_from_the_store(self, folder):
        """With allow_external, a thin pack is indexed against the repository."""
        base = b'content the pack does not hold\n' * 20
        target = base + b'appended\n'

        pack = PackBuilder()
        pack.add_ref_delta(blob_sha1(base), make_delta(base, target))
        data = pack.build()

        idx = StubGenIdx({blob_sha1(base): base}).pack_to_idx(data, allow_external=True)

        assert set(read_idx(folder, data, idx)) == {blob_sha1(target)}

    def test_an_ofs_delta_on_an_external_base(self, folder):
        """A chain that leaves the pack and comes back in resolves too."""
        base = b'content the pack does not hold\n' * 20
        middle = base + b'middle\n'
        top = middle + b'top\n'

        pack = PackBuilder()
        offset_middle = pack.add_ref_delta(blob_sha1(base), make_delta(base, middle))
        pack.add_ofs_delta(offset_middle, make_delta(middle, top))
        data = pack.build()

        idx = StubGenIdx({blob_sha1(base): base}).pack_to_idx(data, allow_external=True)

        assert set(read_idx(folder, data, idx)) == {blob_sha1(middle), blob_sha1(top)}


class TestPackToIdxWhenBroken:
    def test_a_truncated_pack(self, folder):
        """A pack that ends before its checksum is not a pack."""
        pack = PackBuilder()
        pack.add_full(BLOB, b'content\n' * 20)
        data = pack.build()

        with pytest.raises(PackBroken):
            GenIdx(folder).pack_to_idx(data[:20])

    def test_a_bad_checksum(self, folder):
        """A pack whose trailing checksum does not match its content is refused."""
        pack = PackBuilder()
        pack.add_full(BLOB, b'content\n' * 20)
        data = bytearray(pack.build())
        data[-1] ^= 0xFF

        with pytest.raises(PackBroken):
            GenIdx(folder).pack_to_idx(bytes(data))

    def test_a_bad_header(self, folder):
        """A file that does not start with a version 2 pack header is refused."""
        with pytest.raises(PackBroken):
            GenIdx(folder).pack_to_idx(b'PACK\x00\x00\x00\x03' + b'\x00' * 20)

    def test_a_base_in_neither_the_pack_nor_the_store(self, folder):
        """allow_external still refuses a pack whose base cannot be found."""
        base = b'a base that nothing holds\n' * 20
        target = base + b'appended\n'

        pack = PackBuilder()
        pack.add_ref_delta(blob_sha1(base), make_delta(base, target))
        data = pack.build()

        with pytest.raises(PackBroken) as caught:
            StubGenIdx({}).pack_to_idx(data, allow_external=True)
        # the pack holds the entry, the failure is that its base is nowhere
        assert not isinstance(caught.value, PackNotSelfContained)


class TestPackToIdxWhenInput:
    def test_bytes_and_memoryview_give_the_same_idx(self, folder):
        """The pack may be passed as bytes or as a memoryview of the same bytes."""
        base = b'base content\n' * 20
        target = base + b'target\n'

        pack = PackBuilder()
        pack.add_full(BLOB, base)
        pack.add_ref_delta(blob_sha1(base), make_delta(base, target))
        data = pack.build()

        from_bytes = GenIdx(folder).pack_to_idx(data)
        from_view = GenIdx(folder).pack_to_idx(memoryview(data))

        assert from_bytes == from_view
        assert from_bytes == git_index_pack(folder, data)
