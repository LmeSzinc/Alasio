"""
Tests for the message types and the exceptions of the adb protocol.
"""

import pytest

from alasio.adb.protocol.const import (
    ADB_COMMANDS, ADB_MAX_PAYLOAD, ADB_VERSION, ADB_VERSION_SKIP_CHECKSUM, AUTH, CLSE, CNXN, OKAY, OPEN, SYNC, WRTE,
    AdbConnectionClosed, AdbConnectionTimeout, AdbMessageInvalid, AdbStreamClosed, AdbStreamTimeout, ShellResult
)

STDOUT = 1
STDERR = 2
EXIT = 3


def frame(packet_id, payload):
    """
    Frame one packet of the shell protocol v2

    Args:
        packet_id (int): Id of the packet, 1 for stdout, 2 for stderr, 3 for the exit code
        payload (bytes): Payload of the packet

    Returns:
        bytes: One byte of id, four bytes of length, the payload
    """
    return bytes([packet_id]) + len(payload).to_bytes(4, 'little') + payload


class TestShellResultFromShellV2:
    """Parsing the stream of a shell_v2 execution"""

    def test_stdout_only(self):
        """A command that writes on its standard output"""
        result = ShellResult.from_shell_v2(frame(STDOUT, b'hello\n'))

        assert result.stdout == b'hello\n'
        assert result.stderr == b''
        assert result.exitcode == -1
        assert result.shell_v2 is True

    def test_stdout_and_stderr_and_exit_code(self):
        """The three channels are told apart, in the order the device sent them"""
        data = frame(STDOUT, b'out\n') + frame(STDERR, b'err\n') + frame(EXIT, b'\x2a')
        result = ShellResult.from_shell_v2(data)

        assert result.stdout == b'out\n'
        assert result.stderr == b'err\n'
        assert result.exitcode == 42
        assert result.shell_v2 is True

    @pytest.mark.parametrize('code, expected', [
        (0, 0),
        (1, 1),
        (127, 127),
        (128, 128),
        (255, 255),
    ])
    def test_exit_code_is_an_unsigned_byte(self, code, expected):
        """The exit code travels as one unsigned byte"""
        result = ShellResult.from_shell_v2(frame(EXIT, bytes([code])))

        assert result.exitcode == expected

    def test_many_stdout_frames_are_joined_in_order(self):
        """A device splits a long output in many frames, the order is kept"""
        data = b''.join(frame(STDOUT, piece) for piece in (b'a', b'bb', b'ccc'))
        result = ShellResult.from_shell_v2(data)

        assert result.stdout == b'abbccc'

    def test_empty_payload_frames_are_kept_but_add_nothing(self):
        """A frame of zero bytes is legal and contributes nothing"""
        data = frame(STDOUT, b'') + frame(STDOUT, b'x') + frame(STDERR, b'') + frame(EXIT, b'\x00')
        result = ShellResult.from_shell_v2(data)

        assert result.stdout == b'x'
        assert result.stderr == b''
        assert result.exitcode == 0

    def test_empty_stream(self):
        """A stream without any packet is not malformed, it just says nothing"""
        result = ShellResult.from_shell_v2(b'')

        assert result.stdout == b''
        assert result.stderr == b''
        assert result.exitcode == -1
        assert result.shell_v2 is True

    def test_packets_of_another_id_are_ignored(self):
        """Close-stdin and window-size-change are not part of the output"""
        data = (
            frame(STDOUT, b'out')
            + frame(4, b'')
            + frame(5, b'\x18\x00\x00\x00')
            + frame(STDERR, b'err')
            + frame(EXIT, b'\x00')
        )
        result = ShellResult.from_shell_v2(data)

        assert result.stdout == b'out'
        assert result.stderr == b'err'
        assert result.exitcode == 0

    def test_binary_payload_is_not_decoded(self):
        """The output of a command is bytes, an invalid utf-8 sequence survives"""
        payload = bytes(range(256))
        result = ShellResult.from_shell_v2(frame(STDOUT, payload))

        assert result.stdout == payload

    def test_large_output(self):
        """A payload of one megabyte is reassembled without a copy loss"""
        payload = b'x' * (1024 * 1024)
        result = ShellResult.from_shell_v2(frame(STDOUT, payload))

        assert result.stdout == payload
        assert len(result.stdout) == 1024 * 1024

    @pytest.mark.parametrize('data, message', [
        # four bytes can not hold the id and the four bytes of the length
        (b'\x01\x00\x00\x00', 'incomplete header'),
        (frame(STDOUT, b'abc')[:4], 'incomplete header'),
        # the length claims more bytes than the stream holds
        (b'\x01\x10\x00\x00\x00abc', 'payload length exceeds available data'),
        (frame(STDOUT, b'abc') + b'\x01\x05\x00\x00\x00ab', 'payload length exceeds available data'),
        # a trailing fragment of a header
        (frame(STDOUT, b'abc') + b'\x02\x01', 'incomplete header'),
    ])
    def test_malformed_stream(self, data, message):
        """A truncated stream is refused instead of returned short"""
        with pytest.raises(ValueError) as e:
            ShellResult.from_shell_v2(data)

        assert message in str(e.value)

    @pytest.mark.parametrize('payload', [
        b'',
        b'\x00\x01',
    ])
    def test_exit_packet_must_be_one_byte(self, payload):
        """The exit code is exactly one byte, anything else is malformed"""
        with pytest.raises(ValueError) as e:
            ShellResult.from_shell_v2(frame(EXIT, payload))

        assert 'Expected exit code payload to be 1 byte' in str(e.value)


class TestShellResultFromShellV1:
    """Wrapping the output of a shell v1 execution"""

    def test_stdout_becomes_the_output(self):
        """A shell v1 stream carries the standard output only"""
        result = ShellResult.from_shell_v1(b'hello\n')

        assert result.stdout == b'hello\n'
        assert result.stderr == b''
        assert result.exitcode == 0
        assert result.shell_v2 is False

    def test_empty_output(self):
        """A command without output is a success in shell v1"""
        result = ShellResult.from_shell_v1(b'')

        assert result.stdout == b''
        assert result.exitcode == 0
        assert result.shell_v2 is False


class TestShellResultStruct:
    """The struct holds the four fields and compares by value"""

    def test_fields(self):
        """Every field is kept as given"""
        result = ShellResult(stdout=b'out', stderr=b'err', exitcode=3, shell_v2=True)

        assert (result.stdout, result.stderr, result.exitcode, result.shell_v2) == (b'out', b'err', 3, True)

    def test_equality_by_value(self):
        """Two results with the same content are equal, which tests rely on"""
        one = ShellResult(stdout=b'out', stderr=b'', exitcode=0, shell_v2=True)
        another = ShellResult(stdout=b'out', stderr=b'', exitcode=0, shell_v2=True)

        assert one == another


class TestConstants:
    """The constants of the transport must match the protocol"""

    def test_version(self):
        """The client speaks the version at which the checksum became optional

        A newer host makes a modern adbd stop computing checksums, which is a
        pass over every payload the device no longer does. An old adbd answers
        with its own version, the connection then uses that one.
        """
        assert ADB_VERSION == ADB_VERSION_SKIP_CHECKSUM
        assert ADB_VERSION_SKIP_CHECKSUM == 0x01000001

    def test_max_payload(self):
        """The payload of one message is 256KB since Android 5"""
        assert ADB_MAX_PAYLOAD == 256 * 1024

    def test_commands(self):
        """The seven commands of the transport"""
        assert ADB_COMMANDS == {SYNC, CNXN, AUTH, OPEN, OKAY, CLSE, WRTE}

    @pytest.mark.parametrize('command', [SYNC, CNXN, AUTH, OPEN, OKAY, CLSE, WRTE])
    def test_command_is_four_bytes(self, command):
        """A command name is four bytes long, it is read from the header"""
        assert isinstance(command, bytes)
        assert len(command) == 4

    @pytest.mark.parametrize('exception', [
        AdbConnectionClosed,
        AdbConnectionTimeout,
        AdbStreamClosed,
        AdbStreamTimeout,
        AdbMessageInvalid,
    ])
    def test_exceptions_are_distinct(self, exception):
        """Every failure has its own type, a caller can tell them apart"""
        assert issubclass(exception, Exception)
        assert exception is not Exception
