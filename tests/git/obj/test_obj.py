"""
Tests for the GitObject / GitLooseObject object model, the decoded contract.

decoded() of a blob is always bytes, no matter how the object was stored:
the pack plain path, the pack delta path and the loose path all return bytes.
The callers scan (``b'\\x00' in content``), decode or compress the content, a
memoryview makes the scan ~600x slower and has no decode(), see
doc/2026-09-27_update-pack-from-repo.md section 7.10.4.
"""
import zlib

from alasio.git.obj.obj import GitLooseObject, GitObject


def make_plain_blob(content):
    """
    Build a pack blob object, the object data is the zlib compressed content

    Args:
        content (bytes): Blob content

    Returns:
        GitObject: Object of type 3 (blob)
    """
    return GitObject(type=3, size=len(content), data=memoryview(zlib.compress(content)))


def make_ofs_delta(source_content, result_content, instructions):
    """
    Build an OFS_DELTA object for the given delta instructions

    A pack delta object holds ``[offset varint][zlib compressed delta]``, the
    delta itself is ``[source_size varint][result_size varint][instructions]``.
    Both sizes are below 128 here, so they are one byte each.

    Args:
        source_content (bytes): Content the delta is applied on
        result_content (bytes): Expected content after the delta
        instructions (bytes): Delta instructions

    Returns:
        GitObject: Object of type 6 (OFS_DELTA)
    """
    payload = bytes([len(source_content), len(result_content)]) + instructions
    # offset 1 is a single byte, the delta follows compressed
    return GitObject(
        type=6, size=len(result_content), data=memoryview(bytes([1]) + zlib.compress(payload)))


class TestGitObjectDecodedBlob:
    """A pack blob returns bytes from decoded(), plain or delta."""

    def test_plain_blob_decoded_is_bytes(self):
        content = b'hello world\n'
        obj = make_plain_blob(content)
        decoded = obj.decoded
        assert isinstance(decoded, bytes)
        assert decoded == content
        # the buffer used by the delta chains stays a memoryview
        assert isinstance(obj.data, memoryview)

    def test_delta_blob_decoded_is_bytes(self):
        """The delta path must return bytes too, not a memoryview."""
        source_content = b'hello world'
        result_content = b'hello world!\n'
        instructions = (
            # insert 6 bytes
            bytes([6]) + b'hello '
            # copy 5 bytes from offset 6
            + bytes([0x91, 6, 5])
            # insert 2 bytes
            + bytes([2]) + b'!\n'
        )
        source = make_plain_blob(source_content)
        # GitObjectManager.cat() publishes a decoded source, resolved_from()
        # reads its data plain
        _ = source.decoded
        delta = make_ofs_delta(source_content, result_content, instructions)
        obj = delta.resolved_from(source)
        assert obj.type == 3
        decoded = obj.decoded
        assert isinstance(decoded, bytes)
        assert decoded == result_content
        # the buffer used by the delta chains stays a memoryview
        assert isinstance(obj.data, memoryview)
        assert bytes(obj.data) == result_content


class TestGitObjectDecodedTree:
    """A non-blob returns its parse result, never bytes."""

    def test_tree_decoded_is_parse_result(self):
        entry = b'100644 assets.py\x00' + b'\x01' * 20
        obj = GitObject(type=2, size=len(entry), data=memoryview(zlib.compress(entry)))
        decoded = obj.decoded
        assert not isinstance(decoded, (bytes, memoryview))
        assert decoded[0].name == 'assets.py'


class TestGitLooseObjectDecodedBlob:
    """A loose blob returns bytes from decoded()."""

    def test_loose_blob_decoded_is_bytes(self):
        content = b'data\n'
        obj = GitLooseObject(type=3, size=len(content), data=content)
        decoded = obj.decoded
        assert isinstance(decoded, bytes)
        assert decoded == content
