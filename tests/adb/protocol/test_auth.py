"""
Tests for the AUTH half of the handshake.

The key of this file is built from two primes that were generated once for the
suite: a test never reads the adb key of whoever runs it, and the suite does not
need adb to be installed. What the client produces is checked with RSA itself
and with a decoder of the public key format written here, so a mistake of the
module can not cancel itself out. The file the key is read from belongs to
`test_keygen.py`, and the real adb binary runs on the CI runners, see the module
next to this one.
"""

import base64
import builtins
import hashlib
import struct

import pytest

import alasio.adb.protocol.keygen as keygen
import alasio.adb.protocol.tcp_protocol as tcp_protocol
from alasio.adb.protocol.auth import AdbAuthKey
from alasio.adb.protocol.const import AdbAuthKeyInvalid, AdbAuthRejected, AdbConnectionTimeout, AdbMessageInvalid
from alasio.adb.protocol.tcp import AdbTCP
from alasio.adb.protocol.tcp_protocol import AdbProtocolTCP
from alasio.testing.filesystem import fs  # noqa: F401
from tests.adb.protocol.fake_adbd import AUTH_TOKEN, FakeAdbd, ShellReply
from tests.adb.protocol.keydata import D, E, N, P, Q, pem, pkcs8_der

pytestmark = [
    pytest.mark.parallel,
]

# DigestInfo of SHA1 in PKCS#1 v1.5, the padding adbd verifies
SHA1_DIGEST_INFO = bytes.fromhex('3021300906052b0e03021a05000414')

KEY_PATH = '/home/tester/.android/adbkey'


def decode_public_key(blob):
    """
    Decode the public key blob with the layout of struct RSAPublicKey.

    Args:
        blob (bytes): Encoded key

    Returns:
        tuple: (words, n0inv, n, rr, e)
    """
    words, n0inv = struct.unpack_from('<II', blob, 0)
    n = int.from_bytes(blob[8:264], 'little')
    rr = int.from_bytes(blob[264:520], 'little')
    e = int.from_bytes(blob[520:524], 'little')
    return words, n0inv, n, rr, e


def verify_token(n, e, token, signature):
    """
    Verify a signature of an AUTH token with plain RSA.

    Args:
        n (int): Modulus of the key
        e (int): Public exponent of the key
        token (bytes): Token the device asked to sign
        signature (bytes): Signature of the host

    Returns:
        bool: True when the signature carries the digest of the token
    """
    size = (n.bit_length() + 7) // 8
    if len(signature) != size:
        return False
    digest = SHA1_DIGEST_INFO + hashlib.sha1(token).digest()
    block = b'\x00\x01' + b'\xff' * (size - len(digest) - 3) + b'\x00' + digest
    return pow(int.from_bytes(signature, 'big'), e, n).to_bytes(size, 'big') == block


def install_key(fs, monkeypatch, contents):
    """
    Put a key file where the module reads the adb key of the user.

    Args:
        fs: The in-memory filesystem
        monkeypatch: pytest fixture
        contents (str): Content of the key file

    Returns:
        str: Path of the file
    """
    fs.create_file(KEY_PATH, contents=contents)
    monkeypatch.setattr(keygen, 'user_key_path', lambda: KEY_PATH)
    return KEY_PATH


def connect(adbd, timeout=5):
    """
    Connect a client to a fake device.

    Args:
        adbd (FakeAdbd): The device
        timeout (float): Timeout of the connection. Defaults to 5.

    Returns:
        AdbProtocolTCP: The connected client
    """
    client = AdbProtocolTCP(port=adbd.port)
    client.timeout = timeout
    client.connect()
    return client


@pytest.fixture(scope='module')
def key():
    """The key of this file"""
    return AdbAuthKey(N, E, D)


class TestSignToken:
    def test_the_signature_verifies_with_the_public_key(self, key):
        """The device checks the signature with n and e, which have to match"""
        token = bytes(range(20))

        signature = key.sign(token)

        assert verify_token(N, E, token, signature)

    def test_the_signature_is_as_wide_as_the_modulus(self, key):
        """A 2048 bit key signs 256 bytes, the width adbd expects"""
        assert len(key.sign(AUTH_TOKEN)) == 256

    def test_the_signature_is_deterministic(self, key):
        """PKCS#1 v1.5 has no salt, the same token gives the same bytes"""
        assert key.sign(AUTH_TOKEN) == key.sign(AUTH_TOKEN)

    @pytest.mark.parametrize('size', [0, 19, 21, 32])
    def test_only_a_token_of_the_protocol_size_is_signed(self, key, size):
        """Signing something else than the 20 bytes of a device is a bug"""
        with pytest.raises(ValueError):
            key.sign(bytes(size))

    def test_a_key_too_small_for_the_digest_is_refused(self):
        """PKCS#1 needs 11 bytes of padding, a tiny key has none"""
        tiny = AdbAuthKey((1 << 127) | 1, 3, 1)

        with pytest.raises(AdbAuthKeyInvalid):
            tiny.sign(AUTH_TOKEN)

    def test_a_key_with_primes_signs_the_same_bytes(self):
        """The split of the CRT is a faster route to the one signature"""
        plain = AdbAuthKey(N, E, D)
        split = AdbAuthKey(N, E, D, P, Q)

        signature = split.sign(AUTH_TOKEN)

        assert signature == plain.sign(AUTH_TOKEN)
        assert verify_token(N, E, AUTH_TOKEN, signature)

    def test_a_key_with_primes_does_not_use_the_full_exponent(self, monkeypatch):
        """Two exponentiations of 1024 bits replace one of 2048"""
        key = AdbAuthKey(N, E, D, P, Q)
        # the first signature computes the parameters of the CRT and keeps them
        key.sign(AUTH_TOKEN)
        exponents = []
        real_pow = builtins.pow

        def counting_pow(base, exponent, modulus=None):
            exponents.append(exponent)
            return real_pow(base, exponent, modulus)

        monkeypatch.setattr(builtins, 'pow', counting_pow)
        key.sign(AUTH_TOKEN)

        assert len(exponents) == 2
        assert all(exponent.bit_length() <= 1024 for exponent in exponents)


class TestPublicKey:
    def test_the_blob_has_the_size_of_the_adb_format(self, key):
        """struct RSAPublicKey is 524 bytes for a 2048 bit key"""
        assert len(key.public_key_blob()) == 524

    def test_the_blob_carries_the_key(self, key):
        """The modulus and the exponent come back out of the layout"""
        words, _, n, _, e = decode_public_key(key.public_key_blob())

        assert words * 4 == 256
        assert (n, e) == (N, E)

    def test_the_montgomery_parameters_are_the_ones_of_the_format(self, key):
        """n0inv is -1/n mod 2^32 and rr is R^2 mod n, both little endian"""
        _, n0inv, n, rr, _ = decode_public_key(key.public_key_blob())

        assert (n0inv * (n & 0xFFFFFFFF)) % (1 << 32) == (1 << 32) - 1
        assert rr == pow(2, 2048 * 2, n)

    def test_a_key_that_does_not_fit_the_format_is_refused(self):
        """The format has room for a 2048 bit modulus and no more"""
        oversized = AdbAuthKey((1 << 2049) | 1, E, 1)

        with pytest.raises(AdbAuthKeyInvalid):
            oversized.public_key_blob()

    def test_the_payload_is_the_text_and_a_null(self, key):
        """adbd parses the payload as a C string, the name follows the base64"""
        payload = key.public_key_payload()

        assert payload.endswith(b'\x00')
        text, sep, name = payload[:-1].partition(b' ')
        assert sep == b' '
        assert b'@' in name
        assert base64.b64decode(text) == key.public_key_blob()


class TestHandshake:
    def test_a_key_the_device_knows_is_used_without_a_dialog(self, fs, monkeypatch):
        """The everyday case: one signature, no public key, no user"""
        install_key(fs, monkeypatch, pem('PRIVATE KEY', pkcs8_der()))
        reply = ShellReply(stdout=b'hi\n')

        with FakeAdbd(responder=lambda command, shell_v2: reply) as adbd:
            adbd.auth_required = True
            adbd.auth_key = (N, E)
            client = AdbTCP(port=adbd.port)
            client.timeout = 5
            client.connect()

            try:
                assert client.shell('echo hi').stdout == b'hi\n'
            finally:
                client.disconnect()

        assert adbd.auth_challenges == 1
        assert len(adbd.auth_signatures) == 1
        assert verify_token(N, E, AUTH_TOKEN, adbd.auth_signatures[0])
        assert adbd.auth_public_keys == []
        assert adbd.violations == []

    def test_a_new_key_is_offered_and_accepted(self, fs, monkeypatch):
        """A device that does not know the key asks its user about the public key"""
        install_key(fs, monkeypatch, pem('PRIVATE KEY', pkcs8_der()))

        with FakeAdbd() as adbd:
            adbd.auth_required = True
            client = connect(adbd)

            try:
                assert client.is_connected
            finally:
                client.disconnect()

        # the signature of the unknown key is refused, the public key is offered
        assert adbd.auth_challenges == 2
        assert len(adbd.auth_signatures) == 1
        assert len(adbd.auth_public_keys) == 1
        assert adbd.auth_key == (N, E)
        assert adbd.violations == []

    def test_the_user_may_take_longer_than_a_request(self, fs, monkeypatch):
        """The dialog of a device takes time, a request timeout must not close it"""
        install_key(fs, monkeypatch, pem('PRIVATE KEY', pkcs8_der()))

        with FakeAdbd() as adbd:
            adbd.auth_required = True
            adbd.auth_delay = 0.8
            client = connect(adbd, timeout=0.2)

            try:
                assert client.is_connected
                # the request timeout is back once the handshake is over
                assert client._sock.gettimeout() == 0.2
            finally:
                client.disconnect()

    def test_a_dialog_that_never_ends_is_a_timeout(self, fs, monkeypatch):
        """A device that shows the dialog and never answers fails the connect"""
        install_key(fs, monkeypatch, pem('PRIVATE KEY', pkcs8_der()))
        monkeypatch.setattr(tcp_protocol, 'ADB_AUTH_CONFIRM_TIMEOUT', 0.2)

        with FakeAdbd() as adbd:
            adbd.auth_required = True
            adbd.auth_delay = 0.8
            client = AdbProtocolTCP(port=adbd.port)
            client.timeout = 0.2

            with pytest.raises(AdbConnectionTimeout):
                client.connect()

        assert client._sock is None

    def test_a_key_the_device_refuses_is_an_error(self, fs, monkeypatch):
        """A user who denies the dialog must not look like a network error"""
        install_key(fs, monkeypatch, pem('PRIVATE KEY', pkcs8_der()))

        with FakeAdbd() as adbd:
            adbd.auth_required = True
            adbd.auth_allow_new_key = False
            client = AdbProtocolTCP(port=adbd.port)
            client.timeout = 2

            with pytest.raises(AdbAuthRejected) as e:
                client.connect()

        # the signature, then the public key, then a third challenge
        assert adbd.auth_challenges == 3
        assert len(adbd.auth_public_keys) == 1
        assert f'{adbd.port}' in str(e.value)
        assert client._sock is None

    def test_an_auth_with_another_argument_is_a_protocol_error(self, fs, monkeypatch):
        """A device only ever sends a token, anything else is broken"""
        install_key(fs, monkeypatch, pem('PRIVATE KEY', pkcs8_der()))

        with FakeAdbd() as adbd:
            adbd.auth_required = True
            adbd.auth_arg0 = 0
            client = AdbProtocolTCP(port=adbd.port)
            client.timeout = 2

            with pytest.raises(AdbMessageInvalid) as e:
                client.connect()

        assert 'AUTH' in str(e.value)
        assert adbd.auth_signatures == []
        assert client._sock is None

    def test_a_device_that_does_not_ask_never_reads_the_key(self, monkeypatch):
        """An open adbd costs no file IO, whatever the key of the user is"""

        def no_key():
            raise AssertionError('the key of the user was read')

        monkeypatch.setattr(keygen, 'load_user_key', no_key)
        with FakeAdbd() as adbd:
            client = connect(adbd)

            try:
                assert client.is_connected
            finally:
                client.disconnect()

        assert adbd.violations == []

    def test_the_handshake_ignores_the_version_of_the_old_connection(self, fs, monkeypatch):
        """AUTH carries no version, so the checksum of its token can not be judged

        The client remembers the protocol version of the connection it had, and
        an old device on the same address would make the token of a new one look
        like a broken checksum, although nobody can tell the version before the
        CNXN of the device arrives.
        """
        install_key(fs, monkeypatch, pem('PRIVATE KEY', pkcs8_der()))
        client = AdbProtocolTCP()

        # a device of the first version, its checksum is always the sum
        with FakeAdbd(version=0x01000000) as old:
            client.port = old.port
            client.connect()

            try:
                assert client._protocol_version == 0x01000000
            finally:
                client.disconnect()

        # the same address now answers with a device of 0x01000001, which writes
        # 0 in every checksum and asks for a signature
        with FakeAdbd(version=0x01000001) as adbd:
            adbd.zero_checksum = True
            adbd.auth_required = True
            adbd.auth_key = (N, E)
            client.port = adbd.port
            client.connect()

            try:
                assert client.is_connected
            finally:
                client.disconnect()

        assert adbd.auth_challenges == 1
        assert adbd.violations == []
