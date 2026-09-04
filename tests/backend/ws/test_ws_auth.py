"""
Electron-layer ws auth tests:

- Login layer: no/invalid JWT cookie handshake → close(4001) after accept
- Electron layer: restricted topic subscribe rejected (connection kept),
  restricted rpc rejected with ElectronOnlyError response carrying rpc_id
- Valid electron token → everything works
"""

import msgspec
import pytest
import trio
from starlette.websockets import WebSocketState

from alasio.backend.auth.auth import JWT_MANAGER
from alasio.backend.mpipe.token_backend import token_table
from alasio.backend.reactive.base_rpc import rpc
from alasio.backend.reactive.event import RequestEvent
from alasio.backend.ws.ws_server import WebsocketTopicServer
from alasio.backend.ws.ws_topic import BaseTopic
from tests.backend.ws.helpers import FakeTopicSource, ServerHarness

SECRET = b'test-secret'
PASSWORD = 'test-password'


@pytest.fixture(autouse=True)
def reset_token_table():
    """The token table is a module-level singleton; clear it per test."""
    yield
    token_table._tokens.clear()


@pytest.fixture(autouse=True)
def auth_env(monkeypatch):
    """Configure a password so the login layer is enforced."""
    monkeypatch.setattr(JWT_MANAGER, 'secret', SECRET)
    monkeypatch.setattr(JWT_MANAGER, 'pwd', PASSWORD)
    yield
    # restore the cached_property descriptors
    monkeypatch.undo()


class RestrictedTopic(BaseTopic):
    """A topic-level restricted topic (REQUIRE_ELECTRON = True)."""
    NAME = 'restricted'
    REQUIRE_ELECTRON = True

    def __init__(self, conn_id, server):
        super().__init__(conn_id, server)
        self.source = FakeTopicSource(self.topic_name(), {'x': 1})

    async def get_source(self):
        return self.source

    @rpc
    async def secret_op(self):
        return 'ok'


class MixedTopic(BaseTopic):
    """A public topic with one electron-only rpc."""
    NAME = 'mixed'

    def __init__(self, conn_id, server):
        super().__init__(conn_id, server)
        self.source = FakeTopicSource(self.topic_name(), {'m': 1})

    async def get_source(self):
        return self.source

    @rpc
    async def public_op(self):
        return 'ok'

    @rpc(require_electron=True)
    async def private_op(self):
        return 'secret'


class AuthHarnessServer(WebsocketTopicServer):
    ALL_TOPIC_CLASS = {
        RestrictedTopic.topic_name(): RestrictedTopic,
        MixedTopic.topic_name(): MixedTopic,
    }
    DEFAULT_TOPIC_CLASS = {}


class AuthHarness(ServerHarness):
    def __init__(self, headers=None, cookies=None):
        super().__init__(server_cls=AuthHarnessServer)
        self.fake_ws.headers = headers or {}
        self.fake_ws.cookies = cookies or {}

    @staticmethod
    def valid_jwt():
        return JWT_MANAGER.create()

    def send(self, event: RequestEvent):
        self.fake_ws.send_message(msgspec.json.encode(event))

    def send_sub(self, topic):
        self.send(RequestEvent(t=topic))

    def send_rpc(self, topic, func, value=None, rpc_id='r1'):
        self.send(RequestEvent(t=topic, o='rpc', f=func, v=value or {}, i=rpc_id))


def event_dicts(fake_ws):
    """Decode all JSON events sent, ignoring raw bytes (heartbeat ping)."""
    events = []
    for data in fake_ws.sent:
        try:
            events.append(msgspec.json.decode(data))
        except msgspec.DecodeError:
            continue
    return events


class TestLoginLayer:
    @pytest.mark.trio
    async def test_no_cookie_no_token_closes_4001(self):
        """No JWT cookie and no electron token → close(4001) after accept."""
        harness = AuthHarness()
        await harness.server.serve()
        assert (4001, 'Login required') in harness.fake_ws.closed
        # accepted first, then closed: the browser can read the close code
        assert harness.fake_ws.application_state == WebSocketState.DISCONNECTED

    @pytest.mark.trio
    async def test_invalid_cookie_closes_4001(self):
        """A malformed JWT cookie → close(4001)."""
        harness = AuthHarness(cookies={'alasio_token': 'not-a-jwt'})
        await harness.server.serve()
        assert (4001, 'Login required') in harness.fake_ws.closed

    @pytest.mark.trio
    async def test_valid_jwt_connects(self):
        """A valid JWT cookie connects; restricted ops are still refused."""
        harness = AuthHarness(cookies={'alasio_token': AuthHarness.valid_jwt()})
        async with trio.open_nursery() as nursery:
            nursery.start_soon(harness.run_serve)
            await harness.wait_connected()
            # public topic subscribable
            harness.send_sub('mixed')
            await trio.sleep(0.1)
            assert harness.fake_ws.application_state == WebSocketState.CONNECTED
            # restricted rpc refused with ElectronOnlyError + rpc_id
            harness.send_rpc('mixed', 'private_op', rpc_id='r1')
            await trio.sleep(0.1)
            errors = [e for e in event_dicts(harness.fake_ws) if e.get('i') == 'r1']
            assert len(errors) == 1
            assert 'ElectronOnlyError' in errors[0]['v']
            await harness.stop()

    @pytest.mark.trio
    async def test_electron_token_exempts_login(self):
        """
        A valid electron token (in the table) passes the login layer
        without any JWT cookie.
        """
        token_table.seed_from_supervisor(('tok1',))
        harness = AuthHarness(headers={'X-Alasio-Token': 'tok1'})
        async with trio.open_nursery() as nursery:
            nursery.start_soon(harness.run_serve)
            await harness.wait_connected()
            assert harness.fake_ws.application_state == WebSocketState.CONNECTED
            await harness.stop()


class TestRestrictedTopic:
    @pytest.mark.trio
    async def test_subscribe_refused_connection_kept(self):
        """
        Subscribing a restricted topic without a token: error message
        only, connection stays alive (red line).
        """
        harness = AuthHarness(cookies={'alasio_token': AuthHarness.valid_jwt()})
        async with trio.open_nursery() as nursery:
            nursery.start_soon(harness.run_serve)
            await harness.wait_connected()
            harness.send_sub('restricted')
            await trio.sleep(0.1)
            # error message sent
            errors = [e for e in event_dicts(harness.fake_ws) if e.get('t') == 'error']
            assert len(errors) == 1
            assert 'Topic requires electron' in errors[0]['v']
            # connection stays alive, topic not subscribed
            assert 'restricted' not in harness.server.subscribed
            assert harness.fake_ws.application_state != WebSocketState.DISCONNECTED
            await harness.stop()

    @pytest.mark.trio
    async def test_subscribe_with_token_succeeds(self):
        """With a valid electron token the restricted topic subscribes."""
        token_table.seed_from_supervisor(('tok1',))
        harness = AuthHarness(
            headers={'X-Alasio-Token': 'tok1'},
            cookies={'alasio_token': AuthHarness.valid_jwt()},
        )
        async with trio.open_nursery() as nursery:
            nursery.start_soon(harness.run_serve)
            await harness.wait_connected()
            harness.send_sub('restricted')
            await trio.sleep(0.1)
            assert 'restricted' in harness.server.subscribed
            fulls = [e for e in event_dicts(harness.fake_ws) if e.get('t') == 'restricted' and e.get('o') == 'full']
            assert fulls and fulls[0]['v'] == {'x': 1}
            await harness.stop()


class TestRenewal:
    """ws o='auth' renewal protocol."""

    @pytest.mark.trio
    async def test_valid_code_updates_auth_token(self):
        """A valid code updates the connection's auth_token to current()."""
        import time

        from alasio.backend.ws.renew import renewal_manager

        token_table.seed_from_supervisor(('tok1',))
        # stagger the timestamp so current() resolves deterministically
        token_table._tokens['tok2'] = time.time() + 1
        harness = AuthHarness(
            headers={'X-Alasio-Token': 'tok1'},
            cookies={'alasio_token': AuthHarness.valid_jwt()},
        )
        async with trio.open_nursery() as nursery:
            nursery.start_soon(harness.run_serve)
            await harness.wait_connected()
            assert harness.server.auth_token == 'tok1'
            code = renewal_manager.issue()
            harness.send(RequestEvent(t='', o='auth', v=code))
            await trio.sleep(0.1)
            # updated to the latest token in the table
            assert harness.server.auth_token == 'tok2'
            await harness.stop()

    @pytest.mark.trio
    async def test_invalid_code_rejected_connection_kept(self):
        """An unknown code raises AccessDenied; the connection stays alive."""
        token_table.seed_from_supervisor(('tok1',))
        harness = AuthHarness(
            headers={'X-Alasio-Token': 'tok1'},
            cookies={'alasio_token': AuthHarness.valid_jwt()},
        )
        async with trio.open_nursery() as nursery:
            nursery.start_soon(harness.run_serve)
            await harness.wait_connected()
            harness.send(RequestEvent(t='', o='auth', v='deadbeef'))
            await trio.sleep(0.1)
            errors = [e for e in event_dicts(harness.fake_ws) if e.get('t') == 'error']
            assert len(errors) == 1
            assert 'Invalid or expired renewal code' in errors[0]['v']
            # connection keeps working (public rpc still callable)
            harness.send_sub('mixed')
            await trio.sleep(0.1)
            assert 'mixed' in harness.server.subscribed
            await harness.stop()

    @pytest.mark.trio
    async def test_expired_code_rejected(self):
        """An expired code is consumed and rejected."""
        from alasio.backend.ws.renew import renewal_manager

        token_table.seed_from_supervisor(('tok1',))
        harness = AuthHarness(
            headers={'X-Alasio-Token': 'tok1'},
            cookies={'alasio_token': AuthHarness.valid_jwt()},
        )
        code = renewal_manager.issue()
        # expire the code
        renewal_manager._codes[code] = renewal_manager._clock() - 100
        async with trio.open_nursery() as nursery:
            nursery.start_soon(harness.run_serve)
            await harness.wait_connected()
            harness.send(RequestEvent(t='', o='auth', v=code))
            await trio.sleep(0.1)
            errors = [e for e in event_dicts(harness.fake_ws) if e.get('t') == 'error']
            assert len(errors) == 1
            assert 'Invalid or expired renewal code' in errors[0]['v']
            await harness.stop()

    @pytest.mark.trio
    async def test_rotation_notify_renew(self):
        """
        Rotation check: a restricted-subscription connection with a valid
        token gets the 'renew' control message.
        """
        from alasio.backend.lifespan import notify_rotation

        token_table.seed_from_supervisor(('tok1',))
        harness = AuthHarness(
            headers={'X-Alasio-Token': 'tok1'},
            cookies={'alasio_token': AuthHarness.valid_jwt()},
        )
        async with trio.open_nursery() as nursery:
            nursery.start_soon(harness.run_serve)
            await harness.wait_connected()
            harness.send_sub('restricted')
            await trio.sleep(0.1)
            assert 'restricted' in harness.server.subscribed
            # rotate: tok2 enters, tok1 still in the window
            token_table.handle_token('tok2')
            await notify_rotation()
            await trio.sleep(0.1)
            renews = [e for e in event_dicts(harness.fake_ws) if e.get('t') == 'auth' and e.get('v') == 'renew']
            assert len(renews) == 1
            await harness.stop()

    @pytest.mark.trio
    async def test_rotation_evicted_connection_closed_4002(self):
        """An evicted restricted connection is closed with 4002."""
        from alasio.backend.lifespan import notify_rotation

        token_table.seed_from_supervisor(('tok1',))
        harness = AuthHarness(
            headers={'X-Alasio-Token': 'tok1'},
            cookies={'alasio_token': AuthHarness.valid_jwt()},
        )
        async with trio.open_nursery() as nursery:
            nursery.start_soon(harness.run_serve)
            await harness.wait_connected()
            harness.send_sub('restricted')
            await trio.sleep(0.1)
            # evict tok1 from the window
            token_table.handle_token('tok2')
            token_table.handle_token('tok3')
            assert not token_table.verify('tok1')
            await notify_rotation()
            await trio.sleep(0.1)
            assert (4002, 'token rotated') in harness.fake_ws.closed
            await harness.stop()


class TestRestrictedRpc:
    @pytest.mark.trio
    async def test_restricted_rpc_refused_with_rpc_id(self):
        """Electron-only rpc without a token: ElectronOnlyError + rpc_id."""
        harness = AuthHarness(cookies={'alasio_token': AuthHarness.valid_jwt()})
        async with trio.open_nursery() as nursery:
            nursery.start_soon(harness.run_serve)
            await harness.wait_connected()
            # must subscribe the public topic first (rpc requires subscription)
            harness.send_sub('mixed')
            await trio.sleep(0.1)
            harness.send_rpc('mixed', 'private_op', rpc_id='r2')
            await trio.sleep(0.1)
            responses = [e for e in event_dicts(harness.fake_ws) if e.get('i') == 'r2']
            assert len(responses) == 1
            assert responses[0]['v'] == 'ElectronOnlyError: Electron token required'
            await harness.stop()

    @pytest.mark.trio
    async def test_restricted_rpc_succeeds_with_token(self):
        """With a valid electron token the electron-only rpc executes."""
        token_table.seed_from_supervisor(('tok1',))
        harness = AuthHarness(
            headers={'X-Alasio-Token': 'tok1'},
            cookies={'alasio_token': AuthHarness.valid_jwt()},
        )
        async with trio.open_nursery() as nursery:
            nursery.start_soon(harness.run_serve)
            await harness.wait_connected()
            harness.send_sub('mixed')
            await trio.sleep(0.1)
            harness.send_rpc('mixed', 'private_op', rpc_id='r3')
            await trio.sleep(0.1)
            responses = [e for e in event_dicts(harness.fake_ws) if e.get('i') == 'r3']
            assert len(responses) == 1
            # success: value omitted
            assert 'v' not in responses[0]
            await harness.stop()

    @pytest.mark.trio
    async def test_public_rpc_works_without_token(self):
        """A public rpc on a public topic works without any token."""
        harness = AuthHarness(cookies={'alasio_token': AuthHarness.valid_jwt()})
        async with trio.open_nursery() as nursery:
            nursery.start_soon(harness.run_serve)
            await harness.wait_connected()
            harness.send_sub('mixed')
            await trio.sleep(0.1)
            harness.send_rpc('mixed', 'public_op', rpc_id='r4')
            await trio.sleep(0.1)
            responses = [e for e in event_dicts(harness.fake_ws) if e.get('i') == 'r4']
            assert len(responses) == 1
            assert 'v' not in responses[0]
            await harness.stop()

    @pytest.mark.trio
    async def test_token_evicted_then_rejected(self):
        """
        A token that was valid at handshake but evicted from the table is
        rejected in real time (rotation semantics).
        """
        token_table.seed_from_supervisor(('tok1',))
        harness = AuthHarness(
            headers={'X-Alasio-Token': 'tok1'},
            cookies={'alasio_token': AuthHarness.valid_jwt()},
        )
        async with trio.open_nursery() as nursery:
            nursery.start_soon(harness.run_serve)
            await harness.wait_connected()
            # rotate: tok2 replaces tok1 in the window (max 2, evict oldest)
            token_table.handle_token('tok2')
            token_table.handle_token('tok3')
            assert not token_table.verify('tok1')
            harness.send_sub('restricted')
            await trio.sleep(0.1)
            errors = [e for e in event_dicts(harness.fake_ws) if e.get('t') == 'error']
            assert len(errors) == 1
            assert 'Topic requires electron' in errors[0]['v']
            await harness.stop()


class FakeNotifyServer:
    """Minimal server stub for notify_rotation fault-tolerance tests."""

    ALL_TOPIC_CLASS = {RestrictedTopic.topic_name(): RestrictedTopic}

    def __init__(self, subscribed, auth_token, fail_send=False, hang_send=False, hang_close=False):
        """
        Args:
            subscribed (set[str]): Topic names the connection subscribed
            auth_token (str): The connection's electron token
            fail_send (bool): send() raises
            hang_send (bool): send() never returns
            hang_close (bool): close() never returns
        """
        self.subscribed = subscribed
        self.auth_token = auth_token
        self.sent = []
        self.closed = []
        self.fail_send = fail_send
        self.hang_send = hang_send
        self.hang_close = hang_close

    async def send(self, event):
        if self.fail_send:
            raise RuntimeError('send failed')
        if self.hang_send:
            await trio.sleep_forever()
        self.sent.append(event)

    async def close(self, code, reason=None):
        if self.hang_close:
            await trio.sleep_forever()
        self.closed.append((code, reason))


class TestNotifyRotationFaultTolerance:
    """notify_rotation: one stuck/broken connection must not affect others."""

    @pytest.fixture(autouse=True)
    def clear_active(self):
        WebsocketTopicServer.active.clear()
        yield
        WebsocketTopicServer.active.clear()

    @pytest.mark.trio
    async def test_broken_send_does_not_abort_others(self):
        """A connection whose send() raises is skipped, others still notified."""
        from alasio.backend.lifespan import notify_rotation

        token_table.seed_from_supervisor(('tok1',))
        good = FakeNotifyServer(subscribed={'restricted'}, auth_token='tok1')
        bad = FakeNotifyServer(subscribed={'restricted'}, auth_token='tok1', fail_send=True)
        WebsocketTopicServer.active['good'] = good
        WebsocketTopicServer.active['bad'] = bad
        await notify_rotation()
        assert len(good.sent) == 1
        assert bad.sent == []
        assert good.closed == []

    @pytest.mark.trio
    async def test_hanging_send_times_out_and_others_notified(self, monkeypatch):
        """A connection whose send() hangs is cut off by the timeout."""
        from alasio.backend import lifespan as lifespan_module
        from alasio.backend.lifespan import notify_rotation

        monkeypatch.setattr(lifespan_module, 'ROTATION_NOTIFY_TIMEOUT', 0.1)
        token_table.seed_from_supervisor(('tok1',))
        good = FakeNotifyServer(subscribed={'restricted'}, auth_token='tok1')
        hang = FakeNotifyServer(subscribed={'restricted'}, auth_token='tok1', hang_send=True)
        WebsocketTopicServer.active['good'] = good
        WebsocketTopicServer.active['hang'] = hang
        await notify_rotation()
        assert len(good.sent) == 1
        assert hang.sent == []
        assert hang.closed == []

    @pytest.mark.trio
    async def test_broken_close_does_not_abort_others(self, monkeypatch):
        """A connection whose close() hangs is cut off, others still closed."""
        from alasio.backend import lifespan as lifespan_module
        from alasio.backend.lifespan import notify_rotation

        monkeypatch.setattr(lifespan_module, 'ROTATION_NOTIFY_TIMEOUT', 0.1)
        token_table.seed_from_supervisor(('tok1',))
        # good keeps tok1 -> renew; bad lost its token -> close(4002), but hangs
        good = FakeNotifyServer(subscribed={'restricted'}, auth_token='tok1')
        bad = FakeNotifyServer(subscribed={'restricted'}, auth_token='gone', hang_close=True)
        WebsocketTopicServer.active['good'] = good
        WebsocketTopicServer.active['bad'] = bad
        await notify_rotation()
        assert len(good.sent) == 1
        assert good.closed == []
        assert bad.closed == []

    @pytest.mark.trio
    async def test_ordinary_connections_not_notified(self):
        """Connections without restricted subscriptions are untouched."""
        from alasio.backend.lifespan import notify_rotation

        token_table.seed_from_supervisor(('tok1',))
        plain = FakeNotifyServer(subscribed={'public'}, auth_token='tok1')
        restricted = FakeNotifyServer(subscribed={'restricted'}, auth_token='tok1')
        WebsocketTopicServer.active['plain'] = plain
        WebsocketTopicServer.active['restricted'] = restricted
        await notify_rotation()
        assert plain.sent == []
        assert plain.closed == []
        assert len(restricted.sent) == 1
