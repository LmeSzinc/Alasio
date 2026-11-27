"""
Key material the AUTH tests build by hand.

The DER and PEM writers here are a second implementation of the format adb
writes, not a call into the module under test: a mistake of the module can not
cancel itself out against the same mistake in a fixture. The primes are fixtures
of the suite, not a key anybody ever authorized on a device, and they live here
once so that the test modules needing a key agree on it.
"""

import base64

# A test key of 2048 bits, the size adb uses
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

# rsaEncryption, the algorithm of the key inside a PKCS#8 block
RSA_ALGORITHM = bytes.fromhex('300d06092a864886f70d0101010500')


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


def pkcs1_der(n=N, e=E, d=D, p=P, q=Q):
    """
    PKCS#1 RSAPrivateKey of a key, built here and not by the module.

    Args:
        n (int): Modulus. Defaults to the test key.
        e (int): Public exponent. Defaults to the test key.
        d (int): Private exponent. Defaults to the test key.
        p (int): First prime. Defaults to the test key.
        q (int): Second prime. Defaults to the test key.

    Returns:
        bytes: DER of the key
    """
    numbers = (0, n, e, d, p, q, d % (p - 1), d % (q - 1), pow(q, -1, p))
    return der(0x30, b''.join(der_integer(value) for value in numbers))


def pkcs8_der(n=N, e=E, d=D, p=P, q=Q):
    """
    PKCS#8 PrivateKeyInfo that wraps a key.

    Args:
        n (int): Modulus. Defaults to the test key.
        e (int): Public exponent. Defaults to the test key.
        d (int): Private exponent. Defaults to the test key.
        p (int): First prime. Defaults to the test key.
        q (int): Second prime. Defaults to the test key.

    Returns:
        bytes: DER of the key
    """
    return der(0x30, der_integer(0) + RSA_ALGORITHM + der(0x04, pkcs1_der(n, e, d, p, q)))


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
