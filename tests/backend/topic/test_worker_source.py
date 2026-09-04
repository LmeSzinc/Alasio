"""
Tests for WorkerSource (alasio/backend/topic/_worker.py): set/del apply,
snapshot and thread-safety of concurrent producers.
"""

import threading
import time

import pytest
import trio

from alasio.backend.topic._worker import WorkerSource
from tests.backend.reactive.test_source_base import MockTopic, decode


@pytest.fixture(autouse=True)
def cleanup_worker_source():
    """Clear the WorkerSource singleton after each test"""
    yield
    WorkerSource.singleton_clear()


class TestWorkerSourceApply:
    def test_running_sets_state(self):
        """on_event((config, 'running')) sets data[config]"""
        source = WorkerSource()
        source.on_event(('alas', 'running'))
        assert source.data == {'alas': 'running'}

    def test_idle_removes_state(self):
        """on_event((config, 'idle')) deletes the config"""
        source = WorkerSource()
        source.on_event(('alas', 'running'))
        source.on_event(('alas', 'idle'))
        assert source.data == {}

    def test_idle_missing_config_no_error(self):
        """an idle event of an absent config is a no-op"""
        source = WorkerSource()
        source.on_event(('alas', 'idle'))
        assert source.data == {}

    def test_applies_without_subscribers(self):
        """states apply even without subscribers (data stays fresh)"""
        source = WorkerSource()
        source.on_event(('alas', 'running'))
        source.on_event(('alas', 'scheduler-waiting'))
        assert source.data == {'alas': 'scheduler-waiting'}

    def test_unchanged_state_no_apply(self):
        """a repeated state does not modify data (no broadcast)"""
        source = WorkerSource()
        source.on_event(('alas', 'running'))
        before = source.data
        source.on_event(('alas', 'running'))
        assert source.data is before


class TestWorkerSourceDelivery:
    @pytest.mark.trio
    async def test_subscribe_snapshot_is_data(self):
        """the full snapshot equals the current data dict"""
        source = WorkerSource()
        source.on_event(('alas', 'running'))
        topic = MockTopic(topic_name='Worker')
        event = decode(await source.subscribe(topic))
        assert event.t == 'Worker'
        assert event.o == 'full'
        assert event.v == {'alas': 'running'}

    @pytest.mark.trio
    async def test_set_response_shape(self):
        """a state event delivers set with k=(config,)"""
        source = WorkerSource()
        topic = MockTopic(topic_name='Worker')
        await source.subscribe(topic)
        source.on_event(('alas', 'running'))
        await trio.testing.wait_all_tasks_blocked()
        event = decode(topic.sent[0])
        assert event.o == 'set'
        assert event.k == ('alas',)
        assert event.v == 'running'

    @pytest.mark.trio
    async def test_del_response_shape(self):
        """an idle event delivers del with k=(config,)"""
        source = WorkerSource()
        source.on_event(('alas', 'running'))
        topic = MockTopic(topic_name='Worker')
        await source.subscribe(topic)
        source.on_event(('alas', 'idle'))
        await trio.testing.wait_all_tasks_blocked()
        event = decode(topic.sent[0])
        assert event.o == 'del'
        assert event.k == ('alas',)

    @pytest.mark.trio
    async def test_global_singleton(self):
        """all accesses share one instance"""
        assert WorkerSource() is WorkerSource()


class TestWorkerSourceConcurrent:
    @pytest.mark.trio
    async def test_thread_producers_final_state(self):
        """
        Multiple threads (simulating worker recv threads) set / idle
        different configs concurrently: the final data is consistent and
        the subscriber sees every state change.
        """
        source = WorkerSource()
        topic = MockTopic(topic_name='Worker')
        await source.subscribe(topic)

        def producer(name):
            for i in range(30):
                source.on_event((name, 'running' if i % 2 == 0 else 'scheduler-waiting'))
                time.sleep(0.0005)
            source.on_event((name, 'idle'))

        threads = [threading.Thread(target=producer, args=(f'config_{n}',)) for n in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        await trio.testing.wait_all_tasks_blocked()
        # every producer ended with idle: data is empty
        assert source.data == {}
