"""
Tests for the adb key file of the user: where it is, how it is read, how it is made.

A test never reads the adb key of whoever runs it and never runs the adb binary:
the file is put in the in-memory filesystem, and the binary that generates a
missing key is replaced by a writer of the same file. The real binary is run on
the CI runners, see `test_auth_real.py`. The signature of a token and the
encoding of the public key are the subject of `test_auth.py`.
"""

import os
import shutil
import sys
import threading
from types import ModuleType

import pytest

import alasio.adb.protocol.keygen as keygen
from alasio.adb.protocol.const import AdbAuthKeyInvalid, AdbAuthKeyMissing
from alasio.adb.protocol.keygen import load_user_key, user_key_path
from alasio.adb.protocol.tcp_protocol import AdbProtocolTCP
from alasio.ext.concurrent.cmd import CmdlineError, CmdlineResultStr
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.adb.protocol.fake_adbd import FakeAdbd
from tests.adb.protocol.keydata import D, E, N, P, Q, der, der_integer, pem, pkcs1_der, pkcs8_der

pytestmark = [
    pytest.mark.parallel,
]

KEY_PATH = '/home/tester/.android/adbkey'

# A second key, small enough to write by hand: it stands for the key another
# worker generated when this one was still writing its own
OTHER_N = 61 * 53
OTHER_PEM = pem('PRIVATE KEY', pkcs8_der(OTHER_N, 17, 2753, 61, 53))


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


class FakeKeygen:
    """
    Stands in for `run_cmd` running `adb keygen`.

    The command line is recorded, so a test can assert the path of the binary,
    the temporary name and the timeout. The files are written with `open()` and
    not with a helper of the fake filesystem: adb uses a plain fopen, it creates
    no directory, and a missing parent has to fail the same way here.

    Attributes:
        contents (str): PEM written at the target, empty to write nothing
        returncode (int): Exit code of the binary
        stderr (str): Error output of the binary
        error (Exception): Exception raised instead of writing anything, a
            CmdlineError when run_cmd never reaches the binary
        calls (list): (command line, keyword arguments) the module ran, in order
    """

    def __init__(self, contents=None, returncode=0, stderr='', error=None):
        """
        Args:
            contents (str): PEM written at the target. Defaults to None, which
                writes the key of this test file
            returncode (int): Exit code of the binary. Defaults to 0.
            stderr (str): Error output of the binary. Defaults to ''.
            error (Exception): Exception raised instead of writing anything.
                Defaults to None.
        """
        self.contents = pem('PRIVATE KEY', pkcs8_der()) if contents is None else contents
        self.returncode = returncode
        self.stderr = stderr
        self.error = error
        self.calls = []

    def __call__(self, cmd, **kwargs):
        """
        Write the key the way keygen does, and report the exit code.

        Args:
            cmd (list): Command line of the binary
            **kwargs: Arguments of run_cmd, recorded with the command

        Returns:
            CmdlineResultStr: Result of the binary

        Raises:
            Exception: The injected error, when there is one
        """
        self.calls.append((cmd, kwargs))
        if self.error is not None:
            raise self.error
        target = cmd[2]
        if self.contents:
            # a killed keygen may leave a partial file behind, which is what the
            # module has to clean up
            with open(target, 'w') as f:
                f.write(self.contents)
            with open(f'{target}.pub', 'w') as f:
                f.write('ssh-rsa a-public-key tester@host')
        return CmdlineResultStr(cmd=cmd, returncode=self.returncode, stdout='', stderr=self.stderr)

    def install(self, monkeypatch):
        """
        Replace the binary and the runner of the module.

        Args:
            monkeypatch: pytest fixture

        Returns:
            FakeKeygen: This fake
        """
        monkeypatch.setattr(keygen, 'adb_path', lambda: 'adb')
        monkeypatch.setattr(keygen, 'run_cmd', self)
        return self


@pytest.fixture
def fake_keygen(fs, monkeypatch):
    """A fake adb binary, with the key path of the module redirected"""
    monkeypatch.setattr(keygen, 'user_key_path', lambda: KEY_PATH)
    return FakeKeygen().install(monkeypatch)


class TestUserKeyPath:
    def test_the_path_is_the_one_adb_reads(self):
        """adb keeps the key in <home>/.android/adbkey of the user"""
        assert user_key_path() == os.path.join(os.path.expanduser('~'), '.android', 'adbkey')


class TestReadKey:
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

    def test_a_missing_key_without_adb(self, fs, monkeypatch):
        """A machine without a key and without adb can not authenticate"""

        def missing():
            raise AdbAuthKeyMissing('No adb binary on this machine')

        monkeypatch.setattr(keygen, 'user_key_path', lambda: KEY_PATH)
        monkeypatch.setattr(keygen, 'adb_path', missing)

        with pytest.raises(AdbAuthKeyMissing) as e:
            load_user_key()

        assert 'No adb binary on this machine' in str(e.value)
        assert not os.path.exists(KEY_PATH)

    @pytest.mark.parametrize('contents', [
        '',
        'not a key at all',
        '-----BEGIN PRIVATE KEY-----[redacted]-----END PRIVATE KEY-----\n',
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

    def test_the_key_carries_its_primes(self, fs, monkeypatch):
        """The primes are read with the rest, a signature splits on them"""
        install_key(fs, monkeypatch, pem('PRIVATE KEY', pkcs8_der()))

        key = load_user_key()

        assert (key.p, key.q) == (P, Q)
        assert key.p * key.q == key.n

    def test_a_modulus_the_primes_do_not_rebuild(self, fs, monkeypatch):
        """A file that disagrees with itself would sign tokens a device rejects"""
        install_key(fs, monkeypatch, pem('PRIVATE KEY', pkcs8_der(N + 2, E, D, P, Q)))

        with pytest.raises(AdbAuthKeyInvalid) as e:
            load_user_key()

        assert 'do not multiply to its modulus' in str(e.value)

    def test_two_primes_of_the_same_value(self, fs, monkeypatch):
        """A squared prime has no inverse to split a signature with"""
        numbers = (0, P * P, E, D, P, P, D % (P - 1), D % (P - 1), 1)
        body = der(0x30, b''.join(der_integer(value) for value in numbers))
        install_key(fs, monkeypatch, pem('PRIVATE KEY', body))

        with pytest.raises(AdbAuthKeyInvalid) as e:
            load_user_key()

        assert 'do not multiply to its modulus' in str(e.value)


class TestAdbPath:
    def test_adb_on_the_path(self, monkeypatch):
        """platform-tools on PATH is the common case, adbutils is not touched"""
        module = ModuleType('adbutils')

        def finder():
            raise AssertionError('adbutils was asked although adb is on PATH')

        module.adb_path = finder
        monkeypatch.setitem(sys.modules, 'adbutils', module)
        monkeypatch.setattr(shutil, 'which', lambda name: r'C:\platform-tools\adb.exe')

        assert keygen.adb_path() == r'C:\platform-tools\adb.exe'

    @pytest.mark.parametrize('variable', ['ANDROID_HOME', 'ANDROID_SDK_ROOT'])
    def test_the_adb_of_the_android_sdk(self, fs, monkeypatch, variable):
        """The sdk of the CI runners holds adb without putting it on PATH"""
        sdk = '/opt/android' if variable == 'ANDROID_HOME' else '/opt/alternate'
        name = 'adb.exe' if sys.platform == 'win32' else 'adb'
        binary = os.path.join(sdk, 'platform-tools', name)
        fs.create_file(binary, contents='')
        monkeypatch.setitem(sys.modules, 'adbutils', None)
        monkeypatch.setattr(shutil, 'which', lambda which: None)
        monkeypatch.delenv('ANDROID_HOME', raising=False)
        monkeypatch.delenv('ANDROID_SDK_ROOT', raising=False)
        monkeypatch.setenv(variable, sdk)

        assert keygen.adb_path() == binary

    def test_an_sdk_without_platform_tools(self, fs, monkeypatch):
        """A variable that names a folder with no adb is not a binary"""
        monkeypatch.setitem(sys.modules, 'adbutils', None)
        monkeypatch.setattr(shutil, 'which', lambda name: None)
        monkeypatch.setenv('ANDROID_HOME', '/opt/empty')
        monkeypatch.delenv('ANDROID_SDK_ROOT', raising=False)

        with pytest.raises(AdbAuthKeyMissing):
            keygen.adb_path()

    def test_the_adb_of_adbutils(self, monkeypatch):
        """A machine without platform-tools uses the copy inside the wheel"""
        module = ModuleType('adbutils')
        module.adb_path = lambda: '/opt/wheel/adbutils/binaries/adb'
        monkeypatch.setitem(sys.modules, 'adbutils', module)
        monkeypatch.setattr(shutil, 'which', lambda name: None)
        monkeypatch.delenv('ANDROID_HOME', raising=False)
        monkeypatch.delenv('ANDROID_SDK_ROOT', raising=False)

        assert keygen.adb_path() == '/opt/wheel/adbutils/binaries/adb'

    def test_the_finder_of_an_older_adbutils(self, monkeypatch):
        """The name of the finder moved to adbutils._utils"""
        package = ModuleType('adbutils')
        utils = ModuleType('adbutils._utils')
        utils.adb_path = lambda: '/opt/wheel/adbutils/binaries/adb'
        package._utils = utils
        monkeypatch.setitem(sys.modules, 'adbutils', package)
        monkeypatch.setitem(sys.modules, 'adbutils._utils', utils)
        monkeypatch.setattr(shutil, 'which', lambda name: None)
        monkeypatch.delenv('ANDROID_HOME', raising=False)
        monkeypatch.delenv('ANDROID_SDK_ROOT', raising=False)

        assert keygen.adb_path() == '/opt/wheel/adbutils/binaries/adb'

    def test_adbutils_without_a_binary(self, monkeypatch):
        """A wheel that lost its binary is an error, not a path of None"""
        module = ModuleType('adbutils')
        module.adb_path = lambda: None
        monkeypatch.setitem(sys.modules, 'adbutils', module)
        monkeypatch.setattr(shutil, 'which', lambda name: None)
        monkeypatch.delenv('ANDROID_HOME', raising=False)
        monkeypatch.delenv('ANDROID_SDK_ROOT', raising=False)

        with pytest.raises(AdbAuthKeyMissing) as e:
            keygen.adb_path()

        assert 'reinstall adbutils' in str(e.value)

    def test_no_adb_at_all(self, monkeypatch):
        """Neither PATH nor the sdk nor adbutils has a binary for the key"""
        monkeypatch.setitem(sys.modules, 'adbutils', None)
        monkeypatch.setattr(shutil, 'which', lambda name: None)
        monkeypatch.delenv('ANDROID_HOME', raising=False)
        monkeypatch.delenv('ANDROID_SDK_ROOT', raising=False)

        with pytest.raises(AdbAuthKeyMissing) as e:
            keygen.adb_path()

        assert 'platform-tools' in str(e.value)


class TestGenerateUserKey:
    def test_a_missing_key_is_generated(self, fake_keygen):
        """The first contact of a fresh machine creates the key with adb"""
        with logger.mock_capture_writer() as capture:
            key = load_user_key()

        assert (key.n, key.e, key.d) == (N, E, D)
        # adb keygen creates no directory, so the module has to
        assert os.path.isdir('/home/tester/.android')
        assert os.path.isfile(KEY_PATH)
        assert os.path.isfile(f'{KEY_PATH}.pub')
        assert len(fake_keygen.calls) == 1
        command, kwargs = fake_keygen.calls[0]
        assert command[:2] == ['adb', 'keygen']
        assert command[2].startswith(f'{KEY_PATH}.')
        assert command[2].endswith('.tmp')
        assert not os.path.exists(command[2])
        assert not os.path.exists(f'{command[2]}.pub')
        assert kwargs['timeout'] == keygen.ADB_KEYGEN_TIMEOUT
        # a failing keygen reports itself, the exit code is read and given a message
        assert kwargs['check'] is False
        # the console flag is only a thing on Windows
        assert ('creationflags' in kwargs) == (sys.platform == 'win32')
        assert capture.concat() == (
            f'WARNING | Generated a new adb key at {KEY_PATH}, allow it on the device when it asks\n'
        )

    def test_the_key_is_generated_only_once(self, fake_keygen):
        """A second call reads the file adb wrote"""
        load_user_key()
        load_user_key()

        assert len(fake_keygen.calls) == 1

    def test_the_key_of_the_user_is_never_replaced(self, fs, fake_keygen):
        """A machine that already has a key never runs keygen"""
        contents = pem('PRIVATE KEY', pkcs8_der())
        fs.create_file(KEY_PATH, contents=contents)

        key = load_user_key()

        assert key.n == N
        assert fake_keygen.calls == []
        with open(KEY_PATH) as f:
            assert f.read() == contents

    def test_a_broken_key_is_never_replaced(self, fs, fake_keygen):
        """A file that does not parse is an error: a new key loses every device"""
        fs.create_file(KEY_PATH, contents='not a key')

        with pytest.raises(AdbAuthKeyInvalid):
            load_user_key()

        assert fake_keygen.calls == []
        with open(KEY_PATH) as f:
            assert f.read() == 'not a key'

    def test_a_directory_that_can_not_be_created(self, fs, fake_keygen):
        """A read only home is a machine that can not authenticate"""
        # a file where the directory belongs fails makedirs the same way
        fs.create_file('/home/tester/.android')

        with pytest.raises(AdbAuthKeyMissing) as e:
            load_user_key()

        assert '/home/tester/.android' in str(e.value)
        assert fake_keygen.calls == []

    def test_a_keygen_that_fails(self, fake_keygen):
        """A non zero exit is an error, and leaves neither a key nor a tmp"""
        fake_keygen.returncode = 1
        fake_keygen.stderr = 'error: could not generate key'

        with pytest.raises(AdbAuthKeyMissing) as e:
            load_user_key()

        assert 'exited with 1' in str(e.value)
        assert 'could not generate key' in str(e.value)
        assert not os.path.exists(KEY_PATH)
        assert not os.path.exists(fake_keygen.calls[0][0][2])

    def test_a_keygen_that_times_out(self, fake_keygen):
        """A binary that hangs does not hold the handshake forever"""
        fake_keygen.error = CmdlineError(
            msg=f"Command timed out after {keygen.ADB_KEYGEN_TIMEOUT}s: ['adb', 'keygen', '{KEY_PATH}']",
            cmd=['adb', 'keygen', KEY_PATH],
        )

        with pytest.raises(AdbAuthKeyMissing) as e:
            load_user_key()

        assert 'timed out after 60s' in str(e.value)

    def test_a_binary_that_is_gone(self, fake_keygen):
        """A path that no longer holds a program is reported as such"""
        fake_keygen.error = CmdlineError(msg='Command not found: adb', cmd=['adb'])

        with pytest.raises(AdbAuthKeyMissing) as e:
            load_user_key()

        assert 'Command not found: adb' in str(e.value)
        assert 'Can not run adb keygen' in str(e.value)

    def test_a_keygen_that_writes_nothing(self, fake_keygen):
        """An exit code of 0 is not enough, the key has to be there"""
        fake_keygen.contents = ''

        with pytest.raises(AdbAuthKeyMissing) as e:
            load_user_key()

        assert 'wrote no key' in str(e.value)
        assert not os.path.exists(KEY_PATH)

    def test_a_keygen_without_a_public_key_file(self, fake_keygen, monkeypatch):
        """The .pub is a convenience, a key without it still authenticates"""

        def run(cmd, **kwargs):
            with open(cmd[2], 'w') as f:
                f.write(pem('PRIVATE KEY', pkcs8_der()))
            return CmdlineResultStr(cmd=cmd, returncode=0, stdout='', stderr='')

        monkeypatch.setattr(keygen, 'run_cmd', run)

        key = load_user_key()

        assert key.n == N
        assert os.path.isfile(KEY_PATH)
        assert not os.path.exists(f'{KEY_PATH}.pub')

    def test_the_key_is_read_back_from_the_file(self, fake_keygen, monkeypatch):
        """Two workers may generate at once, the file is what counts"""

        def replace(tmp, target):
            # another worker replaced the file after this one finished its keygen
            with open(target, 'w') as f:
                f.write(OTHER_PEM)

        monkeypatch.setattr(keygen, 'atomic_replace_tmp', replace)

        key = load_user_key()

        assert key.n == OTHER_N

    def test_two_workers_generate_at_the_same_time(self, fake_keygen, monkeypatch):
        """The temporary names of two threads never collide"""
        barrier = threading.Barrier(2)
        lock = threading.Lock()

        def run(cmd, **kwargs):
            with lock:
                fake_keygen.calls.append((cmd, kwargs))
            barrier.wait(timeout=10)
            with open(cmd[2], 'w') as f:
                f.write(pem('PRIVATE KEY', pkcs8_der()))
            return CmdlineResultStr(cmd=cmd, returncode=0, stdout='', stderr='')

        monkeypatch.setattr(keygen, 'run_cmd', run)
        keys = []
        errors = []

        def load():
            try:
                keys.append(load_user_key())
            except BaseException as e:
                errors.append(e)

        threads = [threading.Thread(target=load) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        assert errors == []
        assert sorted(key.n for key in keys) == [N, N]
        assert len(fake_keygen.calls) == 2
        first, second = (call[0][2] for call in fake_keygen.calls)
        assert first != second


class TestTheKeyInTheHandshake:
    def test_a_machine_without_a_key_generates_one_and_asks_the_device(self, fs, monkeypatch):
        """The first contact of a fresh machine: keygen, signature, public key

        The key is the only thing a machine can not derive by itself, so it is
        made with adb on the spot. The device then sees a key it does not know,
        which is the dialog its user answers.
        """
        monkeypatch.setattr(keygen, 'user_key_path', lambda: KEY_PATH)
        fake = FakeKeygen().install(monkeypatch)

        with FakeAdbd() as adbd:
            adbd.auth_required = True
            client = AdbProtocolTCP(port=adbd.port)
            client.timeout = 5

            try:
                client.connect()
                assert client.is_connected
            finally:
                client.disconnect()

        assert len(fake.calls) == 1
        # the signature of a key the device never saw, then the public key
        assert adbd.auth_challenges == 2
        assert len(adbd.auth_signatures) == 1
        assert len(adbd.auth_public_keys) == 1
        assert adbd.auth_key == (N, E)
        assert adbd.violations == []
