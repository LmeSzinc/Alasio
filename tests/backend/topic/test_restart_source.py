"""
Tests for RestartSource (alasio/backend/topic/restart.py): phase set / clear,
snapshot and delivery shape.
"""

import pytest
import trio

from alasio.backend.topic.restart import RestartSource
from tests.backend.reactive.test_source_base import MockTopic, decode


@pytest.fixture(autouse=True)
def cleanup_restart_source():
    """Clear the RestartSource singleton after each test"""
    yield
    RestartSource.singleton_clear()


class TestRestartSourceApply:
    def test_phase_sets_data(self):
        """on_event('stopping') sets {phase, update}"""
        source = RestartSource()
        source.on_event('stopping')

        assert source.data['phase'] == 'stopping'
        assert source.data['update'] > 0

    def test_phase_replaces_data(self):
        """each phase change replaces the whole data"""
        source = RestartSource()
        source.on_event('stopping')
        source.on_event('shutting-down')

        assert source.data['phase'] == 'shutting-down'

    def test_empty_phase_clears_data(self):
        """on_event('') clears the data (no restart in progress)"""
        source = RestartSource()
        source.on_event('stopping')
        source.on_event('')

        assert source.data == {}

    def test_clear_without_data_no_change(self):
        """clearing an empty topic does not modify data"""
        source = RestartSource()
        before = source.data
        source.on_event('')
        assert source.data is before

    def test_applies_without_subscribers(self):
        """the phase applies even without subscribers (data stays fresh)"""
        source = RestartSource()
        source.on_event('resuming')
        assert source.data['phase'] == 'resuming'

    def test_flow_view_merges_with_the_phase(self):
        """the flow view sets its keys and keeps the phase in place"""
        source = RestartSource()
        source.on_event('stopping')
        source.on_event({'flow': 'restart', 'cancellable': True, 'upgraded': False})

        assert source.data['phase'] == 'stopping'
        assert source.data['flow'] == 'restart'
        assert source.data['cancellable'] is True
        assert source.data['upgraded'] is False

        # a phase change keeps the flow view
        source.on_event('shutting-down')
        assert source.data['flow'] == 'restart'
        assert source.data['phase'] == 'shutting-down'

    def test_empty_phase_keeps_the_flow_view(self):
        """on_event('') only clears the phase: the flow still owns the backend"""
        source = RestartSource()
        source.on_event('stopping')
        source.on_event({'flow': 'update', 'cancellable': True, 'upgraded': False})
        source.on_event('')

        assert 'phase' not in source.data
        assert source.data['flow'] == 'update'

    def test_idle_flow_clears_the_data(self):
        """no phase and no flow -> the topic is empty (the backend is idle)"""
        source = RestartSource()
        source.on_event('stopping')
        source.on_event({'flow': 'restart', 'cancellable': True, 'upgraded': False})
        source.on_event({'flow': '', 'cancellable': False, 'upgraded': False})
        assert source.data['phase'] == 'stopping'
        source.on_event('')
        assert source.data == {}

    def test_flow_view_alone_is_data(self):
        """a flow view without a phase is data (a flow that has not pushed a
        phase yet is still a flow in progress)"""
        source = RestartSource()
        source.on_event({'flow': 'daily', 'cancellable': False, 'upgraded': False})
        assert source.data['flow'] == 'daily'
        assert source.data['update'] > 0


class TestRestartSourceDelivery:
    @pytest.mark.trio
    async def test_subscribe_empty_sends_no_full(self):
        """an empty topic registers silently (frontend has no phase)"""
        source = RestartSource()
        topic = MockTopic(topic_name='Restart')
        payload = await source.subscribe(topic)
        assert payload is None

    @pytest.mark.trio
    async def test_subscribe_snapshot_is_data(self):
        """the full snapshot equals the current data"""
        source = RestartSource()
        source.on_event('stopping')
        topic = MockTopic(topic_name='Restart')

        payload = await source.subscribe(topic)
        event = decode(payload)
        assert event.t == 'Restart'
        assert event.o == 'full'
        assert event.v['phase'] == 'stopping'

    @pytest.mark.trio
    async def test_phase_change_delivers_full(self):
        """a phase change delivers the whole data as a full event"""
        source = RestartSource()
        topic = MockTopic(topic_name='Restart')
        await source.subscribe(topic)

        source.on_event('shutting-down')
        await trio.testing.wait_all_tasks_blocked()

        event = decode(topic.sent[0])
        assert event.o == 'full'
        assert event.v['phase'] == 'shutting-down'

    @pytest.mark.trio
    async def test_clear_delivers_empty_data(self):
        """clearing delivers an empty full event"""
        source = RestartSource()
        source.on_event('resuming')
        topic = MockTopic(topic_name='Restart')
        await source.subscribe(topic)

        source.on_event('')
        await trio.testing.wait_all_tasks_blocked()

        event = decode(topic.sent[0])
        assert event.o == 'full'
        assert event.v == {}

    @pytest.mark.trio
    async def test_global_singleton(self):
        """all accesses share one instance"""
        assert RestartSource() is RestartSource()
