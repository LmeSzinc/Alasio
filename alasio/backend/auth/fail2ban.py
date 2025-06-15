import time

import trio
from starlette import status
from starlette.exceptions import HTTPException
from starlette.requests import Request

from alasio.backend.starapi.param import HTTPExceptionJson
from alasio.ext.singleton import SingletonNamed


def get_client_ip(request: Request) -> str:
    """
    Get client IP address
    """
    # check X-Forwarded-For header (for proxies/load balancers)
    forwarded_for = request.headers.get('X-Forwarded-For')
    if forwarded_for:
        forwarded_for, _, _ = forwarded_for.partition(',')

    # check X-Real-IP header
    real_ip = request.headers.get('X-Real-IP')
    if real_ip:
        return real_ip

    # use direct connection IP
    return request.client.host if request.client else '127.0.0.1'


class Fail2BanManager(metaclass=SingletonNamed):
    """Manages IP banning based on failed login attempts"""

    def __init__(self, name):
        """
        Args:
            name (str): name for different fail2ban manager
                name is used for SingletonNamed
        """
        self.name = name
        # fail2ban configuration
        # maximum failed attempts before ban, must > 1
        self.max_attempts = 5
        # ban duration in seconds (10 minutes)
        self.ban_duration = 600
        # failure counting window in seconds (10 minutes)
        self.failure_window = 600

        # storage: {ip: (attempts, first_attempt_time)}
        self.failed_attempts: "dict[str, tuple[int, float]]" = {}
        # ban storage: {ip: ban_end_time}
        self.banned_ips: "dict[str, float]" = {}

    async def gc(self):
        """
        Periodically clean up expired data
        """
        while True:
            # cleanup every 5 minutes
            await trio.sleep(300)
            now = time.time()

            # clean expired ban records
            ip_list = list(self.banned_ips)
            for ip in ip_list:
                try:
                    end_time = self.banned_ips[ip]
                except KeyError:
                    continue
                if now > end_time:
                    try:
                        del self.banned_ips[ip]
                    except KeyError:
                        pass

            # clean expired failure attempt records
            ip_list = list(self.failed_attempts)
            window = now - self.failure_window
            for ip in ip_list:
                try:
                    attempts, first_time = self.failed_attempts[ip]
                except KeyError:
                    continue
                if window > first_time:
                    try:
                        del self.failed_attempts[ip]
                    except KeyError:
                        pass


class Fail2Ban:
    def __init__(self, name):
        """
        Args:
            name (str): Endpoint
        """
        self.name = name
        self.ip: "str | None" = None

    def check_ban(self):
        """
        Raises:
            HTTPExceptionJson: HTTP_403_FORBIDDEN if IP banned
        """
        if self.ip is None:
            return
        manager = Fail2BanManager(self.name)

        # No ban
        if self.ip not in manager.banned_ips:
            return

        try:
            end_time = manager.banned_ips[self.ip]
        except KeyError:
            # No ban, race condition that record is clean up
            return
        now = time.time()
        if now < end_time:
            # still in ban, extend end time
            manager.banned_ips[self.ip] = now + manager.ban_duration
            raise HTTPExceptionJson(
                status.HTTP_403_FORBIDDEN,
                detail={
                    'msg': 'banned',
                    'remain': 0,
                    'after': manager.ban_duration,
                }
            )
        else:
            # unban expired IP
            # ignore KeyError to handle race condition that record is clean up
            try:
                del manager.banned_ips[self.ip]
            except KeyError:
                pass

    def record_failure(self, detail='failure') -> HTTPException:
        """
        Record a failed login attempt

        Raises:
            HTTPExceptionJson: HTTP_401_UNAUTHORIZED or HTTP_403_FORBIDDEN if IP banned
        """
        # this shouldn't happen
        assert self.ip is not None
        manager = Fail2BanManager(self.name)
        now = time.time()

        # first failure
        if self.ip not in manager.failed_attempts:
            manager.failed_attempts[self.ip] = (1, now)
            return HTTPExceptionJson(
                status.HTTP_401_UNAUTHORIZED,
                detail={
                    'msg': detail,
                    'remain': manager.max_attempts - 1,
                    'after': manager.ban_duration,
                }
            )

        # within failure window
        attempts, first_time = manager.failed_attempts[self.ip]
        if now - first_time <= manager.failure_window:
            attempts += 1
            if attempts >= manager.max_attempts:
                # ban IP
                manager.banned_ips[self.ip] = now + manager.ban_duration
                try:
                    del manager.failed_attempts[self.ip]
                except KeyError:
                    pass
                return HTTPExceptionJson(
                    status.HTTP_403_FORBIDDEN,
                    detail={
                        'msg': 'banned',
                        'remain': 0,
                        'after': manager.ban_duration,
                    }
                )
            else:
                # increase attempt count
                manager.failed_attempts[self.ip] = (attempts, first_time)
                return HTTPExceptionJson(
                    status.HTTP_401_UNAUTHORIZED,
                    detail={
                        'msg': detail,
                        'remain': manager.max_attempts - attempts,
                        'after': manager.ban_duration,
                    }
                )
        else:
            # reset count outside window
            manager.failed_attempts[self.ip] = (1, now)
            return HTTPExceptionJson(
                status.HTTP_401_UNAUTHORIZED,
                detail={
                    'msg': detail,
                    'remain': manager.max_attempts - 1,
                    'after': manager.ban_duration,
                }
            )

    def record_success(self):
        """
        Record successful login and clear failure records
        """
        if self.ip is None:
            return
        manager = Fail2BanManager(self.name)

        if self.ip in manager.failed_attempts:
            try:
                del manager.failed_attempts[self.ip]
            except KeyError:
                # race condition that record is clean up
                pass

    async def __call__(self, request: Request):
        self.ip = get_client_ip(request)
        return self
