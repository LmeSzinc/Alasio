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
the public key offered on a first contact.
"""

import base64
import binascii
import getpass
import hashlib
import os
import socket

from alasio.adb.protocol.const import AUTH_TOKEN_SIZE, AdbAuthKeyInvalid, AdbAuthKeyMissing
from alasio.ext.path.atomic import atomic_read_bytes

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
    Read the private key adb keeps for the current user.

    Returns:
        AdbAuthKey: The key

    Raises:
        AdbAuthKeyMissing: When the file does not exist
        AdbAuthKeyInvalid: When the file exists but is not a usable RSA key
    """
    path = user_key_path()
    try:
        pem = atomic_read_bytes(path)
    except FileNotFoundError:
        raise AdbAuthKeyMissing(f'No adb key at {path}, run an adb command once to create it')
    except OSError as e:
        raise AdbAuthKeyInvalid(f'Can not read the adb key at {path}: {e}')
    return AdbAuthKey.from_pem(pem)


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
        tuple: (n, e, d)

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
    if n < 3 or not n & 1:
        raise AdbAuthKeyInvalid(f'The modulus of the adb key is not an odd number of {n.bit_length()} bits')
    if not 1 < e < 1 << 32:
        raise AdbAuthKeyInvalid(f'The exponent {e} of the adb key does not fit the adb public key')
    if not 0 < d < n:
        raise AdbAuthKeyInvalid('The private exponent of the adb key is out of range')
    return n, e, d


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
    """

    def __init__(self, n, e, d):
        self.n = n
        self.e = e
        self.d = d

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
        n, e, d = _rsa_numbers(der)
        return cls(n, e, d)

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
        return pow(int.from_bytes(block, 'big'), self.d, self.n).to_bytes(size, 'big')

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
