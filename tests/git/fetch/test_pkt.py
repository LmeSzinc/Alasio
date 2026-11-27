"""
Tests for the pkt-line and pack readers of alasio.git.fetch.pkt.

The readers are the only place that sees the bytes of a transfer, so they are
also where a transfer cut short is caught. A server may reset the connection
after windows discarded the bytes still unread in the receive buffer: the
client sees a clean end of the stream and a pack that lost its tail, and the
trailing sha1 of the pack is the only witness of the loss.
"""
from hashlib import sha1

import pytest
import trio

from alasio.git.fetch.pkt import (
    PACK_CHECKSUM_SIZE, agather_bytes, aparse_packfile_stream, aparse_pkt_line, create_pkt_line, parse_pkt_line,
    receive_chunks
)
from alasio.git.file.exception import PackBroken, PktLineBroken

pytestmark = pytest.mark.parallel

# The bytes of an empty pack: a header that announces no object at all
EMPTY_PACK = b'PACK\x00\x00\x00\x02\x00\x00\x00\x00'


class FakeStream:
    """A stream that hands out prepared answers, the way a socket does."""

    def __init__(self, answers):
        """
        Args:
            answers (list): Chunks to hand out; an exception of the list is
                raised instead of returned, and the list runs out after it
        """
        self.answers = list(answers)
        self.reads = 0

    async def receive_some(self, size):
        """Answer one read, or fail the way a closed socket does."""
        self.reads += 1
        if not self.answers:
            return b''
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


async def collect(stream):
    """
    Gather what receive_chunks() yields.

    Args:
        stream (FakeStream): The stream to read

    Returns:
        list: The chunks
    """
    return [chunk async for chunk in receive_chunks(stream)]


def build_pack(body=EMPTY_PACK):
    """
    Build the bytes of a pack: a body closed by the sha1 of that body.

    Args:
        body (bytes): The pack without its trailing checksum

    Returns:
        bytes: The pack
    """
    return body + sha1(body).digest()


def side_band(data, size):
    """
    Frame the bytes of a pack the way a server sends them: one channel 1
    pkt-line per slice, then the flush-pkt that closes the answer.

    Args:
        data (bytes): Bytes to send
        size (int): Bytes per pkt-line

    Returns:
        list[bytes]: One pkt-line payload per slice, channel byte included
    """
    return [b'\x01' + data[offset:offset + size] for offset in range(0, len(data), size)]


async def agather_lines(iterator):
    """
    Gather what a pkt-line iterator yields, so a raise reaches the test.

    Args:
        iterator: The pkt-line iterator

    Returns:
        list: The lines
    """
    return [line async for line in iterator]


async def read_pack(lines):
    """
    Read a pack out of pkt-line payloads.

    Args:
        lines (list[bytes]): Payloads of the stream

    Returns:
        bytes: The pack the reader gathered
    """

    async def stream():
        for line in lines:
            yield line

    return await agather_bytes(aparse_packfile_stream(stream()))


class TestReceiveChunks:
    """Tests for receive_chunks()."""

    @pytest.mark.trio
    async def test_stops_on_a_clean_close(self):
        """An empty read is the end of the stream."""
        stream = FakeStream([b'abc', b'def', b''])
        assert await collect(stream) == [b'abc', b'def']

    @pytest.mark.trio
    async def test_a_reset_is_the_end_of_the_stream(self):
        """A reset after the last byte is the end, not an error."""
        stream = FakeStream([b'abc', trio.BrokenResourceError('reset')])
        assert await collect(stream) == [b'abc']

    @pytest.mark.trio
    async def test_a_closed_resource_is_the_end_of_the_stream(self):
        """A stream closed by this side ends the reading the same way."""
        stream = FakeStream([b'abc', trio.ClosedResourceError('closed')])
        assert await collect(stream) == [b'abc']

    @pytest.mark.trio
    async def test_a_reset_before_any_byte(self):
        """A server that drops the connection sends nothing, and nothing is kept."""
        stream = FakeStream([trio.BrokenResourceError('reset')])
        assert await collect(stream) == []

    @pytest.mark.trio
    async def test_another_error_is_not_swallowed(self):
        """Only the end of a stream is tolerated, a real failure is raised."""
        stream = FakeStream([trio.BusyResourceError('busy')])
        with pytest.raises(trio.BusyResourceError):
            await collect(stream)


class TestPackChecksum:
    """The trailing sha1 of a pack is checked while the pack streams."""

    @pytest.mark.parametrize('size', [1, 2, 19, 20, 21, 64, 1000])
    @pytest.mark.trio
    async def test_a_complete_pack_is_returned(self, size):
        """The reader gives back exactly the bytes of a complete pack, at every
        boundary a server may split them at: the 20 bytes of the checksum are
        kept out of the hasher across the split."""
        pack = build_pack(EMPTY_PACK + b'x' * 200)
        assert await read_pack(side_band(pack, size)) == pack

    @pytest.mark.trio
    async def test_a_pack_cut_short_raises(self):
        """A stream that ends before the pack does is a broken pack."""
        pack = build_pack(EMPTY_PACK + b'x' * 200)
        with pytest.raises(PackBroken, match='checksum mismatch'):
            await read_pack(side_band(pack[:-100], 64))

    @pytest.mark.trio
    async def test_a_pack_cut_inside_its_checksum_raises(self):
        """A tail that lost part of its checksum is not a pack either."""
        pack = build_pack(EMPTY_PACK + b'x' * 200)
        with pytest.raises(PackBroken, match='checksum mismatch'):
            await read_pack(side_band(pack[:-5], 64))

    @pytest.mark.trio
    async def test_a_stream_shorter_than_a_checksum_raises(self):
        """A stream that never held the checksum of a pack is truncated."""
        with pytest.raises(PackBroken, match='truncated'):
            await read_pack(side_band(b'PACK', 64))

    @pytest.mark.trio
    async def test_a_pack_damaged_in_the_transfer_raises(self):
        """Bytes flipped on the way do not match the checksum of the pack."""
        pack = bytearray(build_pack(EMPTY_PACK + b'x' * 200))
        pack[10] ^= 0xFF
        with pytest.raises(PackBroken, match='checksum mismatch'):
            await read_pack(side_band(bytes(pack), 64))

    @pytest.mark.trio
    async def test_the_checksum_of_a_pack_is_accepted_after_a_damaged_one(self):
        """A reader that raised keeps nothing: the next pack of the same
        connection state is read on its own."""
        pack = build_pack(EMPTY_PACK + b'x' * 64)
        with pytest.raises(PackBroken):
            await read_pack(side_band(pack[:-30], 16))
        assert await read_pack(side_band(pack, 16)) == pack

    @pytest.mark.trio
    async def test_progress_without_a_pack_raises(self):
        """A server that only sent progress sent no pack at all."""
        with pytest.raises(PackBroken, match='truncated'):
            await read_pack([b'\x02Enumerating objects: 1, done.\n'])

    @pytest.mark.trio
    async def test_progress_between_the_chunks_does_not_break_the_checksum(self):
        """A progress line of the side band is not pack data, and the checksum
        covers the pack only."""
        pack = build_pack(EMPTY_PACK + b'x' * 200)
        lines = side_band(pack, 64)
        lines.insert(1, b'\x02Counting objects: 100% (1/1), done.\n')
        assert await read_pack(lines) == pack

    @pytest.mark.trio
    async def test_the_whole_chain_from_raw_bytes(self):
        """The bytes a server writes, framed as pkt-lines and split at
        arbitrary offsets, are read back as the pack they were."""
        pack = build_pack(EMPTY_PACK + b'x' * 200)
        raw = b''.join(create_pkt_line(line) for line in side_band(pack, 64)) + b'0000'
        chunks = [raw[offset:offset + 7] for offset in range(0, len(raw), 7)]

        async def stream():
            for chunk in chunks:
                yield chunk

        assert await agather_bytes(aparse_packfile_stream(aparse_pkt_line(stream()))) == pack

    def test_the_checksum_is_the_twenty_bytes_of_a_sha1(self):
        """The reader keeps exactly the bytes a pack closes with."""
        assert PACK_CHECKSUM_SIZE == 20


class TestPktLineTruncation:
    """A pkt-line stream that ends in the middle of a line was cut short."""

    @pytest.mark.trio
    async def test_a_cut_data_line_is_raised(self):
        """A header that promises more than the stream brought is not a line."""
        stream = FakeStream([create_pkt_line(b'abcdef') + b'000a' + b'ghij'])
        with pytest.raises(PktLineBroken, match='in the middle of a line'):
            await agather_lines(aparse_pkt_line(receive_chunks(stream)))

    @pytest.mark.trio
    async def test_a_cut_length_header_is_raised(self):
        """A stream that ends inside a length header cannot be continued."""
        stream = FakeStream([create_pkt_line(b'abcdef') + b'00'])
        with pytest.raises(PktLineBroken, match='in the middle of a line'):
            await agather_lines(aparse_pkt_line(receive_chunks(stream)))

    @pytest.mark.trio
    async def test_a_flush_pkt_ends_the_stream_cleanly(self):
        """The flush-pkt is the end of a pkt-line stream, and nothing is left."""
        stream = FakeStream([create_pkt_line(b'abcdef') + b'0000'])
        assert await agather_lines(aparse_pkt_line(receive_chunks(stream))) == [b'abcdef', b'']

    def test_a_cut_payload_of_the_sync_parser_is_raised(self):
        """The sync parser rejects a payload that stops short too."""
        with pytest.raises(PktLineBroken, match='in the middle of a line'):
            list(parse_pkt_line(b'0010refs/hea'))

    def test_a_cut_length_header_of_the_sync_parser_is_raised(self):
        """A partial length header is a transfer cut short there as well."""
        with pytest.raises(PktLineBroken, match='length header'):
            list(parse_pkt_line(b'00'))

    def test_the_sync_parser_accepts_a_line_boundary(self):
        """A content that ends after a whole line needs no flush-pkt to be read."""
        content = create_pkt_line(b'refs/heads/master')
        assert list(parse_pkt_line(content)) == [b'refs/heads/master']
