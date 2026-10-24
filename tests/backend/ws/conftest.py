"""
Fixtures for the ws framework tests.
"""

import pytest
import trio

from alasio.backend.ws.ws_server import WebsocketTopicServer
from tests.backend.ws.helpers import EmptyTopic, ErrorTopic, FullOnlyTopic, MismatchTopic, SampleTopic, ServerHarness


@pytest.fixture(autouse=True)
def reset_server_terminated():
    """
    WebsocketTopicServer.server_terminated is a class-level event shared by
    every connection, close_all_connections() sets it. Reset it around every
    test so one test can never terminate the next test's connection.
    """
    WebsocketTopicServer.server_terminated = trio.Event()
    yield
    WebsocketTopicServer.server_terminated = trio.Event()


@pytest.fixture(autouse=True)
def isolate_jwt_manager(monkeypatch):
    """
    Tests must not depend on the user's real deploy.yaml / gui.db.

    JWT_MANAGER.pwd and .secret are cached_property descriptors backed by
    DeployConfig (config/deploy.yaml, a user-managed file) and
    AlasioKeyTable (config/gui.db). Shadow them with fixed test values so
    _check_login always passes without reading user configuration.
    """
    from alasio.backend.auth.auth import JWT_MANAGER

    # instance attributes shadow the cached_property descriptors, setitem
    # writes the instance dict directly: setattr() would read the attribute
    # to check it exists, which evaluates the descriptor and reads the user
    # deploy.yaml / gui.db
    monkeypatch.setitem(JWT_MANAGER.__dict__, 'pwd', '')
    # 32 bytes or more: pyjwt warns below the RFC 7518 Section 3.2 minimum for HS256
    monkeypatch.setitem(JWT_MANAGER.__dict__, 'secret', b'unit-test-secret' * 2)


@pytest.fixture(autouse=True)
def cleanup_topic_singletons():
    """
    Clear all test topic singletons after each test, so named singletons
    (keyed by conn_id) never leak between tests
    """
    yield
    for cls in (SampleTopic, FullOnlyTopic, EmptyTopic, ErrorTopic, MismatchTopic):
        cls.singleton_clear()


@pytest.fixture
async def ws_server_harness():
    """
    Provide a running WebsocketTopicServer driven by a FakeWebSocket.

    The server is gracefully shut down after the test, and all topics are
    unsubscribed like endpoint() does in its finally block.

    Note: tests that rely on virtual time must request the autojump_clock
    fixture themselves, pytest-trio only uses a Clock fixture requested by
    the test function to drive trio.run().
    """
    harness = ServerHarness()
    async with trio.open_nursery() as nursery:
        nursery.start_soon(harness.run_serve)
        await harness.wait_connected()
        yield harness
        # graceful shutdown, serve() returns
        await harness.stop()
        # unsubscribe all topics
        await harness.server.cleanup()
        nursery.cancel_scope.cancel()
