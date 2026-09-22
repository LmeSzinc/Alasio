"""
Tests for DeploymentGateMiddleware (merged admission + login gate).

The middleware is the single web entrance: rule A (no password -> only
electron token passes), rule B (lan mode -> lan sources only) and the
login layer (JWT cookie) run inside one middleware in a fixed order
(admission first, then login). It must never be split or reordered.
"""

import pytest
from starlette.datastructures import URL

from alasio.backend.auth.auth import JWT_MANAGER
from alasio.backend.middleware.gate import DeploymentGateMiddleware
from alasio.backend.mpipe.token_backend import token_table


class CaptureApp:
    """Records whether the inner app was reached."""

    def __init__(self):
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b''})


def make_scope(scope_type, path='/api/test', host='127.0.0.1', headers=None, scheme='http', query_string=b'',
               server=('127.0.0.1', 22267)):
    """
    Build a minimal ASGI scope.

    Args:
        scope_type (str): 'http' or 'websocket'
        path (str):
        host (str): The TCP peer address
        headers (list): Raw header pairs
        scheme (str): 'http', 'https', 'ws' or 'wss'
        query_string (bytes):
        server (tuple | None): The ASGI server address

    Returns:
        Scope:
    """
    return {
        'type': scope_type,
        'method': 'GET',
        'path': path,
        'headers': headers or [],
        'client': (host, 12345),
        'query_string': query_string,
        'scheme': scheme,
        'server': server,
    }


def make_http_scope(**kwargs):
    return make_scope('http', **kwargs)


def make_ws_scope(**kwargs):
    return make_scope('websocket', **kwargs)


async def call_gate(mw, scope):
    """
    Run the middleware against a scope and collect the sent messages.

    Args:
        mw (DeploymentGateMiddleware):
        scope (Scope):

    Returns:
        list: The messages sent through `send`
    """
    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    await mw(scope, receive, send)
    return sent


def status_of(sent):
    """
    Args:
        sent (list): Messages sent by the middleware

    Returns:
        int | None: The http response status, or None if none was sent
    """
    for message in sent:
        if message['type'] == 'http.response.start':
            return message['status']
    return None


def ws_closed(sent):
    """
    Args:
        sent (list): Messages sent by the middleware

    Returns:
        int | None: The websocket close code, or None if not closed
    """
    for message in sent:
        if message['type'] == 'websocket.close':
            return message['code']
    return None


def redirect_of(sent):
    """
    Extract the redirect (http or websocket denial response) from the
    sent messages.

    Args:
        sent (list): Messages sent by the middleware

    Returns:
        tuple[int, dict] | None: (status, header dict) when a redirect
            response was sent, or None otherwise
    """
    for message in sent:
        if message['type'] in ('http.response.start', 'websocket.http.response.start'):
            return message['status'], dict(message.get('headers') or [])
    return None


class FakeBackend:
    def __init__(self, ssl):
        self.WebuiSSLCert = 'cert.pem' if ssl else ''
        self.WebuiSSLKey = 'key.pem' if ssl else ''


class FakeDeployData:
    def __init__(self, ssl):
        self.Backend = FakeBackend(ssl)


@pytest.fixture(autouse=True)
def reset_token_table():
    """The token table is a module-level singleton; clear it per test."""
    yield
    token_table._tokens.clear()


@pytest.fixture(autouse=True)
def auth_env(monkeypatch):
    """Default: no password configured; tests override as needed."""
    monkeypatch.setattr(JWT_MANAGER, 'secret', b'test-secret')
    monkeypatch.setattr(JWT_MANAGER, 'pwd', '')
    yield
    monkeypatch.undo()


@pytest.fixture
def gate(monkeypatch):
    """Factory: build a middleware + CaptureApp pair with a fake deploy data."""

    def make(ssl=False):
        app = CaptureApp()
        mw = DeploymentGateMiddleware(app)
        monkeypatch.setattr(
            DeploymentGateMiddleware, '_deploy_data',
            staticmethod(lambda: FakeDeployData(ssl)),
        )
        return mw, app

    return make


def set_password(monkeypatch, pwd='secret'):
    monkeypatch.setattr(JWT_MANAGER, 'pwd', pwd)


def valid_cookie_header():
    token = JWT_MANAGER.create()
    return [(b'cookie', b'alasio_token=' + token.encode())]


class TestRuleA:
    """No password: only requests carrying a valid electron token pass."""

    @pytest.mark.trio
    async def test_http_without_token_403(self, gate):
        mw, app = gate()
        sent = await call_gate(mw, make_http_scope())
        assert status_of(sent) == 403
        assert not app.called

    @pytest.mark.trio
    async def test_http_with_forged_token_403(self, gate):
        mw, app = gate()
        sent = await call_gate(mw, make_http_scope(headers=[(b'x-alasio-token', b'forged')]))
        assert status_of(sent) == 403
        assert not app.called

    @pytest.mark.trio
    async def test_http_with_valid_token_passes(self, gate):
        token_table.seed_from_supervisor(('tok1',))
        mw, app = gate()
        sent = await call_gate(mw, make_http_scope(headers=[(b'x-alasio-token', b'tok1')]))
        assert status_of(sent) == 200
        assert app.called

    @pytest.mark.trio
    async def test_static_resources_pass_without_token(self, gate):
        mw, app = gate()
        sent = await call_gate(mw, make_http_scope(path='/favicon.png'))
        assert status_of(sent) == 200
        assert app.called

    @pytest.mark.trio
    async def test_ws_without_token_rejected_4001(self, gate):
        mw, app = gate()
        sent = await call_gate(mw, make_ws_scope())
        assert ws_closed(sent) == 4001
        assert not app.called

    @pytest.mark.trio
    async def test_ws_with_valid_token_passes(self, gate):
        token_table.seed_from_supervisor(('tok1',))
        mw, app = gate()
        sent = await call_gate(mw, make_ws_scope(headers=[(b'x-alasio-token', b'tok1')]))
        assert ws_closed(sent) is None
        assert app.called


class TestRuleB:
    """Lan mode (no SSL): only lan sources pass."""

    # the exempt path skips the login layer so only the source check runs
    PATH = '/api/auth/renew'

    @pytest.mark.parametrize('host', [
        '127.0.0.1', '::1', '10.1.2.3', '172.16.0.1', '172.31.255.255',
        '192.168.1.5', '169.254.1.1', 'fe80::1', 'fd00::1',
    ])
    @pytest.mark.trio
    async def test_lan_sources_allowed(self, gate, monkeypatch, host):
        set_password(monkeypatch)
        mw, app = gate()
        await call_gate(mw, make_http_scope(path=self.PATH, host=host))
        assert app.called

    @pytest.mark.parametrize('host', [
        '8.8.8.8', '100.64.0.1', '2001:db8::1', '203.0.113.5',
    ])
    @pytest.mark.trio
    async def test_public_sources_rejected(self, gate, monkeypatch, host):
        set_password(monkeypatch)
        mw, app = gate()
        sent = await call_gate(mw, make_http_scope(path=self.PATH, host=host))
        assert status_of(sent) == 403
        assert not app.called

    @pytest.mark.trio
    async def test_public_mode_skips_source_check(self, gate, monkeypatch):
        set_password(monkeypatch)
        mw, app = gate(ssl=True)
        # public mode only serves https; plaintext is redirected by
        # rule C before the source check would run
        await call_gate(mw, make_http_scope(path=self.PATH, host='8.8.8.8', scheme='https'))
        assert app.called

    @pytest.mark.trio
    async def test_unknown_host_refused(self, gate, monkeypatch):
        set_password(monkeypatch)
        mw, app = gate()
        sent = await call_gate(mw, make_http_scope(path=self.PATH, host='not-an-ip'))
        assert status_of(sent) == 403
        assert not app.called


class TestLoginLayer:
    """Login layer (JWT cookie) on /api http routes."""

    @pytest.mark.trio
    async def test_no_jwt_401(self, gate, monkeypatch):
        set_password(monkeypatch)
        mw, app = gate()
        sent = await call_gate(mw, make_http_scope())
        assert status_of(sent) == 401
        assert not app.called

    @pytest.mark.trio
    async def test_invalid_jwt_401(self, gate, monkeypatch):
        set_password(monkeypatch)
        mw, app = gate()
        sent = await call_gate(mw, make_http_scope(headers=[(b'cookie', b'alasio_token=not-a-jwt')]))
        assert status_of(sent) == 401
        assert not app.called

    @pytest.mark.trio
    async def test_valid_jwt_passes(self, gate, monkeypatch):
        set_password(monkeypatch)
        mw, app = gate()
        await call_gate(mw, make_http_scope(headers=valid_cookie_header()))
        assert app.called

    @pytest.mark.trio
    async def test_exempt_endpoints_skip_login(self, gate, monkeypatch):
        set_password(monkeypatch)
        mw, app = gate()
        # no JWT cookie, but /api/auth/login performs the auth itself
        await call_gate(mw, make_http_scope(path='/api/auth/login'))
        assert app.called

    @pytest.mark.trio
    async def test_ws_scope_not_login_gated(self, gate, monkeypatch):
        # the ws handshake validates the JWT inside serve() so it can
        # close(4001) after accept; the middleware must pass it through
        set_password(monkeypatch)
        mw, app = gate()
        sent = await call_gate(mw, make_ws_scope())
        assert ws_closed(sent) is None
        assert app.called


def _host_header_is_sanitized():
    """
    Check whether starlette drops a host header that fails its host
    syntax check (1.0.1+) and builds the url from the server address.

    Returns:
        bool: True when a malformed host header still builds a valid url
    """
    scope = {
        'type': 'http',
        'scheme': 'http',
        'path': '/api/test',
        'query_string': b'',
        'headers': [(b'host', b'[bad')],
        'server': ('127.0.0.1', 22267),
    }
    try:
        URL(scope=scope).hostname
    except ValueError:
        # the raw header value was used as the url host and it is not
        # a host, so the url cannot be built at all
        return False
    return True


# 0.44.0 (the last upstream version with python 3.8 support) keeps the
# raw host header, 1.6.0 (the py38deps backport) sanitizes it
HOST_HEADER_SANITIZED = _host_header_is_sanitized()


class TestRuleC:
    """Rule C (SSL enforcement): public mode never serves plaintext."""

    @pytest.mark.trio
    async def test_http_plaintext_redirected_308(self, gate):
        # no host header: falls back to the server address (with port)
        mw, app = gate(ssl=True)
        sent = await call_gate(mw, make_http_scope(scheme='http'))
        status_code, headers = redirect_of(sent)
        assert status_code == 308
        assert headers[b'location'] == b'https://127.0.0.1:22267/api/test'
        assert not app.called

    @pytest.mark.trio
    async def test_ws_plaintext_redirected_307(self, gate):
        # websocket denial response: the browser re-handshakes over wss
        mw, app = gate(ssl=True)
        sent = await call_gate(mw, make_ws_scope(scheme='ws'))
        status_code, headers = redirect_of(sent)
        assert status_code == 307
        assert headers[b'location'] == b'wss://127.0.0.1:22267/api/test'
        assert not app.called

    @pytest.mark.trio
    async def test_redirect_keeps_host_port_and_query(self, gate):
        mw, app = gate(ssl=True)
        sent = await call_gate(mw, make_http_scope(
            scheme='http',
            query_string=b'x=1&y=2',
            headers=[(b'host', b'example.com:22267')],
        ))
        status_code, headers = redirect_of(sent)
        assert status_code == 308
        assert headers[b'location'] == b'https://example.com:22267/api/test?x=1&y=2'
        assert not app.called

    @pytest.mark.skipif(HOST_HEADER_SANITIZED, reason='starlette 1.0.1+ sanitizes the host header')
    @pytest.mark.trio
    async def test_redirect_refused_when_host_malformed(self, gate):
        # 0.44.0 keeps the raw host header value: a malformed one cannot
        # build a redirect target, the request is refused with 400
        mw, app = gate(ssl=True)
        sent = await call_gate(mw, make_http_scope(
            scheme='http',
            headers=[(b'host', b'[bad')],
        ))
        assert status_of(sent) == 400
        assert not app.called

    @pytest.mark.skipif(not HOST_HEADER_SANITIZED, reason='starlette < 1.0.1 keeps the raw host header')
    @pytest.mark.parametrize('host_header', [
        # a malformed host header, an unclosed ipv6 bracket
        b'[bad',
        # a host header http cannot carry: the host is the punycode of
        # the idn (0.44.0 echoes the raw utf-8 form into the url)
        '例子.测试'.encode('utf-8'),
    ])
    @pytest.mark.trio
    async def test_redirect_ignores_invalid_host(self, gate, host_header):
        # 1.0.1+ drops a host header that fails its host syntax check and
        # builds the url from the server address: the connection is still
        # upgraded, the header value never reaches the redirect target
        mw, app = gate(ssl=True)
        sent = await call_gate(mw, make_http_scope(
            scheme='http',
            headers=[(b'host', host_header)],
        ))
        status_code, headers = redirect_of(sent)
        assert status_code == 308
        assert headers[b'location'] == b'https://127.0.0.1:22267/api/test'
        assert not app.called

    @pytest.mark.trio
    async def test_redirect_refused_without_server_address(self, gate):
        # neither a host header nor a server address: there is nothing
        # to redirect to, the plaintext connection is refused with 400
        mw, app = gate(ssl=True)
        sent = await call_gate(mw, make_http_scope(scheme='http', server=None))
        assert status_of(sent) == 400
        assert not app.called

    @pytest.mark.trio
    async def test_redirect_refused_when_server_address_is_bare_ipv6(self, gate):
        # a bare ipv6 server address yields no hostname (an url
        # authority needs the brackets), the url would be unusable:
        # 400 instead of a redirect target the browser cannot resolve
        mw, app = gate(ssl=True)
        sent = await call_gate(mw, make_http_scope(
            scheme='http',
            headers=[(b'host', b'[bad')],
            server=('::1', 22267),
        ))
        assert status_of(sent) == 400
        assert not app.called

    @pytest.mark.trio
    async def test_https_passes_with_hsts(self, gate, monkeypatch):
        set_password(monkeypatch)
        mw, app = gate(ssl=True)
        # /api/auth/renew is exempt from the login layer: only the
        # scheme enforcement + admission rules run
        sent = await call_gate(mw, make_http_scope(scheme='https', path='/api/auth/renew'))
        assert status_of(sent) == 200
        assert app.called
        status_code, headers = redirect_of(sent)
        assert headers[b'strict-transport-security'] == b'max-age=31536000'

    @pytest.mark.trio
    async def test_wss_passes(self, gate, monkeypatch):
        set_password(monkeypatch)
        mw, app = gate(ssl=True)
        sent = await call_gate(mw, make_ws_scope(scheme='wss'))
        assert ws_closed(sent) is None
        assert app.called

    @pytest.mark.trio
    async def test_hsts_not_added_to_plaintext_redirect(self, gate):
        # HSTS only appears on https responses (browsers ignore it on
        # plaintext responses anyway)
        mw, app = gate(ssl=True)
        sent = await call_gate(mw, make_http_scope(scheme='http'))
        status_code, headers = redirect_of(sent)
        assert status_code == 308
        assert b'strict-transport-security' not in headers

    @pytest.mark.trio
    async def test_plaintext_allowed_in_lan_mode(self, gate, monkeypatch):
        # no SSL configured: http/ws keep working, no redirect, no HSTS
        set_password(monkeypatch)
        mw, app = gate(ssl=False)
        sent = await call_gate(mw, make_http_scope(scheme='http', path='/api/auth/renew'))
        assert status_of(sent) == 200
        assert app.called
        status_code, headers = redirect_of(sent)
        assert b'strict-transport-security' not in headers


class TestRuleOrder:
    """Admission rules run before the login layer (403 beats 401)."""

    @pytest.mark.trio
    async def test_no_password_no_token_is_403_not_401(self, gate):
        # default fixture: no password -> rule A rejects before the
        # login layer would answer 401
        mw, app = gate()
        sent = await call_gate(mw, make_http_scope())
        assert status_of(sent) == 403
        assert not app.called

    @pytest.mark.trio
    async def test_rule_b_runs_before_login(self, gate, monkeypatch):
        # public source + valid jwt: rule B rejects with 403, the login
        # layer never runs (a valid jwt would have passed it)
        set_password(monkeypatch)
        mw, app = gate()
        sent = await call_gate(mw, make_http_scope(host='8.8.8.8', headers=valid_cookie_header()))
        assert status_of(sent) == 403
        assert not app.called


class TestNonHttpScope:
    """Non http/websocket scopes (lifespan) pass through."""

    @pytest.mark.trio
    async def test_lifespan_scope_passes(self, gate):
        mw, app = gate()
        sent = []

        async def send(message):
            sent.append(message)

        async def receive():
            return {'type': 'lifespan.startup'}

        await mw({'type': 'lifespan'}, receive, send)
        assert app.called
