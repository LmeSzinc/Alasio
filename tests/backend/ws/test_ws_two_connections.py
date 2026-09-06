"""
Two-connection tests: two websocket connections subscribing to viewport
sources of the SAME config -- the real multi-window scenario behind the
manual checklist. Verifies at the ws layer:

- both connections resolve the SAME source instance (keyed registry) and
  share ONE single-flight full build (one get_gui_config, identical
  payloads);
- config events (dispatch) reach every subscribed connection;
- unsubscribing one connection leaves the other untouched;
- two connections on different navs of the same config only receive the
  increments of their own nav (viewport filtering per connection).

No real backend: two WebsocketTopicServer instances are driven by
FakeWebSockets inside one trio nursery (tests/backend/ws/helpers.py).
"""

import pytest
import trio

from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.reactive.source import KeyedSource, NoCachePush
from alasio.backend.ws.ws_topic import BaseTopic
from tests.backend.ws.helpers import HarnessWebsocketServer, ServerHarness

CONFIG = 'config_a'

# view-dependent mappings, mirroring ConfigArgSource: which (task, group,
# arg) the view displays is decided by the GUI structure of the nav
MAPPING = {
    'nav1': {('T1', 'g1', 'a1'): ('card1', 'g1', 'a1')},
    'nav2': {('T2', 'g1', 'a1'): ('card1', 'g1', 'a1')},
}

VIEW = {'card1': {'value': 1}}


class SharedViewportSource(KeyedSource, NoCachePush):
    """
    A no-cache view source keyed by (config, view) like GuiConfigSource
    subclasses: the mapping and the full view are fixed per view.
    `builds` counts full view builds (single-flight probe). The registry
    (KeyedSource) is what makes two connections share one instance.
    """
    TOPIC_NAME = 'shared_view'

    def __init__(self, config_name, view_name):
        super().__init__()
        self.config_name = config_name
        self.view_name = view_name
        self.builds = 0
        self.dict_config_to_topic = MAPPING[view_name]

    @classmethod
    def dispatch(cls, config_name, event):
        """
        [任意线程] Application-level config dispatch used by the tests:
        route one config event to every source of the config (the real
        one lives in GuiConfigSource).
        """
        for _, source in cls.singleton_items():
            if source.config_name == config_name:
                source.on_event(event)

    async def _build_full(self):
        self.builds += 1
        return VIEW

    def _convert(self, event):
        if type(event) is dict:
            task, group, arg = event['task'], event['group'], event['arg']
            value = event['value']
        else:
            task, group, arg = event.task, event.group, event.arg
            value = event.value
        key = self.dict_config_to_topic.get((task, group, arg))
        if key is None:
            return None
        return ResponseEvent(t=self.TOPIC_NAME, o='set', k=(*key, 'value'), v=value)


class SharedViewTopic(BaseTopic):
    """
    A topic bound to the viewport source of (config, view); the view comes
    from the server (per-connection navigation state, like ConnState).
    """
    TOPIC_NAME = 'shared_view'

    async def get_source(self):
        return SharedViewportSource.get(CONFIG, self.server.view_name)


class TwoConnServer(HarnessWebsocketServer):
    """Server with the shared-view topic registered, no default topics."""
    view_name = 'nav1'
    ALL_TOPIC_CLASS = {
        SharedViewTopic.TOPIC_NAME: SharedViewTopic,
    }
    DEFAULT_TOPIC_CLASS = {}


@pytest.fixture(autouse=True)
def cleanup_two_conn():
    """Clean topic singletons and the view source registry after each test"""
    yield
    SharedViewTopic.singleton_clear()
    SharedViewportSource.singleton_clear()


@pytest.fixture
async def two_connections():
    """
    Two independent connections served side by side in one nursery.

    The servers are gracefully shut down and their topics unsubscribed
    after the test, like endpoint() does in its finally block.
    """
    h1 = ServerHarness(TwoConnServer)
    h2 = ServerHarness(TwoConnServer)
    async with trio.open_nursery() as nursery:
        nursery.start_soon(h1.run_serve)
        nursery.start_soon(h2.run_serve)
        await h1.wait_connected()
        await h2.wait_connected()
        yield h1, h2
        await h1.stop()
        await h2.stop()
        # unsubscribe all topics (same as endpoint cleanup)
        await h1.server.cleanup()
        await h2.server.cleanup()
        nursery.cancel_scope.cancel()


def full_of(harness):
    """
    The full events received by one connection

    Args:
        harness (ServerHarness):

    Returns:
        list[dict]:
    """
    return [e for e in harness.sent_events() if e.get('o') == 'full']


def set_events_of(harness):
    """
    The set increments received by one connection

    Args:
        harness (ServerHarness):

    Returns:
        list[dict]:
    """
    return [e for e in harness.sent_events() if e.get('o') == 'set']


class TestTwoConnectionsSameView:
    @pytest.mark.trio
    async def test_share_one_source_and_single_flight_build(self, two_connections):
        """
        Both connections open the same (config, view): they resolve the
        same source instance and, when the second subscribes while the
        first build is still running, share ONE full build. Both receive
        the identical full payload.
        """
        h1, h2 = two_connections
        source = SharedViewportSource.get(CONFIG, 'nav1')
        builds = [0]
        started = trio.Event()
        release = trio.Event()

        async def slow_build():
            builds[0] += 1
            started.set()
            await release.wait()
            return VIEW

        # hold the single-flight build open so the second subscription
        # lands inside the build window
        source._build_full = slow_build

        h1.fake_ws.send_message(b'{"t":"shared_view"}')
        with trio.fail_after(5):
            await started.wait()          # connection 1 is building
        h2.fake_ws.send_message(b'{"t":"shared_view"}')   # arrives mid-build
        await trio.testing.wait_all_tasks_blocked()
        release.set()
        await trio.testing.wait_all_tasks_blocked()

        # one full build shared by both connections
        assert builds[0] == 1
        assert len(full_of(h1)) == 1
        assert len(full_of(h2)) == 1
        assert full_of(h1)[0]['v'] == VIEW
        assert full_of(h1)[0] == full_of(h2)[0]

    @pytest.mark.trio
    async def test_event_reaches_both_subscribed_connections(self, two_connections):
        """
        A config event (dispatch) is delivered to every connection
        subscribed to a source of the config.
        """
        h1, h2 = two_connections
        h1.fake_ws.send_message(b'{"t":"shared_view"}')
        h2.fake_ws.send_message(b'{"t":"shared_view"}')
        await trio.testing.wait_all_tasks_blocked()
        # clear the full events, keep only what follows
        h1.fake_ws.sent.clear()
        h2.fake_ws.sent.clear()

        SharedViewportSource.dispatch(CONFIG, {'task': 'T1', 'group': 'g1', 'arg': 'a1', 'value': 5})
        await trio.testing.wait_all_tasks_blocked()

        assert set_events_of(h1) == [{'t': 'shared_view', 'o': 'set', 'k': ['card1', 'g1', 'a1', 'value'], 'v': 5}]
        assert set_events_of(h1) == set_events_of(h2)

    @pytest.mark.trio
    async def test_unsubscribe_one_connection_keeps_other(self, two_connections):
        """
        Unsubscribing one connection removes only its topic from the
        shared source; the other connection keeps receiving increments.
        """
        h1, h2 = two_connections
        h1.fake_ws.send_message(b'{"t":"shared_view"}')
        h2.fake_ws.send_message(b'{"t":"shared_view"}')
        await trio.testing.wait_all_tasks_blocked()
        source = SharedViewportSource.get(CONFIG, 'nav1')
        assert len(source._subscribers) == 2

        h1.fake_ws.send_message(b'{"t":"shared_view","o":"unsub"}')
        await trio.testing.wait_all_tasks_blocked()
        assert len(source._subscribers) == 1
        assert 'shared_view' not in h1.server.subscribed
        assert 'shared_view' in h2.server.subscribed

        h1.fake_ws.sent.clear()
        h2.fake_ws.sent.clear()
        SharedViewportSource.dispatch(CONFIG, {'task': 'T1', 'group': 'g1', 'arg': 'a1', 'value': 7})
        await trio.testing.wait_all_tasks_blocked()
        # only the remaining subscriber received the increment
        assert set_events_of(h1) == []
        assert len(set_events_of(h2)) == 1


class TestTwoConnectionsDifferentViews:
    @pytest.mark.trio
    async def test_each_connection_only_receives_its_own_nav(self, two_connections):
        """
        Two connections on different navs of the same config subscribe to
        two different sources; a config event only reaches the connection
        whose view displays the argument (per-connection filtering).
        """
        h1, h2 = two_connections
        h2.server.view_name = 'nav2'
        h1.fake_ws.send_message(b'{"t":"shared_view"}')
        h2.fake_ws.send_message(b'{"t":"shared_view"}')
        await trio.testing.wait_all_tasks_blocked()
        # distinct sources, both inside the shared registry of the config
        s1 = SharedViewportSource.get(CONFIG, 'nav1')
        s2 = SharedViewportSource.get(CONFIG, 'nav2')
        assert s1 is not s2

        h1.fake_ws.sent.clear()
        h2.fake_ws.sent.clear()
        # an argument of nav1's view: only connection 1 receives it
        SharedViewportSource.dispatch(CONFIG, {'task': 'T1', 'group': 'g1', 'arg': 'a1', 'value': 5})
        await trio.testing.wait_all_tasks_blocked()
        assert len(set_events_of(h1)) == 1
        assert set_events_of(h2) == []
        # an argument of nav2's view: only connection 2 receives it
        SharedViewportSource.dispatch(CONFIG, {'task': 'T2', 'group': 'g1', 'arg': 'a1', 'value': 6})
        await trio.testing.wait_all_tasks_blocked()
        assert len(set_events_of(h1)) == 1
        assert len(set_events_of(h2)) == 1
