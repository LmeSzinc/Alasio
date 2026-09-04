"""
Tests for ViewportEventSource (alasio/backend/reactive/source.py):
the shared keyed registry, config-level dispatch, view filtering and the
single-flight full build shared by concurrent subscribers.
"""

import threading

import pytest
import trio

from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.reactive.source import ViewportEventSource
from tests.backend.reactive.test_source_base import MockTopic, decode


class FakeViewport(ViewportEventSource):
    """
    A viewport source with a fixed mapping and a fixed full view:
    (task, group, arg) -> (card, group, arg) with keys 'T1.a.b' style.
    """
    TOPIC = 'FakeView'
    VIEW = {'card1': {'value': 1}}

    def __init__(self, config_name, view_name):
        super().__init__(config_name)
        self.view_name = view_name
        self.builds = 0
        self.dict_config_to_topic = {
            ('T1', 'g1', 'a1'): ('card1', 'g1', 'a1'),
            ('T1', 'g1', 'a2'): ('card1', 'g1', 'a2'),
            ('T2', 'g1', 'a1'): ('card2', 'g1', 'a1'),
        }

    @classmethod
    def get(cls, config_name, view_name):
        return super().get(config_name, cls, view_name)

    async def _build_full(self):
        self.builds += 1
        return self.VIEW

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
        return ResponseEvent(t=self.TOPIC, o='set', k=(*key, 'value'), v=value)


class OtherViewport(FakeViewport):
    """
    A second viewport subclass to verify registry sharing between sibling
    classes (dispatch covers both).
    """
    TOPIC = 'OtherView'


@pytest.fixture(autouse=True)
def cleanup_viewports():
    """Clean the shared viewport registry after each test"""
    yield
    ViewportEventSource._by_config.clear()


class TestRegistry:
    @pytest.mark.trio
    async def test_get_creates_and_caches(self):
        """get creates and caches; same key returns the same instance"""
        a1 = FakeViewport.get('config_a', 'nav1')
        a2 = FakeViewport.get('config_a', 'nav1')
        assert a1 is a2
        assert a1.config_name == 'config_a'
        assert a1.view_name == 'nav1'

    @pytest.mark.trio
    async def test_composite_key_isolation(self):
        """(config, view1) and (config, view2) are different instances"""
        a = FakeViewport.get('config_a', 'nav1')
        b = FakeViewport.get('config_a', 'nav2')
        assert a is not b
        # different configs too
        c = FakeViewport.get('config_b', 'nav1')
        assert a is not c

    @pytest.mark.trio
    async def test_get_returns_none_on_build_error(self):
        """a KeyError while constructing the source makes get return None"""

        class BrokenViewport(ViewportEventSource):
            TOPIC = 'Broken'

            def __init__(self, config_name, view_name):
                raise KeyError('config deleted')

            @classmethod
            def get(cls, config_name, view_name):
                return super().get(config_name, cls, view_name)

        try:
            assert BrokenViewport.get('config_x', 'nav1') is None
        finally:
            BrokenViewport._by_config.clear()

    @pytest.mark.trio
    async def test_registry_shared_across_subclasses(self):
        """sibling subclasses share the registry of the base class"""
        a = FakeViewport.get('config_a', 'nav1')
        b = OtherViewport.get('config_a', 'nav2')
        # both live in the same registry table; keys carry the class so
        # sibling subclasses never collide
        assert ViewportEventSource._by_config['config_a'][(FakeViewport, 'nav1')] is a
        assert ViewportEventSource._by_config['config_a'][(OtherViewport, 'nav2')] is b

    @pytest.mark.trio
    async def test_sibling_keys_never_collide(self):
        """the same view name under two subclasses stays two instances"""
        a = FakeViewport.get('config_a', 'dashboard')
        b = OtherViewport.get('config_a', 'dashboard')
        assert a is not b


class TestDispatch:
    @pytest.mark.trio
    async def test_dispatch_reaches_only_config_instances(self):
        """dispatch delivers only to the instances of the config"""
        a = FakeViewport.get('config_a', 'nav1')
        b = FakeViewport.get('config_b', 'nav1')
        topic_a = MockTopic()
        topic_b = MockTopic()
        await a.subscribe(topic_a)
        await b.subscribe(topic_b)

        FakeViewport.dispatch('config_a', {'task': 'T1', 'group': 'g1', 'arg': 'a1', 'value': 5})
        await trio.testing.wait_all_tasks_blocked()
        assert len(topic_a.sent) == 1
        assert topic_b.sent == []

    @pytest.mark.trio
    async def test_dispatch_covers_all_viewport_subclasses(self):
        """one dispatch call covers ConfigArg-like and Dashboard-like sources"""
        a = FakeViewport.get('config_a', 'nav1')
        b = OtherViewport.get('config_a', 'dashboard')
        topic_a = MockTopic()
        topic_b = MockTopic()
        await a.subscribe(topic_a)
        await b.subscribe(topic_b)

        ViewportEventSource.dispatch('config_a', {'task': 'T1', 'group': 'g1', 'arg': 'a1', 'value': 5})
        await trio.testing.wait_all_tasks_blocked()
        assert len(topic_a.sent) == 1
        assert len(topic_b.sent) == 1

    @pytest.mark.trio
    async def test_dispatch_list_payload_expanded(self):
        """a list payload is expanded into single events and filtered"""
        a = FakeViewport.get('config_a', 'nav1')
        topic = MockTopic()
        await a.subscribe(topic)
        events = [
            {'task': 'T1', 'group': 'g1', 'arg': 'a1', 'value': 1},  # hit
            {'task': 'T9', 'group': 'g9', 'arg': 'z9', 'value': 2},  # miss
            {'task': 'T2', 'group': 'g1', 'arg': 'a1', 'value': 3},  # hit
        ]
        FakeViewport.dispatch('config_a', events)
        await trio.testing.wait_all_tasks_blocked()
        payload = topic.sent[0]
        decoded = decode(payload)
        if not isinstance(decoded, list):
            decoded = [decoded]
        assert [(e.k, e.v) for e in decoded] == [
            (('card1', 'g1', 'a1', 'value'), 1),
            (('card2', 'g1', 'a1', 'value'), 3),
        ]

    def test_dispatch_concurrent_safe(self):
        """dispatch is safe while another thread registers / removes instances"""
        stop = threading.Event()
        errors = []

        def dispatcher():
            n = 0
            while not stop.is_set():
                n += 1
                FakeViewport.dispatch('config_a', {'task': 'T1', 'group': 'g1', 'arg': 'a1', 'value': n})

        def registrar():
            n = 0
            while not stop.is_set():
                n += 1
                src = FakeViewport.get('config_a', f'nav{n % 5}')
                try:
                    src._remove()
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=dispatcher, daemon=True),
                   threading.Thread(target=registrar, daemon=True)]
        for t in threads:
            t.start()
        import time
        time.sleep(0.05)
        stop.set()
        for t in threads:
            t.join(timeout=2)
        assert errors == []


class TestOnEventFilter:
    @pytest.mark.trio
    async def test_hit_broadcasts_set(self):
        """a mapped event broadcasts a set response with the view key"""
        a = FakeViewport.get('config_a', 'nav1')
        topic = MockTopic()
        await a.subscribe(topic)
        a.on_event({'task': 'T1', 'group': 'g1', 'arg': 'a1', 'value': 5})
        await trio.testing.wait_all_tasks_blocked()
        event = decode(topic.sent[0])
        assert event.t == 'FakeView'
        assert event.o == 'set'
        assert event.k == ('card1', 'g1', 'a1', 'value')
        assert event.v == 5

    @pytest.mark.trio
    async def test_miss_dropped(self):
        """an unmapped event is dropped: no send, no schedule"""
        a = FakeViewport.get('config_a', 'nav1')
        topic = MockTopic()
        await a.subscribe(topic)
        a.on_event({'task': 'T9', 'group': 'g9', 'arg': 'z9', 'value': 1})
        await trio.testing.wait_all_tasks_blocked()
        assert topic.sent == []

    @pytest.mark.trio
    async def test_no_subscriber_dropped(self):
        """events reaching an instance without subscribers are dropped"""
        a = FakeViewport.get('config_a', 'nav1')
        topic = MockTopic()
        await a.subscribe(topic)
        a.unsubscribe(topic)
        a.on_event({'task': 'T1', 'group': 'g1', 'arg': 'a1', 'value': 1})
        await trio.testing.wait_all_tasks_blocked()
        assert topic.sent == []


class TestSubscribe:
    @pytest.mark.trio
    async def test_subscribe_builds_and_returns_full_payload(self):
        """viewport subscribe builds the full view and returns encoded bytes"""
        a = FakeViewport.get('config_a', 'nav1')
        topic = MockTopic()
        payload = await a.subscribe(topic)
        assert isinstance(payload, bytes)
        event = decode(payload)
        assert event.t == 'FakeView'
        assert event.o == 'full'
        assert event.v == FakeViewport.VIEW
        assert topic in a._subscribers
        assert a.builds == 1

    @pytest.mark.trio
    async def test_subscribe_empty_view_registers_no_full(self):
        """an empty view registers the subscriber but returns None"""
        a = FakeViewport.get('config_a', 'nav1')
        a._build_full = async_none
        topic = MockTopic()
        assert await a.subscribe(topic) is None
        assert topic in a._subscribers

    @pytest.mark.trio
    async def test_increments_flow_after_subscribe(self):
        """after registration increments reach the topic"""
        a = FakeViewport.get('config_a', 'nav1')
        topic = MockTopic()
        await a.subscribe(topic)
        a.on_event({'task': 'T1', 'group': 'g1', 'arg': 'a2', 'value': 7})
        await trio.testing.wait_all_tasks_blocked()
        assert len(topic.sent) == 1


async def async_none():
    return None


class TestSingleFlight:
    @pytest.mark.trio
    async def test_concurrent_subscribers_share_one_build(self):
        """
        Two subscribers joining while a build is running share it: one
        build total, both receive the same encoded payload.
        """
        a = FakeViewport.get('config_a', 'nav1')
        builds = [0]
        started = trio.Event()
        release = trio.Event()

        async def slow_build():
            builds[0] += 1
            started.set()
            await release.wait()
            return FakeViewport.VIEW

        a._build_full = slow_build
        topic1 = MockTopic()
        topic2 = MockTopic()
        results = {}

        async def sub(topic, name):
            results[name] = await a.subscribe(topic)

        async with trio.open_nursery() as nursery:
            nursery.start_soon(sub, topic1, 't1')
            await started.wait()
            # the second subscriber arrives while the build is running
            nursery.start_soon(sub, topic2, 't2')
            await trio.testing.wait_all_tasks_blocked()
            release.set()
            await trio.testing.wait_all_tasks_blocked()

        assert builds[0] == 1
        assert results['t1'] == results['t2']
        assert decode(results['t1']).v == FakeViewport.VIEW
        assert topic1 in a._subscribers
        assert topic2 in a._subscribers

    @pytest.mark.trio
    async def test_waiter_failure_publishes_none(self):
        """
        A failing build wakes its waiters with None (registered, no full);
        the builder re-raises.
        """
        a = FakeViewport.get('config_a', 'nav1')
        started = trio.Event()
        release = trio.Event()

        async def failing_build():
            started.set()
            await release.wait()
            raise KeyError('config deleted')

        a._build_full = failing_build
        topic1 = MockTopic()
        topic2 = MockTopic()
        results = {}
        errors = []

        async def sub(topic, name):
            try:
                results[name] = await a.subscribe(topic)
            except KeyError as e:
                errors.append(name)

        async with trio.open_nursery() as nursery:
            nursery.start_soon(sub, topic1, 't1')
            await started.wait()
            nursery.start_soon(sub, topic2, 't2')
            await trio.testing.wait_all_tasks_blocked()
            release.set()
            await trio.testing.wait_all_tasks_blocked()

        # builder raised (never registered: the build happens before
        # registration); the waiter received None and IS registered
        assert errors == ['t1']
        assert results['t2'] is None
        assert topic1 not in a._subscribers
        assert topic2 in a._subscribers

    @pytest.mark.trio
    async def test_non_overlapping_subscribers_rebuild(self):
        """
        Sequential (non-overlapping) subscribers each rebuild: the full
        view is never cached -- only subscribers overlapping a running
        build share it, so the view can never be stale by more than one
        build window.
        """
        a = FakeViewport.get('config_a', 'nav1')
        topic1 = MockTopic()
        await a.subscribe(topic1)
        assert a.builds == 1
        # a second, sequential subscriber rebuilds (no stale reuse)
        topic2 = MockTopic()
        await a.subscribe(topic2)
        assert a.builds == 2
        # without overlapping waiters nothing is ever cached
        assert a._build_payload is ViewportEventSource._NO_PAYLOAD
        a.unsubscribe(topic1)
        a.unsubscribe(topic2)


class TestIdleGc:
    @pytest.mark.trio
    async def test_idle_instance_removed_by_gc(self):
        """an idle viewport instance is removed from the registry after IDLE_TTL"""
        a = FakeViewport.get('config_a', 'nav1')
        topic = MockTopic()
        await a.subscribe(topic)
        a.unsubscribe(topic)
        # idle longer than IDLE_TTL (8)
        a._last_unsub = a._last_unsub - 20
        FakeViewport.gc_idle()
        assert ViewportEventSource._by_config == {}

    @pytest.mark.trio
    async def test_recent_idle_instance_kept(self):
        """an instance idle within the TTL is kept (fast navigation round trip)"""
        a = FakeViewport.get('config_a', 'nav1')
        topic = MockTopic()
        await a.subscribe(topic)
        a.unsubscribe(topic)
        # still inside the idle window: kept
        FakeViewport.gc_idle()
        assert ViewportEventSource._by_config['config_a'][(FakeViewport, 'nav1')] is a
        # and get() reuses it
        assert FakeViewport.get('config_a', 'nav1') is a

    @pytest.mark.trio
    async def test_subscribed_instance_never_collected(self):
        """an instance with subscribers is never collected"""
        a = FakeViewport.get('config_a', 'nav1')
        topic = MockTopic()
        await a.subscribe(topic)
        a._last_unsub = 0.
        FakeViewport.gc_idle()
        assert ViewportEventSource._by_config['config_a'][(FakeViewport, 'nav1')] is a
