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
ADB_VERSION = 0x01000000
ADB_MAX_PAYLOAD = 256 * 1024


class AdbConnectionClosed(Exception):
    pass


class AdbConnectionTimeout(Exception):
    pass


class AdbStreamClosed(Exception):
    pass


class AdbStreamTimeout(Exception):
    pass


class AdbMessageInvalid(Exception):
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
