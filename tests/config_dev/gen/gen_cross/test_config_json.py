"""
Tests of {nav}_config.json (CrossNavGenerator._generate_nav_config_json and
generate_config_json).

The file is the flattened view of the cards of a nav: one entry per card, one
entry per displayed group, and one entry per arg of that group. A card gets an
`_info` row pointing at the group whose translation is shown as the card title,
and a variant group can point at its ancestor's i18n instead of its own.
"""
import pytest

from alasio.config_dev.parse.base import DefinitionError
from alasio.ext.file.jsonfile import json_dumps_custom_indent
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.config_dev.gen.helpers import GitAddSpy, make_cross

MAIN_ARGS = """\
Campaign:
  args:
    Name:
      value: main
      option: [main, event]
StopCondition:
  args:
    Count: 1
"""

MAIN_TASKS = """\
Farming:
  groups:
    Campaign:
    StopCondition:
"""

GEMS_ARGS = """\
GemsCampaign:
  parent: Campaign
  args:
    Name:
      value: gems
      option: [gems, event]
"""

GEMS_TASKS = """\
GemsFarming:
  groups:
    GemsCampaign:
"""


def nav_config_json(gen, nav):
    """
    Render one nav config to json text.

    Args:
        gen (CrossNavGenerator): Generator under test
        nav (str): Nav name

    Returns:
        str: Pretty printed json of {nav}_config.json
    """
    data = gen._generate_nav_config_json(gen.dict_nav_config[nav])
    return json_dumps_custom_indent(data).decode()


class TestNavConfigJson:
    def test_single_group_card(self, fs):
        """A card of one group lists its args, a hidden arg is not listed."""
        gen = make_cross(fs, {
            'main': """\
Group:
  args:
    Shown: 1
    Hidden:
      dt: static-hide
      value: x
""",
        }, {
            'main': """\
Task:
  groups:
    Group:
  displays:
    - [{info: Group}, Group]
""",
        })
        assert nav_config_json(gen, 'main') == """\
{
  "card-Task-Group": {
    "_info": {"group": "Group", "arg": "_info", "card": "card-Task-Group"},
    "Group": {
      "Shown": {
        "task": "Task",
        "group": "Group",
        "arg": "Shown",
        "dt": "input-int",
        "value": 1
      }
    }
  }
}"""

    def test_variant_with_own_i18n(self, fs):
        """A variant overriding i18n binds the class and keeps its own title."""
        gen = make_cross(fs, {
            'main': """\
Base:
  args:
    Mode:
      value: a
      option: [a, b]
""",
            'gems': """\
Own:
  parent: Base
  override_i18n: true
  args:
    Mode:
      value: b
      option: [a, b]
""",
        }, {
            'main': """\
Task1:
  groups:
    Base:
""",
            'gems': """\
Task2:
  groups:
    Base: Own
""",
        })
        assert nav_config_json(gen, 'gems') == """\
{
  "card-Task2-Base": {
    "_info": {"group": "Own", "arg": "_info", "card": "card-Task2-Base"},
    "Base": {
      "Mode": {
        "task": "Task2",
        "group": "Base",
        "arg": "Mode",
        "cls": "Own",
        "i18ngroup": "Own",
        "dt": "select",
        "value": "b",
        "option": ["a", "b"]
      }
    }
  }
}"""

    def test_variant_without_own_i18n_uses_ancestor(self, fs):
        """A variant without override_i18n shows the title of its ancestor."""
        gen = make_cross(fs, {'main': MAIN_ARGS, 'gems': GEMS_ARGS},
                         {'main': MAIN_TASKS, 'gems': GEMS_TASKS})
        assert nav_config_json(gen, 'gems') == """\
{
  "card-GemsFarming-GemsCampaign": {
    "_info": {"group": "Campaign", "arg": "_info", "card": "card-GemsFarming-GemsCampaign"},
    "GemsCampaign": {
      "Name": {
        "task": "GemsFarming",
        "group": "GemsCampaign",
        "arg": "Name",
        "dt": "select",
        "value": "gems",
        "option": ["gems", "event"]
      }
    }
  }
}"""

    def test_dashboard_group_row(self, fs):
        """A dashboard group adds its type and color to the card."""
        gen = make_cross(fs, {
            'dash': """\
DashboardBase:
  args:
    Time: 2020-01-01 00:00:00
DashboardTotal:
  parent: DashboardBase
Oil:
  parent: DashboardTotal
  dashboard_color: "#FF0000"
  args:
    Value:
      value: 0
      range: 0~100
""",
        }, {
            'dash': """\
OilTask:
  groups:
    Oil:
  displays:
    - [Oil]
""",
        })
        assert nav_config_json(gen, 'dash') == """\
{
  "card-OilTask-Oil": {
    "_info": {"group": "Oil", "arg": "_info", "card": "card-OilTask-Oil"},
    "Oil": {
      "_info": {"group": "Oil", "arg": "_info", "dashboard": "Total", "dashboard_color": "#FF0000"},
      "Time": {
        "task": "OilTask",
        "group": "Oil",
        "arg": "Time",
        "i18ngroup": "DashboardBase",
        "dt": "datetime",
        "value": "2020-01-01T00:00:00Z"
      },
      "Value": {
        "task": "OilTask",
        "group": "Oil",
        "arg": "Value",
        "dt": "input-int",
        "value": 0,
        "ge": 0,
        "le": 100
      }
    }
  }
}"""

    def test_dashboard_nav_has_no_card_info(self, fs):
        """The dashboard nav has no card `_info`, its data stays flat."""
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
  displays:
    - [Oil]
""",
        })
        assert nav_config_json(gen, 'dashboard') == """\
{
  "card-OilTask-Oil": {
    "Oil": {
      "Value": {
        "task": "OilTask",
        "group": "Oil",
        "arg": "Value",
        "dt": "input-int",
        "value": 0
      }
    }
  }
}"""

    def test_card_info_must_exist(self, fs):
        """A card info pointing at an unknown group is reported."""
        gen = make_cross(fs, {'main': MAIN_ARGS}, {'main': MAIN_TASKS})
        config = gen.dict_nav_config['main']
        card = next(iter(config.tasks_data['Farming'].displays.values()))
        card.info = 'Missing'
        with pytest.raises(DefinitionError) as e:
            gen._generate_nav_config_json(config)
        assert e.value.msg == 'No such group "Missing"'
        assert e.value.value is card


class TestGenerateConfigJson:
    def test_writes_every_nav(self, fs):
        """Each nav gets its own {nav}_config.json, staged to git."""
        gen = make_cross(fs, {'main': MAIN_ARGS, 'gems': GEMS_ARGS},
                         {'main': MAIN_TASKS, 'gems': GEMS_TASKS})
        main = gen.dict_nav_config['main']
        gems = gen.dict_nav_config['gems']
        gitadd = GitAddSpy()
        with logger.mock_capture_writer() as capture:
            gen.generate_config_json(gitadd=gitadd)
        assert capture.concat() == f"""\
INFO | Write file {main.config_file}
INFO | Write file {gems.config_file}
"""
        assert gitadd.files == [str(main.config_file), str(gems.config_file)]
        assert main.config_file.exists()
        assert gems.config_file.exists()

    def test_second_run_writes_nothing(self, fs):
        """The same data does not rewrite the files."""
        gen = make_cross(fs, {'main': MAIN_ARGS}, {'main': MAIN_TASKS})
        gen.generate_config_json()
        gitadd = GitAddSpy()
        with logger.mock_capture_writer() as capture:
            gen.generate_config_json(gitadd=gitadd)
        assert capture.concat() == ''
        assert gitadd.files == []

    def test_deletes_empty_nav(self, fs):
        """A nav without card no longer has a config json, the deletion is staged."""
        gen = make_cross(fs, {'main': 'Info:\n'}, {'main': ''})
        config = gen.dict_nav_config['main']
        fs.create_file(config.config_file, contents='{}\n')
        gitadd = GitAddSpy()
        with logger.mock_capture_writer() as capture:
            gen.generate_config_json(gitadd=gitadd)
        assert capture.concat() == f'INFO | Delete file {config.config_file}\n'
        assert not config.config_file.exists()
        assert gitadd.files == [str(config.config_file)]

    def test_absent_empty_nav_is_not_reported(self, fs):
        """A nav without card and without a previous config json deletes nothing."""
        gen = make_cross(fs, {'main': 'Info:\n'}, {'main': ''})
        config = gen.dict_nav_config['main']
        gitadd = GitAddSpy()
        with logger.mock_capture_writer() as capture:
            gen.generate_config_json(gitadd=gitadd)
        assert capture.concat() == ''
        assert gitadd.files == []
        assert not config.config_file.exists()


class TestConfigJsonFile:
    def test_file_content(self, fs):
        """The file on disk is the pretty printed card data."""
        gen = make_cross(fs, {'main': MAIN_ARGS}, {'main': MAIN_TASKS})
        gen.generate_config_json()
        content = gen.dict_nav_config['main'].config_file.atomic_read_text()
        assert content == """\
{
  "card-Farming-Campaign": {
    "_info": {"group": "Campaign", "arg": "_info", "card": "card-Farming-Campaign"},
    "Campaign": {
      "Name": {
        "task": "Farming",
        "group": "Campaign",
        "arg": "Name",
        "dt": "select",
        "value": "main",
        "option": ["main", "event"]
      }
    },
    "StopCondition": {
      "Count": {
        "task": "Farming",
        "group": "StopCondition",
        "arg": "Count",
        "dt": "input-int",
        "value": 1
      }
    }
  }
}"""

    def test_second_run_is_a_noop(self, fs):
        """The same data does not rewrite the file."""
        gen = make_cross(fs, {'main': MAIN_ARGS}, {'main': MAIN_TASKS})
        gen.generate_config_json()
        config = gen.dict_nav_config['main']
        gitadd = GitAddSpy()
        with logger.mock_capture_writer() as capture:
            gen.generate_config_json(gitadd=gitadd)
        assert capture.concat() == ''
        assert gitadd.files == []
        assert config.config_file.exists()
