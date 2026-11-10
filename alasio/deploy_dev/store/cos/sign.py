"""
The signature of a COS request: q-sign-algorithm=sha1.

Every request carries an Authorization header built from four parts:

    key_time    = "{now - 60};{now + expire}"
    sign_key    = HMAC-SHA1(secret_key, key_time).hex()
    http_string = "{method}\\n{uri_path}\\n{params}\\n{headers}\\n"
    to_sign     = "sha1\\n{key_time}\\n{SHA1(http_string).hex()}\\n"
    signature   = HMAC-SHA1(sign_key, to_sign).hex()

and the header itself:

    q-sign-algorithm=sha1&q-ak={secret_id}&q-sign-time={key_time}
    &q-key-time={key_time}&q-header-list={names}&q-url-param-list={names}
    &q-signature={signature}

Two separators, and confusing them is the failure mode this module is written
around:

- the canonical string joins the key/value pairs with '&'
  (`cache-control=no-cache&content-length=41&host=...`), and
- the header lists join the *names* with ';' (`cache-control;content-length;host`).

A request with a single signed header (only host) passes either way, so a test
suite that only lists or reads objects never notices a swapped separator; every
PUT, which signs four or five headers, does. The canonical strings of the tests
therefore always include a multi-header request.

The keys of a pair are lowercased and sorted, the values are percent-encoded
with the unreserved set of RFC 3986 ('-', '_', '.', '~'), so '/', '=', '+' and
every non-ASCII byte are escaped. The URI path of the canonical string is the
escaped path of the request, not the decoded key: the two must be built from the
same string, which is why the store builds its URL itself instead of handing the
parameters to the http client and letting it encode them again.
"""

import hashlib
import hmac
import time
from urllib.parse import quote

# the only algorithm COS accepts for this style of signature
ALGORITHM = 'sha1'

# seconds of a signature, and seconds of the skew allowance before its start:
# the key time starts a minute in the past, so a request signed just after the
# clock of the machine jumped forward is still accepted
DEFAULT_EXPIRE = 3600
CLOCK_SKEW = 60

# the characters percent-encoding leaves alone: the unreserved set of RFC 3986,
# i.e. letters, digits and '-', '_', '.', '~'. Everything else is escaped, '/'
# and '=' included
SAFE_CHARS = '-_.~'

# the request headers that take part in a signature, whatever the request is.
# A header that is not in this set (or below) is sent but not signed, and the
# server ignores it for the signature: Accept, User-Agent, Connection and the
# other headers the http client adds never make a request unsignable
SIGN_HEADERS = frozenset((
    'cache-control',
    'content-disposition',
    'content-encoding',
    'content-language',
    'content-length',
    'content-md5',
    'content-type',
    'expires',
    'host',
    'origin',
    'range',
))

# the header prefixes COS reserves for itself: x-cos-* (storage class, security
# token, ...) and x-ci-* (the image processing parameters)
SIGN_PREFIXES = ('x-cos-', 'x-ci-')


def is_sign_header(name):
    """
    Whether a request header takes part in the signature.

    Args:
        name (str): Name of the header, any case

    Returns:
        bool: True when the header is in the whitelist or in a reserved prefix
    """
    name = name.lower()
    return name in SIGN_HEADERS or name.startswith(SIGN_PREFIXES)


def encode_value(value):
    """
    Percent-encode a value the way the canonical string wants it.

    Args:
        value (str): Value to encode

    Returns:
        str: Encoded value, '/' '=' '+' and non-ASCII escaped, '~' kept
    """
    return quote(str(value), safe=SAFE_CHARS)


def canonical_string(method, path, params, headers):
    """
    Build the canonical HttpString of a request.

    The header and parameter pairs are joined with '&' and sorted by name; the
    method is lowercased and the path is the escaped path of the request, which
    starts with '/'. A header outside the whitelist and a parameter of an empty
    value are left out (an empty parameter is never sent, see the store).

    Args:
        method (str): Http method, e.g. 'PUT'
        path (str): Escaped path of the request, e.g. '/pack/ch/latest.pack'
        params (dict): Query parameters, name -> value
        headers (dict): Request headers, name -> value, any case

    Returns:
        str: The canonical string, ending with a newline
    """
    param_list = '&'.join(
        f'{encode_value(name)}={encode_value(value)}'
        for name, value in sorted(params.items())
    )
    header_list = '&'.join(
        f'{name.lower()}={encode_value(value)}'
        for name, value in sorted((name.lower(), value) for name, value in headers.items())
        if is_sign_header(name)
    )
    return f'{method.lower()}\n{path}\n{param_list}\n{header_list}\n'


class CosSigner:
    """
    The signer of one credential: the header every request carries.

    The secret id is part of the header, the secret key never leaves the
    signature: a signer renders it nowhere, and an error message of the store
    never includes the header it produced.

    Attributes:
        secret_id (str): SecretId of the credential, the q-ak of the header
        token (str): Session token of a temporary credential, None for a long
            term key. The caller sends it as x-cos-security-token, which is a
            x-cos-* header and therefore signed
    """

    def __init__(self, secret_id, secret_key, token=None, clock=time.time, expire=DEFAULT_EXPIRE):
        """
        Args:
            secret_id (str): SecretId of the credential
            secret_key (str): SecretKey of the credential, never rendered
            token (str): Session token of a temporary credential, None for a
                long term key. Defaults to None
            clock (Callable[[], float]): Source of the request time, frozen in
                tests so a signature is reproducible. Defaults to time.time
            expire (int): Seconds the signature is valid for. Defaults to
                DEFAULT_EXPIRE
        """
        if not secret_id:
            raise ValueError('COS SecretId is empty')
        if not secret_key:
            raise ValueError('COS SecretKey is empty')
        self.secret_id = secret_id
        self._secret_key = secret_key
        self.token = token
        self._clock = clock
        self.expire = expire

    def key_time(self):
        """
        The validity window of the signature, e.g. '1761469940;1761473600'.

        Returns:
            str: "{start};{end}" in unix seconds, starting CLOCK_SKEW before now
        """
        now = int(self._clock())
        return f'{now - CLOCK_SKEW};{now + self.expire}'

    def _sign_key(self, key_time):
        """
        Derive the key the request signature is computed with.

        Args:
            key_time (str): Validity window of the signature

        Returns:
            str: Hex HMAC-SHA1 of the window, keyed by the secret key
        """
        return hmac.new(
            self._secret_key.encode('utf-8'),
            key_time.encode('utf-8'),
            hashlib.sha1,
        ).hexdigest()

    def signature(self, method, path, params, headers, key_time=None):
        """
        Compute the q-signature of one request.

        Args:
            method (str): Http method
            path (str): Escaped path of the request
            params (dict): Query parameters
            headers (dict): Request headers
            key_time (str): Validity window to sign with, `key_time()` when
                None. Defaults to None

        Returns:
            str: Hex HMAC-SHA1 signature of the canonical string
        """
        if key_time is None:
            key_time = self.key_time()
        http_string = canonical_string(method, path, params, headers)
        to_sign = 'sha1\n{}\n{}\n'.format(
            key_time,
            hashlib.sha1(http_string.encode('utf-8')).hexdigest(),
        )
        return hmac.new(
            self._sign_key(key_time).encode('utf-8'),
            to_sign.encode('utf-8'),
            hashlib.sha1,
        ).hexdigest()

    def authorization(self, method, path, params, headers):
        """
        Build the Authorization header of one request.

        The header names of the two lists are the ones that actually take part
        in the signature: q-header-list holds the whitelisted headers of the
        request, q-url-param-list the parameters of the request.

        The validity window is read from the clock once and both signed and
        rendered from that one value: a second read could land in another
        second, and the header would name a window the signature was not
        computed over.

        Args:
            method (str): Http method
            path (str): Escaped path of the request
            params (dict): Query parameters
            headers (dict): Request headers

        Returns:
            str: Value of the Authorization header
        """
        key_time = self.key_time()
        header_names = ';'.join(sorted(name.lower() for name in headers if is_sign_header(name)))
        param_names = ';'.join(sorted(params))
        signature = self.signature(method, path, params, headers, key_time=key_time)
        return (
            f'q-sign-algorithm={ALGORITHM}'
            f'&q-ak={self.secret_id}'
            f'&q-sign-time={key_time}'
            f'&q-key-time={key_time}'
            f'&q-header-list={header_names}'
            f'&q-url-param-list={param_names}'
            f'&q-signature={signature}'
        )
