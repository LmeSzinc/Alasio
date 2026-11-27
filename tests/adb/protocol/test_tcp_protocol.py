"""
Tests for the tcp transport of the adb protocol.

The protocol is checked against `fake_adbd`, a device that speaks the wire
format of a real adbd and records what the client sends it.
"""

import socket
import threading
import time

import pytest

import alasio.adb.protocol.tcp_protocol as tcp_protocol
from alasio.adb.protocol import keygen
from alasio.adb.protocol.const import (
    ADB_MAX_PAYLOAD, ADB_MAX_PAYLOAD_V1, ADB_STREAM_CHUNK_LIMIT, ADB_STREAM_RECV_BUFFER_SIZE, ADB_VERSION,
    ADB_VERSION_SKIP_CHECKSUM, CLSE, CNXN, OKAY, OPEN, WRTE, AdbAuthKeyMissing, AdbConnectionClosed,
    AdbConnectionTimeout, AdbMessageInvalid, AdbStreamClosed, AdbStreamTimeout, ShellResult
)
from alasio.adb.protocol.tcp_protocol import MESSAGE_STRUCT, AdbProtocolTCP, AdbStreamTCP
from alasio.logger import logger
from tests.adb.protocol.fake_adbd import FakeAdbd, ShellReply, encode_message

pytestmark = [
    pytest.mark.parallel,
]

# The builtin the module under test calls for the checksum of a payload, kept
# before a test replaces the name inside the module
_BUILTIN_SUM = sum


def counting_sum(calls):
    """
    Build a sum() that records the length of every bytes handed to it.

    The module computes the checksum of a payload with sum(). A test replaces
    the name inside the module with this one, to tell whether the pass over the
    payload happened at all.

    Args:
        calls (list): List the lengths are appended to

    Returns:
        callable: A sum() that records the call and adds up
    """

    def counted(iterable, *args):
        calls.append(len(iterable))
        return _BUILTIN_SUM(iterable, *args)

    return counted


class _Protocol:
    """A stream only asks its protocol to close it and to acknowledge writes"""

    def __init__(self):
        self.closed = []
        self.acknowledged = []

    def close_stream(self, stream):
        self.closed.append(stream)

    def acknowledge_stream(self, stream, count=1, sock=None):
        self.acknowledged.append((stream, count))


class _NullSock:
    """A socket that records what the protocol sends and never answers"""

    def __init__(self):
        self.sent = []
        self.closed = False

    def sendall(self, data):
        self.sent.append(data)

    def shutdown(self, how):
        pass

    def close(self):
        self.closed = True


def make_stream(state='opened', recv_buffer_size=ADB_STREAM_RECV_BUFFER_SIZE, protocol=None):
    """
    Build a stream without a connection

    Args:
        state (str): Initial state of the stream
        recv_buffer_size (int): Bytes of output to buffer before the acknowledgement
            of the device is held back
        protocol (AdbProtocolTCP): Protocol the stream talks to, a stand-in that
            only records the calls by default

    Returns:
        AdbStreamTCP: The stream
    """
    if protocol is None:
        protocol = _Protocol()
    stream = AdbStreamTCP(local_id=1, service='shell:echo', protocol=protocol,
                          recv_buffer_size=recv_buffer_size)
    stream.state = state
    return stream


def wake_repeatedly(stream, interval=0.005):
    """
    Wake the reader with a message every interval, like the dispatch thread

    Args:
        stream (AdbStreamTCP): The stream to wake
        interval (float): Seconds between two messages

    Returns:
        callable: Stops the waker and joins its thread
    """
    stop = threading.Event()

    def run():
        while not stop.wait(interval):
            try:
                stream.recv_event.release()
            except RuntimeError:
                pass

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    def stop_it():
        stop.set()
        thread.join(timeout=5)

    return stop_it


def decode_message(raw):
    """
    Decode one message of the adb transport, independently of the client

    Args:
        raw (bytes): Header and payload of a message

    Returns:
        tuple: (command, arg0, arg1, length, checksum, magic, payload)
    """
    command, arg0, arg1, length, checksum, magic = MESSAGE_STRUCT.unpack(raw[:24])
    return command.to_bytes(4, 'little'), arg0, arg1, length, checksum, magic, raw[24:]


def packed(command, arg0, arg1, data=b'', magic=None, checksum=None):
    """
    Build one message the way a device builds it, for the tests of the client

    Args:
        command (bytes): Command name
        arg0 (int): First argument
        arg1 (int): Second argument
        data (bytes): Payload
        magic (int): Magic to write instead of the correct one
        checksum (int): Checksum to write instead of the correct one

    Returns:
        bytes: The message
    """
    return encode_message(command, arg0, arg1, data, magic=magic, checksum=checksum)


def sent_commands(sock):
    """
    Commands of the messages a socket stand-in recorded

    Args:
        sock (_NullSock): Socket that recorded the messages

    Returns:
        list: Commands, in order
    """
    return [decode_message(raw)[0] for raw in sock.sent]


def buffered(stream):
    """
    Bytes waiting in the buffer of a stream

    Args:
        stream (AdbStreamTCP): Stream to read the buffer of

    Returns:
        bytes: Buffered output, in order
    """
    return b''.join(stream._data_chunks)


class TestRecvUntilClose:
    """Reading a stream until the device closes it"""

    def test_returns_the_buffered_data_when_the_stream_closes(self):
        """Every message received before the close is returned in order"""
        stream = make_stream()
        stream.add_data(b'abc')
        stream.add_data(b'def')
        stream.state = 'closed'

        assert stream.recv_until_close(timeout=1) == b'abcdef'

    def test_clears_the_buffer_after_returning_it(self):
        """The buffer is drained, a second read does not repeat the data"""
        stream = make_stream()
        stream.add_data(b'abc')
        stream.state = 'closed'

        assert stream.recv_until_close(timeout=1) == b'abc'
        assert buffered(stream) == b''

    def test_a_closed_stream_needs_no_timeout(self):
        """The data of a closed stream is returned even with a timeout of zero"""
        stream = make_stream()
        stream.add_data(b'abc')
        stream.state = 'closed'

        assert stream.recv_until_close(timeout=0) == b'abc'

    def test_an_empty_closed_stream_returns_nothing(self):
        """A command without output returns empty bytes, not a timeout"""
        stream = make_stream(state='closed')

        assert stream.recv_until_close(timeout=0) == b''

    def test_many_chunks_are_joined_in_order(self):
        """A device sends the output of a command in many writes"""
        stream = make_stream()
        for chunk in (b'a', b'bb', b'ccc'):
            stream.add_data(chunk)
        stream.state = 'closed'

        assert stream.recv_until_close(timeout=1) == b'abbccc'

    def test_a_single_write_comes_back_as_the_same_object(self):
        """The output of one write is not copied on its way through the stream

        A screenshot is a few hundred kilobytes in one write. The reader of the
        connection already built those bytes, so the stream hands that very
        object back instead of copying it into a buffer and out again.
        """
        payload = b'x' * 300_000
        stream = make_stream()
        stream.add_data(payload)
        stream.state = 'closed'

        assert stream.recv_until_close(timeout=0) is payload

    def test_many_writes_are_copied_once(self):
        """A message that spans writes is joined once, not once per write"""
        stream = make_stream()
        stream.add_data(b'x' * 256_000)
        stream.add_data(b'x' * 44_000)
        stream.state = 'closed'

        data = stream.recv_until_close(timeout=0)

        assert len(data) == 300_000
        assert data == b'x' * 300_000

    def test_times_out_when_the_stream_never_closes(self):
        """A stream that keeps sending without closing still times out"""
        stream = make_stream()
        stop = wake_repeatedly(stream)
        try:
            start = time.perf_counter()
            with pytest.raises(AdbStreamTimeout):
                stream.recv_until_close(timeout=0.2)
            elapsed = time.perf_counter() - start
        finally:
            stop()

        assert 0.15 < elapsed < 2.0

    def test_a_message_does_not_stand_for_the_close(self):
        """A signal of one write is not the close, the reader keeps waiting

        The signal is released by a helper thread while the reader waits, which
        is what the dispatch thread does for every write of the device.
        """
        stream = make_stream()
        stream.add_data(b'partial')
        signalled = threading.Event()

        def signal_once():
            # hold the event, then release it: one message of the device
            stream.recv_event.acquire()
            signalled.set()
            time.sleep(0.05)
            stream.recv_event.release()

        thread = threading.Thread(target=signal_once, daemon=True)
        thread.start()
        try:
            assert signalled.wait(timeout=5)
            with pytest.raises(AdbStreamTimeout):
                stream.recv_until_close(timeout=0.2)
        finally:
            thread.join(timeout=5)


class TestReceiveBuffer:
    """The stream holds back the acknowledgement of a device it can not keep up with

    The classic stream protocol carries one write at a time and has no window:
    a device sends the next write only after the previous one was acknowledged.
    Holding that acknowledgement back is what slows a device down. A client
    that acknowledges every write as it arrives is pulled along by the device,
    whatever it does with the output.
    """

    def test_the_default_buffer_is_one_message(self):
        """The default buffer is the payload of one message of adbd"""
        assert ADB_STREAM_RECV_BUFFER_SIZE == 1024 * 1024
        assert make_stream()._recv_buffer_size == ADB_STREAM_RECV_BUFFER_SIZE

    def test_a_write_that_fits_is_acknowledged_at_once(self):
        """Below the buffer size the device is asked for more output"""
        stream = make_stream(recv_buffer_size=10)

        assert stream.add_data(b'1234') is True
        assert buffered(stream) == b'1234'
        assert stream._data_size == 4
        assert stream._okay_debt == 0

    def test_the_write_that_fills_the_buffer_is_held_back(self):
        """A write that reaches the buffer size is stored but not acknowledged"""
        stream = make_stream(recv_buffer_size=10)

        assert stream.add_data(b'1234') is True
        assert stream.add_data(b'5678') is True
        assert stream.add_data(b'90') is False
        assert buffered(stream) == b'1234567890'
        assert stream._data_size == 10
        assert stream._okay_debt == 1

    def test_a_write_larger_than_the_buffer_is_held_back(self):
        """One write of a device can exceed the buffer, it is not split"""
        stream = make_stream(recv_buffer_size=10)

        assert stream.add_data(b'x' * 100) is False
        # the write is kept as one chunk, not cut to fit the buffer
        assert stream._data_chunks == [b'x' * 100]

    def test_a_stored_write_is_not_copied(self):
        """A write goes into the buffer as the object the connection built

        A screenshot is a few hundred kilobytes in one write. Copying it into a
        bytearray on the way in is a copy that buys nothing: the bytes are
        already immutable and the buffer only holds them.
        """
        payload = b'x' * 300_000
        stream = make_stream()

        stream.add_data(payload)

        assert stream._data_chunks[0] is payload

    def test_small_writes_are_joined_once_the_list_grows_long(self):
        """Many small writes do not become one object each in the buffer

        A device can split its output into writes of a few dozen bytes. Keeping
        one object per write would cost more in object headers and list slots
        than the output itself, so the buffer is joined once the list grows long.
        """
        stream = make_stream(recv_buffer_size=1024)

        for _ in range(ADB_STREAM_CHUNK_LIMIT + 1):
            stream.add_data(b'x' * 10)

        assert len(stream._data_chunks) == 1
        assert stream._data_size == (ADB_STREAM_CHUNK_LIMIT + 1) * 10
        assert buffered(stream) == b'x' * ((ADB_STREAM_CHUNK_LIMIT + 1) * 10)

    def test_the_joined_buffer_still_takes_every_byte(self):
        """The join keeps the order and hands the output back in one piece"""
        stream = make_stream(recv_buffer_size=1024)
        writes = [b'a', b'bb', b'ccc'] * (ADB_STREAM_CHUNK_LIMIT)

        for write in writes:
            stream.add_data(write)
        stream.state = 'closed'

        assert stream.recv_until_close(timeout=0) == b''.join(writes)

    def test_the_list_is_left_alone_below_the_limit(self):
        """The output that fits in a few writes is never copied"""
        stream = make_stream(recv_buffer_size=1024)

        for _ in range(ADB_STREAM_CHUNK_LIMIT):
            stream.add_data(b'x')

        assert len(stream._data_chunks) == ADB_STREAM_CHUNK_LIMIT

    def test_the_reader_releases_the_write_it_made_room_for(self):
        """Taking the output acknowledges the write that was held back"""
        stream = make_stream(recv_buffer_size=10)
        stream.add_data(b'1234567890')

        assert stream.take_data() == b'1234567890'
        assert stream.protocol.acknowledged == [(stream, 1)]
        assert stream._okay_debt == 0

    def test_a_release_waits_for_room(self):
        """An acknowledgement is not released into a buffer that is still full"""
        stream = make_stream(recv_buffer_size=10)
        stream.add_data(b'1234567890')

        # the buffer is full, a release now would let the device write into it
        assert stream._payable_debt() == 0
        assert stream._okay_debt == 1

    def test_taking_an_empty_buffer_acknowledges_nothing(self):
        """A reader that wakes without data does not pull in a write"""
        stream = make_stream(recv_buffer_size=10)

        assert stream.take_data() == b''
        assert stream.protocol.acknowledged == []

    def test_output_of_a_failed_read_is_put_back_in_front(self):
        """Output a reader took but could not deliver keeps its place"""
        stream = make_stream(recv_buffer_size=10)
        stream.add_data(b'new')

        stream._restore_data([b'old'])

        assert buffered(stream) == b'oldnew'
        assert stream._data_size == 6

    def test_a_timeout_keeps_the_output_it_took(self):
        """A read that never sees the close returns nothing but loses nothing

        The output is taken while the read waits, so a full buffer can not stall
        the device into a close it never sends. A timeout has to put it back.
        """
        stream = make_stream(recv_buffer_size=10)
        stop = wake_repeatedly(stream)
        try:
            stream.add_data(b'1234567890')
            with pytest.raises(AdbStreamTimeout):
                stream.recv_until_close(timeout=0.1)
        finally:
            stop()

        assert buffered(stream) == b'1234567890'
        assert stream._data_size == 10

    def test_the_close_takes_the_write_that_came_with_it(self):
        """The last write of a device may arrive together with its close"""
        stream = make_stream(recv_buffer_size=10)
        stream.add_data(b'1234567890')
        stream.state = 'closed'

        assert stream.recv_until_close(timeout=0) == b'1234567890'
        assert buffered(stream) == b''


class TestTimeoutClock:
    """The timeout accounting must not read the wall clock

    The remaining timeout is computed by subtracting an elapsed time. A wall
    clock corrected by NTP by 60s would add or remove 60s from a timeout of a
    few hundred milliseconds: the wait would hang for a minute, or give up at
    once. perf_counter() is monotonic, so a correction cannot reach it.
    """

    def test_the_module_does_not_read_the_wall_clock(self):
        """The module keeps no reference to the wall clock"""
        assert not hasattr(tcp_protocol, 'time')

    def test_a_forward_jump_does_not_cut_the_read_short(self, monkeypatch):
        """A forward correction of the wall clock must not end the read early

        The jump is forward because a regression then fails fast instead of
        hanging the suite for a minute.
        """
        if hasattr(tcp_protocol, 'time'):
            monkeypatch.setattr(tcp_protocol, 'time', lambda: time.perf_counter() + 60.0)

        stream = make_stream()
        stop = wake_repeatedly(stream)
        try:
            start = time.perf_counter()
            with pytest.raises(AdbStreamTimeout):
                stream.recv_until_close(timeout=0.2)
            elapsed = time.perf_counter() - start
        finally:
            stop()

        assert elapsed >= 0.15

    def test_a_forward_jump_does_not_cut_the_open_short(self, monkeypatch):
        """A forward correction of the wall clock must not end the open early"""
        if hasattr(tcp_protocol, 'time'):
            monkeypatch.setattr(tcp_protocol, 'time', lambda: time.perf_counter() + 60.0)

        protocol = AdbProtocolTCP()
        protocol._sock = _NullSock()
        protocol.timeout = 0.2

        start = time.perf_counter()
        with pytest.raises(AdbConnectionTimeout):
            protocol.open_stream('shell:echo')
        elapsed = time.perf_counter() - start

        assert elapsed >= 0.15


class TestStreamIds:
    """The local stream ids of a connection are never handed out twice

    A device may still send a message of a stream that ended, and a reused id
    would put that message on the stream of an unrelated caller: a late CLSE
    would close a stream that is reading the output of its command, and a late
    OKAY would open one that the device refused. adbd itself only ever hands out
    the next id of a counter, and so does the client.
    """

    def test_the_first_ids(self):
        """A connection starts at 1"""
        protocol = AdbProtocolTCP()

        assert [protocol._stream_id_allocate() for _ in range(8)] == [1, 2, 3, 4, 5, 6, 7, 8]

    def test_the_counter_keeps_growing(self):
        """The 17th stream gets 17, not the id of a stream that ended"""
        protocol = AdbProtocolTCP()
        allocated = [protocol._stream_id_allocate() for _ in range(40)]

        assert len(set(allocated)) == 40
        assert allocated == list(range(1, 41))

    def test_a_closed_stream_does_not_free_its_id(self):
        """The id of a stream that ended stays out of the pool for good"""
        protocol = AdbProtocolTCP()
        first = protocol._stream_id_allocate()

        assert protocol._stream_id_allocate() == first + 1
        assert protocol._stream_id_allocate() == first + 2

    def test_a_long_lived_connection_never_repeats(self):
        """A thousand streams of one connection are a thousand ids"""
        protocol = AdbProtocolTCP()
        allocated = {protocol._stream_id_allocate() for _ in range(1000)}

        assert len(allocated) == 1000


class TestOpenStream:
    """Opening a stream"""

    def test_times_out_when_the_device_never_answers(self):
        """A device that does not answer OPEN is given up on at the timeout"""
        protocol = AdbProtocolTCP()
        protocol._sock = _NullSock()
        protocol.timeout = 0.2

        start = time.perf_counter()
        with pytest.raises(AdbConnectionTimeout):
            protocol.open_stream('shell:echo')
        elapsed = time.perf_counter() - start

        assert 0.15 < elapsed < 2.0

    def test_sends_open_before_waiting(self):
        """The service name is sent as the payload of OPEN"""
        protocol = AdbProtocolTCP()
        sock = _NullSock()
        protocol._sock = sock
        protocol.timeout = 0.05

        with pytest.raises(AdbConnectionTimeout):
            protocol.open_stream('shell:echo hi')

        assert len(sock.sent) == 1
        command, arg0, arg1, length, checksum, magic, data = decode_message(sock.sent[0])
        assert command == OPEN
        assert arg0 == 1
        assert arg1 == 0
        assert data == b'shell:echo hi\x00'
        assert length == len(data)
        assert checksum == sum(data) & 0xFFFFFFFF
        assert magic == int.from_bytes(OPEN, 'little') ^ 0xFFFFFFFF

    def test_drops_the_stream_when_the_open_fails(self):
        """A failed open leaves no stream behind"""
        protocol = AdbProtocolTCP()
        protocol._sock = _NullSock()
        protocol.timeout = 0.05

        with pytest.raises(AdbConnectionTimeout):
            protocol.open_stream('shell:echo')

        assert protocol._stream_dict == {}

    def test_abandons_the_id_of_a_failed_open(self):
        """The id of a failed open is never handed out again

        The device may still answer the OPEN after the timeout, with an OKAY of
        a stream it created. A reused id would put that answer on the stream of
        an unrelated caller.
        """
        protocol = AdbProtocolTCP()
        protocol._sock = _NullSock()
        protocol.timeout = 0.05

        with pytest.raises(AdbConnectionTimeout):
            protocol.open_stream('shell:echo')

        # the id of the failed open is spent, the next open gets the next one
        assert protocol._stream_id_next == 2

    def test_rejects_a_receive_buffer_that_is_not_positive(self):
        """A stream that may buffer nothing would never acknowledge a write"""
        protocol = AdbProtocolTCP()

        for recv_buffer_size in (0, -1):
            with pytest.raises(ValueError) as e:
                protocol.open_stream('shell:echo', recv_buffer_size=recv_buffer_size)
            assert 'positive' in str(e.value)

    def test_rejects_a_null_byte_in_the_service(self):
        """The service name ends at the first null, a null inside truncates it"""
        protocol = AdbProtocolTCP()
        protocol._sock = _NullSock()

        with pytest.raises(ValueError) as e:
            protocol.open_stream('shell:echo hi\x00rm -rf /')

        assert 'null byte' in str(e.value)
        assert protocol._stream_dict == {}
        assert _NullSock().sent == []

    def test_rejects_a_trailing_null_byte(self):
        """A null byte is refused wherever it sits, the caller adds none

        The null that ends the service name is added by the client. A caller
        that passes one would get a second one on the wire, the device would
        read an empty command, so every null is refused instead of trimmed.
        """
        protocol = AdbProtocolTCP()
        sock = _NullSock()
        protocol._sock = sock

        with pytest.raises(ValueError):
            protocol.open_stream('shell:echo\x00')

        assert sock.sent == []
        assert protocol._stream_dict == {}

    def test_one_null_terminator_on_the_wire(self):
        """The payload of OPEN is the service and exactly one null"""
        protocol = AdbProtocolTCP()
        sock = _NullSock()
        protocol._sock = sock
        protocol.timeout = 0.05

        with pytest.raises(AdbConnectionTimeout):
            protocol.open_stream('shell:echo')

        assert decode_message(sock.sent[0])[6] == b'shell:echo\x00'

    def test_raises_when_not_connected(self):
        """An open without a connection says so instead of failing on None"""
        protocol = AdbProtocolTCP()

        with pytest.raises(AdbConnectionClosed) as e:
            protocol.open_stream('shell:echo')

        assert 'Not connected' in str(e.value)

    def test_raises_when_the_connection_is_broken(self):
        """A connection the device closed is refused before anything is sent"""
        protocol = AdbProtocolTCP()
        protocol._sock = _NullSock()
        protocol._broken = True

        with pytest.raises(AdbConnectionClosed) as e:
            protocol.open_stream('shell:echo')

        assert 'broken' in str(e.value)


class TestProtocolRoundTrip:
    """The protocol against a device that speaks it"""

    def test_a_stream_returns_the_output_of_the_device(self):
        """One open reads the stream to the close"""
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(stdout=b'hello\n')) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                with client.open_stream('shell,v2:echo hello') as stream:
                    data = stream.recv_until_close(timeout=5)
            finally:
                client.disconnect()

        result = ShellResult.from_shell_v2(data)
        assert result.stdout == b'hello\n'
        assert result.exitcode == 0
        assert adbd.opens == ['shell,v2:echo hello']
        assert adbd.violations == []

    def test_the_device_is_held_back_when_the_reader_does_not_keep_up(self):
        """A device of a stream without a window stalls instead of filling memory

        The client buffers a write and holds its acknowledgement back. The
        device, which sends the next write only after that acknowledgement,
        stops. When the reader takes the output the device resumes and the whole
        output arrives: a held back acknowledgement slows a device down, it does
        not lose its output.
        """
        output = b'x' * 20000
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(stdout=output)) as adbd:
            adbd.wait_write_okay = True
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                stream = client.open_stream('shell,v2:x', recv_buffer_size=10)
                assert stream._recv_buffer_size == 10
                # the first write fills the buffer and is left unacknowledged
                assert adbd.wait_for(lambda: stream._data_size > 0)
                # the device had every chance to send a second one, and can not
                time.sleep(0.3)
                assert adbd.stream_writes == 1
                # it is blocked on the acknowledgement, not out of output
                assert stream.remote_id in adbd.write_stalls

                # the reader takes the output, the device resumes and finishes
                data = stream.recv_until_close(timeout=5)
            finally:
                client.disconnect()

        # 20000 bytes in frames of 4096 plus the header of each frame and the
        # exit code packet, sent in writes of 4096
        assert adbd.stream_writes == 5
        assert ShellResult.from_shell_v2(data).stdout == output
        assert adbd.violations == []

    def test_repeated_streams_reuse_the_connection(self):
        """The connection outlives the streams, one connect serves many calls"""
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(stdout=b'x')) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                for _ in range(20):
                    with client.open_stream('shell:x') as stream:
                        assert stream.recv_until_close(timeout=5) == b'x'
                # every stream id was released: the next call reuses a free one
                assert client._stream_dict == {}
                assert adbd.connections == 1
            finally:
                client.disconnect()

    def test_a_refused_service_raises(self):
        """A CLSE instead of an OKAY means the device has no such service"""
        with FakeAdbd(refuse=lambda service: service.startswith('bogus')) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                with pytest.raises(AdbStreamClosed) as e:
                    client.open_stream('bogus:cmd')
                assert 'refused' in str(e.value)
                assert client._stream_dict == {}
            finally:
                client.disconnect()

    def test_a_command_that_ends_before_the_open_returns_keeps_its_output(self):
        """The device may close the stream before the client looks at it

        The OKAY, the output and the CLSE of a short command arrive back to
        back. The open may observe the stream as opened or already closed, and
        neither may be reported as a refused service, the output is there.
        """
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(stdout=b'fast')) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                stream = client.open_stream('shell,v2:fast')
                assert stream.state in ('opened', 'closed')
                assert stream.remote_id != 0
                result = ShellResult.from_shell_v2(stream.recv_until_close(timeout=5))
                assert result.stdout == b'fast'
                assert result.exitcode == 0
            finally:
                client.disconnect()

    def test_no_two_concurrent_streams_share_an_id(self):
        """Forty streams at once, every one of them gets its own output

        The local ids of the connection are a pool, a stream that is still open
        must never have the id of another one, or the output of a command lands
        on the stream of a different caller.
        """
        count = 40

        def responder(command, shell_v2):
            return ShellReply(stdout=command.encode())

        with FakeAdbd(responder=responder) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            barrier = threading.Barrier(count, timeout=10)
            output = {}
            errors = []

            def run(index):
                command = 'echo %d' % index
                try:
                    barrier.wait()
                    with client.open_stream('shell,v2:%s' % command) as stream:
                        output[index] = ShellResult.from_shell_v2(stream.recv_until_close(timeout=10)).stdout
                except Exception as e:
                    errors.append((index, e))

            threads = [threading.Thread(target=run, args=(i,)) for i in range(count)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

            client.disconnect()

        assert errors == []
        assert output == {i: ('echo %d' % i).encode() for i in range(count)}
        assert adbd.violations == []

    def test_the_service_of_a_unicode_command(self):
        """A command is utf-8 on the wire, a chinese one survives"""
        command = 'echo 你好'

        with FakeAdbd(responder=lambda cmd, shell_v2: ShellReply(stdout=(cmd or '').encode())) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                with client.open_stream('shell,v2:' + command) as stream:
                    data = stream.recv_until_close(timeout=5)
            finally:
                client.disconnect()

        assert adbd.opens == [command and 'shell,v2:' + command]
        assert data.startswith(bytes([1]))
        assert command.encode() in data

    def test_the_device_sees_a_valid_set_of_messages(self):
        """Every message the client sends passes the checks of the device"""
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(stdout=b'y' * 100)) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                for _ in range(5):
                    with client.open_stream('shell:y') as stream:
                        stream.recv_until_close(timeout=5)
            finally:
                client.disconnect()

        assert adbd.violations == []
        assert adbd.opens_without_nul == []
        # a stream the device sends is acknowledged
        assert adbd.write_okays >= 5


class TestMessageFormat:
    """The bytes the client writes follow the transport"""

    def test_message_header(self):
        """The six fields of the header, the checksum and the magic included"""
        protocol = AdbProtocolTCP()
        sock = _NullSock()

        protocol.message_send(sock, OPEN, 7, 8, b'abc')

        command, arg0, arg1, length, checksum, magic, data = decode_message(sock.sent[0])
        assert (command, arg0, arg1, length) == (OPEN, 7, 8, 3)
        assert data == b'abc'
        assert checksum == sum(b'abc')
        assert magic == int.from_bytes(OPEN, 'little') ^ 0xFFFFFFFF

    def test_checksum_of_a_binary_payload(self):
        """The checksum is a sum masked to 32 bits, high bytes do not sign it"""
        protocol = AdbProtocolTCP()
        sock = _NullSock()
        payload = b'\xff' * 1000

        protocol.message_send(sock, WRTE, 1, 2, payload)

        assert decode_message(sock.sent[0])[4] == (1000 * 0xFF) & 0xFFFFFFFF

    def test_the_handshake_of_a_real_connection(self):
        """The CNXN the client sends advertises the version and the features"""
        with FakeAdbd() as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                hello = adbd.hello
                version = adbd.client_version
                max_payload = adbd.client_max_payload
            finally:
                client.disconnect()

        assert hello.startswith(b'host::features=')
        assert b'shell_v2' in hello
        assert version == ADB_VERSION
        assert version >= ADB_VERSION_SKIP_CHECKSUM
        assert max_payload == ADB_MAX_PAYLOAD

    def test_the_max_payload_of_the_device_is_the_send_limit(self):
        """The client never sends more than the device advertised"""
        with FakeAdbd(max_payload=4096) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                assert client._max_payload == 4096
            finally:
                client.disconnect()

    def test_a_device_that_advertises_more_than_we_accept(self):
        """The payload we advertise caps what we accept, not the device"""
        with FakeAdbd(max_payload=1024 * 1024) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                assert client._max_payload == ADB_MAX_PAYLOAD
            finally:
                client.disconnect()

    def test_a_device_that_declares_no_payload(self):
        """A CNXN of 0 is an adbd of the first protocol, which reads 4096

        An old adbd does not fill the field at all. Reading the 0 as a limit
        would make the client fall back to 256KB, more than that device reads
        in one message.
        """
        with FakeAdbd(max_payload=0) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                assert client._max_payload == ADB_MAX_PAYLOAD_V1
            finally:
                client.disconnect()


class TestMessageRecvValidation:
    """A message of the device that is not valid"""

    def test_bad_magic_of_the_banner(self):
        """A banner with a wrong magic is refused at the handshake"""
        with FakeAdbd() as adbd:
            adbd.fault_on_reply = 'bad_magic'
            client = AdbProtocolTCP(port=adbd.port)
            client.timeout = 1

            with pytest.raises(AdbMessageInvalid) as e:
                client.connect()

        assert 'Magic' in str(e.value)
        assert client._sock is None

    def test_bad_checksum_of_the_banner(self):
        """A banner with a wrong checksum is refused at the handshake"""
        with FakeAdbd() as adbd:
            adbd.fault_on_reply = 'bad_checksum'
            client = AdbProtocolTCP(port=adbd.port)
            client.timeout = 1

            with pytest.raises(AdbMessageInvalid) as e:
                client.connect()

        assert 'Checksum' in str(e.value)

    def test_an_impossible_payload_length(self):
        """A header that claims 4GB is refused instead of waited for"""
        with FakeAdbd() as adbd:
            adbd.fault_on_reply = 'oversize_length'
            client = AdbProtocolTCP(port=adbd.port)
            client.timeout = 1

            start = time.perf_counter()
            with pytest.raises(AdbMessageInvalid) as e:
                client.connect()
            elapsed = time.perf_counter() - start

        assert 'Payload length' in str(e.value)
        assert elapsed < 1

    def test_an_unknown_command(self):
        """A command outside the protocol is refused"""
        with FakeAdbd() as adbd:
            adbd.fault_on_reply = 'unknown_command'
            client = AdbProtocolTCP(port=adbd.port)
            client.timeout = 1

            with pytest.raises(AdbMessageInvalid) as e:
                client.connect()

        assert 'Invalid command' in str(e.value)

    def test_a_garbage_banner(self):
        """A peer that does not speak adb fails the handshake, not a read"""
        with FakeAdbd() as adbd:
            # six words of an unknown command, a full header of nonsense
            adbd.cnxn_reply = b'XXXX' * 6
            client = AdbProtocolTCP(port=adbd.port)
            client.timeout = 1

            with pytest.raises(AdbMessageInvalid):
                client.connect()

    def test_an_invalid_message_ends_the_connection(self):
        """A bad message mid session drops the connection, it is not skipped

        The payload of a message that failed validation can not be trusted, the
        rest of the stream would be read as headers. The client must report the
        connection as broken instead of spinning on the same bytes, and the
        streams of it must end at once instead of waiting for their timeouts.
        """
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(stdout=b'x')) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.timeout = 0.3
            client.connect()
            try:
                adbd.fault_on_reply = 'bad_magic'
                with pytest.raises(AdbConnectionClosed):
                    client.open_stream('shell:x')
                assert adbd.wait_for(lambda: client._broken)
                with pytest.raises(AdbConnectionClosed) as e:
                    client.open_stream('shell:x')
                assert 'broken' in str(e.value)
            finally:
                client.disconnect()

    def test_a_message_of_an_unknown_stream_is_dropped(self):
        """A stale message of a stream that ended does not kill the thread"""
        protocol = AdbProtocolTCP()
        sock = _NullSock()
        protocol._sock = sock

        protocol._dispatch_message(sock, WRTE, 1000, 42, b'stale')
        protocol._dispatch_message(sock, OKAY, 1000, 42, b'')
        protocol._dispatch_message(sock, CLSE, 1000, 42, b'')

        # nothing is acknowledged, the stream does not exist any more
        assert sock.sent == []
        assert protocol._stream_dict == {}

    def test_a_write_of_a_stream_that_is_not_open_is_not_acknowledged(self):
        """A device that writes before the OKAY is not confirmed"""
        protocol = AdbProtocolTCP()
        sock = _NullSock()
        protocol._sock = sock
        stream = make_stream(state='opening')
        protocol._stream_dict[stream.local_id] = stream

        with logger.mock_capture_writer() as capture:
            protocol._dispatch_message(sock, WRTE, 9, stream.local_id, b'early')

        assert sock.sent == []
        assert capture.fd.any_contains('Unexpected command')
        assert buffered(stream) == b''


class TestWriteAcknowledgement:
    """The client asks a device for more output only when it has room"""

    @staticmethod
    def dispatch(recv_buffer_size, data):
        """
        Dispatch one write of a device to a stream of a given buffer size

        Args:
            recv_buffer_size (int): Bytes the stream buffers before it holds back
            data (bytes): Payload of the write

        Returns:
            tuple: (stream, socket that recorded what the client sent)
        """
        protocol = AdbProtocolTCP()
        sock = _NullSock()
        protocol._sock = sock
        stream = make_stream(recv_buffer_size=recv_buffer_size, protocol=protocol)
        stream.remote_id = 9
        protocol._stream_dict[stream.local_id] = stream

        protocol._dispatch_message(sock, WRTE, 9, stream.local_id, data)
        return stream, sock

    def test_a_write_that_fits_is_acknowledged(self):
        """The device is asked for the next write while the buffer has room"""
        stream, sock = self.dispatch(recv_buffer_size=10, data=b'1234')

        assert buffered(stream) == b'1234'
        assert sent_commands(sock) == [OKAY]

    def test_a_write_that_fills_the_buffer_is_not_acknowledged(self):
        """A write that fills the buffer is stored and left unacknowledged"""
        stream, sock = self.dispatch(recv_buffer_size=10, data=b'1234567890')

        assert buffered(stream) == b'1234567890'
        assert sent_commands(sock) == []

    def test_the_reader_acknowledges_when_it_takes_the_output(self):
        """The held back write is acknowledged by the reader, not the dispatcher"""
        stream, sock = self.dispatch(recv_buffer_size=10, data=b'1234567890')
        assert sent_commands(sock) == []

        assert stream.take_data() == b'1234567890'
        assert sent_commands(sock) == [OKAY]

    def test_the_acknowledgement_carries_the_ids_of_the_stream(self):
        """OKAY names both sides of the stream, the device needs the pair"""
        stream, sock = self.dispatch(recv_buffer_size=10, data=b'x' * 10)
        stream.take_data()

        command, arg0, arg1, length, checksum, magic, data = decode_message(sock.sent[0])
        assert command == OKAY
        assert arg0 == stream.local_id
        assert arg1 == stream.remote_id
        assert data == b''

    def test_the_reader_is_woken_for_a_write_it_has_to_acknowledge(self):
        """The wait is woken before the acknowledgement decision"""
        stream, sock = self.dispatch(recv_buffer_size=10, data=b'1234567890')

        assert stream.recv_event.acquire(blocking=False) is True


class TestChecksumVersion:
    """The checksum field of the version that does not require one

    From 0x01000001 on the checksum is optional: a device of that version sends
    0 instead of the sum of the payload, and the receiver skips the check before
    it computes anything. Version 0x01000000 has no such right: an old adbd that
    sends 0 is a device that computed nothing. The host plays by the same rule
    on the way out, where the sum is a pass over every payload of the connection
    that the device would only ignore.
    """

    @staticmethod
    def recv_one(version, message):
        """
        Receive one message of a socket pair, as a connection of a version

        Args:
            version (int): Protocol version of the connection
            message (bytes): Bytes the device sends

        Returns:
            tuple: (payload of the message, protocol of the receive)
        """
        protocol = AdbProtocolTCP()
        protocol._protocol_version = version
        reader, writer = socket.socketpair()
        writer.sendall(message)
        try:
            _, arg0, _, payload = protocol.message_recv(reader)
        finally:
            reader.close()
            writer.close()
        return payload, protocol

    def test_the_client_speaks_the_version_that_may_skip_it(self):
        """The CNXN carries 0x01000001, a modern device then stops summing"""
        with FakeAdbd(version=ADB_VERSION_SKIP_CHECKSUM) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                assert adbd.client_version == ADB_VERSION
                assert client._protocol_version == ADB_VERSION_SKIP_CHECKSUM
            finally:
                client.disconnect()

        assert ADB_VERSION >= ADB_VERSION_SKIP_CHECKSUM

    def test_the_connection_uses_the_lower_of_the_two_versions(self):
        """An old device answers with its own version, the connection keeps it

        The checksum of that connection is mandatory in both directions.
        """
        with FakeAdbd() as adbd:
            assert adbd.version == 0x01000000
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                assert client._protocol_version == 0x01000000
            finally:
                client.disconnect()

    def test_a_device_that_sends_no_checksum(self):
        """A device of 0x01000001 that leaves the field at 0 is accepted"""
        with FakeAdbd(version=ADB_VERSION_SKIP_CHECKSUM,
                      responder=lambda command, shell_v2: ShellReply(stdout=b'x\n')) as adbd:
            adbd.zero_checksum = True
            client = AdbProtocolTCP(port=adbd.port)
            client.timeout = 2
            client.connect()
            try:
                assert client._protocol_version == ADB_VERSION_SKIP_CHECKSUM
                with client.open_stream('shell,v2:echo x') as stream:
                    result = ShellResult.from_shell_v2(stream.recv_until_close(timeout=5))
                assert result.stdout == b'x\n'
            finally:
                client.disconnect()

    def test_a_checksum_of_that_version_is_not_verified(self):
        """A wrong checksum of a connection that may omit it is not an error

        The field proves nothing there, so the value is never read. The bytes
        are still covered by the checksum of TCP.
        """
        with FakeAdbd(version=ADB_VERSION_SKIP_CHECKSUM,
                      responder=lambda command, shell_v2: ShellReply(stdout=b'x\n')) as adbd:
            adbd.fault_on_reply = 'bad_checksum'
            client = AdbProtocolTCP(port=adbd.port)
            client.timeout = 2
            client.connect()
            try:
                with client.open_stream('shell,v2:echo x') as stream:
                    result = ShellResult.from_shell_v2(stream.recv_until_close(timeout=5))
                assert result.stdout == b'x\n'
            finally:
                client.disconnect()

    def test_the_sum_is_not_computed_for_that_version(self, monkeypatch):
        """The branch is taken before the sum, not after it

        Computing the sum of a payload and then ignoring it costs the same as
        the check, a pass over every byte of every message of the connection.
        """
        calls = []
        monkeypatch.setattr(tcp_protocol, 'sum', counting_sum(calls), raising=False)

        payload, protocol = self.recv_one(ADB_VERSION_SKIP_CHECKSUM,
                                          packed(WRTE, 1, 1, b'hello', checksum=0xDEADBEEF))

        assert payload == b'hello'
        assert calls == []
        assert protocol._protocol_version == ADB_VERSION_SKIP_CHECKSUM

    def test_the_sum_is_computed_for_an_old_version(self, monkeypatch):
        """The payload of a connection that requires it is summed and checked"""
        calls = []
        monkeypatch.setattr(tcp_protocol, 'sum', counting_sum(calls), raising=False)

        payload, _ = self.recv_one(0x01000000, packed(WRTE, 1, 1, b'hello'))

        assert payload == b'hello'
        assert calls == [5]

    def test_an_old_device_may_not_skip_the_checksum(self):
        """A device of 0x01000000 that sends 0 is refused"""
        with pytest.raises(AdbMessageInvalid) as e:
            self.recv_one(0x01000000, packed(WRTE, 1, 1, b'hello', checksum=0))

        assert 'Checksum' in str(e.value)

    def test_the_sum_of_a_sent_message_is_skipped_on_that_version(self, monkeypatch):
        """The messages of a modern connection carry 0 instead of the sum

        Every write of the connection would pay a pass over its payload for a
        field the device reads nothing in.
        """
        calls = []
        with FakeAdbd(version=ADB_VERSION_SKIP_CHECKSUM,
                      responder=lambda command, shell_v2: ShellReply(stdout=b'x\n')) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.timeout = 2
            client.connect()
            try:
                assert client._send_checksum is False
                monkeypatch.setattr(tcp_protocol, 'sum', counting_sum(calls), raising=False)
                with client.open_stream('shell,v2:echo x') as stream:
                    assert stream.recv_until_close(timeout=5)
            finally:
                client.disconnect()

        assert calls == []
        assert adbd.violations == []

    def test_the_sum_of_a_large_write_is_skipped_on_that_version(self, monkeypatch):
        """A write of 128 KiB carries 0 as well, not only a small message"""
        calls = []
        with FakeAdbd(version=ADB_VERSION_SKIP_CHECKSUM,
                      responder=lambda command, shell_v2: ShellReply(close=False)) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.timeout = 2
            client.connect()
            try:
                with client.open_stream('shell,v2:cat') as stream:
                    monkeypatch.setattr(tcp_protocol, 'sum', counting_sum(calls), raising=False)
                    client.sendto_stream(stream, b'x' * (128 * 1024))
            finally:
                client.disconnect()

        assert calls == []
        assert adbd.violations == []

    def test_the_sum_of_a_sent_message_is_computed_on_an_old_version(self, monkeypatch):
        """A device of 0x01000000 verifies the field, the client fills it in"""
        calls = []
        service = 'shell,v2:echo x'
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(stdout=b'x\n')) as adbd:
            assert adbd.version == 0x01000000
            client = AdbProtocolTCP(port=adbd.port)
            client.timeout = 2
            client.connect()
            try:
                assert client._send_checksum is True
                monkeypatch.setattr(tcp_protocol, 'sum', counting_sum(calls), raising=False)
                with client.open_stream(service) as stream:
                    assert stream.recv_until_close(timeout=5)
            finally:
                client.disconnect()

        # the OPEN carries the sum of its service name, the device reads it
        assert len(service.encode() + b'\x00') in calls
        assert adbd.violations == []

    def test_the_handshake_carries_the_sum_on_every_version(self, monkeypatch):
        """The version of the device is unknown when the CNXN leaves the host

        A device of 0x01000001 would not read the field, an old one refuses a
        CNXN without it, and nothing tells the two apart before the answer.
        """
        calls = []
        monkeypatch.setattr(tcp_protocol, 'sum', counting_sum(calls), raising=False)

        with FakeAdbd(version=ADB_VERSION_SKIP_CHECKSUM) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                assert client._send_checksum is False
            finally:
                client.disconnect()

        # the banner of the CNXN, and only it
        assert calls == [len(adbd.hello)]

    def test_the_flag_follows_the_device_of_the_new_connection(self):
        """A reconnect to an older device sums again

        The version of a connection is remembered on the object, a device on
        the same address may answer with another one at the next connect.
        """
        with FakeAdbd(version=ADB_VERSION_SKIP_CHECKSUM) as modern, FakeAdbd() as old:
            client = AdbProtocolTCP(port=modern.port)
            client.timeout = 2
            client.connect()
            try:
                assert client._send_checksum is False
            finally:
                client.disconnect()

            client.port = old.port
            client.connect()
            try:
                assert client._protocol_version == 0x01000000
                assert client._send_checksum is True
            finally:
                client.disconnect()

    def test_an_old_device_with_a_wrong_checksum(self):
        """A device of 0x01000000 that sends a wrong checksum is refused"""
        with pytest.raises(AdbMessageInvalid) as e:
            self.recv_one(0x01000000, packed(WRTE, 1, 1, b'hello', checksum=0x1))

        assert 'Checksum' in str(e.value)

    def test_a_handshake_of_an_old_device_without_checksum(self):
        """The version of the CNXN itself decides, not the one stored

        An old device that answers with version 0x01000000 and a checksum of 0
        is refused even before the connection version is set.
        """
        protocol = AdbProtocolTCP()
        protocol._protocol_version = ADB_VERSION_SKIP_CHECKSUM
        reader, writer = socket.socketpair()
        writer.sendall(packed(CNXN, 0x01000000, ADB_MAX_PAYLOAD, b'device::features=shell_v2', checksum=0))
        try:
            with pytest.raises(AdbMessageInvalid):
                protocol.message_recv(reader)
        finally:
            reader.close()
            writer.close()

    def test_a_handshake_of_a_new_device_without_checksum(self):
        """A banner of 0x01000001 without a checksum is the normal answer"""
        payload, _ = self.recv_one(
            ADB_VERSION_SKIP_CHECKSUM,
            packed(CNXN, ADB_VERSION_SKIP_CHECKSUM, ADB_MAX_PAYLOAD, b'device::features=shell_v2', checksum=0),
        )

        assert payload == b'device::features=shell_v2'

    def test_a_modern_device_accepts_a_zero_checksum_from_the_host(self):
        """The field is optional in both directions on such a connection

        Verified against a real device: adbd of 0x01000001 answered a CNXN, an
        OPEN and a WRTE whose checksum field is 0. The fake device must accept
        them too, or it would be stricter than the device it mirrors and a test
        would keep a host from using the same right.
        """
        with FakeAdbd(version=ADB_VERSION_SKIP_CHECKSUM) as adbd:
            reader = AdbProtocolTCP()
            sock = socket.create_connection(('127.0.0.1', adbd.port), timeout=5)
            try:
                sock.sendall(packed(CNXN, ADB_VERSION_SKIP_CHECKSUM, ADB_MAX_PAYLOAD,
                                    b'host::features=shell_v2', checksum=0))
                command, _, _, _ = reader.message_recv(sock)
                assert command == CNXN

                sock.sendall(packed(OPEN, 1, 0, b'shell,v2:echo hi\x00', checksum=0))
                command, _, _, _ = reader.message_recv(sock)
                assert command == OKAY
            finally:
                sock.close()

        assert adbd.violations == []
        assert adbd.opens == ['shell,v2:echo hi']


class TestSendToStream:
    """Writing to a stream"""

    @staticmethod
    def open_cat(client):
        """
        Open a stream that stays open, the way a command reading stdin does

        Args:
            client (AdbProtocolTCP): Connected client

        Returns:
            AdbStreamTCP: The open stream
        """
        return client.open_stream('shell,v2,raw:cat')

    def make_client(self, adbd, timeout=1):
        """
        Connect a client to a device

        Args:
            adbd (FakeAdbd): The device
            timeout (float): Timeout of the connection. Defaults to 1.

        Returns:
            AdbProtocolTCP: The connected client
        """
        client = AdbProtocolTCP(port=adbd.port)
        client.timeout = timeout
        client.connect()
        return client

    def test_one_write_is_acknowledged(self):
        """The device answers a WRTE with an OKAY, the call returns after it"""
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(close=False)) as adbd:
            client = self.make_client(adbd)
            try:
                with self.open_cat(client) as stream:
                    client.sendto_stream(stream, b'hello')
                assert adbd.writes == [b'hello']
                assert adbd.violations == []
            finally:
                client.disconnect()

    def test_the_payload_of_the_device_is_not_exceeded(self):
        """A write larger than the max payload of the device is split"""
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(close=False), max_payload=4096) as adbd:
            client = self.make_client(adbd)
            try:
                with self.open_cat(client) as stream:
                    client.sendto_stream(stream, b'x' * 10000)
                assert adbd.writes == [b'x' * 4096, b'x' * 4096, b'x' * 1808]
                assert adbd.violations == []
            finally:
                client.disconnect()

    def test_a_device_that_declares_no_payload_is_not_exceeded(self):
        """A write to a device that advertised 0 is split at 4096, not 256KB

        An adbd of the first protocol leaves the field of its CNXN empty and
        drops a message larger than 4096 together with the connection.
        """
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(close=False), max_payload=0) as adbd:
            client = self.make_client(adbd)
            try:
                with self.open_cat(client) as stream:
                    client.sendto_stream(stream, b'x' * 10000)
                assert adbd.writes == [b'x' * 4096, b'x' * 4096, b'x' * 1808]
                assert adbd.violations == []
            finally:
                client.disconnect()

    def test_an_empty_write_sends_nothing(self):
        """Writing no data is a no-op, an empty WRTE is not a message"""
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(close=False)) as adbd:
            client = self.make_client(adbd)
            try:
                with self.open_cat(client) as stream:
                    client.sendto_stream(stream, b'')
                assert adbd.writes == []
            finally:
                client.disconnect()

    def test_a_device_that_does_not_acknowledge(self):
        """A write without an OKAY times out"""
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(close=False)) as adbd:
            adbd.answer_write_okay = False
            client = self.make_client(adbd, timeout=0.3)
            try:
                stream = self.open_cat(client)
                with pytest.raises(AdbConnectionTimeout):
                    client.sendto_stream(stream, b'hello')
                stream.close()
            finally:
                client.disconnect()

    def test_a_closed_stream_is_refused(self):
        """A stream the device closed takes no more data"""
        protocol = AdbProtocolTCP()
        protocol._sock = _NullSock()
        stream = make_stream(state='closed')
        stream.remote_id = 5

        with pytest.raises(AdbStreamClosed) as e:
            protocol.sendto_stream(stream, b'hello')

        assert 'state' in str(e.value)

    def test_a_stream_without_a_remote_id_is_refused(self):
        """The device assigned no id, there is nothing to write to"""
        protocol = AdbProtocolTCP()
        protocol._sock = _NullSock()
        stream = make_stream(state='opened')

        with pytest.raises(AdbStreamClosed) as e:
            protocol.sendto_stream(stream, b'hello')

        assert 'remote_id' in str(e.value)

    def test_writing_without_a_connection(self):
        """A write without a connection says so"""
        protocol = AdbProtocolTCP()
        stream = make_stream(state='opened')
        stream.remote_id = 5

        with pytest.raises(AdbConnectionClosed):
            protocol.sendto_stream(stream, b'hello')

    def test_a_command_that_exits_while_it_is_written_to(self):
        """A closed stream is reported at once, not waited for until a timeout

        A shell command may exit before the host finished writing: the device
        answers the next WRTE with a CLSE. The caller has to see a closed
        stream, not a timeout of the whole connection a few seconds later.
        """
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(close=False)) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                stream = client.open_stream('shell:cat')
                adbd.close_after_writes = 1

                start = time.perf_counter()
                with pytest.raises(AdbStreamClosed) as e:
                    client.sendto_stream(stream, b'x' * 10)
                elapsed = time.perf_counter() - start

                assert stream.state == 'closed'
                assert elapsed < 1
                assert 'after 0 of 10 bytes' in str(e.value)
            finally:
                client.disconnect()

    def test_a_stream_that_closes_between_two_chunks(self):
        """The bytes acknowledged before the close are reported"""
        payload = b'0' * 4096 + b'1' * 4096 + b'2' * 1808

        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(close=False), max_payload=4096) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                stream = client.open_stream('shell:cat')
                # the first chunk is acknowledged, the second one closes it
                adbd.close_after_writes = 2

                with pytest.raises(AdbStreamClosed) as e:
                    client.sendto_stream(stream, payload)

                # the first chunk is acknowledged before the device closes
                assert 'after 4096 of 10000 bytes' in str(e.value)
                assert adbd.writes == [payload[:4096], payload[4096:8192]]
            finally:
                client.disconnect()


class TestCloseStream:
    """Closing a stream"""

    def test_the_device_is_asked_and_waits_for_the_answer(self):
        """The CLSE carries the two ids, the close ends on the CLSE of the device"""
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(close=False)) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                stream = client.open_stream('shell:cat')
                local_id, remote_id = stream.local_id, stream.remote_id
                stream.close()
                assert adbd.client_closes == [(local_id, remote_id)]
                assert stream.state == 'closed'
                assert client._stream_dict == {}
            finally:
                client.disconnect()

    def test_a_device_that_never_confirms(self):
        """A close the device does not confirm times out"""
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(close=False)) as adbd:
            adbd.answer_close_clse = False
            client = AdbProtocolTCP(port=adbd.port)
            client.timeout = 0.3
            client.connect()
            try:
                stream = client.open_stream('shell:cat')
                with pytest.raises(AdbConnectionTimeout):
                    stream.close()
            finally:
                client.disconnect()

    def test_a_closed_stream_sends_nothing(self):
        """Closing twice does not send a second CLSE"""
        protocol = AdbProtocolTCP()
        sock = _NullSock()
        protocol._sock = sock
        stream = make_stream(state='closed')

        protocol.close_stream(stream)

        assert sock.sent == []

    def test_a_stream_without_a_remote_id_is_not_closable(self):
        """A stream the device never opened is left alone"""
        protocol = AdbProtocolTCP()
        sock = _NullSock()
        protocol._sock = sock
        stream = make_stream(state='opening')

        with logger.mock_capture_writer() as capture:
            protocol.close_stream(stream)

        assert sock.sent == []
        assert capture.fd.any_contains('without remote_id')

    def test_the_error_of_the_block_wins(self):
        """A close that fails does not replace the error of the with block"""
        protocol = AdbProtocolTCP()
        stream = AdbStreamTCP(1, 'shell:cat', protocol=protocol)
        stream.state = 'closed'

        with pytest.raises(ValueError):
            with stream:
                raise ValueError('the real error')

    def test_a_close_that_fails_is_reported(self):
        """A close of a stream that is not connected is an error"""
        protocol = AdbProtocolTCP()
        stream = AdbStreamTCP(1, 'shell:cat', protocol=protocol)
        stream.state = 'opened'
        stream.remote_id = 5

        with pytest.raises(AdbConnectionClosed):
            with stream:
                pass


class TestHalfClose:
    """A stream ends when both sides sent a CLSE

    adbd keeps the stream of a command that ended until the host answers with a
    CLSE of its own. A host that never answers leaves a stream on the device,
    and the device refuses the next OPEN that carries the local id of it.
    """

    def test_the_close_of_the_device_is_answered(self):
        """A command that ends on its own is answered, the device frees it"""
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(stdout=b'x')) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                with client.open_stream('shell,v2:x') as stream:
                    local_id, remote_id = stream.local_id, stream.remote_id
                    assert stream.recv_until_close(timeout=5)

                adbd.wait_for(lambda: adbd.client_closes == [(local_id, remote_id)])
            finally:
                client.disconnect()

    def test_a_refused_service_is_not_answered(self):
        """A stream the device never created has nothing to close"""
        with FakeAdbd(refuse=lambda service: service.startswith('bogus')) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                with pytest.raises(AdbStreamClosed):
                    client.open_stream('bogus:cmd')
                with client.open_stream('shell:ok') as stream:
                    local_id, remote_id = stream.local_id, stream.remote_id
                    stream.recv_until_close(timeout=5)

                adbd.wait_for(lambda: adbd.client_closes == [(local_id, remote_id)])
            finally:
                client.disconnect()

    def test_a_close_is_sent_once(self):
        """The stream the caller closes is not closed a second time"""
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(close=False)) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                first = client.open_stream('shell:cat')
                a = (first.local_id, first.remote_id)
                first.close()

                second = client.open_stream('shell:cat')
                b = (second.local_id, second.remote_id)
                second.close()

                adbd.wait_for(lambda: adbd.client_closes == [a, b])
                # a second CLSE would be recorded as another entry
                time.sleep(0.1)
                assert adbd.client_closes == [a, b]
                assert adbd.violations == []
            finally:
                client.disconnect()


class TestDisconnect:
    """The life of a connection"""

    def test_shell_after_disconnect(self):
        """A call on a closed connection says so"""
        with FakeAdbd() as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            client.disconnect()

            with pytest.raises(AdbConnectionClosed) as e:
                client.open_stream('shell:x')

        assert 'Not connected' in str(e.value)

    def test_disconnect_twice(self):
        """A second disconnect is a no-op"""
        with FakeAdbd() as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            client.disconnect()
            client.disconnect()

        assert client._sock is None

    def test_connect_twice_opens_one_connection(self):
        """A connected client does not reconnect"""
        with FakeAdbd() as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            client.connect()
            try:
                assert adbd.connections == 1
            finally:
                client.disconnect()

    def test_disconnect_releases_the_open_streams(self):
        """The streams of a closed connection are dropped and their ids freed"""
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(close=False)) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            stream = client.open_stream('shell:cat')
            client.disconnect()

        assert client._stream_dict == {}
        # a stream of a dead connection keeps its id out of the new one
        assert client._stream_id_next > stream.local_id

    def test_disconnect_wakes_the_receive_thread(self):
        """The thread that reads the socket ends with the connection"""
        with FakeAdbd() as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            thread = client._recv_thread
            client.disconnect()

            assert thread is not None
            # disconnect() waits for the thread itself, the connection is not
            # released while a thread still reads its socket
            assert thread.is_alive() is False

    def test_a_disconnect_ends_the_connection_with_a_fin(self):
        """The device reads the end of the connection as an end of file

        A close that still holds unread data in the receive buffer sends a RST
        instead, which a device does not read as a connection that ended.
        """
        with FakeAdbd() as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            client.disconnect()

            # the end is recorded by the thread of the device, just after the
            # close that woke the client
            assert adbd.wait_for(lambda: adbd.connection_ends == ['eof'])
            assert adbd.violations == []

    def test_a_disconnect_wakes_a_reader(self):
        """A reader of a dropped connection does not wait for its timeout

        A stream of a connection that is gone can never be closed by the
        device, so a read that waits for the close would wait the whole timeout
        of the call, 20s by default.
        """
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(close=False)) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            stream = client.open_stream('shell:cat')
            errors = []

            def read():
                try:
                    stream.recv_until_close(timeout=20)
                except Exception as e:
                    errors.append(e)

            thread = threading.Thread(target=read, daemon=True)
            thread.start()
            start = time.perf_counter()
            client.disconnect()
            thread.join(timeout=5)
            elapsed = time.perf_counter() - start

        assert thread.is_alive() is False
        assert len(errors) == 1
        assert isinstance(errors[0], AdbConnectionClosed)
        assert 'Connection lost' in str(errors[0])
        assert elapsed < 2

    def test_a_disconnect_interrupts_an_open(self):
        """An open that waits for a silent device ends when the link does"""
        with FakeAdbd() as adbd:
            adbd.open_fault = 'silent'
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            errors = []

            def run():
                try:
                    client.open_stream('shell:never')
                except Exception as e:
                    errors.append(e)

            thread = threading.Thread(target=run, daemon=True)
            thread.start()
            adbd.wait_for(lambda: adbd.opens == ['shell:never'])
            client.disconnect()
            thread.join(timeout=5)

        assert thread.is_alive() is False
        assert len(errors) == 1
        assert isinstance(errors[0], AdbConnectionClosed)
        assert 'while opening' in str(errors[0])

    def test_a_dropped_connection_wakes_a_reader(self):
        """A device that resets the link ends the reads of its streams"""
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(close=False)) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            stream = client.open_stream('shell:cat')
            errors = []

            def read():
                try:
                    stream.recv_until_close(timeout=20)
                except Exception as e:
                    errors.append(e)

            thread = threading.Thread(target=read, daemon=True)
            thread.start()
            start = time.perf_counter()
            adbd.drop_connections()
            thread.join(timeout=5)
            elapsed = time.perf_counter() - start

        assert thread.is_alive() is False
        assert len(errors) == 1
        assert isinstance(errors[0], AdbConnectionClosed)
        assert elapsed < 3

    def test_a_connection_the_device_dropped_is_rebuilt(self):
        """A device that restarts does not leave the client dead forever"""
        with FakeAdbd() as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.timeout = 0.3
            client.connect()
            try:
                adbd.drop_connections()
                assert adbd.wait_for(lambda: client._broken)

                # the first call fails, a connection that is known to be dead is
                # not used for a request that would only time out
                with pytest.raises(AdbConnectionClosed):
                    client.open_stream('shell:x')

                # connect() rebuilds the connection, the next call works
                client.connect()
                assert adbd.wait_for(lambda: adbd.connections == 2)
                with client.open_stream('shell:x') as stream:
                    assert stream.recv_until_close(timeout=5) == b''
            finally:
                client.disconnect()


class _HalfCloseSocket:
    """A socket whose half close ends the reader, the way a device answers a FIN"""

    def __init__(self, reader_ended, answer=True):
        """
        Args:
            reader_ended (threading.Event): Set when the reader of the socket ends
            answer (bool): True to end the reader on the half close, False to
                stand for a device that never answers
        """
        self.calls = []
        self._reader_ended = reader_ended
        self._answer = answer

    def shutdown(self, how):
        self.calls.append(('shutdown', how))
        if self._answer and how == socket.SHUT_WR:
            self._reader_ended.set()

    def close(self):
        # the flag tells whether the reader ended before the descriptor did
        self.calls.append(('close', self._reader_ended.is_set()))


class _ClosedSocket:
    """A socket the peer already closed"""

    def __init__(self):
        self.calls = []

    def shutdown(self, how):
        self.calls.append(('shutdown', how))
        raise OSError(9, 'Bad file descriptor')

    def close(self):
        self.calls.append(('close',))


class TestGracefulClose:
    """The socket is half closed, the reader ends, then the socket is closed"""

    @staticmethod
    def reader(ended):
        """
        Start a thread that stands for the dispatch thread of a connection.

        Args:
            ended (threading.Event): Event the thread waits on

        Returns:
            threading.Thread: The started thread
        """
        thread = threading.Thread(target=ended.wait, daemon=True)
        thread.start()
        return thread

    def test_the_half_close_comes_before_the_join(self):
        """The FIN goes out first, the descriptor is closed after the reader

        `SHUT_WR` leaves the receive direction open: the FIN of the device is
        what wakes the reader, and closing the read side here may drop it.
        """
        reader_ended = threading.Event()
        sock = _HalfCloseSocket(reader_ended)

        AdbProtocolTCP._graceful_close(sock, self.reader(reader_ended))

        assert sock.calls == [('shutdown', socket.SHUT_WR), ('close', True)]

    def test_a_device_that_does_not_answer_is_given_the_timeout(self, monkeypatch):
        """A reader that does not end is waited for at most the join timeout"""
        monkeypatch.setattr(tcp_protocol, 'DISCONNECT_JOIN_TIMEOUT', 0.1)
        reader_ended = threading.Event()
        sock = _HalfCloseSocket(reader_ended, answer=False)
        thread = self.reader(reader_ended)

        start = time.perf_counter()
        AdbProtocolTCP._graceful_close(sock, thread)
        elapsed = time.perf_counter() - start

        # join() may return a little before the timeout it was given, the
        # resolution of the clock on Windows is 15.6ms
        assert elapsed >= 0.05
        # the descriptor is released even though the reader still waits
        assert sock.calls == [('shutdown', socket.SHUT_WR), ('close', False)]

        reader_ended.set()
        thread.join(timeout=1)

    def test_a_socket_the_peer_already_closed_is_still_released(self):
        """A dead link ends with the close instead of an error"""
        sock = _ClosedSocket()

        AdbProtocolTCP._graceful_close(sock, None)

        assert sock.calls == [('shutdown', socket.SHUT_WR), ('close',)]


class TestConnectionRelease:
    """A connection is released at a known point, not by the garbage collector

    The dispatch thread holds the client object for as long as the connection
    is open, so an unreferenced client is not collected and a `__del__` would
    never run for the connection a caller forgot. The `with` block is the
    release point, and a connection that died releases its descriptor itself.
    """

    def test_with_connects_and_disconnects(self):
        """The block runs on a live connection, the block end closes it"""
        with FakeAdbd() as adbd:
            with AdbProtocolTCP(port=adbd.port) as client:
                thread = client._recv_thread
                assert client._sock is not None
                assert client.features.shell_v2 is True
                assert thread is not None and thread.is_alive()

            assert client._sock is None
            assert client._recv_thread is None
            thread.join(timeout=5)
            assert not thread.is_alive()

    def test_with_releases_on_an_error(self):
        """An error in the block closes the connection and keeps propagating"""
        with FakeAdbd() as adbd:
            with pytest.raises(ValueError) as e:
                with AdbProtocolTCP(port=adbd.port) as client:
                    raise ValueError('of the block')

            assert str(e.value) == 'of the block'
            assert client._sock is None

    def test_a_broken_connection_releases_its_descriptor(self):
        """A connection the device dropped does not keep its socket open"""
        with FakeAdbd() as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.timeout = 0.3
            client.connect()
            try:
                sock = client._sock
                assert sock.fileno() != -1

                adbd.drop_connections()
                adbd.wait_for(lambda: sock.fileno() == -1, message='the descriptor is still open')

                # the object is kept for the error message and for a reconnect
                assert client._sock is sock
                assert client._broken is True
                with pytest.raises(AdbConnectionClosed) as e:
                    client.open_stream('shell:x')
                assert 'broken' in str(e.value)
            finally:
                # a socket already closed is not an error of the disconnect
                client.disconnect()

        assert client._sock is None


class TestConnectionLifecycle:
    """A client owned for a long time: ensure, retry, release

    A game script owns one client for the life of a worker and runs a command
    when it needs one; it has no place for a `with` block around the business
    code. The client supports that shape with three calls: connect() is the
    ensure step, disconnect() then connect() is the retry step of a call that
    lost the link, and disconnect() is the release at the end of the worker.
    """

    @staticmethod
    def retrying_command(client, service, tries=3):
        """
        Run one command the way a device module does.

        Args:
            client (AdbProtocolTCP): Client owned by the caller
            service (str): Service name to open
            tries (int): Attempts before the error is given to the caller

        Returns:
            bytes: Everything the stream received
        """
        for attempt in range(tries):
            try:
                # free on a healthy connection, rebuilds a dead one
                client.connect()
                with client.open_stream(service) as stream:
                    return stream.recv_until_close(timeout=5)
            except (AdbConnectionClosed, AdbConnectionTimeout):
                if attempt + 1 == tries:
                    raise
                client.disconnect()
                client.connect()

    def test_is_connected_follows_the_socket(self):
        """The flag a retry loop reads before a call"""
        with FakeAdbd() as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            assert client.is_connected is False

            client.connect()
            assert client.is_connected is True

            client.disconnect()
            assert client.is_connected is False

    def test_is_connected_after_the_device_dropped_the_link(self):
        """A link the device dropped is not a connection"""
        with FakeAdbd() as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                adbd.drop_connections()
                assert adbd.wait_for(lambda: not client.is_connected, message='still connected')
            finally:
                client.disconnect()

    def test_disconnect_then_connect_replaces_the_connection(self):
        """The two calls a retry loop makes open a second connection"""
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(stdout=b'again\n')) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                first_sock = client._sock
                first_thread = client._recv_thread

                client.disconnect()
                client.connect()

                assert client.is_connected is True
                assert client._sock is not first_sock
                assert client._recv_thread is not first_thread
                first_thread.join(timeout=5)
                assert not first_thread.is_alive()
                assert adbd.connections == 2
                with client.open_stream('shell:echo again') as stream:
                    assert stream.recv_until_close(timeout=5) == b'again\n'
            finally:
                client.disconnect()

    def test_connect_after_the_device_restarted(self):
        """The ensure step brings a client whose link died back to a device"""
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(stdout=b'back\n')) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                adbd.drop_connections()
                adbd.wait_for(lambda: not client.is_connected, message='still connected')

                client.connect()

                assert client.is_connected is True
                with client.open_stream('shell:echo back') as stream:
                    assert stream.recv_until_close(timeout=5) == b'back\n'
            finally:
                client.disconnect()

    def test_a_new_connection_does_not_rewind_the_stream_ids(self):
        """A message of the old connection can not reach a stream of the new one"""
        with FakeAdbd() as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                with client.open_stream('shell:echo hi') as stream:
                    used = stream.local_id

                client.disconnect()
                client.connect()

                with client.open_stream('shell:echo hi') as stream:
                    assert stream.local_id > used
            finally:
                client.disconnect()

    def test_concurrent_connect_opens_one_connection(self):
        """Many callers of the ensure step share the one connection"""
        with FakeAdbd() as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            start = threading.Event()
            errors = []

            def connect():
                start.wait()
                try:
                    client.connect()
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=connect, daemon=True) for _ in range(8)]
            for thread in threads:
                thread.start()
            start.set()
            for thread in threads:
                thread.join(timeout=10)
            try:
                assert errors == []
                assert adbd.connections == 1
                assert client.is_connected is True
            finally:
                client.disconnect()

    def test_the_owner_pattern_survives_a_dropped_link(self):
        """The ensure step repairs the link before the command is sent"""
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(stdout=b'ok\n')) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                assert self.retrying_command(client, 'shell:echo 1') == b'ok\n'

                adbd.drop_connections()
                adbd.wait_for(lambda: not client.is_connected, message='still connected')

                # the caller never sees the failure of the link
                assert self.retrying_command(client, 'shell:echo 2') == b'ok\n'
                assert adbd.connections == 2
            finally:
                client.disconnect()

    def test_the_owner_pattern_retries_after_a_lost_link(self):
        """A command that lost the link midway is repeated on a new one

        A device that restarts while an OPEN waits is the case the retry step
        exists for: the ensure step was already done, and only the call itself
        can report that the link died.
        """
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(stdout=b'hi\n')) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            results, errors = [], []

            def run():
                try:
                    results.append(self.retrying_command(client, 'shell:echo hi'))
                except Exception as e:
                    errors.append(e)

            adbd.open_fault = 'silent'
            thread = threading.Thread(target=run, daemon=True)
            thread.start()
            try:
                adbd.wait_for(lambda: adbd.opens == ['shell:echo hi'])
                adbd.open_fault = ''
                adbd.drop_connections()
                thread.join(timeout=10)

                assert thread.is_alive() is False
                assert errors == []
                assert results == [b'hi\n']
                assert adbd.connections == 2
            finally:
                client.disconnect()


class TestLogging:
    """The lines the transport writes, read with the logger capture

    The dispatch thread writes as well, so a line produced by it is awaited in
    the capture rather than checked after a sleep.
    """

    @staticmethod
    def wait_for_line(capture, text, timeout=5):
        """
        Wait until a line appears in the capture

        Args:
            capture (CaptureWriter): Writer of `logger.mock_capture_writer()`
            text (str): Line or part of a line to wait for
            timeout (float): Seconds to wait. Defaults to 5.

        Returns:
            str: The whole captured text
        """
        timeout = time.monotonic() + timeout
        while time.monotonic() < timeout:
            text_now = capture.concat()
            if text in text_now:
                return text_now
            time.sleep(0.01)
        raise AssertionError(f'{text!r} was not logged, the capture holds {capture.concat()!r}')

    def test_the_lines_of_a_connection(self):
        """A connect and a disconnect write one line each, in order"""
        with FakeAdbd() as adbd:
            addr = f'127.0.0.1:{adbd.port}'
            client = AdbProtocolTCP(port=adbd.port)
            with logger.mock_capture_writer() as capture:
                client.connect()
                client.disconnect()

        assert capture.concat() == f"""\
INFO | Connecting to {addr}
INFO | Connected to {addr}
INFO | Disconnecting to {addr}
INFO | Disconnected to {addr}
"""

    def test_a_second_connect_writes_nothing(self):
        """The ensure step of a healthy connection is silent"""
        with FakeAdbd() as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                with logger.mock_capture_writer() as capture:
                    client.connect()

                assert capture.concat() == ''
            finally:
                client.disconnect()

    def test_a_second_disconnect_writes_nothing(self):
        """Releasing a released client is silent"""
        with FakeAdbd() as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            client.disconnect()

            with logger.mock_capture_writer() as capture:
                client.disconnect()

            assert capture.concat() == ''

    def test_the_line_of_a_broken_connection(self):
        """The dispatch thread names the address and the reason of the end"""
        with FakeAdbd() as adbd:
            addr = f'127.0.0.1:{adbd.port}'
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                with logger.mock_capture_writer() as capture:
                    adbd.drop_connections()
                    text = self.wait_for_line(capture, 'is broken')

                # the thread was waiting for the header of the next message
                prefix = f'WARNING | Connection to {addr} is broken, receiving ended: AdbConnectionClosed: '
                assert text.startswith(prefix)
                assert 'receiving header' in text[len(prefix):]
                assert text.count('\n') == 1
            finally:
                client.disconnect()

    def test_the_line_of_a_stream_closed_without_a_remote_id(self):
        """A stream the device never opened says why it is not closable"""
        protocol = AdbProtocolTCP()
        protocol._sock = _NullSock()
        stream = make_stream(state='opening')

        with logger.mock_capture_writer() as capture:
            protocol.close_stream(stream)

        assert capture.concat() == f'WARNING | Cannot close stream without remote_id: {stream}\n'


class TestConnectFailure:
    """The handshake that does not end in a CNXN"""

    def test_a_refused_connect(self, monkeypatch):
        """A connect the system refuses is reported as refused, not as a timeout

        socket.timeout is an OSError, a handler that catches OSError first would
        report a refused connection as a slow one. The OS itself is not used to
        raise it: a firewall that drops the SYN of a dead loopback port answers
        with a timeout, which is a property of the machine and not of adb.
        """

        class RefusingSocket:
            """A socket whose connect is refused, the way a dead port answers"""

            def __init__(self, *args, **kwargs):
                pass

            def settimeout(self, value):
                pass

            def setsockopt(self, *args):
                pass

            def connect(self, address):
                raise ConnectionRefusedError(10061, 'No connection could be made')

            def close(self):
                pass

            def shutdown(self, how):
                pass

        monkeypatch.setattr(tcp_protocol.socket, 'socket', RefusingSocket)
        client = AdbProtocolTCP(port=5555)
        client.timeout = 1

        with pytest.raises(ConnectionRefusedError):
            client.connect()

        assert client._sock is None

    def test_a_device_that_never_answers(self):
        """A silent device is a timeout, not a closed connection

        socket.timeout is an OSError, a handler that catches OSError first
        reports a slow device as a closed one.
        """
        with FakeAdbd() as adbd:
            adbd.silent = True
            client = AdbProtocolTCP(port=adbd.port)
            client.timeout = 0.3

            with pytest.raises(AdbConnectionTimeout):
                client.connect()

        assert client._sock is None

    def test_a_connect_that_times_out(self, monkeypatch):
        """The connect itself is bounded by the timeout of the connection"""
        calls = []

        class TimeoutSocket:
            """A socket whose connect times out, the way an unreachable host does"""

            def __init__(self, *args, **kwargs):
                calls.append('socket')

            def settimeout(self, value):
                calls.append(value)

            def setsockopt(self, *args):
                pass

            def connect(self, address):
                raise socket.timeout('timed out')

            def close(self):
                pass

            def shutdown(self, how):
                pass

        monkeypatch.setattr(tcp_protocol.socket, 'socket', TimeoutSocket)
        client = AdbProtocolTCP(host='192.0.2.1', port=5555)
        client.timeout = 0.2

        with pytest.raises(AdbConnectionTimeout):
            client.connect()

        assert client._sock is None
        # the timeout was set before the connect, which is what bounds it
        assert calls == ['socket', 0.2]

    def test_a_device_that_requires_authentication_without_a_key(self, monkeypatch):
        """A secure device fails the connect when this user has no adb key"""

        def no_key():
            raise AdbAuthKeyMissing('No adb key at /nonexistent')

        monkeypatch.setattr(keygen, 'load_user_key', no_key)
        with FakeAdbd() as adbd:
            adbd.auth_required = True
            client = AdbProtocolTCP(port=adbd.port)
            client.timeout = 1

            with pytest.raises(AdbAuthKeyMissing):
                client.connect()

        assert adbd.auth_challenges == 1
        assert client._sock is None

    def test_a_failed_handshake_leaves_no_state(self):
        """The features and the payload of a dead handshake are the defaults"""
        with FakeAdbd() as adbd:
            adbd.fault_on_reply = 'bad_magic'
            client = AdbProtocolTCP(port=adbd.port)
            client.timeout = 1
            client._max_payload = 42
            client.features = 'stale'

            with pytest.raises(AdbMessageInvalid):
                client.connect()

        assert client._max_payload == ADB_MAX_PAYLOAD
        assert client.features.features == []

    def test_a_device_of_another_protocol_version(self):
        """The version of the device is not checked, the checksum is"""
        with FakeAdbd(version=ADB_VERSION_SKIP_CHECKSUM) as adbd:
            client = AdbProtocolTCP(port=adbd.port)
            client.connect()
            try:
                with client.open_stream('shell:x') as stream:
                    assert stream.recv_until_close(timeout=5) == b''
            finally:
                client.disconnect()

        assert adbd.violations == []


class TestRecvExact:
    """Reading the bytes of a message"""

    @staticmethod
    def socket_pair():
        """
        Build a pair of connected sockets with a short timeout

        Returns:
            tuple: (reader, writer)
        """
        reader, writer = socket.socketpair()
        reader.settimeout(0.2)
        return reader, writer

    def test_reads_a_message_in_one_go(self):
        """The fast path of a header that arrives whole"""
        reader, writer = self.socket_pair()
        try:
            writer.sendall(b'x' * 24)
            assert AdbProtocolTCP.recv_exact(reader, 24) == b'x' * 24
        finally:
            reader.close()
            writer.close()

    def test_reads_a_message_in_pieces(self):
        """A header that arrives in pieces is reassembled"""
        reader, writer = self.socket_pair()

        def write():
            for piece in (b'abcdefgh', b'ijklmnop', b'qrstuvwx'):
                writer.sendall(piece)
                time.sleep(0.01)

        thread = threading.Thread(target=write, daemon=True)
        thread.start()
        try:
            assert AdbProtocolTCP.recv_exact(reader, 24) == b'abcdefghijklmnopqrstuvwx'
        finally:
            thread.join(timeout=5)
            reader.close()
            writer.close()

    def test_a_closed_peer(self):
        """A peer that closes the connection is not a timeout"""
        reader, writer = self.socket_pair()
        writer.close()
        try:
            with pytest.raises(AdbConnectionClosed):
                AdbProtocolTCP.recv_exact(reader, 24)
        finally:
            reader.close()

    def test_a_timeout(self):
        """A peer that sends nothing is a timeout"""
        reader, writer = self.socket_pair()
        try:
            with pytest.raises(AdbConnectionTimeout):
                AdbProtocolTCP.recv_exact(reader, 24)
        finally:
            reader.close()
            writer.close()

    def test_a_closed_peer_in_the_middle_of_a_message(self):
        """A connection that dies inside a message is a closed connection"""
        reader, writer = self.socket_pair()
        writer.sendall(b'abc')
        writer.close()
        try:
            with pytest.raises(AdbConnectionClosed) as e:
                AdbProtocolTCP.recv_exact(reader, 24)
            assert 'when receiving chunk' in str(e.value)
        finally:
            reader.close()

    def test_without_a_header_timeout(self):
        """The dispatch thread waits through the idle timeouts of the socket"""
        reader, writer = self.socket_pair()

        def write_late():
            time.sleep(0.3)
            writer.sendall(b'z' * 24)

        thread = threading.Thread(target=write_late, daemon=True)
        thread.start()
        try:
            start = time.perf_counter()
            assert AdbProtocolTCP.recv_exact(reader, 24, header_timeout=False) == b'z' * 24
            assert time.perf_counter() - start >= 0.25
        finally:
            thread.join(timeout=5)
            reader.close()
            writer.close()
