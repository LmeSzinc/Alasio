"""
Tests for AsyncGitClient: the retry of a git:// exchange.

The exchange of a real server is covered by test_fetch_git.py, which talks to
`git daemon`. These tests cover what a daemon cannot be asked to do on purpose:
fail one attempt, and accept the next one.
"""
from collections import deque
from hashlib import sha1

import pytest
import trio

from alasio.git.fetch.client_git import AsyncGitClient, parse_refs
from alasio.git.fetch.pkt import FetchPayload, create_pkt_line
from alasio.git.file.exception import PackBroken, PktLineBroken
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401

pytestmark = pytest.mark.parallel

HOST = '127.0.0.1'
PORT = 9418
PATH = '/server.git'
URL = f'git://{HOST}:{PORT}{PATH}'

# The bytes of an empty pack: a header that announces no object at all
EMPTY_PACK = b'PACK\x00\x00\x00\x02\x00\x00\x00\x00'
# The sha1 a server advertises its only branch with
SHA = b'a' * 40

ADVERTISEMENT = create_pkt_line(SHA + b' refs/heads/master\x00multi_ack thin-pack\n') + b'0000'


def build_pack(body=EMPTY_PACK):
    """
    Build the bytes of a pack: a body closed by the sha1 of that body.

    Args:
        body (bytes): The pack without its trailing checksum

    Returns:
        bytes: The pack
    """
    return body + sha1(body).digest()


def side_band(data, size=64):
    """
    Frame the bytes of a pack the way a server sends them.

    Args:
        data (bytes): Bytes to send
        size (int): Bytes per pkt-line

    Returns:
        bytes: The channel 1 pkt-lines of the pack, closed by a flush-pkt
    """
    lines = (create_pkt_line(b'\x01' + data[offset:offset + size]) for offset in range(0, len(data), size))
    return b''.join(lines) + b'0000'


def build_payload():
    """
    Build the request of one fetch.

    Returns:
        FetchPayload: The request
    """
    payload = FetchPayload()
    payload.add_line(f'want {SHA.decode()}')
    payload.add_delimiter()
    payload.add_done()
    return payload


class FakeServer:
    """
    A git:// server that answers one connection with prepared bytes.

    The advertisement is answered as soon as the handshake arrives, the pack
    only once the request of the transfer arrived, the way a real server talks.

    Attributes:
        sent (list[bytes]): What the client sent on this connection
    """

    def __init__(self, advertisement=ADVERTISEMENT, answer=b'', error=None):
        """
        Args:
            advertisement (bytes): The ref advertisement of the server
            answer (bytes): The answer of the request, sent after it arrived
            error (Exception): Raised on every read instead, for a server that
                fails before it says anything
        """
        self.advertisement = advertisement
        self.answer = answer
        self.error = error
        self.sent = []
        self.pending = bytearray(advertisement)

    async def send_all(self, data):
        """Take one request of the client."""
        self.sent.append(bytes(data))
        if len(self.sent) == 2:
            # the pack comes after the request of the transfer
            self.pending.extend(self.answer)

    async def receive_some(self, size):
        """Answer one read with the bytes of the server."""
        if self.error is not None:
            raise self.error
        chunk = bytes(self.pending[:size])
        del self.pending[:size]
        return chunk

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class Connections:
    """
    The servers the client opens, in the order it opens them.

    Attributes:
        servers (list[FakeServer]): Servers waiting for a connection
        opened (list[tuple]): Address of every connection the client opened
    """

    def __init__(self):
        self.servers = deque()
        self.opened = []

    def add(self, server):
        """
        Args:
            server (FakeServer): Server to serve the next connection with
        """
        self.servers.append(server)


@pytest.fixture
def connections(monkeypatch):
    """
    Replace the tcp connections of the client with prepared servers.

    Args:
        monkeypatch (pytest.MonkeyPatch): Patcher of the test

    Yields:
        Connections: The servers of the test
    """
    servers = Connections()

    async def open_tcp_stream(host, port):
        servers.opened.append((host, port))
        return servers.servers.popleft()

    monkeypatch.setattr(trio, 'open_tcp_stream', open_tcp_stream)
    yield servers


class TestRetryErrors:
    """The failures of a git:// transfer that another attempt may fix."""

    def test_a_connection_failure_is_retried(self):
        """A refused or dropped connection is a failed attempt."""
        assert OSError in AsyncGitClient.retry_errors()

    def test_a_broken_pack_is_retried(self):
        """A reset can lose the tail of a pack without an error on the socket,
        so the broken pack itself has to be a retryable failure."""
        assert PackBroken in AsyncGitClient.retry_errors()

    def test_a_cut_pkt_line_is_retried(self):
        """A reset can cut the answer in the middle of a line as well, and that
        is a failed attempt too."""
        assert PktLineBroken in AsyncGitClient.retry_errors()

    def test_a_failure_of_the_protocol_is_not_retried(self):
        """A failure that a fresh connection cannot fix is not in the set."""
        assert ValueError not in AsyncGitClient.retry_errors()


class TestFetchPack:
    """A pack is fetched again when an attempt did not bring it whole."""

    @pytest.mark.trio
    async def test_a_truncated_pack_is_fetched_again(self, connections, autojump_clock):
        """The first connection loses the tail of the pack, the second one
        serves it: the caller receives the whole pack."""
        pack = build_pack(EMPTY_PACK + b'x' * 200)
        connections.add(FakeServer(answer=side_band(pack[:-100])))
        connections.add(FakeServer(answer=side_band(pack)))

        client = AsyncGitClient(HOST, PORT, PATH)
        with logger.mock_capture_writer() as capture:
            data = await client.fetch_pack(build_payload())

        assert data == pack
        assert len(connections.opened) == 2
        assert capture.fd.any_contains(f'Request to "{URL}" failed (attempt 1/3): PackBroken: Pack checksum')

    @pytest.mark.trio
    async def test_every_attempt_sends_the_request_again(self, connections, autojump_clock):
        """A pack cannot be resumed, so every attempt runs the whole exchange on
        a connection of its own."""
        pack = build_pack(EMPTY_PACK + b'x' * 200)
        first = FakeServer(answer=side_band(pack[:-100]))
        second = FakeServer(answer=side_band(pack))
        connections.add(first)
        connections.add(second)

        client = AsyncGitClient(HOST, PORT, PATH)
        await client.fetch_pack(build_payload())

        payload = build_payload().build()
        assert len(first.sent) == 2
        assert b'git-upload-pack /server.git\x00host=127.0.0.1\x00' in first.sent[0]
        assert first.sent[1] == payload
        assert second.sent[1] == payload

    @pytest.mark.trio
    async def test_the_failures_are_waited_between(self, connections, autojump_clock):
        """Every attempt of a transfer is tried, and the waits of the backoff
        happened before the retries."""
        pack = build_pack(EMPTY_PACK + b'x' * 200)
        connections.add(FakeServer(answer=side_band(pack[:-100])))
        connections.add(FakeServer(answer=side_band(pack[:-100])))
        connections.add(FakeServer(answer=side_band(pack)))

        client = AsyncGitClient(HOST, PORT, PATH)
        start = trio.current_time()
        with logger.mock_capture_writer() as capture:
            await client.fetch_pack(build_payload())

        assert len(connections.opened) == 3
        assert trio.current_time() - start == 2 * AsyncGitClient.RETRY_BACKOFF
        assert capture.fd.any_contains('(attempt 1/3)')
        assert capture.fd.any_contains('(attempt 2/3)')

    @pytest.mark.trio
    async def test_the_last_failure_is_raised(self, connections, autojump_clock):
        """A server that always truncates the pack fails the transfer with the
        failure of the last attempt."""
        pack = build_pack(EMPTY_PACK + b'x' * 200)
        for _ in range(3):
            connections.add(FakeServer(answer=side_band(pack[:-100])))

        client = AsyncGitClient(HOST, PORT, PATH)
        with pytest.raises(PackBroken):
            await client.fetch_pack(build_payload())

        assert len(connections.opened) == 3

    @pytest.mark.trio
    async def test_a_failure_outside_the_set_is_raised_at_once(self, connections, autojump_clock):
        """An error a fresh connection cannot fix is raised as it is, after one
        connection, and it is logged like any failed attempt."""
        connections.add(FakeServer(error=ValueError('a bug of the protocol')))

        client = AsyncGitClient(HOST, PORT, PATH)
        with logger.mock_capture_writer() as capture:
            with pytest.raises(ValueError, match='a bug of the protocol'):
                await client.fetch_pack(build_payload())

        assert len(connections.opened) == 1
        assert capture.fd.any_contains('failed (attempt 1/3): ValueError: a bug of the protocol')

    @pytest.mark.trio
    async def test_a_single_shot_transfer_is_not_retried(self, connections, autojump_clock):
        """A caller that asks for one attempt gets one attempt."""
        pack = build_pack(EMPTY_PACK + b'x' * 200)
        connections.add(FakeServer(answer=side_band(pack[:-100])))

        client = AsyncGitClient(HOST, PORT, PATH)
        with pytest.raises(PackBroken):
            await client.fetch_pack(build_payload(), attempts=1)

        assert len(connections.opened) == 1

    @pytest.mark.trio
    async def test_the_pack_is_written_by_the_attempt_that_completed_it(self, connections, autojump_clock, fs):
        """The file of a fetch holds the whole pack, written by the attempt that
        brought it: the failed attempt wrote nothing."""
        fs.create_file('/work/keep.txt', contents='')
        pack = build_pack(EMPTY_PACK + b'x' * 200)
        connections.add(FakeServer(answer=side_band(pack[:-100])))
        connections.add(FakeServer(answer=side_band(pack)))

        client = AsyncGitClient(HOST, PORT, PATH)
        data = await client.fetch_pack(build_payload(), output_file='/work/initial.pack')

        assert data == pack
        with open('/work/initial.pack', 'rb') as f:
            assert f.read() == pack

    @pytest.mark.trio
    async def test_a_pack_that_never_arrived_whole_is_not_written(self, connections, autojump_clock, fs):
        """A fetch that failed every attempt leaves no file behind, not a
        truncated pack that fails much later."""
        fs.create_file('/work/keep.txt', contents='')
        pack = build_pack(EMPTY_PACK + b'x' * 200)
        for _ in range(3):
            connections.add(FakeServer(answer=side_band(pack[:-100])))

        client = AsyncGitClient(HOST, PORT, PATH)
        with pytest.raises(PackBroken):
            await client.fetch_pack(build_payload(), output_file='/work/initial.pack')

        with pytest.raises(FileNotFoundError):
            open('/work/initial.pack', 'rb')


class TestRefs:
    """The advertisement of a server and its parsing."""

    @pytest.mark.trio
    async def test_the_advertisement_is_parsed(self, connections):
        """The refs of the server are read from the advertisement."""
        connections.add(FakeServer())

        client = AsyncGitClient(HOST, PORT, PATH)
        assert await client.refs() == {SHA: b'refs/heads/master'}

    def test_the_capabilities_of_a_line_are_stripped(self):
        """The capabilities a server appends to its first ref belong to the
        advertisement, not to the name of the ref."""
        content = create_pkt_line(SHA + b' refs/heads/master\x00multi_ack thin-pack\n') + b'0000'
        assert parse_refs(content) == {SHA: b'refs/heads/master'}

    def test_a_line_without_a_ref_is_skipped(self):
        """The HEAD line of an advertisement names no ref of the repository."""
        content = create_pkt_line(SHA + b' HEAD\x00multi_ack\n') + b'0000'
        assert parse_refs(content) == {}

    def test_a_short_sha1_is_skipped(self):
        """A line that does not carry a full object id is not a ref."""
        content = create_pkt_line(b'abc refs/heads/master\n') + b'0000'
        with logger.mock_capture_writer() as capture:
            assert parse_refs(content) == {}
        assert capture.fd.any_contains('Unexpected sha1 length')

    @pytest.mark.trio
    async def test_a_cut_advertisement_is_fetched_again(self, connections, autojump_clock):
        """An advertisement that stops in the middle of a line is a transfer cut
        short, so the refs are read again on a fresh connection instead of
        being handed back half read."""
        connections.add(FakeServer(advertisement=ADVERTISEMENT[:30]))
        connections.add(FakeServer())

        client = AsyncGitClient(HOST, PORT, PATH)
        with logger.mock_capture_writer() as capture:
            refs = await client.refs()

        assert refs == {SHA: b'refs/heads/master'}
        assert len(connections.opened) == 2
        assert capture.fd.any_contains('(attempt 1/3): PktLineBroken')

    @pytest.mark.trio
    async def test_an_advertisement_that_never_completed_fails_the_refs(self, connections, autojump_clock):
        """A server that always truncates its advertisement fails the call, it
        does not hand back the refs it managed to say."""
        for _ in range(3):
            connections.add(FakeServer(advertisement=ADVERTISEMENT[:30]))

        client = AsyncGitClient(HOST, PORT, PATH)
        with pytest.raises(PktLineBroken):
            await client.refs()

        assert len(connections.opened) == 3
