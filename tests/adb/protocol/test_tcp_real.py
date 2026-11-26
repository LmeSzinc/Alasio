"""
Tests against a real device.

A real adbd is never guessed: a machine may have nothing on 5555, and a device
that is half configured would turn a green suite red. The caller names the
device, and without a name every test of this module is skipped:

    $env:ALASIO_ADB_REAL_SERIAL = '127.0.0.1:16384'
    pytest tests/adb/protocol/test_tcp_real.py -q

The serial is a host and a port, because this client talks to adbd over TCP and
never through the adb server: `emulator-5554` of `adb devices` is an address of
the adb server, not of the device. The port of an emulator that forwards adbd is
shown by `adb devices` as the serial itself, e.g. `127.0.0.1:16384`, which is
what this module expects. A serial without a port uses 5555, the default port of
adbd over TCP.

The tests are read only: every command is an echo, a read of a property or a
sleep, nothing writes to the device.
"""

import os
import threading
import time

import pytest

from alasio.adb.protocol.const import ADB_MAX_PAYLOAD, AdbStreamClosed, AdbStreamTimeout
from alasio.adb.protocol.tcp import AdbTCP

# Environment variable that names the device to test against
SERIAL_ENV = 'ALASIO_ADB_REAL_SERIAL'
# Port adbd listens on when the serial names no port
DEFAULT_PORT = 5555


def parse_serial(serial):
    """
    Split a serial into the address of adbd.

    Args:
        serial (str): `host:port`, or a host without a port

    Returns:
        tuple: (host, port)

    Raises:
        ValueError: When the port is not a number
    """
    host, sep, port = serial.rpartition(':')
    if not sep:
        return serial, DEFAULT_PORT
    return host, int(port)


@pytest.fixture(scope='module')
def serial():
    """
    Serial of the device, the tests are skipped without one.

    Returns:
        str: The serial, as the caller wrote it
    """
    value = os.environ.get(SERIAL_ENV, '').strip()
    if not value:
        pytest.skip(f'Set {SERIAL_ENV} to a device serial to run the real device tests, e.g. 127.0.0.1:16384')
    return value


@pytest.fixture(scope='module')
def address(serial):
    """
    Address of adbd of the device under test.

    Args:
        serial (str): Serial of the device

    Returns:
        tuple: (host, port)
    """
    return parse_serial(serial)


@pytest.fixture(scope='module')
def device(address):
    """
    A connected client, shared by the tests of the module.

    Yields:
        AdbTCP: Client connected to the device under test
    """
    client = AdbTCP(*address)
    client.timeout = 10
    client.connect()
    try:
        yield client
    finally:
        client.disconnect()


class TestParseSerial:
    """The serial of the environment is an address of adbd"""

    @pytest.mark.parametrize('serial, expected', [
        ('127.0.0.1:16384', ('127.0.0.1', 16384)),
        ('127.0.0.1:5555', ('127.0.0.1', 5555)),
        ('localhost:5555', ('localhost', 5555)),
        ('192.168.1.2', ('192.168.1.2', 5555)),
    ])
    def test_parse(self, serial, expected):
        """A host and a port are split, a host alone gets the default port"""
        assert parse_serial(serial) == expected

    @pytest.mark.parametrize('serial', ['127.0.0.1:', '127.0.0.1:abc'])
    def test_a_broken_port(self, serial):
        """A port that is not a number is refused before any connection"""
        with pytest.raises(ValueError):
            parse_serial(serial)


class TestRealHandshake:
    """The handshake of the device"""

    def test_the_banner_gives_the_features(self, device):
        """The device answers a CNXN with a banner the client can read"""
        assert device.features.features, 'the banner of the device lists no feature at all'
        assert isinstance(device.features.shell_v2, bool)

    def test_the_payload_of_the_device_is_respected(self, device):
        """The device advertises a payload the client does not exceed"""
        assert 0 < device._max_payload <= ADB_MAX_PAYLOAD

    def test_the_connection_survives_many_calls(self, device):
        """The dispatch thread of the connection is still alive after calls"""
        for _ in range(5):
            assert device.shell('echo alive').stdout.strip() == b'alive'

    def test_the_client_is_connected(self, device):
        """The socket and the dispatch thread are those of a live connection"""
        assert device._sock is not None
        assert device._recv_thread.is_alive()
        assert device._recv_thread.daemon


class TestRealShell:
    """Commands on the device"""

    def test_echo(self, device):
        """The output of a command comes back on stdout"""
        result = device.shell('echo alasio-adb-probe')

        assert result.stdout.strip() == b'alasio-adb-probe'
        assert result.exitcode == 0

    def test_the_exit_code(self, device):
        """A failing command reports its exit code"""
        result = device.shell('exit 42')

        assert result.exitcode == 42

    def test_the_standard_error(self, device):
        """stderr is its own channel under shell_v2"""
        result = device.shell('echo oops >&2')

        assert result.stderr.strip() == b'oops'
        assert result.stdout.strip() == b''
        assert result.shell_v2 is device.features.shell_v2

    def test_a_command_without_output(self, device):
        """A silent command is an empty result with an exit code of 0"""
        result = device.shell('true')

        assert result.stdout == b''
        assert result.exitcode == 0

    def test_a_command_that_writes_a_lot(self, device):
        """An output larger than one message is reassembled in order"""
        result = device.shell('i=0; while [ $i -lt 2000 ]; do echo 0123456789; i=$((i+1)); done')

        assert result.exitcode == 0
        lines = result.stdout.splitlines()
        assert len(lines) == 2000
        assert lines[0] == b'0123456789'
        assert lines[-1] == b'0123456789'

    def test_the_shell_v1_of_the_device(self, device):
        """The plain shell of the same device merges the channels"""
        result = device.shell('echo v1', shell_v2=False)

        assert result.stdout.strip() == b'v1'
        assert result.shell_v2 is False

    def test_a_command_with_a_quote(self, device):
        """The command is passed to the shell of the device, not split here"""
        result = device.shell("echo 'a b' | tr -d ' '")

        assert result.stdout.strip() == b'ab'

    def test_a_command_that_reads_a_property(self, device):
        """The shell of the device sees the same properties as getprop"""
        result = device.shell('getprop ro.build.version.sdk')

        assert result.stdout.strip().isdigit()

    def test_many_streams_at_once(self, device):
        """The stream ids of a connection are unique under concurrency"""
        count = 8
        barrier = threading.Barrier(count, timeout=10)
        output = {}
        errors = []

        def run(index):
            try:
                barrier.wait()
                result = device.shell('echo %d' % index, timeout=20)
                output[index] = result.stdout.strip()
            except Exception as e:
                errors.append((index, e))

        threads = [threading.Thread(target=run, args=(i,)) for i in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        assert errors == []
        assert output == {i: str(i).encode() for i in range(count)}

    def test_a_timeout_leaves_the_connection_usable(self, device):
        """A command that does not end in time does not break the connection"""
        start = time.perf_counter()
        with pytest.raises(AdbStreamTimeout):
            device.shell('sleep 5', timeout=0.5)
        elapsed = time.perf_counter() - start

        assert elapsed < 4
        assert device.shell('echo after-timeout').stdout.strip() == b'after-timeout'

    def test_an_unknown_service(self, device):
        """A service the device does not know is refused, not waited for"""
        with pytest.raises(AdbStreamClosed):
            device.open_stream('alasio-no-such-service:cmd')


class TestRealProps:
    """The properties of the device"""

    def test_the_properties_are_read(self, device):
        """The property map is not empty and holds the usual keys"""
        props = device.props

        assert 'ro.build.version.sdk' in props.props
        assert 'ro.product.model' in props.props

    def test_the_sdk_level(self, device):
        """The sdk level is a positive int"""
        assert device.sdk_ver > 0

    def test_the_properties_match_getprop(self, device):
        """The cached map holds what getprop prints for one key"""
        one = device.shell('getprop ro.build.version.release').stdout.strip().decode('utf-8', 'replace')
        cached = device.props.get_str('ro.build.version.release')

        assert cached == one

    def test_the_abilist_keeps_its_commas(self, device):
        """A comma separated property is not truncated by the cache"""
        value = device.props.get_str('ro.product.cpu.abilist')

        assert value
        assert ',' in value
        assert device.props.get('ro.product.cpu.abilist') == value.split(',')

    def test_a_missing_property(self, device):
        """A property the device does not have is None, not an empty string"""
        assert device.props.get_str('alasio.no.such.property') is None
        assert device.props.get('alasio.no.such.property') == []
