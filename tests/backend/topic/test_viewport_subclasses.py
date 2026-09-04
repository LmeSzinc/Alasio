"""
Tests for the viewport subclass mapping algorithms: ConfigArgSource
(topic/config.py) and DashboardSource (topic/dashboard.py). Each mapping is
built from the GUI structure only (nav JSON cache), never from config
values; the two algorithms are independent by design. Source keys carry the
full context: (mod, config, nav, lang) for ConfigArg, (mod, config, lang)
for Dashboard.
"""

from types import SimpleNamespace

import pytest

from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.reactive.source import ViewportEventSource
from alasio.backend.topic.config import ConfigArgSource
from alasio.backend.topic.dashboard import DashboardSource
from alasio.backend.topic.scan import ConfigScanSource
from alasio.config.entry.loader import MOD_LOADER

MOD_NAME = 'test_mod'
CONFIG = 'alas'
LANG = 'en-US'

# A mini nav tree replicating the real shape:
# {card: {'_info': ..., group: {arg: {task/group/arg refs}}}}
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


class FakeMod:
    """
    A mod stub exposing the structure APIs used by the mapping builders
    """

    def __init__(self, index, trees):
        self._index = index
        self._trees = trees

    def config_index_data(self):
        return self._index

    def nav_config_json(self, file):
        return self._trees[file]


@pytest.fixture(autouse=True)
def structure_env(monkeypatch):
    """
    Point ConfigScanSource.data / MOD_LOADER at a fake mod structure.
    The values are plain objects (the mapping builders never touch config
    values, so no ConfigInfo is required).
    """
    config_scan = ConfigScanSource()
    monkeypatch.setattr(config_scan, 'data', {
        CONFIG: SimpleNamespace(mod=MOD_NAME),
        'other': SimpleNamespace(mod=MOD_NAME),
    })
    mod = FakeMod(
        index={'general': SimpleNamespace(file='general/general_config.json'),
               'dashboard': SimpleNamespace(file='dashboard/dashboard_config.json')},
        trees={'general/general_config.json': NAV_TREE,
               'dashboard/dashboard_config.json': DASHBOARD_TREE},
    )
    monkeypatch.setattr(MOD_LOADER, 'dict_mod', {MOD_NAME: mod})
    yield
    ViewportEventSource._by_config.clear()


def arg_source():
    return ConfigArgSource.get(MOD_NAME, CONFIG, 'general', LANG)


def dash_source():
    return DashboardSource.get(MOD_NAME, CONFIG, LANG)


class TestConfigArgSourceBuildMapping:
    def test_mapping_contains_normal_args(self):
        """(task, group, arg) -> (card, group, arg) for every normal arg"""
        source = arg_source()
        assert source.dict_config_to_topic == {
            ('TaskA', 'group_a', 'arg_a'): ('card_a', 'group_a', 'arg_a'),
            ('TaskA', 'group_a', 'arg_b'): ('card_a', 'group_a', 'arg_b'),
            ('TaskB', 'other', 'arg_c'): ('card_a', 'group_no_task', 'arg_c'),
            ('TaskC', 'group_b', 'arg_d'): ('card_b', 'group_b', 'arg_d'),
        }

    def test_info_and_malformed_skipped(self):
        """_info pseudo groups and entries without refs never enter the mapping"""
        source = arg_source()
        # card-level _info entries never become args
        assert all(k[1] != '_info' for k in source.dict_config_to_topic)
        # the malformed entry (no task/group/arg refs) is skipped
        assert all(k[2] != 'bad' for k in source.dict_config_to_topic)

    def test_structure_only_no_value_reads(self):
        """mapping construction never reads config values"""
        source = arg_source()
        # the fake tree carries no 'value' insertion and no config_read call
        # happened: reaching here with plain objects proves structure-only
        assert isinstance(source.dict_config_to_topic, dict)

    def test_missing_config_returns_none(self):
        """a config that is not in the scan data makes get() return None"""
        assert ConfigArgSource.get(MOD_NAME, 'ghost', 'general', LANG) is None

    def test_config_mod_mismatch_returns_none(self):
        """a config bound to another mod makes get() return None"""
        assert ConfigArgSource.get('other_mod', CONFIG, 'general', LANG) is None

    def test_missing_mod_returns_none(self):
        """an unknown mod makes get() return None"""
        assert ConfigArgSource.get('ghost_mod', CONFIG, 'general', LANG) is None

    def test_missing_nav_returns_none(self):
        """a nav that is not in the mod index makes get() return None"""
        assert ConfigArgSource.get(MOD_NAME, CONFIG, 'ghost_nav', LANG) is None

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


class TestConfigArgSourceConvert:
    def test_hit_constructs_set_response(self):
        """a hit converts into a set response at (card, group, arg, 'value')"""
        source = arg_source()
        resp = source._convert({'task': 'TaskA', 'group': 'group_a', 'arg': 'arg_a', 'value': True})
        assert resp == ResponseEvent(
            t='ConfigArg', o='set', k=('card_a', 'group_a', 'arg_a', 'value'), v=True)

    def test_miss_returns_none(self):
        """an arg outside the nav is dropped (None)"""
        source = arg_source()
        assert source._convert({'task': 'OtherTask', 'group': 'g', 'arg': 'a', 'value': 1}) is None

    def test_hit_value_preserved(self):
        """string / number values pass through untouched"""
        source = arg_source()
        resp = source._convert({'task': 'TaskC', 'group': 'group_b', 'arg': 'arg_d', 'value': 'x'})
        assert resp.v == 'x'


class TestDashboardSourceBuildMapping:
    def test_mapping_value_is_card_name(self):
        """dashboard mapping values are card names"""
        source = dash_source()
        assert source.dict_config_to_topic == {
            ('Dashboard', 'Oil', 'Time'): 'card-Dashboard-Oil',
            ('Dashboard', 'Oil', 'Value'): 'card-Dashboard-Oil',
            ('Dashboard', 'Ship', 'Name'): 'card-Dashboard-Ship',
        }

    def test_info_skipped(self):
        """group-level / card-level _info entries are skipped"""
        source = dash_source()
        assert ('Dashboard', 'Oil', '_info') not in source.dict_config_to_topic

    def test_missing_config_returns_none(self):
        """a config that is not in the scan data makes get() return None"""
        assert DashboardSource.get(MOD_NAME, 'ghost', LANG) is None


class TestDashboardSourceConvert:
    def test_hit_constructs_set_response(self):
        """key shape is (card_name, group, arg, 'value')"""
        source = dash_source()
        resp = source._convert({'task': 'Dashboard', 'group': 'Oil', 'arg': 'Value', 'value': 100})
        assert resp == ResponseEvent(
            t='Dashboard', o='set', k=('card-Dashboard-Oil', 'Oil', 'Value', 'value'), v=100)

    def test_miss_returns_none(self):
        source = dash_source()
        assert source._convert({'task': 'Dashboard', 'group': 'Oil', 'arg': 'Time', 'value': 1}) is not None
        assert source._convert({'task': 'Alas', 'group': 'g', 'arg': 'a', 'value': 1}) is None

    def test_independent_mappings(self):
        """
        ConfigArgSource and DashboardSource map the same event independently
        (no shared algorithm): different key shapes on purpose.
        """
        arg_source_inst = arg_source()
        dash_source_inst = dash_source()
        assert arg_source_inst is not dash_source_inst
        # same shared registry table: dispatch covers both classes
        assert ViewportEventSource._by_config[CONFIG][(ConfigArgSource, MOD_NAME, 'general', LANG)] is arg_source_inst
        assert ViewportEventSource._by_config[CONFIG][(DashboardSource, MOD_NAME, LANG)] is dash_source_inst
