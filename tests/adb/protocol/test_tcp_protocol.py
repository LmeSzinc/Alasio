import socket
import threading
import time
from struct import Struct

import pytest

import alasio.adb.protocol.tcp_protocol as tcp_protocol
from alasio.adb.protocol.const import (
    ADB_MAX_PAYLOAD, ADB_VERSION, CLSE, CNXN, OKAY, OPEN, WRTE, AdbConnectionTimeout, AdbStreamTimeout
)
from alasio.adb.protocol.tcp import AdbTCP
from alasio.adb.protocol.tcp_protocol import AdbProtocolTCP, AdbStreamTCP

_MESSAGE = Struct('<6I')


class _Protocol:
    """A stream only asks its protocol to close it"""

    def __init__(self):
        self.closed = []

    def close_stream(self, stream):
        self.closed.append(stream)


class _Socket:
    """The protocol only sends through sendall() while a stream opens"""

    def __init__(self):
        self.sent = []

    def sendall(self, data):
        self.sent.append(data)


def make_stream(state='opened'):
    """
    Build a stream without a connection

    Args:
        state (str): Initial state of the stream

    Returns:
        AdbStreamTCP: The stream
    """
    stream = AdbStreamTCP(local_id=1, service='shell:echo', protocol=_Protocol())
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


class TestRecvUntilClose:
    """Reading a stream until the device closes it"""

    def test_returns_the_buffered_data_when_the_stream_closes(self):
        """Every message received before the close is returned in order"""
        stream = make_stream()
        stream.data_buffer.append(b'abc')
        stream.data_buffer.append(b'def')
        stream.state = 'closed'

        assert stream.recv_until_close(timeout=1) == b'abcdef'

    def test_clears_the_buffer_after_returning_it(self):
        """The buffer is drained, a second read does not repeat the data"""
        stream = make_stream()
        stream.data_buffer.append(b'abc')
        stream.state = 'closed'

        assert stream.recv_until_close(timeout=1) == b'abc'
        assert list(stream.data_buffer) == []

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
        protocol._sock = _Socket()
        protocol.timeout = 0.2

        start = time.perf_counter()
        with pytest.raises(AdbConnectionTimeout):
            protocol.open_stream('shell:echo')
        elapsed = time.perf_counter() - start

        assert elapsed >= 0.15


class TestOpenStream:
    """Opening a stream"""

    def test_times_out_when_the_device_never_answers(self):
        """A device that does not answer OPEN is given up on at the timeout"""
        protocol = AdbProtocolTCP()
        protocol._sock = _Socket()
        protocol.timeout = 0.2

        start = time.perf_counter()
        with pytest.raises(AdbConnectionTimeout):
            protocol.open_stream('shell:echo')
        elapsed = time.perf_counter() - start

        assert 0.15 < elapsed < 2.0

    def test_sends_open_before_waiting(self):
        """The service name is sent as the payload of OPEN"""
        protocol = AdbProtocolTCP()
        sock = _Socket()
        protocol._sock = sock
        protocol.timeout = 0.05

        with pytest.raises(AdbConnectionTimeout):
            protocol.open_stream('shell:echo hi')

        assert len(sock.sent) == 1
        command, arg0, arg1, data_length, _check, _magic = _MESSAGE.unpack(sock.sent[0][:24])
        assert command.to_bytes(4, 'little') == OPEN
        assert sock.sent[0][24:] == b'shell:echo hi\x00'
        assert data_length == len(b'shell:echo hi\x00')

    def test_drops_the_stream_when_the_open_fails(self):
        """A failed open leaves no stream and no allocated id behind"""
        protocol = AdbProtocolTCP()
        protocol._sock = _Socket()
        protocol.timeout = 0.05
        before = set(protocol._stream_id_pool)

        with pytest.raises(AdbConnectionTimeout):
            protocol.open_stream('shell:echo')

        assert protocol._stream_dict == {}
        assert set(protocol._stream_id_pool) == before


def _pack(command, arg0, arg1, data=b''):
    """
    Build one adb message

    Args:
        command (bytes): Command name
        arg0 (int): First argument
        arg1 (int): Second argument
        data (bytes): Payload

    Returns:
        bytes: The complete message
    """
    value = int.from_bytes(command, 'little')
    header = _MESSAGE.pack(value, arg0, arg1, len(data), sum(data) & 0xFFFFFFFF, value ^ 0xFFFFFFFF)
    return header + data


def _recv_exact(sock, length):
    """
    Read exactly length bytes

    Args:
        sock (socket.socket): Connected socket
        length (int): Bytes to read

    Returns:
        bytes | None: The data, None when the peer closed the connection
    """
    buffer = b''
    while len(buffer) < length:
        chunk = sock.recv(length - len(buffer))
        if not chunk:
            return None
        buffer += chunk
    return buffer


def _read_message(sock):
    """
    Read one adb message

    Args:
        sock (socket.socket): Connected socket

    Returns:
        tuple | None: (command, arg0, arg1, data), None at the end of the stream
    """
    header = _recv_exact(sock, 24)
    if header is None:
        return None
    command, arg0, arg1, length, _check, _magic = _MESSAGE.unpack(header)
    data = b''
    if length:
        data = _recv_exact(sock, length)
        if data is None:
            return None
    return command.to_bytes(4, 'little'), arg0, arg1, data


class _FakeAdbd(threading.Thread):
    """A minimal adbd: the CNXN handshake, then one shell stream per OPEN"""

    def __init__(self, reply):
        super().__init__(daemon=True)
        self.reply = reply
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(('127.0.0.1', 0))
        self.server.listen(8)
        self.port = self.server.getsockname()[1]

    def run(self):
        while True:
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(connection,), daemon=True).start()

    def _serve(self, sock):
        try:
            message = _read_message(sock)
            if not message or message[0] != CNXN:
                return
            sock.sendall(_pack(CNXN, ADB_VERSION, ADB_MAX_PAYLOAD, b'device::features=shell_v2,cmd'))
            while True:
                message = _read_message(sock)
                if message is None:
                    return
                command, arg0, arg1, _data = message
                if command == OPEN:
                    local, remote = arg0, arg0 + 1000
                    sock.sendall(_pack(OKAY, remote, local))
                    body = (
                        bytes([1]) + len(self.reply).to_bytes(4, 'little') + self.reply
                        + bytes([3]) + (1).to_bytes(4, 'little') + b'\x00'
                    )
                    sock.sendall(_pack(WRTE, remote, local, body))
                    sock.sendall(_pack(CLSE, remote, local))
                elif command == WRTE:
                    sock.sendall(_pack(OKAY, arg1, arg0))
                elif command == CLSE:
                    return
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def close(self):
        self.server.close()


class TestProtocolRoundTrip:
    """The protocol against a device that speaks it"""

    def test_a_shell_call_returns_the_device_output(self):
        """One shell call opens a stream, reads it to the close and parses it"""
        adbd = _FakeAdbd(b'hello')
        adbd.start()
        client = AdbTCP(host='127.0.0.1', port=adbd.port)
        try:
            client.connect()
            result = client.shell('echo hello')
        finally:
            client.disconnect()
            adbd.close()

        assert result.stdout == b'hello'
        assert result.exitcode == 0
        assert result.shell_v2 is True

    def test_repeated_shell_calls_reuse_the_connection(self):
        """The connection outlives the streams, one connect serves many calls"""
        adbd = _FakeAdbd(b'x')
        adbd.start()
        client = AdbTCP(host='127.0.0.1', port=adbd.port)
        try:
            client.connect()
            for _ in range(20):
                assert client.shell('echo x').stdout == b'x'
            # every stream id was released: the next call reuses a free one
            assert client._stream_dict == {}
        finally:
            client.disconnect()
            adbd.close()
