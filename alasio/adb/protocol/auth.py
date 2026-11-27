"""
Host authentication of the adb transport.

A device with a secure adbd answers the CNXN of the host with an AUTH token
instead of a CNXN, and only lets the host in after it signed the token with a
private key the device was told to trust. That key is the one adb itself keeps
in `<home>/.android/adbkey`: a machine where adb ever started has the file, and
every device that allowed debugging from this machine already knows its public
half. The exchange therefore costs a signature and no user interaction, except
for a device that sees the key for the first time, where the device itself asks
its user.

This module implements the host half: read the key, sign the token, and encode
the public key offered on a first contact. A machine where adb never ran has no
key, and the first contact of such a machine generates one with the adb binary
itself, so the file the handshake uses stays the very file adb reads.
"""

import base64
import binascii
import getpass
import hashlib
import os
import socket
import sys
import threading

from alasio.adb.protocol.const import AUTH_TOKEN_SIZE, AdbAuthKeyInvalid, AdbAuthKeyMissing
from alasio.ext.concurrent.cmd import CmdlineError, run_cmd
from alasio.ext.path.atomic import atomic_read_bytes, atomic_replace_tmp
from alasio.logger import logger

# ASN.1 tags of the DER encoding of a private key
TAG_INTEGER = 0x02
TAG_OCTET_STRING = 0x04
TAG_SEQUENCE = 0x30

# Labels of the PEM blocks a private key comes in: PKCS#8 wraps the PKCS#1
# RSA numbers an older tool may write directly
PEM_PKCS8 = 'PRIVATE KEY'
PEM_PKCS1 = 'RSA PRIVATE KEY'

# DigestInfo of SHA1 in PKCS#1 v1.5, the padding adb signs a token with. The
# signature predates the version field of the protocol, and a device of any
# version verifies exactly this, not a modern digest
SHA1_DIGEST_INFO = bytes.fromhex('3021300906052b0e03021a05000414')

# Bytes of the modulus of the adb public key format, a key of another size has
# no encoding there
PUBLIC_KEY_MODULUS_SIZE = 2048 // 8
# struct RSAPublicKey of libcrypto_utils: the words of the modulus, the two
# Montgomery parameters, the modulus and the exponent, 3 * 4 + 2 * 256 bytes
PUBLIC_KEY_ENCODED_SIZE = 3 * 4 + 2 * PUBLIC_KEY_MODULUS_SIZE

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
        return _read_key(path)
    except AdbAuthKeyMissing:
        # A file that exists but does not parse is never replaced: the key is
        # the identity of this machine, and a new one loses every authorization
        return _generate_user_key(path)


def _read_key(path):
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


def _adb_path():
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


def _generate_user_key(path):
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
    adb = _adb_path()
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

    return _read_key(path)


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


def _der_item(data, offset):
    """
    Read one DER TLV.

    Args:
        data (bytes): DER to read
        offset (int): Offset of the tag

    Returns:
        tuple: (tag, value, offset after the value)

    Raises:
        AdbAuthKeyInvalid: When the DER is truncated or its length is unsupported
    """
    if offset + 2 > len(data):
        raise AdbAuthKeyInvalid('The DER of the adb key ends inside a header')
    tag = data[offset]
    length = data[offset + 1]
    offset += 2
    if length & 0x80:
        count = length & 0x7F
        if not 1 <= count <= 4:
            raise AdbAuthKeyInvalid(f'The DER of the adb key has a length of {count} bytes')
        if offset + count > len(data):
            raise AdbAuthKeyInvalid('The DER of the adb key ends inside a length')
        length = int.from_bytes(data[offset:offset + count], 'big')
        offset += count
    end = offset + length
    if end > len(data):
        raise AdbAuthKeyInvalid('The DER of the adb key ends inside a value')
    return tag, data[offset:end], end


def _der_children(value):
    """
    Read the items of a DER container.

    Args:
        value (bytes): Content of the container

    Returns:
        list: (tag, value) of every item
    """
    items = []
    offset = 0
    while offset < len(value):
        tag, item, offset = _der_item(value, offset)
        items.append((tag, item))
    return items


def _rsa_numbers(der):
    """
    Read the RSA numbers of a PKCS#1 or a PKCS#8 private key.

    Args:
        der (bytes): DER of the key

    Returns:
        tuple: (n, e, d, p, q)

    Raises:
        AdbAuthKeyInvalid: When the DER is not an RSA private key
    """
    tag, body, _ = _der_item(der, 0)
    if tag != TAG_SEQUENCE:
        raise AdbAuthKeyInvalid(f'The adb key is not a DER sequence but a tag {tag:#x}')
    items = _der_children(body)
    if len(items) >= 2 and items[0][0] == TAG_INTEGER and items[1][0] == TAG_SEQUENCE:
        # PKCS#8 PrivateKeyInfo: version, algorithm, privateKey octet string
        if len(items) < 3 or items[2][0] != TAG_OCTET_STRING:
            raise AdbAuthKeyInvalid('The PKCS#8 adb key has no private key octet string')
        tag, body, _ = _der_item(items[2][1], 0)
        if tag != TAG_SEQUENCE:
            raise AdbAuthKeyInvalid('The PKCS#8 adb key is not a DER sequence')
        items = _der_children(body)

    # PKCS#1 RSAPrivateKey: version, modulus, public exponent, private exponent,
    # the two primes and the three parameters of the CRT
    if len(items) != 9 or any(tag != TAG_INTEGER for tag, _ in items):
        raise AdbAuthKeyInvalid('The adb key is not an RSA private key')
    n = int.from_bytes(items[1][1], 'big')
    e = int.from_bytes(items[2][1], 'big')
    d = int.from_bytes(items[3][1], 'big')
    p = int.from_bytes(items[4][1], 'big')
    q = int.from_bytes(items[5][1], 'big')
    if n < 3 or not n & 1:
        raise AdbAuthKeyInvalid(f'The modulus of the adb key is not an odd number of {n.bit_length()} bits')
    if not 1 < e < 1 << 32:
        raise AdbAuthKeyInvalid(f'The exponent {e} of the adb key does not fit the adb public key')
    if not 0 < d < n:
        raise AdbAuthKeyInvalid('The private exponent of the adb key is out of range')
    # The primes are what makes a signature cheap, and a pair that does not
    # rebuild the modulus would sign every token into a value the device
    # rejects. A file that disagrees with itself is refused here, where the
    # message can still say why
    if p < 2 or q < 2 or p == q or p * q != n:
        raise AdbAuthKeyInvalid('The two primes of the adb key do not multiply to its modulus')
    return n, e, d, p, q


def _decode_pem(pem):
    """
    Decode the DER of the PEM block of a key.

    Args:
        pem (bytes | str): Content of the key file

    Returns:
        tuple: (label, der), label like 'PRIVATE KEY'

    Raises:
        AdbAuthKeyInvalid: When there is no PEM block or its base64 is broken
    """
    if isinstance(pem, str):
        pem = pem.encode('utf-8')
    label = ''
    chunks = []
    for line in pem.splitlines():
        line = line.strip()
        if line.startswith(b'-----BEGIN ') and line.endswith(b'-----'):
            label = line[11:-5].decode('ascii', 'replace')
        elif line.startswith(b'-----END '):
            continue
        elif line:
            chunks.append(line)
    if not label or not chunks:
        raise AdbAuthKeyInvalid('The adb key file has no PEM block')
    try:
        der = base64.b64decode(b''.join(chunks), validate=True)
    except (binascii.Error, ValueError) as e:
        raise AdbAuthKeyInvalid(f'The {label} PEM block of the adb key has invalid base64: {e}')
    return label, der


def _user_name():
    """
    Name of the user, the part of the public key a device shows in its dialog.

    Returns:
        str: Name, or 'unknown' when the environment does not tell it
    """
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return 'unknown'


def _host_name():
    """
    Name of this host, the part of the public key a device shows in its dialog.

    Returns:
        str: Name, or 'unknown' when the system does not tell it
    """
    try:
        return socket.gethostname()
    except OSError:
        return 'unknown'


class AdbAuthKey:
    """
    The RSA private key a host authenticates with.

    Attributes:
        n (int): Modulus
        e (int): Public exponent
        d (int): Private exponent
        p (int): First prime, None when the key was built without one
        q (int): Second prime, None when the key was built without one
    """

    def __init__(self, n, e, d, p=None, q=None):
        self.n = n
        self.e = e
        self.d = d
        self.p = p
        self.q = q
        # The CRT parameters, computed by the first signature and kept: a key
        # of a device that asks for no AUTH never pays for them
        self._crt = None

    def __str__(self):
        return f'{self.__class__.__name__}(bits={self.n.bit_length()}, e={self.e})'

    __repr__ = __str__

    @classmethod
    def from_pem(cls, pem):
        """
        Read a PEM private key.

        Args:
            pem (bytes | str): Content of the key file

        Returns:
            AdbAuthKey: The key

        Raises:
            AdbAuthKeyInvalid: When the PEM is not a usable RSA private key
        """
        label, der = _decode_pem(pem)
        if label not in (PEM_PKCS8, PEM_PKCS1):
            raise AdbAuthKeyInvalid(f'The PEM block {label!r} of the adb key is not {PEM_PKCS8!r} or {PEM_PKCS1!r}')
        n, e, d, p, q = _rsa_numbers(der)
        return cls(n, e, d, p, q)

    def _modulus_size(self):
        """
        Width of a signature of this key.

        Returns:
            int: Bytes of the modulus
        """
        return (self.n.bit_length() + 7) // 8

    def sign(self, token):
        """
        Sign an AUTH token the way adb signs it.

        The signature is PKCS#1 v1.5 over SHA1, which is what RSA_sign of adb
        produces and what adbd verifies. It is not a choice: a device of any
        version knows no other digest.

        Args:
            token (bytes): The 20 bytes the device sent

        Returns:
            bytes: Signature, as wide as the modulus

        Raises:
            ValueError: When the token is not the size of an AUTH token
            AdbAuthKeyInvalid: When the key is too small to hold the digest
        """
        if len(token) != AUTH_TOKEN_SIZE:
            raise ValueError(f'An AUTH token is {AUTH_TOKEN_SIZE} bytes, got {len(token)}')
        size = self._modulus_size()
        digest = SHA1_DIGEST_INFO + hashlib.sha1(token).digest()
        padding = size - len(digest) - 3
        if padding < 8:
            raise AdbAuthKeyInvalid(f'A {self.n.bit_length()} bit adb key is too small to sign an AUTH token')
        block = b'\x00\x01' + b'\xff' * padding + b'\x00' + digest
        return self._exponentiate(int.from_bytes(block, 'big')).to_bytes(size, 'big')

    def _crt_parameters(self):
        """
        The exponents and the inverse that split a signature with the CRT.

        Two exponentiations of half the width and a recombination cost about a
        third of one exponentiation of the full width: a signature of a 2048
        bit key takes 8 ms instead of 24 ms, while the exchange it belongs to
        waits for the device anyway.

        Returns:
            tuple | None: (dp, dq, qinv), None when the key carries no primes
        """
        if self._crt is None and self.p and self.q:
            # Two threads that sign with the same key race here, and both write
            # the same numbers: the second computation is wasted work, not a
            # wrong result, so there is nothing to lock
            self._crt = (self.d % (self.p - 1), self.d % (self.q - 1), pow(self.q, -1, self.p))
        return self._crt

    def _exponentiate(self, value):
        """
        Raise a padded block to the private exponent.

        Args:
            value (int): Padded block, less than the modulus

        Returns:
            int: Signature, less than the modulus
        """
        crt = self._crt_parameters()
        if crt is None:
            # a key handed over as n, e, d alone, e.g. one a test built by hand
            return pow(value, self.d, self.n)
        dp, dq, qinv = crt
        m1 = pow(value, dp, self.p)
        m2 = pow(value, dq, self.q)
        # the two remainders agree modulo the modulus, the recombination picks
        # the one below n, which is the width of a signature
        return m2 + ((qinv * (m1 - m2)) % self.p) * self.q

    def public_key_blob(self):
        """
        Encode the public key in the binary format adbd decodes.

        Returns:
            bytes: The 524 bytes of struct RSAPublicKey

        Raises:
            AdbAuthKeyInvalid: When the modulus does not fit the format
        """
        size = PUBLIC_KEY_MODULUS_SIZE
        if self.n.bit_length() > size * 8:
            raise AdbAuthKeyInvalid(
                f'A {self.n.bit_length()} bit adb key has no public key, the format holds {size * 8}'
            )
        # Montgomery parameters of the key, which adbd needs to run the modulo
        # multiplication of a signature verification
        n0 = self.n & 0xFFFFFFFF
        n0inv = (1 << 32) - pow(n0, -1, 1 << 32)
        rr = pow(2, size * 8 * 2, self.n)
        return b''.join((
            (size // 4).to_bytes(4, 'little'),
            (n0inv & 0xFFFFFFFF).to_bytes(4, 'little'),
            self.n.to_bytes(size, 'little'),
            rr.to_bytes(size, 'little'),
            self.e.to_bytes(4, 'little'),
        ))

    def public_key_text(self):
        """
        Base64 of the public key with the name adb appends to it.

        The name is what the device shows in the dialog of a first contact, it is
        not part of the key.

        Returns:
            str: `base64(blob) user@host`
        """
        blob = base64.b64encode(self.public_key_blob()).decode('ascii')
        return f'{blob} {_user_name()}@{_host_name()}'

    def public_key_payload(self):
        """
        Payload of the AUTH message that offers the key to a device.

        A device parses the text as a C string, so the payload carries its null
        terminator.

        Returns:
            bytes: `public_key_text()` and a null byte
        """
        return self.public_key_text().encode('utf-8') + b'\x00'
