import http.cookies
import ipaddress

import jwt
from starlette import status
from starlette.datastructures import URL
from starlette.responses import RedirectResponse, Response

from alasio.backend.mpipe.token_backend import token_table
from alasio.ext.cache import cached_property
from alasio.ext.starapi.param import error_detail

# Rule C: when SSL is configured, plaintext schemes are upgraded to
# their TLS counterparts and never reach the business logic.
_REDIRECT_SCHEMES = {'http': 'https', 'ws': 'wss'}

# HSTS value injected into https responses. One year, no
# includeSubDomains (subdomains may host non-https services).
_HSTS_VALUE = b'max-age=31536000'

# LAN source whitelist (rule B). Explicit network objects instead of the
# ipaddress `is_private` / `is_ula` convenience flags: their exact
# coverage differs across python versions (is_ula only exists on 3.9+,
# IPv6 is_private changed meaning), the explicit list is version-proof.
_LAN_NETS = [
    # RFC1918 private segments
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
    # link-local
    ipaddress.ip_network('169.254.0.0/16'),
    ipaddress.ip_network('fe80::/10'),
    # unique local addresses (ULA)
    ipaddress.ip_network('fc00::/7'),
]

# loopback host names accepted by the source check
_LOOPBACK_HOSTS = ('127.0.0.1', '::1', 'localhost')


def _is_lan_source(host):
    """
    Check whether a client source belongs to the trusted lan set:
    loopback + RFC1918 + link-local + ULA. There is no configurable
    exception whitelist: the set is fixed.

    Args:
        host (str): The TCP peer address (request.client.host)

    Returns:
        bool: True when the source is allowed in lan mode
    """
    if host in _LOOPBACK_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # not an ip address (e.g. a hostname), refuse to be safe
        return False
    if ip.is_loopback:
        return True
    for net in _LAN_NETS:
        if ip in net:
            return True
    return False


class DeploymentGateMiddleware:
    """
    Global admission + login middleware covering both http and
    websocket scopes.

    The middleware merges four layers that must always run together,
    in this fixed order (they must never be split or reordered):

    0. Rule C (SSL enforcement): when SSL is configured (public mode),
       every plaintext connection (http/ws) is upgraded to https/wss
       with a redirect and never reaches the business logic. It runs
       before everything else, so no rule below can ever serve
       business over plaintext. The TCP layer already rejects plaintext
       (hypercorn serves the port TLS-only when ssl is configured);
       this rule is the application-layer enforcement for any plaintext
       that does reach the app, and the HSTS header it injects into
       https responses makes the browser upgrade http itself after the
       first https visit.

    1. Rule A (no password → refuse all web access): when the password
       is empty, only requests carrying a valid electron token
       (X-Alasio-Token verified against the backend token table) pass;
       all other business access (/api/*, /api/ws, /api/preview) → 403.
       Static resources (non-/api prefix) pass so the config guidance
       page renders.
    2. Rule B (lan mode refuses public sources): in lan mode business
       access is source-checked with `ipaddress`: loopback, RFC1918,
       link-local and ULA pass; everything else (including CGNAT
       100.64/10 and public IPv6) → 403. The lan set is fixed, there is
       no configurable exception whitelist. public mode does not check
       sources.
    3. Login layer (JWT cookie): /api http routes require a valid JWT
       in the alasio_token cookie → 401, except the endpoints that
       perform the authentication themselves (/api/auth/login,
       /api/auth/renew). Websocket scopes are NOT login-gated here: the
       handshake validates the JWT inside serve() so it can close(4001)
       after accept (a refused handshake only surfaces as 1006 to the
       browser, the frontend cannot read the close code).

    The order matters: rule C runs first, then rule A before the login
    layer, so without a password the rejection is a 403 from rule A
    (never a 401), and the login layer's no-password leniency
    (validate_token issues a fresh token when no password is
    configured) is only reachable behind rule A — the merged middleware
    makes this ordering structural instead of depending on
    add_middleware order.

    Mode detection: both WebuiSSLCert and WebuiSSLKey configured → public;
    otherwise → lan. The two modes behave identically except SSL.

    Relationship between the rules: rule A only trusts the electron token
    (source-independent → an XFF-forged 127.0.0.1 carries no privilege,
    a reverse proxy disguising the source gains nothing); rule B only
    trusts the source. They stack independently.

    Boundaries: a local TCP forwarder (frp / ngrok / unencrypted reverse
    proxy) presents a loopback source and bypasses rule B → the user is
    actively exposing the service on their own machine, risk is theirs;
    router NAT port forwarding presents a public source and is rejected
    in lan mode (the difference from frp is whether the forwarding
    happens on this machine); CGNAT users are rejected in lan mode →
    configure SSL for real remote access.
    """

    # endpoints that perform the authentication themselves
    EXEMPT = {'/api/auth/login', '/api/auth/renew'}

    def __init__(self, app):
        """
        Args:
            app (ASGIApp):
        """
        self.app = app

    @staticmethod
    def _deploy_data():
        from alasio.deploy.config.model import DeployConfig
        return DeployConfig().config.data

    @cached_property
    def _is_public(self):
        """
        Returns:
            bool: True when SSL is configured (public mode)

        Cached: the deploy config is read once per middleware instance;
        changing the SSL settings requires a backend restart to take
        effect.
        """
        backend = self._deploy_data().Backend
        return bool(backend.WebuiSSLCert and backend.WebuiSSLKey)

    @cached_property
    def _has_password(self):
        """
        Returns:
            bool: True when a password is configured

        Cached: mirrors JwtManager.pwd (itself cached), so changing the
        password requires a backend restart to take effect.
        """
        from alasio.backend.auth.auth import JWT_MANAGER

        return bool(JWT_MANAGER.pwd)

    def _has_electron_token(self, scope):
        """
        Args:
            scope (Scope):

        Returns:
            bool: True when the request carries a valid electron token
        """

        return token_table.verify_header(scope)

    def _client_host(self, scope):
        """
        Args:
            scope (Scope):

        Returns:
            str: The TCP peer address, or '' when unavailable
        """
        client = scope.get('client')
        if client:
            host = client[0]
            return host if isinstance(host, str) else str(host)
        return ''

    def _is_plaintext(self, scope):
        """
        Args:
            scope (Scope):

        Returns:
            bool: True when the connection arrived without TLS
        """
        return scope.get('scheme') in _REDIRECT_SCHEMES

    def _redirect_url(self, scope):
        """
        Build the https/wss url that mirrors the current plaintext
        request, keeping the host header (with port), path and query.

        starlette builds the url from the scope: a host header that
        passes its host syntax check is kept as is, a missing or
        malformed one is dropped and the server address is used
        instead (1.0.1+). 0.44.0 keeps the raw header value, which
        makes such a request unbuildable (see Returns).

        Args:
            scope (Scope):

        Returns:
            str | None: The redirect target, or None when no usable
                target can be built: a url without a hostname (no host
                header and no server address, or a server address that
                yields none, e.g. a bare ipv6 address) or a url
                starlette cannot parse
        """
        try:
            url = URL(scope=scope)
            # the guard covers the host header starlette dropped and the
            # host it never had: without a hostname the url is relative
            # or carries an unusable authority (a bare ipv6 address)
            if not url.hostname:
                return None
            return str(url.replace(scheme=_REDIRECT_SCHEMES[url.scheme]))
        except (KeyError, ValueError):
            return None

    async def _redirect_plaintext(self, scope, receive, send):
        """
        Upgrade a plaintext connection in public mode: 308 for http
        (permanent, cached by the browser), 307 for websocket (the
        browser re-handshakes over wss after the redirect). When the
        redirect target cannot be determined the connection is refused
        with 400 instead.

        Args:
            scope (Scope):
            receive (Receive):
            send (Send):
        """
        url = self._redirect_url(scope)
        if url is None:
            response = Response(status_code=status.HTTP_400_BAD_REQUEST)
            await response(scope, receive, send)
            return
        if scope['type'] == 'http':
            status_code = status.HTTP_308_PERMANENT_REDIRECT
        else:
            status_code = status.HTTP_307_TEMPORARY_REDIRECT
        response = RedirectResponse(url, status_code=status_code)
        await response(scope, receive, send)

    @staticmethod
    def _with_hsts(send):
        """
        Wrap `send` to inject the Strict-Transport-Security header into
        every http response (only reachable over https, see __call__).

        Args:
            send (Send):

        Returns:
            Send: The wrapped send callable
        """

        async def wrapped(message):
            if message['type'] == 'http.response.start':
                message = dict(message)
                headers = list(message.get('headers') or [])
                if not any(
                    name.lower() == b'strict-transport-security' for name, _ in headers
                ):
                    headers.append((b'strict-transport-security', _HSTS_VALUE))
                message['headers'] = headers
            await send(message)

        return wrapped

    def _check(self, scope):
        """
        Apply rule A then rule B.

        Args:
            scope (Scope):

        Returns:
            tuple[str, dict]: (err key, data) when rejected, or
                ('', {}) when allowed
        """
        # Rule A: no password → only electron token passes
        if not self._has_password and not self._has_electron_token(scope):
            return 'DEPLOY_PASSWORD_NOT_SET', {}
        # Rule B: lan mode → source check
        if not self._is_public:
            host = self._client_host(scope)
            if not _is_lan_source(host):
                # never expose the rejected source to the client
                return 'DEPLOY_CERT_NOT_SET', {}
        return '', {}

    def _read_cookie(self, scope):
        """
        Read the alasio_token cookie from the raw scope headers.

        Args:
            scope (Scope):

        Returns:
            str: The cookie value, or '' when absent / unparsable
        """
        for name, value in scope.get('headers', []):
            if name != b'cookie':
                continue
            cookie = http.cookies.SimpleCookie()
            try:
                cookie.load(value.decode('latin-1'))
            except http.cookies.CookieError:
                return ''
            morsel = cookie.get('alasio_token')
            if morsel is not None:
                return morsel.value
        return ''

    def _check_login(self, scope):
        """
        Validate the login layer (JWT cookie) for an http scope.

        Args:
            scope (Scope):

        Returns:
            bool: True when the JWT cookie is valid
        """
        from alasio.backend.auth.auth import JWT_MANAGER

        token = self._read_cookie(scope)
        try:
            JWT_MANAGER.validate_token(token)
        except jwt.PyJWTError:
            return False
        return True

    async def _reject_http(self, scope, receive, send, err, data=None):
        """
        Send a 403 json response for an http scope.

        Args:
            scope (Scope):
            receive (Receive):
            send (Send):
            err (str): Error key for frontend translation
            data (dict, optional): Extra error data
        """
        response = Response(
            error_detail(err, data),
            status_code=status.HTTP_403_FORBIDDEN,
            media_type='application/json',
        )
        await response(scope, receive, send)

    async def _reject_ws(self, scope, receive, send, message):
        """
        Reject a websocket handshake. The browser only surfaces 1006 for
        a refused handshake; the frontend reconnect loop stops after its
        attempts are exhausted, so a persistently rejected ws settles
        into the login / guidance page instead of looping forever.

        Args:
            scope (Scope):
            receive (Receive):
            send (Send):
            message (str):
        """
        await send({'type': 'websocket.close', 'code': 4001})

    async def __call__(self, scope, receive, send):
        """
        Args:
            scope (Scope):
            receive (Receive):
            send (Send):
        """
        scope_type = scope['type']
        if scope_type not in ('http', 'websocket'):
            await self.app(scope, receive, send)
            return

        # Rule C (SSL enforcement): public mode never serves plaintext.
        # Plaintext connections are upgraded to https/wss, secure http
        # responses get the HSTS header so the browser upgrades http
        # itself on subsequent visits. Only http scopes are wrapped:
        # HSTS is carried by ordinary http responses, a websocket
        # handshake (101) has no HSTS semantics, so ws scopes pass
        # through unwrapped.
        if self._is_public:
            if self._is_plaintext(scope):
                await self._redirect_plaintext(scope, receive, send)
                return
            if scope_type == 'http':
                send = self._with_hsts(send)

        path = scope.get('path', '')
        if not path.startswith('/api'):
            # static resources and the guidance page pass through
            await self.app(scope, receive, send)
            return

        # Rules A + B (admission) first: 403 / 4001
        err, data = self._check(scope)
        if err:
            if scope_type == 'http':
                await self._reject_http(scope, receive, send, err, data)
            else:
                await self._reject_ws(scope, receive, send, err)
            return

        # Login layer (http only; the ws handshake validates inside
        # serve() so it can close(4001) after accept)
        if scope_type == 'http' and path not in self.EXEMPT:
            if not self._check_login(scope):
                response = Response(
                    error_detail('AUTH_TOKEN_INVALID'),
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    headers={'WWW-Authenticate': 'Bearer'},
                    media_type='application/json',
                )
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)
