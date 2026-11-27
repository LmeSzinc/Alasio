"""
The key file of a real adb, checked as an artifact.

Every other test of this folder builds its key from numbers written in the test,
so the shape of the file adb really writes is only ever modelled there. This
module reads a key that adb 34.0.3 generated once and that is committed next to
the suite: the numbers of the private key are exactly what `adb keygen` wrote,
with the line endings normalized to LF because git does that to a text file
anyway. `adbkey.pub` is the same output with the name of the machine replaced by
`tester@testhost`, because the login name and the host name of a developer have
no place in a repository. The name is not part of the key.

The key is a throwaway. It was never authorized on a device, the private half is
committed on purpose as a fixture, and nothing outside this file signs with it.

The pair is read and parsed once per module (`pair`), not once per test: the
files never change while the suite runs, and every test only inspects them.
"""

import base64
import hashlib
import os

import pytest

from alasio.adb.protocol import auth, keygen
from alasio.adb.protocol.auth import SHA1_DIGEST_INFO, AdbAuthKey
from alasio.adb.protocol.const import AUTH_TOKEN_SIZE, AdbAuthKeyInvalid
from alasio.adb.protocol.keygen import load_user_key
from alasio.testing.filesystem import fs  # noqa: F401

pytestmark = [
    pytest.mark.parallel,
]

FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'adbkey')
KEY_FILE = os.path.join(FOLDER, 'adbkey')
PUB_FILE = os.path.join(FOLDER, 'adbkey.pub')
# the only part of the pair that is not the output of adb
DESENSITIZED_NAME = 'tester@testhost'


class KeyPair:
    """
    The committed pair, read from the disk and parsed once.

    Attributes:
        pem (bytes): Content of the private key file, LF line endings
        text (str): Content of the public key file, the trailing newline removed
        key (AdbAuthKey): The private half, parsed by the module under test
        blob (bytes): The 524 bytes the device receives
        name (str): The `user@host` label adb appends to the text
    """

    def __init__(self, pem, text):
        self.pem = pem
        self.text = text
        self.key = AdbAuthKey.from_pem(pem)
        self.blob = base64.b64decode(text.partition(' ')[0])
        self.name = text.partition(' ')[2]


@pytest.fixture(scope='module')
def pair():
    """
    The key pair of the folder, read once for the whole module.

    Returns:
        KeyPair: The pair of the fixture folder
    """
    with open(KEY_FILE, 'rb') as f:
        pem = f.read()
    with open(PUB_FILE, encoding='utf-8') as f:
        text = f.read().strip()
    return KeyPair(pem, text)


def decode_public_key(blob):
    """
    Decode the blob with the layout of struct RSAPublicKey.

    Args:
        blob (bytes): Encoded key of 524 bytes

    Returns:
        tuple: (n0inv, n, rr, e)
    """
    words, n0inv = int.from_bytes(blob[0:4], 'little'), int.from_bytes(blob[4:8], 'little')
    assert words == 64
    n = int.from_bytes(blob[8:264], 'little')
    rr = int.from_bytes(blob[264:520], 'little')
    e = int.from_bytes(blob[520:524], 'little')
    return n0inv, n, rr, e


def verify_token(n, e, token, signature):
    """
    Verify a signature of an AUTH token with plain RSA.

    Args:
        n (int): Modulus
        e (int): Public exponent
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


class TestTheFileAdbWrites:
    def test_the_private_key_is_a_pkcs8_block(self, pair):
        """adb writes an unencrypted PKCS#8 PEM, one line per 64 characters"""
        pem = pair.pem.rstrip(b'\r\n')

        assert pem.startswith(b'-----BEGIN PRIVATE KEY-----')
        assert pem.endswith(b'-----END PRIVATE KEY-----')
        # an encrypted key carries a cipher and a salt above the base64, and a
        # passphrase would make the file useless to a backend
        assert b'Proc-Type' not in pem
        assert b'DEK-Info' not in pem
        assert b'ENCRYPTED' not in pem
        body = pem.partition(b'\n')[2].rpartition(b'-----END')[0]
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        # every line but the last one is full, the last one holds the remainder
        assert all(len(line) == 64 for line in lines[:-1])
        assert 0 < len(lines[-1]) <= 64

    def test_both_line_endings_of_a_text_mode_write(self, pair):
        """adb opens the file in text mode, so Windows writes CRLF and POSIX LF"""
        # the fixture is stored with LF because git normalizes it, and a user on
        # Windows has the same bytes with CRLF: the reader has to take both
        crlf = AdbAuthKey.from_pem(pair.pem.replace(b'\n', b'\r\n'))
        lf = AdbAuthKey.from_pem(pair.pem)

        assert (lf.n, lf.e, lf.d) == (crlf.n, crlf.e, crlf.d)

    def test_the_public_key_is_the_text_of_adb(self, pair):
        """`<base64 blob> user@host`, not the ssh-rsa line of openssh"""
        blob, sep, name = pair.text.partition(' ')

        assert sep == ' '
        assert 'ssh-rsa' not in pair.text
        assert len(base64.b64decode(blob)) == 524
        assert name == DESENSITIZED_NAME

    def test_the_name_is_the_only_part_that_was_replaced(self, pair):
        """The blob of the pair and the numbers of the private key are adb's"""
        # a replaced name would leave the blob intact, a replaced key would not
        assert pair.blob == pair.key.public_key_blob()


class TestTheKeyOfThePair:
    def test_the_numbers_are_the_ones_of_a_2048_bit_key(self, pair):
        """RSA-2048 with e=65537, the only key adb generates"""
        assert pair.key.n.bit_length() == 2048
        assert pair.key.e == 65537
        assert 0 < pair.key.d < pair.key.n

    def test_the_primes_rebuild_the_modulus(self, pair):
        """The fixture carries usable primes, the CRT has something to split"""
        assert pair.key.p != pair.key.q
        assert pair.key.p * pair.key.q == pair.key.n
        assert pow(pair.key.q, -1, pair.key.p) > 0

    def test_the_module_reads_the_file_of_the_user(self, monkeypatch):
        """The read path of the handshake is the one under test, not from_pem"""
        monkeypatch.setattr(keygen, 'user_key_path', lambda: KEY_FILE)

        key = load_user_key()

        assert key.n.bit_length() == 2048
        assert key.p * key.q == key.n

    def test_a_broken_copy_of_it_is_refused(self, fs, monkeypatch, pair):
        """The fixture is a key like any other: a truncated one is not"""
        # the pair of the module scope is read before the fake filesystem of the
        # function scope is installed, so the real bytes are the ones truncated
        fs.create_file(KEY_FILE, contents=pair.pem[:400])
        monkeypatch.setattr(keygen, 'user_key_path', lambda: KEY_FILE)

        with pytest.raises(AdbAuthKeyInvalid):
            load_user_key()


class TestThePublicKeyOfThePair:
    def test_the_blob_is_the_one_adb_wrote(self, pair):
        """The public half of the file is rebuilt from the private half"""
        assert pair.key.public_key_blob() == pair.blob

    def test_the_montgomery_parameters_of_the_blob(self, pair):
        """n0inv is -1/n mod 2^32 and rr is R^2 mod n, the format adbd decodes"""
        n0inv, n, rr, e = decode_public_key(pair.blob)

        assert (e, n.bit_length()) == (65537, 2048)
        assert (n0inv * (n & 0xFFFFFFFF)) % (1 << 32) == (1 << 32) - 1
        assert rr == pow(2, 2048 * 2, n)

    def test_the_text_is_the_one_of_the_module(self, monkeypatch, pair):
        """The name is the only thing the machine contributes to the text"""
        monkeypatch.setattr(auth, '_user_name', lambda: 'tester')
        monkeypatch.setattr(auth, '_host_name', lambda: 'testhost')

        assert pair.key.public_key_text() == pair.text
        assert pair.key.public_key_payload() == pair.text.encode() + b'\x00'


class TestTheSignatureOfThePair:
    def test_the_signature_is_the_one_adbd_verifies(self, pair):
        """The device checks the signature with the n and e of the .pub file"""
        _, n, _, e = decode_public_key(pair.blob)
        token = bytes(range(AUTH_TOKEN_SIZE))

        signature = pair.key.sign(token)

        assert verify_token(n, e, token, signature)

    def test_the_split_of_the_primes_gives_the_same_bytes(self, pair):
        """The CRT is a faster route to the signature of the plain exponent"""
        key = pair.key
        token = bytes(range(AUTH_TOKEN_SIZE))
        size = (key.n.bit_length() + 7) // 8
        digest = SHA1_DIGEST_INFO + hashlib.sha1(token).digest()
        block = b'\x00\x01' + b'\xff' * (size - len(digest) - 3) + b'\x00' + digest
        plain = pow(int.from_bytes(block, 'big'), key.d, key.n).to_bytes(size, 'big')

        assert key.sign(token) == plain

    def test_a_second_signature_of_the_token_is_the_same(self, pair):
        """PKCS#1 v1.5 has no salt, a device may drop a connection and retry"""
        token = bytes(range(AUTH_TOKEN_SIZE))
        first = pair.key.sign(token)

        # the parameters of the CRT are kept between two signatures
        assert pair.key.sign(token) == first
