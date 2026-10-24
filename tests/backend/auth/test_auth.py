import hashlib
import time

import jwt
import pytest

from alasio.backend.auth.auth import JwtManager

# An HS256 key is at least 32 bytes, RFC 7518 Section 3.2: pyjwt warns about a
# shorter key with InsecureKeyLengthWarning
SECRET = b'test-secret' * 4
PASSWORD = 'test-password'
# the JWT subject is never the plaintext password (red line)
SUB = 'v1:' + hashlib.sha256(PASSWORD.encode()).hexdigest()


class TestValidateToken:
    """
    Tests for JwtManager.validate_token
    """

    def setup_method(self):
        self.jwt = JwtManager()
        # cached_property is a non-data descriptor, instance attribute assignment shadows it
        self.jwt.secret = SECRET
        self.jwt.pwd = PASSWORD

    def _make_token(self, sub=SUB, iat=None, omit_iat=False, exp=3600, extra=None):
        """
        Create a signed token for tests, mimicking JwtManager.create() which always
        includes exp. Pass sub=None to omit sub, omit_iat=True to omit iat, and
        exp=None to omit exp.

        Args:
            sub (str): Subject claim, None to omit
            iat (float): Issued-at timestamp, None means now
            omit_iat (bool): Omit the iat claim entirely
            exp (float): Expiry seconds from now, None to omit
            extra (dict): Extra claims to include

        Returns:
            str: Signed JWT
        """
        now = int(time.time())
        data = {'sub': sub}
        if not omit_iat:
            data['iat'] = now if iat is None else iat
        if exp is not None:
            data['exp'] = now + exp
        if extra:
            data.update(extra)
        if sub is None:
            del data['sub']
        return jwt.encode(data, SECRET, algorithm='HS256')

    def test_valid_token_keep_current(self):
        """Valid token issued within the renew window keeps the current token."""
        result = self.jwt.validate_token(self._make_token(iat=time.time()))
        assert result == ''

    def test_valid_token_renew(self):
        """Token older than renew_hours gets a renewed token."""
        iat = int(time.time()) - 2 * 3600
        result = self.jwt.validate_token(self._make_token(iat=iat))
        assert result != ''
        data = jwt.decode(result, SECRET, algorithms=[self.jwt.algorithm])
        assert data['sub'] == SUB
        assert data['iat'] > iat

    def test_missing_exp(self):
        with pytest.raises(jwt.PyJWTError, match='Missing exp'):
            self.jwt.validate_token(self._make_token(exp=None))

    def test_missing_iat(self):
        with pytest.raises(jwt.PyJWTError, match='Missing iat'):
            self.jwt.validate_token(self._make_token(omit_iat=True))

    def test_missing_sub(self):
        """Missing sub raises 'Missing sub', not the copied 'Missing iat' message."""
        with pytest.raises(jwt.PyJWTError, match='Missing sub'):
            self.jwt.validate_token(self._make_token(sub=None))

    def test_password_mismatch(self):
        with pytest.raises(jwt.PyJWTError, match='Password incorrect'):
            self.jwt.validate_token(self._make_token(sub='wrong-sub'))

    def test_invalid_token(self):
        with pytest.raises(jwt.PyJWTError):
            self.jwt.validate_token('not-a-jwt-token')

    def test_no_password_no_token_creates(self):
        """Without a configured password and without a token, a new token is created."""
        self.jwt.pwd = ''
        result = self.jwt.validate_token('')
        assert result != ''
        data = jwt.decode(result, SECRET, algorithms=[self.jwt.algorithm])
        assert data['sub'] == ''

    def test_legacy_plaintext_sub_rejected(self):
        """A token carrying the plaintext password as sub must be rejected."""
        with pytest.raises(jwt.PyJWTError, match='Password incorrect'):
            self.jwt.validate_token(self._make_token(sub=PASSWORD))


class TestCreate:
    """
    Tests for JwtManager.create
    """

    def setup_method(self):
        self.jwt = JwtManager()
        self.jwt.secret = SECRET
        self.jwt.pwd = PASSWORD

    def test_create_payload(self):
        token = self.jwt.create()
        data = jwt.decode(token, SECRET, algorithms=['HS256'])
        # sub is the password digest, never the plaintext
        assert data['sub'] == SUB
        assert data['sub'] != PASSWORD
        assert 'iat' in data
        assert 'exp' in data
        assert data['exp'] > data['iat']

    def test_create_no_password(self):
        """Without a password the subject is empty."""
        self.jwt.pwd = ''
        token = self.jwt.create()
        data = jwt.decode(token, SECRET, algorithms=['HS256'])
        assert data['sub'] == ''


class TestSub:
    """
    Tests for the JWT subject digest (_sub)
    """

    def setup_method(self):
        self.jwt = JwtManager()
        self.jwt.pwd = PASSWORD

    def test_sub_is_versioned_sha256(self):
        assert self.jwt._sub == SUB

    def test_sub_empty_when_no_password(self):
        self.jwt.pwd = ''
        assert self.jwt._sub == ''

    def test_sub_stable_across_calls(self):
        assert self.jwt._sub == self.jwt._sub


class TestValidatePwd:
    """
    Tests for JwtManager.validate_pwd (compare_digest semantics)
    """

    def setup_method(self):
        self.jwt = JwtManager()
        self.jwt.secret = SECRET
        self.jwt.pwd = PASSWORD

    def test_correct_password_returns_token(self):
        token = self.jwt.validate_pwd(PASSWORD)
        data = jwt.decode(token, SECRET, algorithms=[self.jwt.algorithm])
        assert data['sub'] == SUB

    def test_wrong_password_raises(self):
        with pytest.raises(jwt.PyJWTError, match='Password incorrect'):
            self.jwt.validate_pwd('wrong')

    def test_empty_password_with_configured_pwd_raises(self):
        """Empty input must not pass when a password is configured."""
        with pytest.raises(jwt.PyJWTError, match='Password incorrect'):
            self.jwt.validate_pwd('')

    def test_empty_password_no_pwd_configured_creates(self):
        """Empty password matches an unconfigured password (open mode)."""
        self.jwt.pwd = ''
        token = self.jwt.validate_pwd('')
        assert token != ''
