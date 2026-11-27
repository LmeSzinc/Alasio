"""
Tests for the AUTH half of the handshake.

The key of this file is built from two primes that were generated once for the
suite: a test never reads the adb key of whoever runs it, and the suite does not
need adb to be installed. What the client produces is checked with RSA itself
and with a decoder of the public key format written here, so a mistake of the
module can not cancel itself out.
"""

import base64
import hashlib
import os
import struct

import pytest

import alasio.adb.protocol.auth as auth
import alasio.adb.protocol.tcp_protocol as tcp_protocol
from alasio.adb.protocol.auth import AdbAuthKey, load_user_key, user_key_path
from alasio.adb.protocol.const import (
    AdbAuthKeyInvalid, AdbAuthKeyMissing, AdbAuthRejected, AdbConnectionTimeout, AdbMessageInvalid
)
from alasio.adb.protocol.tcp import AdbTCP
from alasio.adb.protocol.tcp_protocol import AdbProtocolTCP
from alasio.testing.filesystem import fs  # noqa: F401
from tests.adb.protocol.fake_adbd import AUTH_TOKEN, FakeAdbd, ShellReply

pytestmark = [
    pytest.mark.parallel,
]

# A test key of 2048 bits, the size adb uses. The primes are fixtures of this
# file, not a key anybody ever authorized on a device.
P = int(
    'ee12260a8f29a42d7ffba4da6fde2f027d4c12710b1dbc8a180e6dee3c958207'
    'c4ff3c1c8547ee59e7b4772ac341d95389466338f6e90bdb1b7ee206204a7291'
    '786dbb39e25a7a6502a7da739a39e84a81f38197f476b57eb0af2aac922d118c'
    'a2662a341e4f0fcad5bf434b446ff97358d8c739f1590cc9e6d4c108d39d6f53', 16
)
Q = int(
    'c76ff55cfff0cc3b2a599a5facda92911ef25539f1c8bfa8a63f27e2329f2bb4'
    'd19dc45f2932679806c19a1f23f2aea0cdcebe48409d4bdfc2d328ccd69bed8fb'
    '83b1d309216eb8446b91211fed024ab1c14e415843e208f34fda5c9e07942ee0'
    '781854d565084dc0d63c5beb778cf576584d0082987638ff84b11a2c7235597', 16
)
E = 65537
N = P * Q
D = pow(E, -1, (P - 1) * (Q - 1))

# DigestInfo of SHA1 in PKCS#1 v1.5, the padding adbd verifies
SHA1_DIGEST_INFO = bytes.fromhex('3021300906052b0e03021a05000414')
# rsaEncryption, the algorithm of the key inside a PKCS#8 block
RSA_ALGORITHM = bytes.fromhex('300d06092a864886f70d0101010500')

KEY_PATH = '/home/tester/.android/adbkey'


def der_length(length):
    """
    Encode the length of a DER value.

    Args:
        length (int): Number of bytes of the value

    Returns:
        bytes: Encoded length
    """
    if length < 0x80:
        return bytes((length,))
    raw = length.to_bytes((length.bit_length() + 7) // 8, 'big')
    return bytes((0x80 | len(raw),)) + raw


def der(tag, body):
    """
    Encode one DER value.

    Args:
        tag (int): ASN.1 tag
        body (bytes): Content of the value

    Returns:
        bytes: Tag, length and content
    """
    return bytes((tag,)) + der_length(len(body)) + body


def der_integer(value):
    """
    Encode a DER integer.

    Args:
        value (int): Value to encode

    Returns:
        bytes: DER INTEGER
    """
    raw = value.to_bytes((value.bit_length() + 7) // 8 or 1, 'big')
    if raw[0] & 0x80:
        raw = b'\x00' + raw
    return der(0x02, raw)


def pkcs1_der():
    """
    PKCS#1 RSAPrivateKey of the test key, built here and not by the module.

    Returns:
        bytes: DER of the key
    """
    numbers = (0, N, E, D, P, Q, D % (P - 1), D % (Q - 1), pow(Q, -1, P))
    return der(0x30, b''.join(der_integer(value) for value in numbers))


def pkcs8_der():
    """
    PKCS#8 PrivateKeyInfo that wraps the test key.

    Returns:
        bytes: DER of the key
    """
    return der(0x30, der_integer(0) + RSA_ALGORITHM + der(0x04, pkcs1_der()))


def pem(label, body):
    """
    Wrap DER in a PEM block.

    Args:
        label (str): Label of the block, like 'PRIVATE KEY'
        body (bytes): DER of the block

    Returns:
        str: PEM text
    """
    text = base64.b64encode(body).decode('ascii')
    lines = [text[i:i + 64] for i in range(0, len(text), 64)]
    return '\n'.join([f'-----BEGIN {label}-----', *lines, f'-----END {label}-----']) + '\n'


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
    monkeypatch.setattr(auth, 'user_key_path', lambda: KEY_PATH)
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


class TestUserKeyPath:
    def test_the_path_is_the_one_adb_reads(self):
        """adb keeps the key in <home>/.android/adbkey of the user"""
        assert user_key_path() == os.path.join(os.path.expanduser('~'), '.android', 'adbkey')


class TestLoadUserKey:
    def test_a_pkcs8_key(self, fs, monkeypatch):
        """The block adb itself writes is PKCS#8"""
        install_key(fs, monkeypatch, pem('PRIVATE KEY', pkcs8_der()))

        key = load_user_key()

        assert (key.n, key.e, key.d) == (N, E, D)

    def test_a_pkcs1_key(self, fs, monkeypatch):
        """A key of an older tool is the bare RSA numbers"""
        install_key(fs, monkeypatch, pem('RSA PRIVATE KEY', pkcs1_der()))

        key = load_user_key()

        assert (key.n, key.e, key.d) == (N, E, D)

    def test_a_key_file_of_bytes(self, fs, monkeypatch):
        """The PEM is read as bytes as well as text"""
        install_key(fs, monkeypatch, pem('PRIVATE KEY', pkcs8_der()).encode())

        key = load_user_key()

        assert key.n == N

    def test_a_missing_key(self, fs, monkeypatch):
        """A user without a key gets an error that names the file"""
        monkeypatch.setattr(auth, 'user_key_path', lambda: KEY_PATH)

        with pytest.raises(AdbAuthKeyMissing) as e:
            load_user_key()

        assert KEY_PATH in str(e.value)

    @pytest.mark.parametrize('contents', [
        '',
        'not a key at all',
        '-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n',
        pem('PRIVATE KEY', b'\x30\x82\xff\xff\x02\x01\x00'),
    ])
    def test_a_file_that_is_not_a_key(self, fs, monkeypatch, contents):
        """A broken file is an error, never a reason to make a new key"""
        install_key(fs, monkeypatch, contents)

        with pytest.raises(AdbAuthKeyInvalid):
            load_user_key()

    def test_an_encrypted_key(self, fs, monkeypatch):
        """A key that needs a passphrase is refused, as adb refuses it"""
        install_key(fs, monkeypatch, pem('ENCRYPTED PRIVATE KEY', b'\x30\x00'))

        with pytest.raises(AdbAuthKeyInvalid) as e:
            load_user_key()

        assert 'ENCRYPTED PRIVATE KEY' in str(e.value)

    def test_a_key_of_another_algorithm(self, fs, monkeypatch):
        """An EC key is not an RSA key, whatever its PEM label says"""
        # PKCS#8 of an EC key: version, algorithm, an octet string holding a
        # sequence that is not the RSA numbers
        ec_algorithm = bytes.fromhex('300706052b8104000a')
        body = der(0x30, der_integer(0) + ec_algorithm + der(0x04, der(0x30, der_integer(1))))
        install_key(fs, monkeypatch, pem('PRIVATE KEY', body))

        with pytest.raises(AdbAuthKeyInvalid):
            load_user_key()


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

        monkeypatch.setattr(auth, 'load_user_key', no_key)
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
