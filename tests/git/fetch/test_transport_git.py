"""
Tests for the reading side of GitTransport, see alasio.git.fetch.transport_git.
"""
import pytest
import trio

from alasio.git.fetch.transport_git import receive_chunks

pytestmark = pytest.mark.parallel


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
