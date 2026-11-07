"""
The signature of a COS request: the canonical string, the encoding and the
Authorization header.

The canonical strings are the ones the official SDK produces for the same
requests (compared byte for byte when the client was written), so a change of
the separator, of the sorting or of the encoding fails here. The signatures of
a fixed credential and a frozen clock are a regression guard of this
implementation; the external judge of the signature is the real bucket test,
see test_real.py.
"""

import pytest

from alasio.deploy_dev.store.cos.sign import CosSigner, canonical_string, encode_value, is_sign_header

# pure computation over the signer, no file, no socket, no clock of the machine:
# safe beside any other marked file
pytestmark = [
    pytest.mark.parallel,
]

HOST = 'alasio-1250000000.cos.ap-guangzhou.myqcloud.com'

PUT_HEADERS = {
    'Host': HOST,
    'Content-Type': 'application/octet-stream',
    'Content-MD5': 'XUFAKrxLKna5cZ2REBfFkg==',
    'Content-Length': '41',
    'Cache-Control': 'no-cache',
}

FROZEN_NOW = 1761470000


def frozen_signer(token=None):
    """
    A signer of a fixed credential and a frozen clock.

    Args:
        token (str): Session token. Defaults to None

    Returns:
        CosSigner: The signer, its key time is 1761469940;1761473600
    """
    return CosSigner('AKIDEXAMPLE', 'SECRETEXAMPLE', token=token, clock=lambda: FROZEN_NOW)


class TestCanonicalString:
    """The bytes the signature is computed over."""

    def test_put_with_five_headers(self):
        """A PUT signs five headers, joined with '&' in sorted order."""
        assert canonical_string('PUT', '/pack/ch/packrepo/latest.pack', {}, PUT_HEADERS) == (
            'put\n'
            '/pack/ch/packrepo/latest.pack\n'
            '\n'
            'cache-control=no-cache'
            '&content-length=41'
            '&content-md5=XUFAKrxLKna5cZ2REBfFkg%3D%3D'
            '&content-type=application%2Foctet-stream'
            '&host=alasio-1250000000.cos.ap-guangzhou.myqcloud.com'
            '\n'
        )

    def test_list_with_params(self):
        """The root path is '/', the query parameters are encoded and sorted."""
        assert canonical_string(
            'GET', '/',
            {'list-type': '2', 'max-keys': '1000', 'prefix': 'pack/ch/'},
            {'Host': HOST},
        ) == (
            'get\n'
            '/\n'
            'list-type=2&max-keys=1000&prefix=pack%2Fch%2F\n'
            f'host={HOST}\n'
        )

    def test_get_with_one_header(self):
        """A GET signs the host only."""
        assert canonical_string(
            'GET', '/pack/ch/manifest.json', {}, {'Host': HOST},
        ) == (
            'get\n'
            '/pack/ch/manifest.json\n'
            '\n'
            f'host={HOST}\n'
        )

    def test_header_and_param_pairs_are_joined_with_ampersand(self):
        """The pairs are joined with '&', not with the ';' of the header lists.

        A request that signs a single header passes with either separator,
        which is why the LIST and GET vectors above cannot catch a swap: this
        one can. The ';' is the separator of q-header-list, see
        TestAuthorization.
        """
        canonical = canonical_string('PUT', '/k', {'b': '2', 'a': '1'}, PUT_HEADERS)
        header_line = canonical.splitlines()[3]
        assert ';' not in header_line
        assert 'cache-control=no-cache&content-length=41' in header_line
        assert canonical.splitlines()[2] == 'a=1&b=2'

    def test_header_names_are_lowercased_and_sorted(self):
        """The case of the request headers does not change the canonical string."""
        lowercase = canonical_string('GET', '/k', {}, {'Host': HOST, 'Range': 'bytes=0-1'})
        mixed = canonical_string('GET', '/k', {}, {'HOST': HOST, 'RANGE': 'bytes=0-1'})
        assert lowercase == mixed
        assert lowercase.splitlines()[3] == f'host={HOST}&range=bytes%3D0-1'

    def test_headers_outside_the_whitelist_are_left_out(self):
        """A header of the http client (Accept, User-Agent) is not signed."""
        canonical = canonical_string(
            'GET', '/k', {},
            {'Host': HOST, 'Accept': '*/*', 'User-Agent': 'python-httpx2/2.12.0'},
        )
        assert canonical == f'get\n/k\n\nhost={HOST}\n'

    def test_reserved_prefixes_are_signed(self):
        """x-cos-* and x-ci-* headers are signed, whatever their name."""
        canonical = canonical_string(
            'GET', '/k', {},
            {'Host': HOST, 'X-Cos-Security-Token': 'TOKEN', 'X-Ci-Processing': 'imageMogr2'},
        )
        assert canonical.splitlines()[3] == (
            f'host={HOST}&x-ci-processing=imageMogr2&x-cos-security-token=TOKEN')


class TestEncode:
    """The percent-encoding of a value of the canonical string."""

    @pytest.mark.parametrize('value, expected', [
        # the unreserved set is kept
        ('AZaz09-_.~', 'AZaz09-_.~'),
        # separators of the canonical string itself are escaped
        ('a b', 'a%20b'),
        ('a+b', 'a%2Bb'),
        ('a/b', 'a%2Fb'),
        ('a=b', 'a%3Db'),
        ('a&b', 'a%26b'),
        ('a;b', 'a%3Bb'),
        # a non-ascii value is utf-8 of the text
        ('中文.pack', '%E4%B8%AD%E6%96%87.pack'),
        ('', ''),
    ])
    def test_encode_value(self, value, expected):
        """Every value is escaped to the unreserved set of RFC 3986."""
        assert encode_value(value) == expected

    def test_quote_of_content_md5(self):
        """The padding of a base64 MD5 is escaped, the value is not."""
        assert encode_value('XUFAKrxLKna5cZ2REBfFkg==') == 'XUFAKrxLKna5cZ2REBfFkg%3D%3D'


class TestSignHeader:
    """Which headers take part in the signature."""

    @pytest.mark.parametrize('name, expected', [
        ('Host', True),
        ('content-md5', True),
        ('Cache-Control', True),
        ('X-Cos-Security-Token', True),
        ('x-ci-processing', True),
        ('Accept', False),
        ('User-Agent', False),
        ('Authorization', False),
    ])
    def test_is_sign_header(self, name, expected):
        """The whitelist and the reserved prefixes decide, case-insensitively."""
        assert is_sign_header(name) is expected


class TestAuthorization:
    """The header every request carries."""

    def test_put_authorization(self):
        """The five signed headers are listed with ';', the signature is stable."""
        assert frozen_signer().authorization(
            'PUT', '/pack/ch/packrepo/latest.pack', {}, PUT_HEADERS,
        ) == (
            'q-sign-algorithm=sha1'
            '&q-ak=AKIDEXAMPLE'
            '&q-sign-time=1761469940;1761473600'
            '&q-key-time=1761469940;1761473600'
            '&q-header-list=cache-control;content-length;content-md5;content-type;host'
            '&q-url-param-list='
            '&q-signature=a8d934ed57def6f040684ec8f08531f9305ab036'
        )

    def test_header_list_uses_semicolon(self):
        """The header list is the names joined with ';', not with '&'."""
        authorization = frozen_signer().authorization('PUT', '/k', {}, PUT_HEADERS)
        fields = dict(part.split('=', 1) for part in authorization.split('&'))
        assert fields['q-header-list'] == 'cache-control;content-length;content-md5;content-type;host'

    def test_parameter_list_is_sorted(self):
        """The parameter list is the sorted names of the query."""
        authorization = frozen_signer().authorization(
            'GET', '/', {'prefix': 'pack/', 'list-type': '2'}, {'Host': HOST})
        fields = dict(part.split('=', 1) for part in authorization.split('&'))
        assert fields['q-url-param-list'] == 'list-type;prefix'

    def test_token_is_signed(self):
        """A session token is a x-cos-* header and is part of the signature."""
        authorization = frozen_signer(token='TOKEN').authorization(
            'GET', '/pack/ch/manifest.json', {},
            {'Host': HOST, 'X-Cos-Security-Token': 'TOKEN'},
        )
        assert '&q-header-list=host;x-cos-security-token&' in authorization
        assert authorization.endswith(
            '&q-signature=f850be5b9ff7ce25b81f50164a84e67080a5e8b1')

    def test_key_time_window(self):
        """The window starts CLOCK_SKEW before now and lasts expire seconds."""
        assert frozen_signer().key_time() == '1761469940;1761473600'

    def test_signature_changes_with_the_secret(self):
        """Two credentials do not produce the same signature."""
        other = CosSigner('AKIDEXAMPLE', 'OTHERSECRET', clock=lambda: FROZEN_NOW)
        assert other.signature('PUT', '/k', {}, PUT_HEADERS) != frozen_signer().signature(
            'PUT', '/k', {}, PUT_HEADERS)

    def test_empty_credential_is_rejected(self):
        """A signer without a SecretId or a SecretKey is a configuration error."""
        with pytest.raises(ValueError):
            CosSigner('', 'SECRET')
        with pytest.raises(ValueError):
            CosSigner('AKID', '')
