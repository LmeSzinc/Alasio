"""
A fake adbd: the device end of the adb transport over TCP.

The module is written from the protocol description and it does not import
`alasio.adb.protocol`: a mirror built on the code under test would repeat the
bugs of the client and could never catch one. The framing follows the adb
transport (a 24 byte header of six little endian unsigned int, the checksum of
the payload and the magic of the command), the service strings follow adbd
(`shell:<cmd>` and `shell,v2:<cmd>`), the stream protocol follows the endpoints
(OPEN is answered by OKAY or CLSE, WRTE is acknowledged by OKAY, CLSE ends the
stream) and the shell protocol v2 follows its packet framing (one byte of id, a
little endian length, the payload).

The device is a mirror of a real one in both directions: it checks what the host
sends (magic, checksum, payload size, the trailing null byte of a service name)
the way adbd checks it, and every answer can be delayed, broken or withheld on
purpose, so a test can check what the client does when a device misbehaves.

Examples:
    >>> with FakeAdbd(responder=lambda cmd, v2: ShellReply(stdout=b'hi\\n')) as adbd:
    ...     client = AdbTCP(port=adbd.port)
    ...     client.connect()
    ...     assert client.shell('echo hi').stdout == b'hi\\n'
    ...     client.disconnect()

    >>> adbd = FakeAdbd(refuse=lambda service: service.startswith('bogus'))
"""

import base64
import hashlib
import socket
import threading
import time
from struct import Struct, unpack_from

# Commands of the transport, transcribed from the protocol description rather
# than imported from the client under test
CNXN = b'CNXN'
AUTH = b'AUTH'
OPEN = b'OPEN'
OKAY = b'OKAY'
CLSE = b'CLSE'
WRTE = b'WRTE'

# Header of every message: command, arg0, arg1, data length, data checksum, magic
HEADER = Struct('<6I')
HEADER_SIZE = 24

# Protocol versions, the skipping of the checksum was added in 0x01000001
ADB_VERSION = 0x01000000
ADB_VERSION_SKIP_CHECKSUM = 0x01000001
# Payload of the first version of the transport, accepted by an adbd that does
# not fill the max payload field of its CNXN at all
ADB_MAX_PAYLOAD_V1 = 4096
# Payload a device of this fake accepts, the same constant as the client
ADB_MAX_PAYLOAD = 256 * 1024

# Packet ids of the shell protocol v2
SHELL_STDOUT = 1
SHELL_STDERR = 2
SHELL_EXIT = 3

# Banner of a device with shell_v2, in the format adbd sends it: the type of the
# peer, the properties and the features
DEFAULT_FEATURES = b'shell_v2,cmd,stat_v2,ls_v2,fixed_push_mkdir,apex,abb'
DEFAULT_BANNER = (
    b'device::ro.product.name=fake;ro.product.model=FAKE;'
    b'ro.product.device=fake;features=' + DEFAULT_FEATURES
)
# Banner of a device older than Android 7, which has no shell_v2 at all
LEGACY_FEATURES = b'cmd,stat_v2,ls_v2,fixed_push_mkdir,apex'
LEGACY_BANNER = (
    b'device::ro.product.name=legacy;ro.product.model=LEGACY;'
    b'ro.product.device=legacy;features=' + LEGACY_FEATURES
)
# Token of an unauthenticated connection, a device sends 20 random bytes
AUTH_TOKEN = bytes(range(20))
# First argument of AUTH: the device sends a token, the host answers with the
# signature of it and, when the key is new to the device, with the public key
ADB_AUTH_TOKEN = 1
ADB_AUTH_SIGNATURE = 2
ADB_AUTH_RSAPUBLICKEY = 3
AUTH_TOKEN_SIZE = 20
# struct RSAPublicKey of the adb public key format, see libcrypto_utils
ANDROID_PUBKEY_MODULUS_SIZE = 2048 // 8
ANDROID_PUBKEY_ENCODED_SIZE = 3 * 4 + 2 * ANDROID_PUBKEY_MODULUS_SIZE
# DigestInfo of SHA1 in PKCS#1 v1.5, the only digest adbd verifies
SHA1_DIGEST_INFO = bytes.fromhex('3021300906052b0e03021a05000414')


def encode_message(command, arg0, arg1, data=b'', magic=None, checksum=None, length=None):
    """
    Encode one message of the adb transport.

    Args:
        command (bytes): Four byte command name, like b'CNXN'
        arg0 (int): First argument of the command
        arg1 (int): Second argument of the command
        data (bytes): Payload of the message
        magic (int): Magic to write instead of the correct one. Defaults to
            None, which writes `command ^ 0xFFFFFFFF`.
        checksum (int): Checksum to write instead of the correct one. Defaults
            to None, which writes the sum of the payload.
        length (int): Length to write instead of the real one. Defaults to None,
            which writes the length of the payload.

    Returns:
        bytes: Header and payload, ready to send
    """
    value = int.from_bytes(command, 'little')
    if magic is None:
        magic = value ^ 0xFFFFFFFF
    if checksum is None:
        checksum = sum(data) & 0xFFFFFFFF
    if length is None:
        length = len(data)
    return HEADER.pack(value, arg0, arg1, length, checksum, magic) + data


def next_auth_token(token):
    """
    Token of the next challenge, a device sends new bytes for every round.

    Args:
        token (bytes): Token of the previous challenge

    Returns:
        bytes: 20 bytes of a new token
    """
    return hashlib.sha256(token).digest()[:AUTH_TOKEN_SIZE]


def verify_signature(public_key, token, signature):
    """
    Verify the signature of an AUTH token the way adbd verifies it.

    Args:
        public_key (tuple): (n, e) of the key the device trusts
        token (bytes): The token the device asked the host to sign
        signature (bytes): Signature the host sent

    Returns:
        bool: True when the signature matches the token
    """
    n, e = public_key
    size = (n.bit_length() + 7) // 8
    if len(signature) != size:
        return False
    digest = SHA1_DIGEST_INFO + hashlib.sha1(token).digest()
    padding = size - len(digest) - 3
    if padding < 8:
        return False
    expected = b'\x00\x01' + b'\xff' * padding + b'\x00' + digest
    decrypted = pow(int.from_bytes(signature, 'big'), e, n).to_bytes(size, 'big')
    return decrypted == expected


def decode_public_key(blob):
    """
    Decode the binary public key format of adb.

    The layout is struct RSAPublicKey of libcrypto_utils: the modulus in 32 bit
    words, the Montgomery parameters n0inv and rr, the modulus and the exponent,
    all little endian.

    Args:
        blob (bytes): Encoded key

    Returns:
        tuple | None: (n, e), None when the blob is not a valid key
    """
    if len(blob) != ANDROID_PUBKEY_ENCODED_SIZE:
        return None
    words, n0inv = unpack_from('<II', blob, 0)
    n = int.from_bytes(blob[8:8 + ANDROID_PUBKEY_MODULUS_SIZE], 'little')
    rr = int.from_bytes(blob[8 + ANDROID_PUBKEY_MODULUS_SIZE:8 + 2 * ANDROID_PUBKEY_MODULUS_SIZE], 'little')
    e = int.from_bytes(blob[8 + 2 * ANDROID_PUBKEY_MODULUS_SIZE:], 'little')
    if words * 4 != ANDROID_PUBKEY_MODULUS_SIZE or n < 3 or not n & 1:
        return None
    if (n0inv * (n & 0xFFFFFFFF)) % (1 << 32) != (1 << 32) - 1:
        return None
    if rr != pow(2, ANDROID_PUBKEY_MODULUS_SIZE * 8 * 2, n):
        return None
    if not 1 < e < 1 << 32:
        return None
    return n, e


def split_service(service):
    """
    Split a service name the way adbd parses it.

    Args:
        service (str): Service name sent by the host, e.g. "shell,v2:echo hi"

    Returns:
        tuple: (name, options, command), the command is None when the service
            has no colon at all
    """
    name, sep, command = service.partition(':')
    if not sep:
        command = None
    parts = name.split(',')
    return parts[0], parts[1:], command


def shell_v2_stream(stdout=b'', stderr=b'', exitcode=0, frame_size=4096):
    """
    Frame the output of a command the way the shell protocol v2 does.

    Args:
        stdout (bytes): Bytes the command writes on its standard output
        stderr (bytes): Bytes the command writes on its standard error
        exitcode (int): Exit code of the command, one byte on the wire
        frame_size (int): Largest payload of one frame. Defaults to 4096.

    Returns:
        bytes: The framed stream, ending with the frame of the exit code
    """
    packets = []
    for packet_id, payload in ((SHELL_STDOUT, stdout), (SHELL_STDERR, stderr)):
        for start in range(0, len(payload), frame_size):
            piece = payload[start:start + frame_size]
            packets.append(bytes([packet_id]) + len(piece).to_bytes(4, 'little') + piece)
    packets.append(bytes([SHELL_EXIT]) + (1).to_bytes(4, 'little') + bytes([exitcode & 0xFF]))
    return b''.join(packets)


class ShellReply:
    """
    What the fake device answers to one shell command.
    """

    def __init__(self, stdout=b'', stderr=b'', exitcode=0, delay=0.0, close=True):
        """
        Args:
            stdout (bytes): Standard output of the command
            stderr (bytes): Standard error of the command
            exitcode (int): Exit code of the command, one byte on the wire
            delay (float): Seconds to sleep before answering. Defaults to 0.
            close (bool): True to close the stream after the answer, False to
                leave it open, so the host can time out on it
        """
        self.stdout = stdout
        self.stderr = stderr
        self.exitcode = exitcode
        self.delay = delay
        self.close = close


class FakeAdbd:
    """
    A device that speaks the adb transport on a port of the loopback.

    The attributes below are the switches of the device: they are read when the
    answer is built, so a test may change one between two messages. The
    recordings are appended under a lock, a test reads them after the client
    call returned.

    Attributes:
        banner (bytes): Payload of the CNXN answer of the device
        version (int): Protocol version in the CNXN answer
        max_payload (int): Payload the device advertises in its CNXN. 0 models
            an adbd of the first protocol, which accepts ADB_MAX_PAYLOAD_V1
            per message although it declares nothing
        responder (callable): (command, shell_v2) -> ShellReply of a shell call
        refuse (callable): service -> True to answer OPEN with CLSE
        auth_required (bool): Answer the CNXN with AUTH instead of CNXN
        silent (bool): Never answer anything, the connection stays open
        open_fault (str): Fault injected in the answer of OPEN, one of
            'refuse', 'silent', 'close', 'bad_magic', 'bad_checksum',
            'unknown_command', 'oversize_length'
        open_delay (float): Seconds to sleep before answering an OPEN
        fault_on_reply (str): Fault injected in the next message sent, the same
            names as open_fault without 'refuse', 'silent' and 'close'
        answer_write_okay (bool): True to acknowledge WRTE of the host
        close_after_writes (int): Close the stream instead of acknowledging the
            WRTE once the host sent this many messages on the connection, the way
            a command that exited answers a host that is still writing. 0 leaves
            every write acknowledged
        answer_close_clse (bool): True to answer the CLSE of the host with a
            CLSE of its own, False to leave the stream closing forever
        reply_chunk_size (int): Largest payload of one WRTE of the device
        frame_size (int): Largest payload of one frame of shell_v2
        zero_checksum (bool): True to send every message with a checksum of 0,
            which a device of version 0x01000001 may do
        auth_required (bool): Answer the CNXN with an AUTH token instead of a
            CNXN, the way a device with a secure adbd does
        auth_key (tuple): (n, e) of the key the device already trusts. None
            trusts no key, so the first signature fails and the host has to
            offer its public key
        auth_allow_new_key (bool): True to accept the public key the host
            offers, the way the user of a device taps "allow" in the dialog
        auth_delay (float): Seconds to sleep after accepting a public key, which
            models the time a user spends on the dialog
        auth_arg0 (int): Argument of the AUTH token, 1 on a real adbd

    Recordings:
        hello (bytes): The CNXN payload the host sent
        client_version (int): Protocol version the host sent
        client_max_payload (int): Payload the host advertised
        messages (list): Every valid message received, as (command, arg0, arg1, data)
        opens (list): Service names of every OPEN, in order
        opens_without_nul (list): Payloads of OPEN that missed the trailing null
        writes (list): Payloads of every WRTE of the host
        client_closes (list): (arg0, arg1) of every CLSE of the host
        write_okays (int): Number of OKAY the host sent for a WRTE
        violations (list): (reason, message) of everything the host did wrong
        connections (int): Number of accepted connections
        connection_ends (list): How the read loop of every connection ended:
            `eof` for a host that closed, `reset` for a reset, `error` for
            another socket error, `aborted` for a connection that ended before
            its read loop (a refused handshake, a device that gave up)
        auth_challenges (int): Number of AUTH tokens sent
        auth_signatures (list): Signatures the host sent, in order
        auth_public_keys (list): Public key payloads the host offered, in order
    """

    def __init__(self, banner=DEFAULT_BANNER, version=ADB_VERSION, max_payload=ADB_MAX_PAYLOAD,
                 responder=None, refuse=None):
        """
        Args:
            banner (bytes): Payload of the CNXN answer. Defaults to a device
                with shell_v2.
            version (int): Protocol version of the CNXN answer. Defaults to
                0x01000000.
            max_payload (int): Payload the device accepts and advertises.
                Defaults to ADB_MAX_PAYLOAD. A device that passes 0 models an
                adbd that does not fill the field: it advertises 0 and accepts
                ADB_MAX_PAYLOAD_V1 per message.
            responder (callable): (command, shell_v2) -> ShellReply. Defaults to
                a command that writes nothing and exits with 0.
            refuse (callable): service -> bool, True to refuse the service the
                way adbd refuses one it can not create. Defaults to None, which
                accepts every service.
        """
        self.banner = banner
        self.version = version
        self.max_payload = max_payload
        if responder is None:
            responder = lambda command, shell_v2: ShellReply()  # noqa: E731
        self.responder = responder
        self.refuse = refuse

        # switches of the device
        self.auth_required = False
        self.auth_key = None
        self.auth_allow_new_key = True
        self.auth_delay = 0.0
        self.auth_arg0 = ADB_AUTH_TOKEN
        self.silent = False
        self.open_fault = ''
        self.open_delay = 0.0
        self.fault_on_reply = ''
        self.answer_write_okay = True
        self.answer_close_clse = True
        # Close the stream instead of acknowledging a WRTE once the host wrote
        # this many messages, the way a command that exited answers a host that
        # is still writing. 0 leaves the writes acknowledged.
        self.close_after_writes = 0
        # True to leave the checksum of every message at 0, which a device of
        # version 0x01000001 is allowed to do
        self.zero_checksum = False
        self.reply_chunk_size = 4096
        self.frame_size = 4096
        # raw bytes to answer instead of a built CNXN, for a garbage handshake
        self.cnxn_reply = None

        # recordings
        self.hello = b''
        self.client_version = 0
        self.client_max_payload = 0
        self.messages = []
        self.opens = []
        self.opens_without_nul = []
        self.writes = []
        self.client_closes = []
        self.write_okays = 0
        self.violations = []
        self.connections = 0
        self.connection_ends = []
        self.auth_challenges = 0
        self.auth_signatures = []
        self.auth_public_keys = []

        self._lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._threads = []
        self._sockets = []
        self._closed = False
        self._remote_id = 1000
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(('127.0.0.1', 0))
        self._server.listen(16)
        self.port = self._server.getsockname()[1]
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __str__(self):
        return f'{self.__class__.__name__}(port={self.port})'

    __repr__ = __str__

    # ------------------------------------------------------------------ life

    def close(self):
        """
        Stop the device: close the listener, wake every blocked read and join
        the threads.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            sockets = list(self._sockets)
            threads = list(self._threads) + [self._accept_thread]
        try:
            self._server.close()
        except OSError:
            pass
        for sock in sockets:
            close_socket(sock)
        for thread in threads:
            if thread is not threading.current_thread() and thread.is_alive():
                thread.join(timeout=5)

    def drop_connections(self):
        """
        Close every connection, the listener stays open.

        This is what a device that restarts does to the host: the socket is
        gone, the address still accepts a new one.
        """
        with self._lock:
            sockets = list(self._sockets)
            self._sockets = []
        for sock in sockets:
            close_socket(sock)

    def snapshot(self):
        """
        Read the recordings of the device as plain data.

        Returns:
            dict: Counters and lists of what the device saw, json serializable
        """
        with self._lock:
            return {
                'connections': self.connections,
                'connection_ends': list(self.connection_ends),
                'opens': list(self.opens),
                'opens_without_nul': len(self.opens_without_nul),
                'writes': len(self.writes),
                'write_okays': self.write_okays,
                'violations': [reason for reason, _ in self.violations],
            }

    def wait_for(self, predicate, timeout=5, message=None):
        """
        Wait until a recording of the device satisfies a predicate.

        Args:
            predicate (callable): None-ary callable, True when the state is the
                one the test waits for
            timeout (float): Seconds to wait. Defaults to 5.
            message (str): Message of the assertion. Defaults to None.

        Returns:
            bool: True when the predicate held before the timeout

        Raises:
            AssertionError: When the predicate never held
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.002)
        assert predicate(), message or 'the device never reached the expected state'
        return True

    # --------------------------------------------------------------- accept

    def _accept_loop(self):
        while True:
            try:
                sock, _ = self._server.accept()
            except OSError:
                return
            with self._lock:
                if self._closed:
                    close_socket(sock)
                    return
                self._sockets.append(sock)
            thread = threading.Thread(target=self._serve_connection, args=(sock,), daemon=True)
            with self._lock:
                self._threads.append(thread)
            thread.start()

    def _serve_connection(self, sock):
        """
        Serve one connection: the handshake, then the messages of the host.
        """
        end = 'aborted'
        try:
            message = self._recv_message(sock)
            if message is None:
                return
            command, arg0, arg1, data = message
            with self._lock:
                self.connections += 1
                self.hello = data
                self.client_version = arg0
                self.client_max_payload = arg1
            if command != CNXN:
                self._violation(f'first message is not CNXN but {command}', message)
                return

            if self.silent:
                self._drain(sock)
                return

            if self.auth_required and not self._authenticate(sock):
                return

            if self.cnxn_reply is not None:
                _sendall(sock, self.cnxn_reply)
            else:
                self._send(sock, CNXN, self.version, self.max_payload, self.banner)

            self._read_loop(sock)
        except ConnectionResetError:
            # the host closed a socket whose receive buffer still held data
            end = 'reset'
        except OSError:
            # another socket error, e.g. the device closed the socket itself
            end = 'error'
        else:
            end = 'eof'
        finally:
            close_socket(sock)
            with self._lock:
                self.connection_ends.append(end)

    def _authenticate(self, sock):
        """
        Run the AUTH exchange of a secure adbd.

        A device that trusts the key of the host sends a token and accepts the
        signature of it. A device that sees the key for the first time rejects
        the signature, sends another token and lets the host offer its public
        key, which is what the dialog of the device is about.

        Args:
            sock (socket.socket): Connection to authenticate

        Returns:
            bool: True when the host is authorized, False when the connection is
                over
        """
        token = AUTH_TOKEN
        while True:
            with self._lock:
                self.auth_challenges += 1
            self._send(sock, AUTH, self.auth_arg0, 0, token)
            message = self._recv_message(sock)
            if message is None:
                return False
            command, arg0, arg1, data = message
            if command != AUTH:
                self._violation(f'{command} during authentication', message)
                return False
            if arg0 == ADB_AUTH_SIGNATURE:
                with self._lock:
                    self.auth_signatures.append(data)
                if self.auth_key is not None and verify_signature(self.auth_key, token, data):
                    return True
                # a signature the device can not verify is answered by a new
                # token, which is also what a device that does not know the key
                # does when the host has only sent the signature
                token = next_auth_token(token)
                continue
            if arg0 == ADB_AUTH_RSAPUBLICKEY:
                with self._lock:
                    self.auth_public_keys.append(data)
                key = self._accept_public_key(data, message)
                if key is None:
                    return False
                if not self.auth_allow_new_key:
                    # the user denied the dialog, the device keeps asking for a
                    # key that it accepts
                    token = next_auth_token(token)
                    continue
                if self.auth_delay:
                    time.sleep(self.auth_delay)
                self.auth_key = key
                return True
            self._violation(f'AUTH arg0={arg0} from the host', message)
            return False

    def _accept_public_key(self, data, message):
        """
        Decode the public key a host offers, the way adbd decodes it.

        Args:
            data (bytes): Payload of the AUTH message
            message (tuple): The message, for the violation record

        Returns:
            tuple | None: (n, e) of the key, None when the payload is not one
        """
        if not data.endswith(b'\x00'):
            self._violation('the public key of the host is not null terminated', message)
            return None
        text, _, _ = data[:-1].partition(b' ')
        try:
            blob = base64.b64decode(text, validate=True)
        except ValueError:
            self._violation('the public key of the host is not base64', message)
            return None
        key = decode_public_key(blob)
        if key is None:
            self._violation('the public key of the host is not an adb public key', message)
        return key

    def _read_loop(self, sock):
        """
        Read the messages of the host until it closes the connection.
        """
        while True:
            message = self._recv_message(sock)
            if message is None:
                return
            command, arg0, arg1, data = message
            if command == OPEN:
                thread = threading.Thread(
                    target=self._serve_open, args=(sock, arg0, data), daemon=True, name='fake-adbd-open'
                )
                with self._lock:
                    self._threads.append(thread)
                thread.start()
            elif command == WRTE:
                with self._lock:
                    self.writes.append(data)
                    written = len(self.writes)
                if self.close_after_writes and written >= self.close_after_writes:
                    self._send(sock, CLSE, arg1, arg0)
                elif self.answer_write_okay:
                    self._send(sock, OKAY, arg1, arg0)
            elif command == CLSE:
                with self._lock:
                    self.client_closes.append((arg0, arg1))
                # the peer of a closed stream answers with its own CLSE: our id
                # is arg0 of the message we send, the id of the peer is arg1
                if self.answer_close_clse:
                    self._send(sock, CLSE, arg1, arg0)
            elif command == OKAY:
                with self._lock:
                    self.write_okays += 1
            else:
                self._violation(f'unexpected command {command} from the host', message)

    def _drain(self, sock):
        """
        Read and drop every message until the host closes the connection.
        """
        while self._recv_message(sock) is not None:
            pass

    # ------------------------------------------------------------- services

    def _serve_open(self, sock, local_id, data):
        """
        Answer one OPEN the way adbd does, then serve the stream.

        Args:
            sock (socket.socket): Connection the OPEN came from
            local_id (int): Stream id of the host
            data (bytes): Payload of the OPEN, the service name with its null
        """
        if data.endswith(b'\x00'):
            service = data[:-1].decode('utf-8', 'replace')
        else:
            with self._lock:
                self.opens_without_nul.append(data)
            service = data.decode('utf-8', 'replace')
        with self._lock:
            self.opens.append(service)

        fault = self.open_fault
        if fault == 'close':
            close_socket(sock)
            return
        if self.open_delay:
            time.sleep(self.open_delay)
        if fault == 'silent':
            return
        if fault == 'refuse' or not self.accepts(service):
            # adbd answers a service it can not create with a CLSE and never
            # assigned a stream id, the remote id stays 0
            self._send(sock, CLSE, 0, local_id)
            return

        name, options, command = split_service(service)
        if name != 'shell':
            # osap, abb, sync and exec are services of their own, this device
            # only mirrors the shell
            self._send(sock, CLSE, 0, local_id)
            return

        remote_id = self._allocate_remote_id()
        if fault in ('bad_magic', 'bad_checksum', 'unknown_command', 'oversize_length'):
            self.fault_on_reply = fault
        self._send(sock, OKAY, remote_id, local_id)
        if self.fault_on_reply:
            # a fault that was meant for the OKAY is never left behind
            self.fault_on_reply = ''

        reply = self.responder(command, 'v2' in options)
        if reply.delay:
            time.sleep(reply.delay)
        if 'v2' in options:
            payload = shell_v2_stream(reply.stdout, reply.stderr, reply.exitcode, self.frame_size)
        else:
            # a shell without v2 has no channel for the standard error, adbd
            # merges both into the output of the stream
            payload = reply.stdout + reply.stderr

        chunk_size = self.reply_chunk_size
        for start in range(0, len(payload), chunk_size):
            self._send(sock, WRTE, remote_id, local_id, payload[start:start + chunk_size])
        if reply.close:
            self._send(sock, CLSE, remote_id, local_id)

    def accepts(self, service):
        """
        Check whether the device creates a service.

        Args:
            service (str): Service name of the OPEN

        Returns:
            bool: True when the device accepts the service
        """
        if self.refuse is None:
            return True
        return not self.refuse(service)

    def _allocate_remote_id(self):
        with self._lock:
            self._remote_id += 1
            return self._remote_id

    # ---------------------------------------------------------------- wire

    def _send(self, sock, command, arg0, arg1, data=b''):
        """
        Send one message, applying the one shot fault of `fault_on_reply`.
        """
        fault = self.fault_on_reply
        self.fault_on_reply = ''
        magic = None
        checksum = None
        length = None
        if fault == 'bad_magic':
            magic = int.from_bytes(command, 'little') ^ 0xFFFFFFFF ^ 0x1
        elif fault == 'bad_checksum':
            checksum = (sum(data) & 0xFFFFFFFF) ^ 0x1
        elif fault == 'unknown_command':
            command = b'XXXX'
        elif fault == 'oversize_length':
            length = 0xFFFFFFFF
        elif self.zero_checksum:
            # a device of 0x01000001 may not compute one at all
            checksum = 0
        with self._send_lock:
            _sendall(sock, encode_message(command, arg0, arg1, data, magic, checksum, length))

    def _recv_message(self, sock):
        """
        Read one message of the host and validate it the way adbd validates it.

        Args:
            sock (socket.socket): Connection to read

        Returns:
            tuple | None: (command, arg0, arg1, data), None when the connection
                is closed or the host broke the protocol
        """
        header = _recv_exact(sock, HEADER_SIZE)
        if header is None:
            return None
        value, arg0, arg1, length, checksum, magic = HEADER.unpack(header)
        command = value.to_bytes(4, 'little')
        message = (command, arg0, arg1, b'')
        if magic != value ^ 0xFFFFFFFF:
            # a device drops a connection that speaks another dialect
            self._violation(f'magic of {command} is {magic:#x}', message)
            return None
        # an adbd that advertises nothing is one of the first protocol, it
        # still reads ADB_MAX_PAYLOAD_V1 per message
        limit = self.max_payload or ADB_MAX_PAYLOAD_V1
        if length > limit:
            # reading the length of a broken header would wait for gigabytes
            self._violation(f'{command} claims {length} bytes, the limit is {limit}', message)
            return None
        data = b''
        if length:
            data = _recv_exact(sock, length)
            if data is None:
                return None
        message = (command, arg0, arg1, data)
        # adbd verifies the checksum only on a connection older than the version
        # that made the field optional. The CNXN carries the version of the host
        # in its own header, every message after it belongs to the connection,
        # whose version is the lower of the two sides
        version = arg0 if command == CNXN else min(self.client_version, self.version)
        if version < ADB_VERSION_SKIP_CHECKSUM and checksum != sum(data) & 0xFFFFFFFF:
            self._violation(f'checksum of {command} is {checksum:#x}', message)
            return None
        with self._lock:
            self.messages.append(message)
        return message

    def _violation(self, reason, message):
        with self._lock:
            self.violations.append((reason, message))


def _recv_exact(sock, length):
    """
    Read exactly length bytes.

    Args:
        sock (socket.socket): Connection to read
        length (int): Bytes to read

    Returns:
        bytes | None: The data, None when the peer closed the connection
    """
    chunks = []
    remain = length
    while remain:
        chunk = sock.recv(remain)
        if not chunk:
            return None
        chunks.append(chunk)
        remain -= len(chunk)
    return b''.join(chunks)


def _sendall(sock, data):
    """
    Send bytes, ignoring a connection the peer already closed.

    Args:
        sock (socket.socket): Connection to write
        data (bytes): Bytes to send
    """
    try:
        sock.sendall(data)
    except OSError:
        pass


def close_socket(sock):
    """
    Close a socket, waking a read that another thread blocks on.

    Args:
        sock (socket.socket): Socket to close
    """
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass
