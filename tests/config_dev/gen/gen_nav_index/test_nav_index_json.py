"""
Tests of nav.index.json (GenNavIndex.nav_index_data).

The file is the translation of the navigation component: one entry per nav
with its `_info`, one entry per card with the name of the group shown as the
card title, and a `scheduler` flag on the cards displaying a Scheduler group.
"""
import json

import pytest

from alasio.config_dev.gen.gen_nav_index import GenNavIndex
from alasio.config_dev.parse.base import DefinitionError
from alasio.ext.file.jsonfile import json_dumps_custom_indent
from alasio.ext.path.calc import joinpath
from alasio.testing.filesystem import fs  # noqa: F401
from tests.config_dev.gen.helpers import make_cross, nav_dir

NAVS = {
    'main': """\
Scheduler:
  args:
    Enable: true
Campaign:
  args:
    Name:
      value: main
      option: [main, event]
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
    Scheduler:
    Campaign:
""",
    'gems': """\
GemsFarming:
  groups:
    GemsCampaign:
""",
}


def build(fs, navs=NAVS, tasks=TASKS, index=None, i18n=None):
    """
    Build a GenNavIndex with the cards of every nav generated.

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture
        navs (dict[str, str]): key: nav name, value of {nav}.args.yaml
        tasks (dict[str, str]): key: nav name, value of {nav}.tasks.yaml
        index (dict, optional): Content of the existing nav.index.json
        i18n (dict[str, dict], optional): key: nav name, value of {nav}_i18n.json

    Returns:
        GenNavIndex:
    """
    gen = make_cross(fs, navs, tasks, GenNavIndex)
    if index is not None:
        fs.create_file(gen.nav_index_file, contents=json.dumps(index, ensure_ascii=False))
    for nav, data in (i18n or {}).items():
        fs.create_file(
            joinpath(nav_dir(fs, nav), f'{nav}_i18n.json'),
            contents=json.dumps(data, ensure_ascii=False))
    for nav in gen.dict_nav_config.values():
        gen._generate_nav_config_json(nav)
    return gen


class TestNavIndexData:
    def test_nav_and_card_names(self, fs):
        """Each nav lists its cards, the card takes the name of its group."""
        gen = build(fs)
        assert json_dumps_custom_indent(gen.nav_index_data).decode() == """\
{
  "gems": {
    "_info": {
      "i18n": {
        "zh-CN": "gems",
        "en-US": "gems"
      }
    },
    "card-GemsFarming-GemsCampaign": {
      "i18n": {
        "zh-CN": "Campaign",
        "en-US": "Campaign"
      }
    }
  },
  "main": {
    "_info": {
      "i18n": {
        "zh-CN": "main",
        "en-US": "main"
      }
    },
    "card-Farming-Campaign": {
      "scheduler": true,
      "i18n": {
        "zh-CN": "Campaign",
        "en-US": "Campaign"
      }
    }
  }
}"""

    def test_scheduler_flag(self, fs):
        """Only the card displaying a Scheduler group is flagged."""
        gen = build(fs)
        assert gen.nav_index_data['main']['card-Farming-Campaign']['scheduler'] is True
        assert 'scheduler' not in gen.nav_index_data['gems']['card-GemsFarming-GemsCampaign']

    def test_multiline_card_name(self, fs):
        """A card of a group named with multiple lines lists them, not a str repr."""
        gen = build(fs, i18n={'main': {'Campaign': {'_info': {'zh-CN': {'name': ['the name', 'the subtitle']}}}}})
        assert gen.nav_index_data['main']['card-Farming-Campaign']['i18n'] == {
            'zh-CN': ['the name', 'the subtitle'],
            'en-US': 'Campaign',
        }

    def test_existing_nav_info_is_kept(self, fs):
        """The manual translation of a nav name survives the generation."""
        gen = build(fs, index={'main': {'_info': {'i18n': {'zh-CN': '主界面', 'en-US': 'Main'}}}})
        assert gen.nav_index_data['main']['_info']['i18n'] == {'zh-CN': '主界面', 'en-US': 'Main'}
        # a nav without translation falls back to its name
        assert gen.nav_index_data['gems']['_info']['i18n'] == {'zh-CN': 'gems', 'en-US': 'gems'}

    def test_task_without_display_has_no_nav_info(self, fs):
        """A nav of tasks without a card is not listed as a nav entry."""
        gen = build(fs, navs={'main': """\
Group:
  args:
    X: 1
"""}, tasks={'main': """\
Task:
  displays: null
  groups:
    Group:
"""})
        assert gen.nav_index_data == {}

    def test_card_info_must_be_known(self, fs):
        """A card info pointing at a group without translation is reported."""
        gen = build(fs)
        gen.dict_group_name_i18n.pop('Campaign')
        with pytest.raises(DefinitionError) as e:
            _ = gen.nav_index_data
        assert e.value.msg == (
            'Card "gems.card-GemsFarming-GemsCampaign._info" reference a non-exist group: "Campaign"'
        )
