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

This module implements the arithmetic of that exchange: the key as the numbers
of an RSA private key, the signature of a token, and the encoding of the public
key offered on a first contact. The file the key lives in belongs to `keygen`,
which reads it and generates it with the adb binary when the machine has none.
"""

import base64
import binascii
import getpass
import socket

from alasio.adb.protocol.const import AUTH_TOKEN_SIZE, AdbAuthKeyInvalid

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
# version verifies exactly this, not a modern digest. adb hands the 20 byte
# token of the device to RSA_sign as the digest itself, so the token is
# appended unchanged instead of being hashed once more
SHA1_DIGEST_INFO = bytes.fromhex('3021300906052b0e03021a05000414')

# Bytes of the modulus of the adb public key format, a key of another size has
# no encoding there
PUBLIC_KEY_MODULUS_SIZE = 2048 // 8
# struct RSAPublicKey of libcrypto_utils: the words of the modulus, the two
# Montgomery parameters, the modulus and the exponent, 3 * 4 + 2 * 256 bytes
PUBLIC_KEY_ENCODED_SIZE = 3 * 4 + 2 * PUBLIC_KEY_MODULUS_SIZE


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
        version knows no other digest. adb passes the token to RSA_sign as the
        digest, and RSA_sign takes its input to be the digest already, so the
        token is what the DigestInfo carries: hashing it again signs a value no
        adbd recovers.

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
        digest = SHA1_DIGEST_INFO + token
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
