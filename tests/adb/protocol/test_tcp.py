"""
Tests for the high level client on top of the tcp transport.

`AdbTCP` turns a stream into a `ShellResult`, caches the properties of the
device and reads its sdk level. The device is `fake_adbd`, which answers the
way a real adbd does, including the banner of an old one.
"""

import pytest

from alasio.adb.protocol.const import AdbStreamClosed, AdbStreamTimeout, ShellResult
from alasio.adb.protocol.tcp import AdbTCP
from alasio.logger import logger
from tests.adb.protocol.fake_adbd import LEGACY_BANNER, FakeAdbd, ShellReply, shell_v2_stream

pytestmark = [
    pytest.mark.parallel,
]

GETPROP = b"""\
[ro.build.version.sdk]: [32]
[ro.product.brand]: [HUAWEI]
[ro.product.cpu.abilist]: [arm64-v8a,armeabi-v7a,armeabi]
[ro.build.type]: [user]
"""


def connect(adbd, timeout=5):
    """
    Connect a client to a fake device

    Args:
        adbd (FakeAdbd): The device
        timeout (float): Timeout of the connection. Defaults to 5.

    Returns:
        AdbTCP: The connected client
    """
    client = AdbTCP(port=adbd.port)
    client.timeout = timeout
    client.connect()
    return client


class TestShellV2:
    """The shell service of a device with the shell_v2 feature"""

    def test_output_and_exit_code(self):
        """stdout, stderr and the exit code come out separated"""
        reply = ShellReply(stdout=b'out\n', stderr=b'err\n', exitcode=42)

        with FakeAdbd(responder=lambda command, shell_v2: reply) as adbd:
            client = connect(adbd)
            try:
                result = client.shell('sh -c out')
            finally:
                client.disconnect()

        assert result == ShellResult(stdout=b'out\n', stderr=b'err\n', exitcode=42, shell_v2=True)
        assert adbd.opens == ['shell,v2:sh -c out']

    def test_a_command_without_output(self):
        """A silent command is not an error, it is an empty result"""
        with FakeAdbd() as adbd:
            client = connect(adbd)
            try:
                result = client.shell('true')
            finally:
                client.disconnect()

        assert result == ShellResult(stdout=b'', stderr=b'', exitcode=0, shell_v2=True)

    def test_the_exit_code_of_a_failure(self):
        """An exit code of 1 is reported, it is not mistaken for a break"""
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(exitcode=1)) as adbd:
            client = connect(adbd)
            try:
                result = client.shell('false')
            finally:
                client.disconnect()

        assert result.exitcode == 1

    def test_a_large_output(self):
        """The output is rebuilt from many frames and many messages"""
        payload = bytes(range(256)) * 300

        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(stdout=payload)) as adbd:
            adbd.frame_size = 1000
            adbd.reply_chunk_size = 4096
            client = connect(adbd)
            try:
                result = client.shell('cat big')
            finally:
                client.disconnect()

        assert result.stdout == payload
        assert result.exitcode == 0

    def test_both_channels_of_one_command(self):
        """stdout and stderr keep their own order and their own channel"""
        reply = ShellReply(stdout=b'a' * 5000, stderr=b'b' * 5000)

        with FakeAdbd(responder=lambda command, shell_v2: reply, ) as adbd:
            adbd.frame_size = 512
            client = connect(adbd)
            try:
                result = client.shell('mix')
            finally:
                client.disconnect()

        assert result.stdout == b'a' * 5000
        assert result.stderr == b'b' * 5000

    def test_the_command_is_not_escaped(self):
        """A command with quotes and a pipe reaches the device as it is"""
        command = 'echo "a b" | grep a'

        with FakeAdbd(responder=lambda cmd, shell_v2: ShellReply(stdout=cmd.encode())) as adbd:
            client = connect(adbd)
            try:
                result = client.shell(command)
            finally:
                client.disconnect()

        assert adbd.opens == ['shell,v2:' + command]
        assert result.stdout == command.encode()

    def test_a_refused_shell(self):
        """A device without the shell service is an error, not an empty result"""
        with FakeAdbd(refuse=lambda service: True) as adbd:
            client = connect(adbd)
            try:
                with pytest.raises(AdbStreamClosed):
                    client.shell('echo hi')
            finally:
                client.disconnect()

    def test_the_timeout_of_the_call_reaches_the_stream(self):
        """The timeout of shell() is the timeout of the read, not the default"""
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(stdout=b'late', delay=1.0)) as adbd:
            client = connect(adbd)
            try:
                with pytest.raises(AdbStreamTimeout):
                    client.shell('slow', timeout=0.1)
            finally:
                client.disconnect()


class TestShellV1:
    """The shell service of a device without the shell_v2 feature"""

    def test_an_old_device_falls_back(self):
        """The banner of an old device has no shell_v2, the plain shell is used"""
        with FakeAdbd(banner=LEGACY_BANNER, version=0x01000000) as adbd:
            client = connect(adbd)
            try:
                assert client.features.shell_v2 is False
                result = client.shell('echo hi')
            finally:
                client.disconnect()

        assert adbd.opens == ['shell:echo hi']
        assert result == ShellResult(stdout=b'', stderr=b'', exitcode=0, shell_v2=False)

    def test_the_merged_output_of_an_old_device(self):
        """A shell v1 stream merges the two channels, there is no exit code"""
        with FakeAdbd(banner=LEGACY_BANNER, responder=lambda command, shell_v2: ShellReply(
            stdout=b'out\n', stderr=b'err\n', exitcode=3
        )) as adbd:
            client = connect(adbd)
            try:
                result = client.shell('mix')
            finally:
                client.disconnect()

        assert result.stdout == b'out\nerr\n'
        assert result.stderr == b''
        assert result.exitcode == 0
        assert result.shell_v2 is False

    def test_shell_v2_can_be_turned_off(self):
        """A caller may ask for the plain shell on a modern device"""
        with FakeAdbd() as adbd:
            client = connect(adbd)
            try:
                result = client.shell('echo hi', shell_v2=False)
            finally:
                client.disconnect()

        assert adbd.opens == ['shell:echo hi']
        assert result.shell_v2 is False

    def test_a_new_device_that_does_not_list_the_feature(self):
        """The feature list decides, not the version of the protocol"""
        banner = b'device::ro.product.name=x;features=cmd,stat_v2'

        with FakeAdbd(banner=banner, version=0x01000001) as adbd:
            client = connect(adbd)
            try:
                client.shell('echo hi')
            finally:
                client.disconnect()

        assert adbd.opens == ['shell:echo hi']

    def test_a_binary_output(self):
        """A shell v1 stream can carry any byte, it is not decoded"""
        payload = bytes(range(256))

        with FakeAdbd(banner=LEGACY_BANNER, responder=lambda command, shell_v2: ShellReply(stdout=payload)) as adbd:
            client = connect(adbd)
            try:
                result = client.shell('cat bin')
            finally:
                client.disconnect()

        assert result.stdout == payload


class TestProps:
    """The properties of the device"""

    def test_parsed_from_getprop(self):
        """The property map comes from one getprop of the device"""
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(stdout=GETPROP)) as adbd:
            client = connect(adbd)
            try:
                props = client.props
            finally:
                client.disconnect()

        assert props.props == {
            'ro.build.version.sdk': '32',
            'ro.product.brand': 'HUAWEI',
            'ro.product.cpu.abilist': 'arm64-v8a,armeabi-v7a,armeabi',
            'ro.build.type': 'user',
        }
        assert props.get_str('ro.product.brand') == 'HUAWEI'
        assert props.get_int('ro.build.version.sdk') == 32
        assert props.get('ro.product.cpu.abilist') == ['arm64-v8a', 'armeabi-v7a', 'armeabi']

    def test_getprop_runs_once(self):
        """The property map is cached for the life of the client"""
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(stdout=GETPROP)) as adbd:
            client = connect(adbd)
            try:
                first = client.props
                second = client.props
            finally:
                client.disconnect()

        assert first is second
        assert adbd.opens == ['shell:getprop']

    def test_getprop_uses_the_plain_shell(self):
        """A property dump has no exit code, the v1 stream is enough"""
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(stdout=GETPROP)) as adbd:
            client = connect(adbd)
            try:
                client.props
            finally:
                client.disconnect()

        assert adbd.opens == ['shell:getprop']

    def test_a_device_without_properties(self):
        """An empty dump is an empty map, not an error"""
        with FakeAdbd() as adbd:
            client = connect(adbd)
            try:
                props = client.props
            finally:
                client.disconnect()

        assert props.props == {}
        assert props.get_str('ro.product.brand') is None


class TestSdkVer:
    """The sdk level of the device"""

    def test_a_valid_level(self):
        """The level is an int, read from the property map"""
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(stdout=GETPROP)) as adbd:
            client = connect(adbd)
            try:
                with logger.mock_capture_writer() as capture:
                    sdk = client.sdk_ver
            finally:
                client.disconnect()

        assert sdk == 32
        assert capture.concat() == 'INFO | [sdk_ver]: 32\n'

    def test_a_missing_level(self):
        """A device that does not report a level is logged and reported as 0"""
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(stdout=b'[ro.product.brand]: [HUAWEI]\n')) as adbd:
            client = connect(adbd)
            try:
                with logger.mock_capture_writer() as capture:
                    sdk = client.sdk_ver
            finally:
                client.disconnect()

        assert sdk == 0
        assert capture.concat() == 'ERROR | SDK version invalid: None\n'

    def test_a_level_of_zero(self):
        """A reported level of 0 is invalid as well, it is not silently kept"""
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(stdout=b'[ro.build.version.sdk]: [0]\n')) as adbd:
            client = connect(adbd)
            try:
                with logger.mock_capture_writer() as capture:
                    sdk = client.sdk_ver
            finally:
                client.disconnect()

        assert sdk == 0
        assert capture.concat() == 'ERROR | SDK version invalid: 0\n'

    def test_a_level_that_is_not_a_number(self):
        """A garbage level is reported as 0"""
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(stdout=b'[ro.build.version.sdk]: [abc]\n')) as adbd:
            client = connect(adbd)
            try:
                with logger.mock_capture_writer() as capture:
                    sdk = client.sdk_ver
            finally:
                client.disconnect()

        assert sdk == 0
        assert capture.concat() == 'ERROR | SDK version invalid: None\n'

    def test_a_recent_level(self):
        """A high level of a new android is not truncated"""
        with FakeAdbd(responder=lambda command, shell_v2: ShellReply(stdout=b'[ro.build.version.sdk]: [35]\n')) as adbd:
            client = connect(adbd)
            try:
                sdk = client.sdk_ver
            finally:
                client.disconnect()

        assert sdk == 35


class TestDeviceFeatures:
    """The features of the banner are used, the version is not"""

    def test_a_device_with_every_feature(self):
        """The features of the banner are parsed into the client"""
        with FakeAdbd() as adbd:
            client = connect(adbd)
            try:
                features = client.features
            finally:
                client.disconnect()

        assert features.shell_v2 is True
        assert b'shell_v2' in features.features
        assert b'cmd' in features.features

    def test_a_legacy_banner_has_no_shell_v2(self):
        """An old device is recognised by its feature list"""
        with FakeAdbd(banner=LEGACY_BANNER) as adbd:
            client = connect(adbd)
            try:
                assert client.features.shell_v2 is False
            finally:
                client.disconnect()


class TestShellV2Framing:
    """The v2 stream of the device is framed by the shell protocol"""

    def test_the_frames_of_a_reply(self):
        """stdout and stderr of a v2 reply are framed, not concatenated"""
        reply = shell_v2_stream(stdout=b'out', stderr=b'err', exitcode=7)
        result = ShellResult.from_shell_v2(reply)

        assert (result.stdout, result.stderr, result.exitcode) == (b'out', b'err', 7)
