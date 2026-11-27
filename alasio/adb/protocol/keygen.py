"""
The private key adb keeps for the current user.

A device with a secure adbd only lets a host in after the host signed a token
with a private key the device was told to trust. That key is the one adb itself
keeps in `<home>/.android/adbkey`: a machine where adb ever started has the file,
and every device that allowed debugging from this machine already knows its
public half. This module owns that file: where it is, how it is read, and how it
is made when the machine has none yet.

The key is made by the adb binary and never by this module. A key written here
would have to reproduce every choice adb makes (the 2048 bit modulus, the
exponent, the PEM block, the format of the public half), and a machine that runs
adb later must read the very file this module wrote. Running `adb keygen` once
keeps the two identical by construction instead of by agreement.

The file is never replaced once it exists, and a file that does not parse is an
error rather than a reason to make another one: the key is the identity of this
machine, and a new one loses every device that authorized the old one.
"""

import os
import sys
import threading

from alasio.adb.protocol.auth import AdbAuthKey
from alasio.adb.protocol.const import AdbAuthKeyInvalid, AdbAuthKeyMissing
from alasio.ext.concurrent.cmd import CmdlineError, run_cmd
from alasio.ext.path.atomic import atomic_read_bytes, atomic_replace_tmp
from alasio.logger import logger

# Seconds a keygen of adb may take. A 2048 bit key takes under a second on every
# machine measured, the timeout only stops a hung binary from holding the
# handshake forever
ADB_KEYGEN_TIMEOUT = 60
# A console window of the adb child would flash over the backend on Windows, the
# flag means nothing on the other platforms
CREATE_NO_WINDOW = 0x08000000


def user_key_path():
    """
    Path of the private key adb keeps for the current user.

    adb resolves the home directory of the user by itself, with
    SHGetFolderPathW(CSIDL_PROFILE) on Windows and $HOME elsewhere, which is the
    directory expanduser() returns as well.

    Returns:
        str: `<home>/.android/adbkey`
    """
    return os.path.join(os.path.expanduser('~'), '.android', 'adbkey')


def load_user_key():
    """
    Read the private key adb keeps for the current user, creating it once.

    A machine where adb ever ran has the key, so the call costs a read of the
    file. A machine where it never ran has none, and the key is generated with
    the adb binary, which is what makes a fresh install able to talk to a secure
    device without asking the user to run an adb command first.

    Returns:
        AdbAuthKey: The key

    Raises:
        AdbAuthKeyMissing: When the file does not exist and no adb binary can
            create one
        AdbAuthKeyInvalid: When the file exists but is not a usable RSA key
    """
    path = user_key_path()
    try:
        return read_key(path)
    except AdbAuthKeyMissing:
        # A file that exists but does not parse is never replaced: the key is
        # the identity of this machine, and a new one loses every authorization
        return generate_user_key(path)


def read_key(path):
    """
    Read and decode the private key at a path.

    Args:
        path (str): Path of the key

    Returns:
        AdbAuthKey: The key

    Raises:
        AdbAuthKeyMissing: When the file does not exist
        AdbAuthKeyInvalid: When the file exists but is not a usable RSA key
    """
    try:
        pem = atomic_read_bytes(path)
    except FileNotFoundError:
        raise AdbAuthKeyMissing(f'No adb key at {path}')
    except OSError as e:
        raise AdbAuthKeyInvalid(f'Can not read the adb key at {path}: {e}')
    return AdbAuthKey.from_pem(pem)


def adb_path():
    """
    Path of the adb binary that generates a key.

    adb is on PATH on a machine where platform-tools were installed, and it sits
    in the android sdk on the runners of the CI, which install the sdk without
    putting it on the PATH of the shell. The wheel of adbutils carries its own
    copy for a machine where neither is there, and adbutils is imported only on
    that fallback: it pulls in requests, Pillow and retry2, which a handshake
    with an open device never needs.

    Returns:
        str: Path of the adb binary

    Raises:
        AdbAuthKeyMissing: When adb is nowhere to be found
    """
    # only this one call locates a program, the rest of the module never does
    import shutil

    path = shutil.which('adb')
    if path:
        return path

    path = _adb_in_sdk()
    if path:
        return path

    finder = _adbutils_finder()
    if finder is None:
        raise AdbAuthKeyMissing(
            'No adb key and no adb binary to make one, install platform-tools or run an adb command once'
        )
    path = finder()
    if not path:
        raise AdbAuthKeyMissing('The adb binary of adbutils is missing, reinstall adbutils')
    return path


def _adb_in_sdk():
    """
    Path of adb inside the android sdk the environment names.

    A platform-tools install that was never added to PATH is the usual shape of
    a CI runner, and the variable naming the sdk is what both the sdk manager
    and adb itself read.

    Returns:
        str | None: Path of the adb binary, None when the sdk holds none
    """
    name = 'adb.exe' if sys.platform == 'win32' else 'adb'
    for variable in ('ANDROID_HOME', 'ANDROID_SDK_ROOT'):
        root = os.environ.get(variable)
        if not root:
            continue
        path = os.path.join(root, 'platform-tools', name)
        if os.path.isfile(path):
            return path
    return None


def _adbutils_finder():
    """
    The function of adbutils that locates its bundled adb, if adbutils is here.

    The name moved over the versions of adbutils, both are tried.

    Returns:
        callable | None: `adbutils.adb_path` or `adbutils._utils.adb_path`
    """
    try:
        import adbutils
    except ImportError:
        return None
    finder = getattr(adbutils, 'adb_path', None)
    if finder is not None:
        return finder
    try:
        from adbutils._utils import adb_path
    except ImportError:
        return None
    return adb_path


def generate_user_key(path):
    """
    Create the key of this user with the adb binary, then read it back.

    The binary writes the key and its `.pub` next to a temporary name, which is
    then replaced into place: a reader never sees a half written key, and a
    keygen that fails leaves the target of another worker untouched.

    Args:
        path (str): Path the key is expected at

    Returns:
        AdbAuthKey: The key read back from the file

    Raises:
        AdbAuthKeyMissing: When adb is missing, fails, times out or writes nothing
        AdbAuthKeyInvalid: When the file adb wrote is not a usable RSA key
    """
    adb = adb_path()
    # adb keygen does not create the directory, and ~/.android does not exist on
    # a machine where adb never ran
    directory = os.path.dirname(path)
    if directory:
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as e:
            # a read only home, or a file where the directory belongs, is still
            # a machine that can not authenticate
            raise AdbAuthKeyMissing(f'Can not create {directory} for the adb key: {e}')

    # Two workers of the same machine may find no key at the same moment, so the
    # temporary name is unique per process and thread
    tmp = f'{path}.{os.getpid()}.{threading.get_ident()}.tmp'
    try:
        _run_keygen(adb, tmp)
        try:
            atomic_replace_tmp(tmp, path)
        except FileNotFoundError:
            # an exit code of 0 with no file behind it is a broken binary, not a
            # key this machine can use
            raise AdbAuthKeyMissing(f'{adb} keygen finished but wrote no key at {path}')
        pub = f'{path}.pub'
        pub_tmp = f'{tmp}.pub'
        if os.path.exists(pub_tmp):
            # adb reads no public key file, it derives the key from the private
            # one, but the pair is what the user expects to find in ~/.android
            try:
                atomic_replace_tmp(pub_tmp, pub)
            except FileNotFoundError:
                pass
    finally:
        # a keygen that failed or was killed by the timeout may leave a file of
        # this process behind, and the next run must not mistake it for a key
        _remove_leftover(tmp)

    logger.warning(f'Generated a new adb key at {path}, allow it on the device when it asks')

    return read_key(path)


def _run_keygen(adb, target):
    """
    Run `adb keygen` on a path.

    Args:
        adb (str): Path of the adb binary
        target (str): Path the key is written to

    Raises:
        AdbAuthKeyMissing: When the binary can not be started, fails or times out
    """
    # the command of a backend has no console to flash one, and the flag is not
    # understood on the other platforms
    kwargs = {'creationflags': CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
    try:
        result = run_cmd([adb, 'keygen', target], timeout=ADB_KEYGEN_TIMEOUT, check=False, **kwargs)
    except CmdlineError as e:
        # a binary that is gone, can not be started, or hangs past the timeout
        raise AdbAuthKeyMissing(f'Can not run {adb} keygen: {e}')
    if result.returncode:
        # adb keygen reports its failures on stderr, which is empty on success
        detail = f': {result.stderr}' if result.stderr else ''
        raise AdbAuthKeyMissing(f'{adb} keygen exited with {result.returncode}{detail}')


def _remove_leftover(file):
    """
    Remove a temporary file of a keygen, missing or locked is not an error.

    Args:
        file (str): Path to remove
    """
    for name in (file, f'{file}.pub'):
        try:
            os.remove(name)
        except OSError:
            # the file is either gone or held by the system, and neither is
            # worth failing a handshake the key itself is ready for
            pass
