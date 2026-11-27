from collections import deque

import msgspec

SYNC = b'SYNC'
CNXN = b'CNXN'
AUTH = b'AUTH'
OPEN = b'OPEN'
OKAY = b'OKAY'
CLSE = b'CLSE'
WRTE = b'WRTE'

ADB_COMMANDS = {SYNC, CNXN, AUTH, OPEN, OKAY, CLSE, WRTE}
# First argument of AUTH. A secure adbd sends a token, the host answers with the
# signature of that token and, when the device does not know the key yet, with
# the public key itself
ADB_AUTH_TOKEN = 1
ADB_AUTH_SIGNATURE = 2
ADB_AUTH_RSAPUBLICKEY = 3
# A device sends 20 random bytes to sign
AUTH_TOKEN_SIZE = 20
# Seconds to wait for the user to allow the key on the device. A first contact
# shows a dialog, and the timeout of a request would close the connection before
# the user can read it
ADB_AUTH_CONFIRM_TIMEOUT = 60
# From this version on the checksum of a message is optional: a peer of that
# version may send 0 instead of the sum of the payload, and a connection of that
# version verifies nothing
ADB_VERSION_SKIP_CHECKSUM = 0x01000001
# Version this client advertises in CNXN, the protocol version of a connection
# is the lower of the two sides. An old adbd answers with its own version, the
# connection then uses 0x01000000 and every message is checksummed
ADB_VERSION = ADB_VERSION_SKIP_CHECKSUM
# Payload of one message of the first version of the transport. An adbd that
# predates the field leaves it empty and reads 4096 bytes per message
ADB_MAX_PAYLOAD_V1 = 4096
# Payload this client accepts and advertises in CNXN, the default since Android 5
ADB_MAX_PAYLOAD = 256 * 1024
# Bytes of output a stream buffers before it holds back the acknowledgement of
# the device. A device sends the next write of a stream only after the previous
# one was acknowledged, so holding one back is what slows a device down.
# Without it a writer that is not read grows the buffer without a bound. The
# value is the payload of one message of adbd, so a reader that keeps up takes
# the output in pieces of a message and a device is only held back by a reader
# that fell a whole message behind
ADB_STREAM_RECV_BUFFER_SIZE = 1024 * 1024
# A device may split its output into many small writes, and every write is a
# separate object in the buffer of a stream. A write smaller than this is held
# back and combined with the small writes that follow it, so a reader that fell
# far behind holds a few objects per kilobyte instead of one object per write.
# A write this large is worth an object of its own and is kept as it is: drawing
# the line by the number of writes instead would combine the writes of a large
# transfer, which copies the whole of it a second time. 4096 is the payload of
# an old adbd, so the writes of a device that old already stand on their own
ADB_STREAM_CHUNK_SIZE = 4096
# Small writes are also combined once there are this many of them, whatever
# their size. A device that writes a byte at a time would otherwise hold a
# thousand objects for one kilobyte of output, and the wait for a chunk size is
# paid back in object headers. Only writes below the chunk size are counted
# here, so this never combines a write that was worth an object already
ADB_STREAM_CHUNK_LIMIT = 32
# Consecutive OPENs a device may leave unanswered before its connection is
# dropped. Opening a service is a local operation of the device and adbd
# answers it in milliseconds, so a peer that misses the whole timeout of one
# request is not a device that is slow, and one that misses this many in a row
# is not an adbd. Dropping the connection is what lets the next connect()
# rebuild it, instead of every command paying the timeout forever
ADB_OPEN_TIMEOUT_LIMIT = 3


class AdbConnectionClosed(Exception):
    pass


class AdbConnectionTimeout(Exception):
    pass


class AdbAddressInvalid(Exception):
    pass


class AdbConnectionFailed(Exception):
    pass


class AdbStreamClosed(Exception):
    pass


class AdbStreamTimeout(Exception):
    pass


class AdbMessageInvalid(Exception):
    pass


class AdbAuthKeyMissing(Exception):
    pass


class AdbAuthKeyInvalid(Exception):
    pass


class AdbAuthRejected(Exception):
    pass


class ShellResult(msgspec.Struct):
    stdout: bytes
    stderr: bytes
    exitcode: int
    shell_v2: bool

    @classmethod
    def from_shell_v2(cls, data: bytes) -> "ShellResult":
        """
        Parses a raw byte stream from an ADB shell_v2 execution
        and creates a ShellResult instance.

        The shell_v2 stream consists of concatenated packets. Each packet has:
        - 1-byte ID (1=stdout, 2=stderr, 3=exitcode)
        - 4-byte little-endian length
        - N-byte payload

        Args:
            data: The complete byte stream received from the shell_v2 service.

        Returns:
            A new ShellResult instance with parsed data.

        Raises:
            ValueError: If the byte stream is malformed.
        """
        stdout_chunks = deque()
        stderr_chunks = []
        exitcode: int = -1  # Default exit code if not found in stream

        cursor = 0
        data = memoryview(data)
        data_len = len(data)

        while cursor < data_len:
            # Each packet must have at least a 5-byte header (ID + Length)
            header_end = cursor + 5
            if header_end > data_len:
                raise ValueError(
                    f'Malformed shell_v2 stream: incomplete header at position {cursor}'
                )

            packet_id = data[cursor]
            payload_len = int.from_bytes(data[cursor + 1: header_end], 'little')
            payload_end = header_end + payload_len

            if payload_end > data_len:
                raise ValueError(
                    f'Malformed shell_v2 stream: payload length exceeds available data at position {cursor}'
                )

            payload = data[header_end:payload_end]

            if packet_id == 1:
                stdout_chunks.append(payload)
            elif packet_id == 2:
                stderr_chunks.append(payload)
            elif packet_id == 3:
                if payload_len != 1:
                    raise ValueError(f'Expected exit code payload to be 1 byte, got {payload_len}')
                try:
                    exitcode = payload[0]
                except IndexError:
                    raise ValueError(f'Expected exit code payload to be 1 byte, actually no payload')
            # else: We can ignore unknown packet types

            # Move cursor to the start of the next packet
            cursor = payload_end

        # Combine all chunks and create the final object
        return cls(
            stdout=b''.join(stdout_chunks),
            stderr=b''.join(stderr_chunks),
            exitcode=exitcode,
            shell_v2=True,
        )

    @classmethod
    def from_shell_v1(cls, data: bytes) -> "ShellResult":
        """
        Wrap result of shell v1 as ShellResult, to provide a consistent function result
        """
        return cls(stdout=data, stderr=b'', exitcode=0, shell_v2=False)
