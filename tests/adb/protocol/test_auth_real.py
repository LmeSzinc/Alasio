"""
AUTH against the adb binary of the machine, run on CI.

The unit tests of this folder replace the runner of the keygen, so no test there
ever sees the bytes adb writes: the format of a real key, the name of the public
key and the padding of a real signature are all modelled by hand. The runners of
github actions ship the android platform-tools, which is the one place adb is
present without anybody installing it, so the real binary is exercised here and
nowhere else.

Every test works in a folder of the system temp and points user_key_path() at
it: the key of the user who runs the suite is never read and never written.

The file is skipped outside CI, like the other tests of the real disk: a
developer machine may have no adb at all, and the fake keygen of test_keygen.py
is the coverage there. On CI a machine without adb fails the test that needs it
with the message of AdbAuthKeyMissing, instead of turning the run green.
"""

import base64
import hashlib
import os
import shutil
import tempfile

import pytest

from alasio.adb.protocol import keygen
from alasio.adb.protocol.auth import SHA1_DIGEST_INFO
from alasio.adb.protocol.const import AUTH_TOKEN_SIZE, AdbAuthKeyInvalid
from alasio.adb.protocol.keygen import load_user_key
from alasio.ext import env
from alasio.ext.concurrent.cmd import run_cmd

pytestmark = [
    pytest.mark.skipif(
        not env.CI, reason='the adb binary of the CI runners is the real one, the local runs use the fake keygen'),
    pytest.mark.parallel,
]


@pytest.fixture
def folder(monkeypatch):
    """
    A folder of the real disk that stands in for the home of the user.

    Args:
        monkeypatch: pytest fixture

    Yields:
        str: Absolute path of an empty folder
    """
    path = tempfile.mkdtemp(prefix='alasio_adbkey_')
    monkeypatch.setattr(keygen, 'user_key_path', lambda: os.path.join(path, 'adbkey'))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def key_path():
    """
    Path the module under test reads the key from.

    Returns:
        str: Path of the private key
    """
    return keygen.user_key_path()


def run_adb(*args):
    """
    Run the adb binary of this machine, which is the one the module found.

    Args:
        *args (str): Arguments of adb

    Returns:
        CmdlineResultStr: Output of the binary, whatever its exit code
    """
    # a test asks adb a question a failure answers as well, e.g. the usage of a
    # command, so the exit code is data here and not an error
    return run_cmd([keygen.adb_path(), *args], check=False)


def decode_public_key(blob):
    """
    Decode the public key blob with the layout of struct RSAPublicKey.

    Args:
        blob (bytes): Encoded key

    Returns:
        tuple: (n, e)
    """
    n = int.from_bytes(blob[8:264], 'little')
    e = int.from_bytes(blob[520:524], 'little')
    return n, e


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


class TestTheAdbOfTheMachine:
    def test_the_binary_is_a_working_adb(self):
        """The module locates a program that answers as adb"""
        result = run_adb('version')

        assert result.returncode == 0
        assert 'Android Debug Bridge' in result.stdout

    def test_the_keygen_is_supported(self):
        """A platform-tools too old to know keygen would fail the generation"""
        result = run_adb('keygen')

        # adb answers a bare keygen with a usage error; a binary that predates the
        # command says it does not know it, which is the case this guards against
        output = result.stdout + result.stderr
        assert 'unknown command' not in output
        assert 'keygen' in output


class TestTheKeyOfTheMachine:
    def test_the_binary_makes_a_key_the_module_reads(self, folder):
        """The whole point of the generation: adb writes, the module reads"""
        key = load_user_key()

        assert key.n.bit_length() == 2048
        assert key.e == 65537
        with open(key_path(), 'rb') as f:
            head = f.readline()
        assert head.strip() == b'-----BEGIN PRIVATE KEY-----'
        # adb writes the pair, the .pub is the base64 blob and the name of the
        # machine, a format of its own rather than the ssh-rsa line of openssh
        with open(f'{key_path()}.pub') as f:
            text = f.read()
        blob, sep, name = text.strip().partition(' ')
        assert sep == ' '
        assert base64.b64decode(blob)
        assert '@' in name

    def test_the_second_call_reads_the_key_instead_of_making_one(self, folder):
        """The key is the identity of the machine, it is made once"""
        first = load_user_key()
        with open(key_path(), 'rb') as f:
            content = f.read()

        second = load_user_key()

        assert (second.n, second.e, second.d) == (first.n, first.e, first.d)
        with open(key_path(), 'rb') as f:
            assert f.read() == content

    def test_a_key_that_adb_made_by_itself_is_read(self, folder):
        """A machine where adb ran before the suite, the usual case"""
        result = run_adb('keygen', key_path())
        assert result.returncode == 0, result.stderr

        key = load_user_key()

        assert key.n.bit_length() == 2048
        # the two exponents are a pair: what one locks the other opens
        assert pow(pow(42, key.e, key.n), key.d, key.n) == 42

    def test_the_directory_of_the_key_is_created(self, folder, monkeypatch):
        """adb keygen does not create a directory, ~/.android is the module's job"""
        target = os.path.join(folder, 'new', 'home', '.android', 'adbkey')
        monkeypatch.setattr(keygen, 'user_key_path', lambda: target)

        key = load_user_key()

        assert os.path.isfile(target)
        assert os.path.isfile(f'{target}.pub')
        assert key.n.bit_length() == 2048

    def test_a_broken_key_is_never_replaced(self, folder):
        """The file is the identity of the machine, a broken one is an error"""
        with open(key_path(), 'wb') as f:
            f.write(b'-----BEGIN PRIVATE KEY-----\nnot base64\n-----END PRIVATE KEY-----\n')

        with pytest.raises(AdbAuthKeyInvalid):
            load_user_key()

        with open(key_path(), 'rb') as f:
            assert f.read().startswith(b'-----BEGIN PRIVATE KEY-----\nnot base64')


class TestTheContractWithAdb:
    def test_the_public_key_agrees_with_adb_pubkey(self, folder):
        """`adb pubkey` is adb itself turning the private key into the text"""
        load_user_key()

        result = run_adb('pubkey', key_path())

        assert result.returncode == 0, result.stderr
        assert load_user_key().public_key_text() == result.stdout.strip()

    def test_the_file_of_the_public_key_holds_the_blob(self, folder):
        """The .pub adb writes next to the private key is the same key"""
        key = load_user_key()

        with open(f'{key_path()}.pub') as f:
            text = f.read().strip()
        encoded = base64.b64decode(text.partition(' ')[0])

        assert encoded == key.public_key_blob()

    def test_the_signature_is_one_adbd_accepts(self, folder):
        """The numbers of the real public key verify the signature of a token"""
        key = load_user_key()

        with open(f'{key_path()}.pub') as f:
            text = f.read().strip()
        n, e = decode_public_key(base64.b64decode(text.partition(' ')[0]))
        token = bytes(range(AUTH_TOKEN_SIZE))

        signature = key.sign(token)

        assert (n, e) == (key.n, key.e)
        assert verify_token(n, e, token, signature)
