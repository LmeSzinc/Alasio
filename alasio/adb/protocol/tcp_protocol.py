import socket
from select import select
from struct import Struct
from threading import Lock, Thread, current_thread
from time import perf_counter
from typing import Literal

from alasio.adb.protocol import keygen
from alasio.adb.protocol.cnxn import DeviceFeatures
from alasio.adb.protocol.const import (
    ADB_AUTH_CONFIRM_TIMEOUT, ADB_AUTH_RSAPUBLICKEY, ADB_AUTH_SIGNATURE, ADB_AUTH_TOKEN, ADB_COMMANDS, ADB_MAX_PAYLOAD,
    ADB_MAX_PAYLOAD_V1, ADB_STREAM_CHUNK_LIMIT, ADB_STREAM_CHUNK_SIZE, ADB_STREAM_RECV_BUFFER_SIZE, ADB_VERSION,
    ADB_VERSION_SKIP_CHECKSUM, AUTH, AUTH_TOKEN_SIZE, CLSE, CNXN, OKAY, OPEN, WRTE, AdbAuthRejected,
    AdbConnectionClosed, AdbConnectionTimeout, AdbMessageInvalid, AdbStreamClosed, AdbStreamTimeout
)
from alasio.logger import logger

# Header of every message of the transport: command, arg0, arg1, data length,
# data checksum and magic, six little endian unsigned 32 bit integers
MESSAGE_STRUCT = Struct('<6I')

# Seconds disconnect() waits for the dispatch thread to end after the half
# close it sends. A device answers the FIN of the half close by closing its
# side, so this is only spent on a link that is already gone.
DISCONNECT_JOIN_TIMEOUT = 2


def _join_chunks(chunks: "list[bytes]") -> bytes:
    """
    Build the output of a stream from the chunks of its buffer.

    A device that sent its whole output in one write is the common case, and
    the chunk of that write is handed back as the object the reader of the
    connection built, so its payload is not copied at all.

    Args:
        chunks (list[bytes]): Chunks of the buffer, in order

    Returns:
        bytes: The chunks as one bytes object
    """
    if not chunks:
        return b''
    if len(chunks) == 1:
        return chunks[0]
    return b''.join(chunks)


class AdbStreamTCP:
    def __init__(self, local_id: int, service: str, protocol: "AdbProtocolTCP",
                 recv_buffer_size: int = ADB_STREAM_RECV_BUFFER_SIZE):
        self.local_id = local_id
        self.remote_id = 0  # will be set after A_OPEN
        self.service = service
        self.protocol = protocol

        self.state: "Literal['opening', 'opened', 'closing', 'closed']" = 'opening'
        # Internal event that will be set when message received
        self.send_event = Lock()
        self.send_event.acquire()  # service is send on stream opening
        self.recv_event = Lock()
        # True once the CLSE of this side was sent. A stream ends when both
        # sides sent one, the device keeps a stream the host never answered,
        # and a kept stream makes the device refuse the next OPEN that uses the
        # same local id.
        self.close_sent = False
        self._close_lock = Lock()
        # True when the connection was dropped under the stream: the output is
        # incomplete, and a caller must not mistake it for the output of a
        # command that ended
        self.aborted = False

        # Bytes of output to hold before the acknowledgements of the device are
        # held back, see ADB_STREAM_RECV_BUFFER_SIZE
        self._recv_buffer_size = recv_buffer_size
        # Output the stream holds, in the order the device sent it. A chunk is
        # the bytes object the reader of the connection built, so a write goes
        # in and comes out without being copied, and the output of a single
        # write is handed back as the very same object. The dispatch thread
        # appends and the reader takes, so the output is guarded by one lock
        self._chunks: "list[bytes]" = []
        # Writes too small to be worth an object of their own, and their total
        # size. They are the tail of the output, behind _chunks, and are combined
        # once they make up ADB_STREAM_CHUNK_SIZE, so a device that splits its
        # output into many small writes does not leave one object per write
        # behind. A write of that size or larger never comes here, so a large
        # transfer is not copied into a second object
        self._run: "list[bytes]" = []
        self._run_size = 0
        # Bytes of output the reader has not consumed yet, the output that holds
        # the device back while it is at the buffer size
        self._pending_size = 0
        # Writes of the device this stream did not acknowledge yet. A device
        # sends the next write of a stream only after the previous one was
        # acknowledged, so an acknowledgement that is held back stalls it. It
        # is at most 1 for a device that keeps to the protocol, because this
        # stream never holds one back while its buffer has room
        self._okay_debt = 0
        self._buffer_lock = Lock()

    def __str__(self):
        return (f'{self.__class__.__name__}(remote_id={self.remote_id}, local_id={self.local_id}, '
                f'state={self.state}, service={self.service})')

    __repr__ = __str__

    def mark_close_sent(self) -> bool:
        """
        Claim the CLSE of this stream.

        Returns:
            bool: True for the caller that has to send it, False for every
                caller after that
        """
        with self._close_lock:
            if self.close_sent:
                return False
            self.close_sent = True
            return True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self.close()
        except (AdbConnectionClosed, AdbConnectionTimeout) as e:
            # a close that fails must not replace the error of the block, that
            # error is what the caller has to see
            if exc_type is None:
                raise
            logger.warning(f'Failed to close stream after an error: {e}')

    def close(self):
        """
        Raises:
            AdbConnectionClosed:
            AdbConnectionTimeout:
        """
        self.protocol.close_stream(self)

    def add_data(self, data: bytes) -> bool:
        """
        Store the payload of a write of the device.

        Args:
            data (bytes): Payload of the write

        Returns:
            bool: True when the write has to be acknowledged at once, False
                when the buffer is full and the acknowledgement is held back
                until the reader consumes the output
        """
        with self._buffer_lock:
            if len(data) < ADB_STREAM_CHUNK_SIZE:
                # a device can split its output into many small writes, and one
                # object per write costs more in headers than the output itself.
                # They wait here until they are worth an object of their own
                self._run.append(data)
                self._run_size += len(data)
                if self._run_size >= ADB_STREAM_CHUNK_SIZE or len(self._run) >= ADB_STREAM_CHUNK_LIMIT:
                    self._chunks.append(self._combine_run())
            else:
                # a write this large is worth an object already, and combining
                # it would copy the whole of a large transfer a second time
                if self._run:
                    self._chunks.append(self._combine_run())
                self._chunks.append(data)
            self._pending_size += len(data)
            if self._pending_size < self._recv_buffer_size:
                return True
            self._okay_debt += 1
            return False

    def take_data(self) -> bytes:
        """
        Take the output the stream holds and let the device send more.

        Returns:
            bytes: Everything held, in order. Empty bytes for nothing held,
                which acknowledges nothing
        """
        chunks = self.take_chunks()
        return _join_chunks(chunks)

    def take_chunks(self) -> "list[bytes]":
        """
        Take the output the stream holds and let the device send more.

        The chunks are handed over as they are, which is what a caller that
        collects the output of many writes wants: it can join them once instead
        of once per write. A read that timed out consumed the output it held but
        left it here, and it is handed over by the read that returns it, in
        order and once.

        Returns:
            list[bytes]: Everything held, in order. An empty list for nothing
                held, which acknowledges nothing
        """
        with self._buffer_lock:
            if not self._chunks and not self._run:
                # nothing was taken, so nothing can be released: a reader that
                # wakes without data must not pull a write of the device into
                # a buffer that is still full
                return []
            if self._run:
                self._chunks.append(self._combine_run())
            chunks = self._chunks
            self._chunks = []
            self._pending_size = 0
            debt = self._payable_debt()
        if debt:
            self.protocol.acknowledge_stream(self, debt)
        return chunks

    def _consume(self):
        """
        Consume the output that arrived since the last consume.

        Consuming pays for the writes of the device and leaves the output where
        it is, so a read that ends in a timeout keeps it instead of putting it
        back: the acknowledgement is on the wire already, and output back in the
        buffer would be output the device sends again. The output is handed over
        by the read that sees the close, once, in order.
        """
        with self._buffer_lock:
            if not self._pending_size:
                # a reader that wakes without data must not pull a write of the
                # device into a buffer that is still full
                return
            self._pending_size = 0
            debt = self._payable_debt()
        if debt:
            self.protocol.acknowledge_stream(self, debt)

    def _combine_run(self) -> bytes:
        """
        Combine the small writes the stream holds into one object.

        The caller holds the buffer lock.

        Returns:
            bytes: The combined writes, in order
        """
        run = _join_chunks(self._run)
        self._run = []
        self._run_size = 0
        return run

    def _payable_debt(self) -> int:
        """
        Claim the acknowledgements the reader made room for.

        The caller holds the buffer lock. A held back acknowledgement is only
        released when the buffer has room again: released while it is still
        full, the device would send the next write into a full buffer, and the
        reader would wait for output a stalled device never sends.

        Returns:
            int: Number of acknowledgements to send
        """
        if self._pending_size >= self._recv_buffer_size:
            return 0
        debt = self._okay_debt
        self._okay_debt = 0
        return debt

    def recv_until_close(self, timeout: "int | float" = 20) -> bytes:
        """
        Receive data of the stream until the device closes it.

        With the buffer of chunks: the output is consumed as it arrives so a full
        buffer can not stall the device into a close it never sends, and nothing
        is moved on a timeout, so the next call returns the output of the device
        once, in order.

        Args:
            timeout (int | float): Seconds to wait for the close. Defaults to 20.

        Returns:
            bytes: Everything received on the stream, in order

        Raises:
            AdbStreamTimeout: When the device does not close the stream in time
            AdbConnectionClosed: When the connection was dropped under the
                stream, its output is incomplete
        """
        # the state decides first: the data of an already closed stream is
        # returned whatever the timeout is, and a signal of an earlier message
        # can not stand for the close
        while self.state != 'closed' and self.state != 'closing':
            # the accounting is only reached when a message woke the wait, and
            # the clock must not move under it: a wall clock correction would
            # stretch the timeout by the size of the jump, or cut it short
            if timeout < 0.001:
                # nothing to undo: the output stays in the buffer, consumed or
                # not, and a caller that retries gets all of it
                raise AdbStreamTimeout('Timeout when receiving stream until close')
            start = perf_counter()
            self.recv_event.acquire(timeout=timeout)
            timeout -= perf_counter() - start
            self._consume()

        if self.aborted:
            raise AdbConnectionClosed(f'Connection lost while receiving the stream: {self}')

        # the last write of a device may arrive together with its close
        self._consume()
        # the chunks are joined once, at the end
        return _join_chunks(self.take_chunks())


class AdbProtocolTCP:
    def __init__(self, host='127.0.0.1', port=5555):
        """
        Args:
            host (str):
            port (int):
        """
        super().__init__()
        self.host = host
        self.port = port
        # Seconds of a request: the deadline of the whole handshake, of a
        # message, and of the wait for an answer of the device
        self.timeout = 5

        self._connect_lock = Lock()
        self._send_lock = Lock()
        self._sock: "socket.socket | None" = None
        self._recv_thread: "Thread | None" = None
        # True when the receiving thread gave up on the connection, the socket
        # object is still here but it can neither read nor write any more
        self._broken = False

        # internal states
        # maximum payload the device accepts, init with our MAX_PAYLOAD
        self._max_payload = ADB_MAX_PAYLOAD
        # protocol version of this connection: the lower of ours and the one
        # the device answered with, the checksum is optional from 0x01000001 on
        self._protocol_version = ADB_VERSION

        # True while an outgoing message carries the sum of its payload. A
        # connection of ADB_VERSION_SKIP_CHECKSUM or newer ignores the field, so
        # the pass over every payload is only spent where it is read: the
        # handshake, which is sent before the version of the device is known,
        # always carries it
        self._send_checksum = True

        # local stream ID, a counter that is never rewound: the device may
        # still send a message of a stream that ended, and a reused id would
        # put that message on the stream of an unrelated caller
        self._stream_id_next = 1
        self._stream_dict: "dict[int, AdbStreamTCP]" = {}

        # device features
        self.features = DeviceFeatures([])

    def __enter__(self):
        """
        Connect to the device, for a `with` block.

        Returns:
            AdbProtocolTCP: This client, connected

        Raises:
            ConnectionRefusedError: When nothing listens on the address
            AdbConnectionTimeout: When the device does not answer in time
            AdbConnectionClosed: When the device closes the connection during
                the handshake
            AdbMessageInvalid: When the device does not answer with a CNXN
            AdbAuthKeyMissing: When this user has no adb key to authenticate with
            AdbAuthKeyInvalid: When the adb key of this user can not be used
            AdbAuthRejected: When the device does not accept the adb key of this
                user, for example because its user denied the dialog
        """
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Disconnect from the device, for a `with` block.

        A client can not release itself when it is garbage collected: the
        dispatch thread holds this object for as long as the connection is open
        (`Thread(target=self._task_dispatch_message)` keeps a bound method), so
        a `__del__` would only run after the connection already ended, and
        never for the connection a caller forgot. The `with` block is what
        releases the socket and joins the thread at a known point.
        """
        try:
            self.disconnect()
        except Exception as e:
            # a disconnect that fails must not replace the error of the block,
            # that error is what the caller has to see
            if exc_type is None:
                raise
            logger.warning(f'Failed to disconnect after an error: {e}')

    def _stream_id_allocate(self) -> int:
        local_id = self._stream_id_next
        self._stream_id_next += 1
        return local_id

    def _require_sock(self) -> socket.socket:
        """
        Get the socket of the connection.

        Returns:
            socket.socket: The connected socket

        Raises:
            AdbConnectionClosed: When there is no connection, or when the
                connection is known to be broken
        """
        sock = self._sock
        addr = f'{self.host}:{self.port}'
        if sock is None:
            raise AdbConnectionClosed(f'Not connected to {addr}, call connect() first')
        if self._broken:
            raise AdbConnectionClosed(f'Connection to {addr} is broken, call connect() again')
        return sock

    @staticmethod
    def _close_socket(sock: "socket.socket | None"):
        """
        Close a socket of this thread, sending a FIN instead of a RST.

        The caller must be the only thread that uses the socket: the thread
        that opened it, or the dispatch thread that read it. A close from
        another thread is what `_graceful_close()` exists to avoid.

        Args:
            sock (socket.socket): Socket to close, None is ignored
        """
        if sock is None:
            return
        # stop both directions before the close, so a close that still holds
        # unread data in the receive buffer sends a FIN and not a RST
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except (OSError, AttributeError):
            # not connected, already closed, or a socket stand-in without it
            pass
        try:
            sock.close()
        except OSError:
            pass

    @staticmethod
    def _graceful_close(sock: "socket.socket", thread: "Thread | None"):
        """
        Half close a socket, let its dispatch thread end, then close it.

        `shutdown(SHUT_WR)` sends a FIN and keeps the receive direction open,
        so the device answers with its own FIN and the dispatch thread ends on
        a readable event that every platform delivers. The descriptor is closed
        only after that thread is gone: a close from another thread is not able
        to interrupt a read that already waits on it on every platform, and it
        frees a descriptor the thread may still be using. See
        python/cpython#154224 for the macOS case.

        A device that does not answer the FIN is given
        `DISCONNECT_JOIN_TIMEOUT` seconds, after which the descriptor is closed
        anyway and the read of that thread ends on the close or on the timeout
        of the socket.

        Args:
            sock (socket.socket): Socket to close
            thread (Thread): Dispatch thread that reads it, None when there is none
        """
        try:
            try:
                # the receive direction stays open: the FIN of the device is
                # what wakes the dispatch thread, closing the read side here
                # may drop that FIN together with the data of the device
                sock.shutdown(socket.SHUT_WR)
            except (OSError, AttributeError):
                # already closed, or a socket stand-in without shutdown
                pass
            if thread is not None and thread.is_alive() and thread is not current_thread():
                thread.join(timeout=DISCONNECT_JOIN_TIMEOUT)
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _abort_streams(self):
        """
        Drop every stream of the connection and wake the callers waiting on it.

        The caller holds `_connect_lock`. A caller that waits on the stream of a
        connection that is gone must not wait for the timeout of the wait.
        """
        streams = self._stream_dict
        self._stream_dict = {}
        for stream in streams.values():
            stream.aborted = True
            stream.state = 'closed'
            for event in (stream.send_event, stream.recv_event):
                try:
                    event.release()
                except RuntimeError:
                    pass

    def _detach_connection(self):
        """
        Forget the current connection and wake the callers waiting on it.

        The caller holds `_connect_lock`. The socket and the thread come back
        to be released by the caller outside of that lock: the dispatch thread
        takes the same lock on its way out, so joining it here would deadlock.

        Returns:
            tuple: (socket, thread), both None when there is no connection
        """
        sock = self._sock
        thread = self._recv_thread
        self._sock = None
        self._recv_thread = None
        self._broken = False
        if sock is None:
            return None, None

        # we don't need graceful stream close before socket close, because adbd
        # will do anyway, so we just release resources on our side. The streams
        # go first: a caller that is sending has to see the connection closed
        # instead of reaching a socket that may not send any more
        self._abort_streams()
        return sock, thread

    def connect(self):
        """
        Connect to the device and read its CNXN.

        The call is what a long lived owner runs before an operation: it is a
        no-op on a healthy connection and rebuilds the one the device dropped.
        A caller that wants a new connection while the current one is still
        healthy calls disconnect() and then connect().

        Raises:
            ConnectionRefusedError: When nothing listens on the address
            AdbConnectionTimeout: When the device does not answer in time
            AdbConnectionClosed: When the device closes the connection during
                the handshake
            AdbMessageInvalid: When the device does not answer with a CNXN
            AdbAuthKeyMissing: When this user has no adb key to authenticate with
            AdbAuthKeyInvalid: When the adb key of this user can not be used
            AdbAuthRejected: When the device does not accept the adb key of this
                user, for example because its user denied the dialog
        """
        with self._connect_lock:
            # check if already connected
            if self._sock is not None and not self._broken:
                return
            # the receiving thread gave up on the previous connection, its
            # socket is dead although the object is still here
            if self._sock is not None:
                sock, _thread = self._detach_connection()
                # the dispatch thread of a broken connection closed its own
                # socket, this only releases an object it is done with
                self._close_socket(sock)

            addr = f'{self.host}:{self.port}'
            logger.info(f'Connecting to {addr}')

            # open TCP connection
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # bound the connect itself, an unreachable host would otherwise wait
            # for the default timeout of the system, tens of seconds on most
            sock.settimeout(self.timeout)
            # a request is small and waits for its answer, Nagle would delay
            # each of them by up to 40ms
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            cnxn = (
                b"host::features=shell_v2,cmd,stat_v2,ls_v2,fixed_push_mkdir,apex,abb,"
                b"fixed_push_symlink_timestamp,abb_exec,remount_shell,track_app,"
                b"sendrecv_v2,sendrecv_v2_brotli,sendrecv_v2_lz4,sendrecv_v2_zstd,sendrecv_v2_dry_run_send"
            )
            try:
                try:
                    # may raise ConnectionRefusedError
                    sock.connect((self.host, self.port))
                except socket.timeout as e:
                    raise AdbConnectionTimeout(f'{e.__class__.__name__} when connecting: {e}')

                # The handshake has no version field, so the checksum of the AUTH
                # tokens below can not be judged yet: a device of 0x01000001
                # writes 0 there and an old one writes the sum, which makes
                # skipping the check the only rule that accepts both. A
                # connection of an earlier version may still be the one the
                # object remembers, so the value is reset for every handshake.
                self._protocol_version = ADB_VERSION
                # the message of the handshake is sent before the device told
                # its version, so it always carries the sum
                self._send_checksum = True
                # send CNXN
                self.message_send(sock, CNXN, ADB_VERSION, ADB_MAX_PAYLOAD, cnxn)

                # recv CNXN from device, signing the token when its adbd is secure
                arg0, arg1, data = self._recv_cnxn(sock)
            except Exception:
                # cleanup on error
                self._close_socket(sock)
                self.features = DeviceFeatures([])
                self._max_payload = ADB_MAX_PAYLOAD
                self._protocol_version = ADB_VERSION
                self._send_checksum = True
                raise

            logger.info(f'Connected to {addr}')
            self._sock = sock
            self._broken = False
            self._protocol_version = min(arg0, ADB_VERSION)
            # a device of that version reads nothing in the field, the sum of
            # every payload of the connection is a pass that proves nothing
            self._send_checksum = self._protocol_version < ADB_VERSION_SKIP_CHECKSUM
            # an old adbd does not fill the field and answers 0, which is the
            # 4096 bytes of the first protocol. Taking the 0 literally would
            # send it 256KB messages it drops, together with the connection
            self._max_payload = min(arg1, ADB_MAX_PAYLOAD) if arg1 else ADB_MAX_PAYLOAD_V1
            self.features = DeviceFeatures.from_cnxn(data)

            # start recv thread, it reads the socket it was started for and
            # stops as soon as the connection is replaced
            self._recv_thread = Thread(target=self._task_dispatch_message, args=(sock,), daemon=True)
            self._recv_thread.start()

    def _recv_cnxn(self, sock: socket.socket):
        """
        Read the CNXN of the device, answering the AUTH challenges on the way.

        A device with a secure adbd answers the CNXN with an AUTH token: the host
        signs the token with the key adb keeps for this user, and offers the
        public key when the device does not know it yet. A device that was
        allowed once accepts the signature without a dialog; only the first
        contact needs a user, and then the device itself asks.

        Args:
            sock (socket.socket): Connected socket, right after the CNXN of the host

        Returns:
            tuple: (version, max_payload, banner) of the CNXN of the device

        Raises:
            AdbMessageInvalid: When the device answers something else
            AdbAuthKeyMissing: When this user has no adb key
            AdbAuthKeyInvalid: When the adb key of this user can not be used
            AdbAuthRejected: When the device does not accept the adb key
            AdbConnectionClosed:
            AdbConnectionTimeout:
        """
        key = None
        challenge = 0
        extended = False
        try:
            while True:
                command, arg0, arg1, data = self.message_recv(sock)
                if command == CNXN:
                    return arg0, arg1, data
                if command != AUTH:
                    raise AdbMessageInvalid(f'Expect {CNXN} after connection but got {command}')
                if arg0 != ADB_AUTH_TOKEN or len(data) != AUTH_TOKEN_SIZE:
                    # a device only ever asks the host to sign a token
                    raise AdbMessageInvalid(f'Unexpected AUTH with arg0={arg0} length={len(data)}')

                challenge += 1
                if key is None:
                    # a device that does not ask for authentication needs no key,
                    # so the file is only read when a challenge arrives
                    key = keygen.load_user_key()
                if challenge == 1:
                    # a device that knows the key of this host answers the
                    # signature with a CNXN and no dialog
                    self.message_send(sock, AUTH, ADB_AUTH_SIGNATURE, 0, key.sign(data))
                    continue
                if challenge == 2:
                    # the key is new to the device, which shows a dialog. The
                    # answer comes when its user takes a decision, and that takes
                    # longer than the timeout of a request
                    logger.info(f'Device {self.host}:{self.port} asks to allow the adb key of this user')
                    sock.settimeout(ADB_AUTH_CONFIRM_TIMEOUT)
                    extended = True
                    self.message_send(sock, AUTH, ADB_AUTH_RSAPUBLICKEY, 0, key.public_key_payload())
                    continue
                # the device asks again after both the signature and the public
                # key: its user denied the dialog, or it does not accept the key
                raise AdbAuthRejected(f'Device {self.host}:{self.port} refused the adb key of this user')
        finally:
            if extended:
                try:
                    sock.settimeout(self.timeout)
                except OSError:
                    # the socket is already closed, connect() cleans it up
                    pass

    def disconnect(self):
        """
        End the connection and release everything it holds.

        The connection ends with a half close: the socket sends a FIN, the
        device answers with its own, and the dispatch thread ends on that
        readable event. The descriptor is closed here, after the thread is
        gone, so no thread closes a socket another thread reads. A device that
        does not answer still ends the call within `DISCONNECT_JOIN_TIMEOUT`.

        The object holds nothing after this call. A second call is a no-op.
        """
        with self._connect_lock:
            # check if already disconnected
            if self._sock is None:
                return

            addr = f'{self.host}:{self.port}'
            logger.info(f'Disconnecting to {addr}')
            sock, thread = self._detach_connection()

        # release outside of the lock, the dispatch thread takes it as well
        self._graceful_close(sock, thread)

        logger.info(f'Disconnected to {addr}')

    @property
    def is_connected(self) -> bool:
        """
        Whether the client has a connection it can use.

        A caller that owns the client for a long time checks this to decide
        whether a call needs a connect, the call itself raises
        AdbConnectionClosed when the connection is gone.

        Returns:
            bool: True while the socket is open and the dispatch thread did not
                give up on it
        """
        return self._sock is not None and not self._broken

    def message_send(self, sock: socket.socket, command: bytes, arg0, arg1, data=b''):
        """
        Raises:
            AdbConnectionClosed:
            AdbConnectionTimeout:
        """
        # print('send', command, arg0, arg1, data)
        data_length = len(data)
        # the checksum of an outgoing message is read only by a peer older than
        # ADB_VERSION_SKIP_CHECKSUM, a pass over the payload of every message of
        # a newer connection would prove nothing there
        if self._send_checksum:
            data_check = sum(data) & 0xFFFFFFFF
        else:
            data_check = 0
        command = int.from_bytes(command, 'little')
        magic = command ^ 0xFFFFFFFF

        header = MESSAGE_STRUCT.pack(command, arg0, arg1, data_length, data_check, magic)
        payload = header + data

        try:
            with self._send_lock:
                sock.sendall(payload)
        except socket.timeout as e:
            # socket.timeout is an OSError, it has to be caught before the
            # wider clause or a slow device would look like a closed one
            raise AdbConnectionTimeout(f'{e.__class__.__name__} while sending: {e}')
        except (ConnectionError, OSError) as e:
            # BrokenPipeError
            # ConnectionAbortedError
            # ConnectionResetError
            raise AdbConnectionClosed(f'{e.__class__.__name__} while sending: {e}')

    @staticmethod
    def recv_exact(sock: socket.socket, length: int, header_timeout=True) -> bytes:
        """
        Read exactly `length` bytes from a socket.

        The timeout of the socket is the deadline of the whole read and not of
        every recv() call: a peer that sends one byte just before the timeout
        of each call would otherwise hold the caller for `length` times the
        timeout, and the dispatch thread with it. The timeout of the socket is
        left as it is, because the senders of the connection share it: a
        shorter one would fail a write of another thread.

        Args:
            sock (socket.socket): Connected socket to read
            length (int): Bytes to read
            header_timeout (bool): True to bound the read by the timeout of the
                socket. False to wait through the idle timeouts of the socket
                until the first byte, which is what the dispatch thread does
                between the messages of a device. Defaults to True.

        Returns:
            bytes: Exactly `length` bytes

        Raises:
            AdbConnectionClosed: When the peer closes the connection
            AdbConnectionTimeout: When the read does not finish in time
        """
        deadline = None
        if header_timeout:
            timeout = sock.gettimeout()
            if timeout is not None:
                deadline = perf_counter() + timeout

        # the first byte is read directly, one recv() is bounded by the timeout
        # of the socket already
        part = ' when receiving header' if header_timeout else ''
        while 1:
            try:
                chunk = sock.recv(length)
                break
            except socket.timeout as e:
                # before OSError, socket.timeout is a subclass of it
                if not header_timeout:
                    # the device is idle, the dispatch thread waits for it
                    continue
                raise AdbConnectionTimeout(f'{e.__class__.__name__}{part}: {e}')
            except (ConnectionError, OSError) as e:
                raise AdbConnectionClosed(f'{e.__class__.__name__}{part}: {e}')

        if not chunk:
            raise AdbConnectionClosed('No data when receiving header')
        if len(chunk) >= length:
            return chunk

        # the message started arriving, so the rest of it is bounded by the
        # timeout even for a caller that waits for an idle device
        if deadline is None:
            timeout = sock.gettimeout()
            if timeout is not None:
                deadline = perf_counter() + timeout

        chunks = [chunk]
        remain = length - len(chunk)
        while remain > 0:
            if deadline is not None:
                remaining = deadline - perf_counter()
                if remaining <= 0:
                    raise AdbConnectionTimeout(f'Timeout when receiving chunk: {length - remain} of {length} bytes')
                try:
                    # wait for the rest of the message without touching the
                    # timeout of the socket, select() reports when the next
                    # bytes are there and the recv() below returns at once
                    readable, _, _ = select([sock], [], [], remaining)
                except (ConnectionError, OSError) as e:
                    raise AdbConnectionClosed(f'{e.__class__.__name__} when receiving chunk: {e}')
                if not readable:
                    raise AdbConnectionTimeout(f'Timeout when receiving chunk: {length - remain} of {length} bytes')

            try:
                chunk = sock.recv(remain)
            except socket.timeout as e:
                raise AdbConnectionTimeout(f'{e.__class__.__name__} when receiving chunk: {e}')
            except (ConnectionError, OSError) as e:
                raise AdbConnectionClosed(f'{e.__class__.__name__} when receiving chunk: {e}')
            if not chunk:
                raise AdbConnectionClosed('No data when receiving chunk')

            chunks.append(chunk)
            remain -= len(chunk)

        return chunks[0] if len(chunks) == 1 else b''.join(chunks)

    def message_recv(self, stream: socket.socket, header_timeout=True) -> "tuple[bytes, int, int, bytes]":
        """
        Receive one message from stream

        Raises:
            AdbConnectionClosed:
            AdbConnectionTimeout:
            AdbMessageInvalid:
        """
        header = self.recv_exact(stream, 24, header_timeout=header_timeout)
        command, arg0, arg1, data_length, data_check, magic = MESSAGE_STRUCT.unpack(header)

        # check command
        command_name = header[:4]
        if command_name not in ADB_COMMANDS:
            raise AdbMessageInvalid(f'Invalid command {command_name}, header={header}')

        # validate magic
        expected_magic = command ^ 0xFFFFFFFF
        if magic != expected_magic:
            raise AdbMessageInvalid(f'Magic not match, expect {expected_magic}, got {magic}')

        # a device can not send more than the payload we advertised, believing
        # an impossible length would wait for gigabytes of data
        if data_length > ADB_MAX_PAYLOAD:
            raise AdbMessageInvalid(f'Payload length {data_length} exceeds {ADB_MAX_PAYLOAD}')

        # recv payload
        if data_length > 0:
            payload = self.recv_exact(stream, data_length)
            # From 0x01000001 on the checksum is optional for the whole
            # connection: the peer sends 0 instead of the sum of the payload and
            # the text of the protocol is that nobody verifies it. The check is
            # skipped before the sum is computed, a pass over every payload
            # would prove nothing there and costs the same as the copy of it.
            # The version of the CNXN is in its own header, every message after
            # it belongs to the connection the handshake negotiated.
            version = min(arg0, ADB_VERSION) if command_name == CNXN else self._protocol_version
            if version < ADB_VERSION_SKIP_CHECKSUM:
                checksum = sum(payload) & 0xFFFFFFFF
                if checksum != data_check:
                    raise AdbMessageInvalid(f'Checksum not match, expect {data_check}, got {checksum}')
        else:
            payload = b''

        return command_name, arg0, arg1, payload

    def open_stream(self, service: str, recv_buffer_size: int = ADB_STREAM_RECV_BUFFER_SIZE
                    ) -> AdbStreamTCP:
        """
        Args:
            service (str): Command like "shell:echo hello"
            recv_buffer_size (int): Bytes of output to buffer before the writes
                of the device are held back. Defaults to
                ADB_STREAM_RECV_BUFFER_SIZE

        Returns:
            AdbStreamTCP: The opened stream

        Raises:
            ValueError: When the buffer size is not positive
            AdbConnectionClosed: When there is no connection
            AdbConnectionTimeout: When the device does not answer in time
            AdbStreamClosed: When the device refuses the service
        """
        if recv_buffer_size <= 0:
            raise ValueError(f'Receive buffer size must be positive: {recv_buffer_size}')
        if '\x00' in service:
            # the service name ends at the first null byte, sending one inside
            # would truncate the name on the device
            raise ValueError(f'Service name can not contain a null byte: {service!r}')

        sock = self._require_sock()
        with self._connect_lock:
            local_id = self._stream_id_allocate()
            stream = AdbStreamTCP(local_id, service, protocol=self, recv_buffer_size=recv_buffer_size)
            self._stream_dict[local_id] = stream

        try:
            # send OPEN
            service_data = service.encode('utf-8')
            if not service_data.endswith(b'\x00'):
                service_data += b'\x00'
            self.message_send(sock, OPEN, stream.local_id, 0, service_data)

            # wait until opened
            # use connection-level timeout as this should be fast and not related to stream
            timeout = self.timeout
            while stream.state != 'opened' and stream.state != 'closed':
                # the clock must not move under the accounting: a wall clock
                # correction would stretch the timeout or cut it short
                if timeout < 0.001:
                    raise AdbConnectionTimeout(f'Timeout when opening stream {stream}')
                start = perf_counter()
                stream.send_event.acquire(timeout=timeout)
                timeout -= perf_counter() - start
        except Exception:
            # the device may still answer this id, the stream is dropped and the
            # id is never handed out again, so a late answer can not open or
            # close the stream of an unrelated caller
            with self._connect_lock:
                self._stream_dict.pop(local_id, None)
            raise

        if stream.aborted:
            # the connection was dropped while this open was waiting
            raise AdbConnectionClosed(f'Connection lost while opening stream {service}')

        if stream.state == 'closed' and not stream.remote_id:
            # adbd refuses a service it can not create with a CLSE and no OKAY,
            # a caller must not mistake that for a command without output. A
            # command that already ended has a remote id and keeps its output.
            raise AdbStreamClosed(f'Device refused the service: {service}')

        # opened
        return stream

    def close_stream(self, stream: AdbStreamTCP):
        """
        Close a stream

        Raises:
            AdbConnectionClosed:
            AdbConnectionTimeout:
        """
        state = stream.state
        if state == 'closed':
            return

        remote_id = stream.remote_id
        if not remote_id:
            logger.warning(f'Cannot close stream without remote_id: {stream}')
            return

        sock = self._require_sock()

        # request to close, a stream that already got the CLSE of the device
        # was answered by the dispatch thread
        if state == 'opened' or state == 'opening':
            if stream.mark_close_sent():
                self.message_send(sock, CLSE, stream.local_id, remote_id)

        # wait until closed, the device confirms with a CLSE of its own but any
        # message of the stream wakes the wait, so the state decides and not the
        # signal
        timeout = self.timeout
        while stream.state != 'closed':
            # use connection-level timeout as this should be fast and not related to stream
            if timeout < 0.001:
                raise AdbConnectionTimeout(f'Timeout when closing stream {stream}')
            start = perf_counter()
            stream.recv_event.acquire(timeout=timeout)
            timeout -= perf_counter() - start

    def sendto_stream(self, stream: AdbStreamTCP, data: bytes):
        """
        Write data to a stream, a chunk per acknowledged message.

        Args:
            stream (AdbStreamTCP): Open stream to write
            data (bytes): Data to write, empty data sends nothing

        Raises:
            AdbStreamClosed: When the stream is not open any more. The message
                says how many bytes were acknowledged, the rest of the data may
                have been written to the device as well
            AdbConnectionClosed: When there is no connection
            AdbConnectionTimeout: When the device does not acknowledge a chunk
        """
        sock = self._require_sock()
        if stream.state != 'opened':
            raise AdbStreamClosed(f'Cannot send to a stream in state {stream.state}: {stream}')
        remote_id = stream.remote_id
        if not remote_id:
            raise AdbStreamClosed(f'Cannot send to a stream without remote_id: {stream}')

        # the device advertised how much it accepts in one message, a larger
        # payload is dropped by an old adbd and closes the connection
        chunk_size = self._max_payload
        for start in range(0, len(data), chunk_size):
            chunk = data[start:start + chunk_size]
            # drop a signal left by an earlier message, only the OKAY of this
            # chunk may end the wait below
            stream.send_event.acquire(blocking=False)
            # a command that exited closes its stream, and the rest of the data
            # has nobody to read it
            if stream.state != 'opened':
                raise AdbStreamClosed(f'Stream closed after {start} of {len(data)} bytes: {stream}')
            self.message_send(sock, WRTE, stream.local_id, remote_id, chunk)
            # wait OKAY
            if not stream.send_event.acquire(timeout=self.timeout):
                raise AdbConnectionTimeout(f'Timeout when sendto stream {stream}')
            if stream.state != 'opened':
                raise AdbStreamClosed(f'Stream closed after {start} of {len(data)} bytes: {stream}')

    def acknowledge_stream(self, stream: AdbStreamTCP, count: int = 1, sock: "socket.socket | None" = None):
        """
        Acknowledge writes of the device that the stream held back.

        Args:
            stream (AdbStreamTCP): Stream to acknowledge
            count (int): Number of writes to acknowledge. Defaults to 1
            sock (socket.socket): Connection to send on, the current one by
                default. The dispatch thread passes the socket it reads, which
                may not be the current one any more

        Raises:
            AdbConnectionClosed: When there is no connection
            AdbConnectionTimeout: When the device does not take the message
        """
        if sock is None:
            sock = self._sock
            if sock is None:
                # the connection is gone and its streams were dropped with it
                return
        for _ in range(count):
            if stream.state != 'opened' or not stream.remote_id:
                # a stream that ended has no write left to acknowledge
                return
            self.message_send(sock, OKAY, stream.local_id, stream.remote_id)

    def _dispatch_message(self, sock: socket.socket, command: bytes, remote_id: int, local_id: int, data: bytes):
        """
        Dispatch one message of the device to its stream.

        Args:
            sock (socket.socket): Connection the message came from
            command (bytes): Command of the message
            remote_id (int): Stream id of the device
            local_id (int): Stream id of ours
            data (bytes): Payload of the message
        """
        try:
            stream = self._stream_dict[local_id]
        except KeyError:
            # message from unknown stream id, drop
            return

        # stream opened
        if command == OKAY:
            if stream.state == 'opening':
                # received first OKAY
                stream.remote_id = remote_id
                stream.state = 'opened'
                try:
                    stream.send_event.release()
                except RuntimeError:
                    # allow double release, we just need to notify main thread message received
                    pass
            elif stream.state == 'opened':
                # received OKAY from sent message
                try:
                    stream.send_event.release()
                except RuntimeError:
                    pass
            else:
                logger.warning(f'Unexpected command={command} on stream {stream}')

        # dispatch message to stream
        elif command == WRTE:
            if stream.state == 'opened':
                # the write is buffered before it is acknowledged: a device
                # sends the next write of a stream only after this one is
                # acknowledged, so holding the acknowledgement back while the
                # buffer is full is what slows a device down
                acknowledge = stream.add_data(data)
                try:
                    stream.recv_event.release()
                except RuntimeError:
                    pass
                if acknowledge:
                    self.acknowledge_stream(stream, sock=sock)
            else:
                logger.warning(f'Unexpected command={command} on stream {stream}')

        # stream close
        elif command == CLSE:
            # release stream
            with self._connect_lock:
                self._stream_dict.pop(local_id, None)
            stream.state = 'closed'
            # a stream ends when both sides sent a CLSE. adbd keeps the stream
            # of a command that ended until the host answers, and refuses the
            # next OPEN of a host that reuses the local id of a kept stream.
            # A stream that already sent its CLSE, or one the device refused
            # without creating it (remote_id 0), has nothing to answer.
            if remote_id and stream.mark_close_sent():
                try:
                    self.message_send(sock, CLSE, local_id, remote_id)
                except (AdbConnectionClosed, AdbConnectionTimeout) as e:
                    logger.warning(f'Failed to answer the CLSE of stream {stream}: {e}')
            try:
                stream.send_event.release()
            except RuntimeError:
                pass
            try:
                stream.recv_event.release()
            except RuntimeError:
                pass

    def _task_dispatch_message(self, sock: socket.socket):
        reason = 'closed'
        while 1:
            # a reconnect replaces self._sock, the thread of the previous
            # connection must not read the socket of the new one
            if self._sock is not sock:
                break
            try:
                msg = self.message_recv(sock, header_timeout=False)
            except (AdbConnectionClosed, AdbConnectionTimeout) as e:
                reason = f'{e.__class__.__name__}: {e}'
                break
            except AdbMessageInvalid as e:
                # the stream of a message that failed validation can not be
                # trusted: the payload of it would be read as a header, and a
                # device that never stops sending would spin this thread
                reason = f'AdbMessageInvalid: {e}'
                break
            try:
                self._dispatch_message(sock, *msg)
            except (AdbConnectionClosed, AdbConnectionTimeout) as e:
                reason = f'{e.__class__.__name__}: {e}'
                break
            except Exception as e:
                # a thread that dies silently leaves every stream waiting for a
                # message that will never be dispatched
                logger.exception(f'Unexpected error when dispatching a message: {e}')
                reason = f'unexpected error: {e}'
                break

        with self._connect_lock:
            if self._sock is not sock:
                # the connection was replaced while this thread was ending, the
                # streams of the new one are not ours to touch
                return
            # the connection died under the client, connect() rebuilds it
            self._broken = True
            self._abort_streams()
            # release the descriptor here: a broken connection kept until the
            # next connect() may hold a descriptor for as long as the caller
            # keeps the object, and the next connect() may never come
            self._close_socket(sock)
        logger.warning(f'Connection to {self.host}:{self.port} is broken, receiving ended: {reason}')
