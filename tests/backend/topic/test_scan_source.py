"""
Tests for ConfigScanSource (alasio/backend/topic/scan.py): TTL freshness,
subscribe-time reinit and RPC-style forced reinit.
"""


import pytest
import trio

from alasio.backend.topic.scan import ConfigScanSource
from tests.backend.reactive.test_source_base import MockTopic, decode


class FakeScanTable:
    """
    A ScanTable stub whose scan() result is programmable.
    Values are plain dicts (msgspec-encodable stand-ins of ConfigInfo).
    """
    data = {'alas': {'mod': 'alas'}}

    def scan(self):
        return dict(self.data)


@pytest.fixture(autouse=True)
def fake_scan(monkeypatch):
    """Replace ScanTable so no real disk scan happens"""
    monkeypatch.setattr('alasio.backend.topic.scan.ScanTable', FakeScanTable)
    yield
    ConfigScanSource.singleton_clear()


class TestConfigScanSource:
    @pytest.mark.trio
    async def test_global_singleton(self):
        """ConfigScanSource is a global singleton"""
        assert ConfigScanSource() is ConfigScanSource()

    @pytest.mark.trio
    async def test_fetch_init_ttl_fresh_returns_none(self):
        """a fetch inside the TTL window returns None"""
        source = ConfigScanSource()
        assert await source.fetch_init() is not None
        assert await source.fetch_init() is None

    @pytest.mark.trio
    async def test_fetch_init_force_rereads(self):
        """force=True re-reads regardless of the TTL"""
        source = ConfigScanSource()
        await source.fetch_init()
        assert await source.fetch_init(force=True) is not None

    @pytest.mark.trio
    async def test_fetch_init_expired_rereads(self, monkeypatch):
        """an expired TTL re-reads"""
        source = ConfigScanSource()
        await source.fetch_init()
        source._lastrun = 0.
        assert await source.fetch_init() is not None

    @pytest.mark.trio
    async def test_get_source_reinit_then_snapshot(self):
        """
        The topic data-preparation path: reinit refreshes, subscribe
        snapshots the data (cache topic; the full is the subscribe return).
        """
        source = ConfigScanSource()
        topic = MockTopic(topic_name='ConfigScan')
        await source.reinit()
        assert list(source.data) == ['alas']
        assert source.data['alas']['mod'] == 'alas'
        event = decode(await source.subscribe(topic))
        assert event.t == 'ConfigScan'
        assert event.o == 'full'
        assert list(event.v) == ['alas']

    @pytest.mark.trio
    async def test_rpc_force_reinit_broadcasts_to_subscribers(self):
        """
        After an RPC modified the disk, reinit(force=True) rescans and
        broadcasts a full event to the subscribers.
        """
        source = ConfigScanSource()
        topic = MockTopic(topic_name='ConfigScan')
        await source.subscribe(topic)
        # the RPC handler changes the disk
        FakeScanTable.data = {'alas': {'mod': 'alas'},
                              'new_config': {'mod': 'alas'}}
        await source.reinit(force=True)
        await trio.testing.wait_all_tasks_blocked()
        event = decode(topic.sent[-1])
        assert event.o == 'full'
        assert sorted(event.v) == ['alas', 'new_config']

    @pytest.mark.trio
    async def test_create_default_config_chain(self, monkeypatch):
        """
        create_default_config: ensure data -> create defaults -> force reinit
        """
        source = ConfigScanSource()
        created = []

        def fake_create():
            created.append(True)
            return True

        monkeypatch.setattr(source, '_create_default_config', fake_create)
        calls = []

        async def fake_reinit(force=False):
            calls.append(force)

        monkeypatch.setattr(source, 'reinit', fake_reinit)
        await source.create_default_config()
        # data refresh first (force=False), then the forced reinit after
        # the default configs were created
        assert created == [True]
        assert calls == [False, True]
