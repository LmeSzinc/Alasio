"""
Tests for the shared GUI view source GuiConfigSource
(topic/_gui_config.py) and its two concrete view sources:
ConfigArgSource (topic/config.py) and DashboardSource (topic/dashboard.py).

The event mapping (task, group, arg) -> (card, group, arg) is projected
from the full view (MOD_LOADER.get_gui_config) on the first subscribe,
so the mapping is always consistent with the full. The constructor only
runs an in-memory existence check (config / mod); a deleted nav is not
validated there -- get_gui_config returns {} for an unknown nav and the
subscribe yields an empty view + empty mapping. Source keys carry the
full context: (mod, config, nav, lang) for ConfigArg, (mod, config,
lang) for Dashboard.
"""

from types import SimpleNamespace

import pytest

from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.topic.config import ConfigArgSource
from alasio.backend.topic.dashboard import DashboardSource
from alasio.backend.topic.scan import ConfigScanSource
from alasio.config.entry.loader import MOD_LOADER
from tests.backend.reactive.test_source_base import MockTopic

MOD_NAME = 'test_mod'
CONFIG = 'alas'
LANG = 'en-US'

# A mini view replicating the shape get_gui_config returns:
# {card: {'_info': ..., group: {arg: {task/group/arg refs + value}}}}
# Rows without a (task, group, arg) reference never enter a real view
# (the loader skips them); the fake keeps one for the skip check.
NAV_TREE = {
    'card_a': {
        '_info': {'group': 'Info', 'arg': '_info', 'card': 'card_a'},
        'group_a': {
            'arg_a': {'task': 'TaskA', 'group': 'group_a', 'arg': 'arg_a', 'dt': 'checkbox'},
            'arg_b': {'task': 'TaskA', 'group': 'group_a', 'arg': 'arg_b', 'dt': 'input'},
        },
        'group_no_task': {
            # an arg whose reference points elsewhere (kept)
            'arg_c': {'task': 'TaskB', 'group': 'other', 'arg': 'arg_c'},
        },
    },
    'card_b': {
        '_info': {'group': 'InfoB', 'arg': '_info', 'card': 'card_b'},
        'group_b': {
            'arg_d': {'task': 'TaskC', 'group': 'group_b', 'arg': 'arg_d'},
            # malformed entry without task/group/arg refs: skipped
            'bad': {'dt': 'checkbox', 'name': 'no refs'},
        },
    },
}

DASHBOARD_TREE = {
    'card-Dashboard-Oil': {
        'Oil': {
            '_info': {'group': 'Oil', 'arg': '_info', 'dashboard': 'Total'},
            'Time': {'task': 'Dashboard', 'group': 'Oil', 'arg': 'Time'},
            'Value': {'task': 'Dashboard', 'group': 'Oil', 'arg': 'Value'},
        },
    },
    'card-Dashboard-Ship': {
        '_info': {'group': 'ShipInfo', 'arg': '_info', 'card': 'card-Dashboard-Ship'},
        'Ship': {
            'Name': {'task': 'Dashboard', 'group': 'Ship', 'arg': 'Name'},
        },
    },
}

EXPECTED_NAV_MAPPING = {
    ('TaskA', 'group_a', 'arg_a'): ('card_a', 'group_a', 'arg_a'),
    ('TaskA', 'group_a', 'arg_b'): ('card_a', 'group_a', 'arg_b'),
    ('TaskB', 'other', 'arg_c'): ('card_a', 'group_no_task', 'arg_c'),
    ('TaskC', 'group_b', 'arg_d'): ('card_b', 'group_b', 'arg_d'),
}

EXPECTED_DASH_MAPPING = {
    ('Dashboard', 'Oil', 'Time'): ('card-Dashboard-Oil', 'Oil', 'Time'),
    ('Dashboard', 'Oil', 'Value'): ('card-Dashboard-Oil', 'Oil', 'Value'),
    ('Dashboard', 'Ship', 'Name'): ('card-Dashboard-Ship', 'Ship', 'Name'),
}


def fake_get_gui_config(mod_name, config_name, nav_name, lang):
    """
    Stub of MOD_LOADER.get_gui_config: returns the view of the nav, or {}
    for an unknown nav (mirrors the built-in nav check of the loader).
    """
    if nav_name == 'general':
        return NAV_TREE
    if nav_name == 'dashboard':
        return DASHBOARD_TREE
    return {}


@pytest.fixture(autouse=True)
def structure_env(monkeypatch):
    """
    Point ConfigScanSource.data / MOD_LOADER at a fake environment: the
    constructor validation reads the scan data + dict_mod only (plain
    objects suffice, no structure APIs are called anymore); the full view
    comes from the stubbed get_gui_config.
    """
    config_scan = ConfigScanSource()
    monkeypatch.setattr(config_scan, 'data', {
        CONFIG: SimpleNamespace(mod=MOD_NAME),
        'other': SimpleNamespace(mod=MOD_NAME),
    })
    monkeypatch.setattr(MOD_LOADER, 'dict_mod', {MOD_NAME: object()})
    monkeypatch.setattr(MOD_LOADER, 'get_gui_config', fake_get_gui_config)
    yield
    ConfigArgSource.singleton_clear()
    DashboardSource.singleton_clear()


def arg_source():
    return ConfigArgSource.get(MOD_NAME, CONFIG, 'general', LANG)


def dash_source():
    return DashboardSource.get(MOD_NAME, CONFIG, LANG)


async def subscribe_arg():
    """
    Create and subscribe a ConfigArg source: the mapping is projected on
    the first subscribe (the full view + mapping are built together).
    """
    source = arg_source()
    await source.subscribe(MockTopic())
    return source


async def subscribe_dash():
    source = dash_source()
    await source.subscribe(MockTopic())
    return source


class TestConstructorValidation:
    def test_mapping_empty_until_subscribe(self):
        """the mapping stays {} until the first subscribe builds it"""
        source = arg_source()
        assert source.dict_config_to_topic == {}

    def test_missing_config_returns_none(self):
        """a config that is not in the scan data makes get() return None"""
        assert ConfigArgSource.get(MOD_NAME, 'ghost', 'general', LANG) is None

    def test_config_mod_mismatch_returns_none(self):
        """a config bound to another mod makes get() return None"""
        assert ConfigArgSource.get('other_mod', CONFIG, 'general', LANG) is None

    def test_missing_mod_returns_none(self):
        """an unknown mod makes get() return None"""
        assert ConfigArgSource.get('ghost_mod', CONFIG, 'general', LANG) is None

    def test_missing_nav_not_validated(self):
        """
        A nav outside the index is NOT validated in the constructor: it
        surfaces as an empty view at subscribe time (get_gui_config
        returns {} for an unknown nav). get() succeeds.
        """
        source = ConfigArgSource.get(MOD_NAME, CONFIG, 'ghost_nav', LANG)
        assert source is not None
        assert source.dict_config_to_topic == {}

    def test_keyed_by_full_context(self):
        """(mod, config, nav, lang) instances are isolated"""
        a = arg_source()
        b = ConfigArgSource.get(MOD_NAME, CONFIG, 'general', LANG)
        assert a is b
        # a different lang is a different instance (view builds differ)
        c = ConfigArgSource.get(MOD_NAME, CONFIG, 'general', 'zh-CN')
        assert a is not c
        assert a.lang == LANG
        assert c.lang == 'zh-CN'


class TestBuildFullProjection:
    @pytest.mark.trio
    async def test_subscribe_projects_mapping_from_full(self):
        """
        The first subscribe builds the full view and projects the mapping
        from it: (task, group, arg) -> (card, group, arg) per row.
        """
        source = await subscribe_arg()
        assert source.dict_config_to_topic == EXPECTED_NAV_MAPPING

    @pytest.mark.trio
    async def test_info_and_malformed_skipped(self):
        """
        '_info' pseudo rows and rows without references never enter the
        mapping (they never carry (task, group, arg) references).
        """
        source = await subscribe_arg()
        assert all(k[1] != '_info' for k in source.dict_config_to_topic)
        assert all(k[2] != 'bad' for k in source.dict_config_to_topic)

    @pytest.mark.trio
    async def test_projection_reads_no_values(self):
        """
        The projection is structure-only: the fake view rows carry no
        'value' key, and the projection still yields the full mapping.
        """
        source = await subscribe_arg()
        assert len(source.dict_config_to_topic) == len(EXPECTED_NAV_MAPPING)

    @pytest.mark.trio
    async def test_empty_view_yields_empty_mapping(self):
        """
        An unknown nav (get_gui_config -> {}) registers the subscriber
        without a full (empty view) and leaves the mapping empty: every
        later event is dropped.
        """
        source = ConfigArgSource.get(MOD_NAME, CONFIG, 'ghost_nav', LANG)
        topic = MockTopic()
        payload = await source.subscribe(topic)
        assert payload is None
        assert topic in source._subscribers
        assert source.dict_config_to_topic == {}
        # events on the empty view are dropped
        source.on_event({'task': 'TaskA', 'group': 'group_a', 'arg': 'arg_a', 'value': 1})
        assert topic.sent == []

    @pytest.mark.trio
    async def test_mapping_assigned_before_subscriber_registered(self):
        """
        Ordering guarantee: by the time subscribe() returns, the mapping
        is projected (registration happens after the build), so any event
        forwarded later converts against the finished mapping.
        """
        source = arg_source()
        topic = MockTopic()
        await source.subscribe(topic)
        assert source.dict_config_to_topic == EXPECTED_NAV_MAPPING
        assert topic in source._subscribers


class TestConfigArgSourceConvert:
    @pytest.mark.trio
    async def test_hit_constructs_set_response(self):
        """a hit converts into a set response at (card, group, arg, 'value')"""
        source = await subscribe_arg()
        resp = source._convert({'task': 'TaskA', 'group': 'group_a', 'arg': 'arg_a', 'value': True})
        assert resp == ResponseEvent(
            t='ConfigArg', o='set', k=('card_a', 'group_a', 'arg_a', 'value'), v=True)

    @pytest.mark.trio
    async def test_miss_returns_none(self):
        """an arg outside the nav is dropped (None)"""
        source = await subscribe_arg()
        assert source._convert({'task': 'OtherTask', 'group': 'g', 'arg': 'a', 'value': 1}) is None

    @pytest.mark.trio
    async def test_hit_value_preserved(self):
        """string / number values pass through untouched"""
        source = await subscribe_arg()
        resp = source._convert({'task': 'TaskC', 'group': 'group_b', 'arg': 'arg_d', 'value': 'x'})
        assert resp.v == 'x'

    @pytest.mark.trio
    async def test_event_before_subscribe_dropped(self):
        """
        Before the first subscribe the mapping is empty: events arriving
        on the unsubscribed instance are dropped (also guarded by the
        no-subscriber early return of NoCachePush).
        """
        source = arg_source()
        topic = MockTopic()
        source.on_event({'task': 'TaskA', 'group': 'group_a', 'arg': 'arg_a', 'value': 1})
        assert topic.sent == []


class TestDashboardSourceBuildMapping:
    @pytest.mark.trio
    async def test_mapping_value_is_card_location(self):
        """dashboard mapping values are (card, group, arg) display locations"""
        source = await subscribe_dash()
        assert source.dict_config_to_topic == EXPECTED_DASH_MAPPING

    @pytest.mark.trio
    async def test_info_skipped(self):
        """group-level / card-level _info entries are skipped"""
        source = await subscribe_dash()
        assert ('Dashboard', 'Oil', '_info') not in source.dict_config_to_topic

    def test_missing_config_returns_none(self):
        """a config that is not in the scan data makes get() return None"""
        assert DashboardSource.get(MOD_NAME, 'ghost', LANG) is None


class TestDashboardSourceConvert:
    @pytest.mark.trio
    async def test_hit_constructs_set_response(self):
        """key shape is (card_name, group, arg, 'value')"""
        source = await subscribe_dash()
        resp = source._convert({'task': 'Dashboard', 'group': 'Oil', 'arg': 'Value', 'value': 100})
        assert resp == ResponseEvent(
            t='Dashboard', o='set', k=('card-Dashboard-Oil', 'Oil', 'Value', 'value'), v=100)

    @pytest.mark.trio
    async def test_miss_returns_none(self):
        source = await subscribe_dash()
        assert source._convert({'task': 'Dashboard', 'group': 'Oil', 'arg': 'Time', 'value': 1}) is not None
        assert source._convert({'task': 'Alas', 'group': 'g', 'arg': 'a', 'value': 1}) is None

    def test_independent_mappings(self):
        """
        ConfigArgSource and DashboardSource map the same event through the
        shared GuiConfigSource algorithm; they stay distinct instances and
        own separate per-class registries (dispatch covers both classes
        through the application-level route list).
        """
        arg_source_inst = arg_source()
        dash_source_inst = dash_source()
        assert arg_source_inst is not dash_source_inst
        # separate registries: each class owns its own keyed table
        assert (MOD_NAME, CONFIG, 'general', LANG) in ConfigArgSource.singleton_instances()
        assert (MOD_NAME, CONFIG, LANG) in DashboardSource.singleton_instances()
        assert arg_source_inst.nav_name == 'general'
        assert dash_source_inst.nav_name == 'dashboard'
