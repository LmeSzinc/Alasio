"""
Tests of config.index.json (GenConfigIndex.config_index_data).

The file tells the frontend, for one nav, which {nav}_config.json and
{nav}_i18n.json to load, and which task / group values to read from the user
config to fill the inputs.
"""

from alasio.config_dev.gen.gen_config_index import GenConfigIndex
from alasio.ext.file.jsonfile import json_dumps_custom_indent
from alasio.testing.filesystem import fs  # noqa: F401
from tests.config_dev.gen.helpers import make_cross

NAVS = {
    'main': """\
Campaign:
  args:
    Name:
      value: main
      option: [main, event]
StopCondition:
  args:
    Count: 1
""",
    'gems': """\
GemsCampaign:
  parent: Campaign
  args:
    Name:
      value: gems
      option: [gems, event]
""",
}

TASKS = {
    'main': """\
Farming:
  groups:
    Campaign:
    StopCondition:
""",
    'gems': """\
GemsFarming:
  groups:
    GemsCampaign:
""",
}


def build(fs):
    """
    Build a GenConfigIndex with {nav}_config.json of every nav generated.

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture

    Returns:
        GenConfigIndex:
    """
    gen = make_cross(fs, NAVS, TASKS, GenConfigIndex)
    for nav in gen.dict_nav_config.values():
        gen._generate_nav_config_json(nav)
    return gen


class TestConfigIndexData:
    def test_nav_entry(self, fs):
        """Each nav points at its config json, its i18n files and its values."""
        gen = build(fs)
        assert json_dumps_custom_indent(gen.config_index_data).decode() == """\
{
  "main": {
    "file": "main/main_config.json",
    "i18n": [
      "main/main_i18n.json"
    ],
    "config": {
      "task": ["Farming"],
      "group": []
    }
  },
  "gems": {
    "file": "gems/gems_config.json",
    "i18n": [
      "gems/gems_i18n.json"
    ],
    "config": {
      "task": ["GemsFarming"],
      "group": []
    }
  }
}"""

    def test_nav_without_config_is_skipped(self, fs):
        """A nav whose cards were not generated has no entry."""
        gen = make_cross(fs, NAVS, TASKS, GenConfigIndex)
        assert gen.config_index_data == {}

    def test_variant_reads_the_i18n_of_its_own_nav(self, fs):
        """An arg overridden by the variant is translated in the nav of the variant."""
        gen = build(fs)
        assert gen.config_index_data['gems']['i18n'] == ['gems/gems_i18n.json']

    def test_inherited_arg_reads_the_ancestor_i18n(self, fs):
        """An arg taken from an ancestor is translated in the nav of the ancestor."""
        gen = make_cross(fs, {
            'main': """\
Campaign:
  args:
    Name:
      value: main
      option: [main, event]
    Mode:
      value: normal
      option: [normal, hard]
""",
            'gems': """\
GemsCampaign:
  parent: Campaign
  args:
    Name: gems
""",
        }, {
            'main': """\
Farming:
  groups:
    Campaign:
""",
            'gems': """\
GemsFarming:
  groups:
    GemsCampaign:
""",
        }, GenConfigIndex)
        for nav in gen.dict_nav_config.values():
            gen._generate_nav_config_json(nav)
        assert gen.config_index_data['gems']['i18n'] == [
            'gems/gems_i18n.json',
            'main/main_i18n.json',
        ]

    def test_i18n_is_deduplicated(self, fs):
        """Two groups of the same nav load one i18n file."""
        gen = make_cross(fs, {
            'main': """\
A:
  args:
    X: 1
B:
  args:
    Y: 2
""",
        }, {
            'main': """\
Task:
  groups:
    A:
    B:
""",
        }, GenConfigIndex)
        for nav in gen.dict_nav_config.values():
            gen._generate_nav_config_json(nav)
        assert gen.config_index_data['main']['i18n'] == ['main/main_i18n.json']

    def test_group_values_of_a_partial_task(self, fs):
        """A nav showing one group of another task reads that group only."""
        gen = make_cross(fs, NAVS, {
            'main': TASKS['main'],
            'gems': """\
GemsFarming:
  groups:
    GemsCampaign:
    Campaign:
      task: Farming
""",
        }, GenConfigIndex)
        for nav in gen.dict_nav_config.values():
            gen._generate_nav_config_json(nav)
        assert gen.config_index_data['gems']['config']['task'] == ['GemsFarming']
        assert gen.config_index_data['gems']['config']['group'].value == [['Farming', 'Campaign']]

    def test_dashboard_nav_reads_its_task(self, fs):
        """A dashboard nav reads its values from the task owning the group."""
        gen = make_cross(fs, {
            'dashboard': """\
Oil:
  args:
    Value: 0
""",
        }, {
            'dashboard': """\
OilTask:
  groups:
    Oil:
""",
        }, GenConfigIndex)
        for nav in gen.dict_nav_config.values():
            gen._generate_nav_config_json(nav)
        assert gen.config_index_data['dashboard']['config']['task'] == ['OilTask']
        assert gen.config_index_data['dashboard']['config']['group'].value == []
