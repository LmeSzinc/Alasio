import time

import jwt
import msgspec
from starlette import status
from starlette.exceptions import HTTPException
from typing_extensions import Annotated

from alasio.backend.auth.fail2ban import Fail2Ban
from alasio.ext.starapi.param import Cookie, Depends, SetCookie
from alasio.ext.starapi.router import APIRouter
from alasio.config.table.key import AlasioKeyTable
from alasio.ext.cache import cached_property


class JwtManager:
    def __init__(self):
        self.algorithm = 'HS256'
        # expire after 168 hours (7 days)
        self.expire_hours = 168
        # renew tokens that has issued at 1 hour ago
        self.renew_hours = 1

    @cached_property
    def pwd(self) -> str:
        return ''

    @cached_property
    def secret(self):
        """
        Returns:
            bytes:
        """
        return AlasioKeyTable('gui').jwt_secret

    def create(self):
        """
        Returns:
            str:
        """
        # cache secret first
        secret = self.secret
        # create
        now = int(time.time())
        exp = now + 3600 * self.expire_hours
        data = {'sub': self.pwd, 'iat': now, 'exp': exp}
        token = jwt.encode(data, secret, algorithm=self.algorithm)
        return token

    def validate_pwd(self, pwd):
        """
        Args:
            pwd (str):

        Returns:
            str: New token

        Raises:
            jwt.PyJWTError: If password incorrect
        """
        if pwd != self.pwd:
            raise jwt.PyJWTError('Password incorrect')
        return self.create()

    def validate_token(self, token):
        """
        Args:
            token (str):

        Returns:
            str: The renewed token, or '' to keep using current token
                if no password and no token, create new token

        Raises:
            jwt.PyJWTError: If token invalid
        """
        if not token and not self.pwd:
            return self.create()

        # cache secret first
        secret = self.secret
        # may raise jwt.PyJWTError
        data = jwt.decode(token, secret, algorithms=self.algorithm)
        try:
            data['exp']
        except KeyError:
            raise jwt.PyJWTError('Missing exp') from None
        try:
            iat = data['iat']
        except KeyError:
            raise jwt.PyJWTError('Missing iat') from None
        try:
            sub = data['sub']
        except KeyError:
            raise jwt.PyJWTError('Missing iat') from None

        # check password
        if sub != self.pwd:
            raise jwt.PyJWTError('Password incorrect')

        # renew token
        now = time.time()
        if iat - now > 3600 * self.renew_hours:
            return self.create()
        else:
            return ''


JWT_MANAGER = JwtManager()

router = APIRouter('/auth')


class LoginRequest(msgspec.Struct):
    pwd: str = ''


@router.post('/login')
async def auth_login(
        request: LoginRequest,
        cookie: SetCookie,
        fail2ban: Annotated[Fail2Ban, Depends(Fail2Ban('/login'))],
):
    fail2ban.check_ban()
    try:
        new = JWT_MANAGER.validate_pwd(request.pwd)
    except jwt.PyJWTError:
        raise fail2ban.record_failure()

    # success
    fail2ban.record_success()
    cookie.set_cookie(
        key='alasio_token', value=new, max_age=JWT_MANAGER.expire_hours * 3600,
        httponly=True, samesite="strict", secure=True,
    )


@router.get('/renew')
async def auth_renew(
        token: Annotated[str, Cookie('alasio_token', default='')],
        cookie: SetCookie,
):
    try:
        new = JWT_MANAGER.validate_token(token)
    except jwt.PyJWTError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            # detail must be quoted to become a valid json
            detail='"Token invalid or expired"',
            headers={'WWW-Authenticate': 'Bearer'},
        ) from None

    # success
    if new:
        cookie.set_cookie(
            key='alasio_token', value=new, max_age=JWT_MANAGER.expire_hours * 3600,
            httponly=True, samesite="strict", secure=True,
        )
