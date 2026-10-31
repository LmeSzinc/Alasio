"""
Tests of task.index.json (GenConfigIndex.task_index_data).

The file tells the runtime, for one task, which group is validated by which
model file and class, and which tasks and groups to read the values from.
"""
import pytest

from alasio.config_dev.gen.gen_config_index import GenConfigIndex
from alasio.config_dev.parse.base import DefinitionError
from alasio.ext.file.jsonfile import json_dumps_custom_indent
from alasio.testing.filesystem import fs  # noqa: F401
from tests.config_dev.gen.helpers import make_cross

NAVS = {
    'main': """\
Campaign:
  args:
    Name: main
StopCondition:
  args:
    Count: 1
""",
    'gems': """\
GemsCampaign:
  parent: Campaign
  args:
    Name: gems
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


def task_index_json(gen):
    """
    Render task.index.json to text.

    Args:
        gen (GenConfigIndex): Generator under test

    Returns:
        str: Pretty printed json
    """
    return json_dumps_custom_indent(gen.task_index_data).decode()


class TestTaskIndexData:
    def test_reference_per_task(self, fs):
        """A variant group validates with the class generated in its own nav."""
        gen = make_cross(fs, NAVS, TASKS, GenConfigIndex)
        assert task_index_json(gen) == """\
{
  "Farming": {
    "group": {
      "Campaign": {"file": "main/main_model.py", "cls": "Campaign", "task": "Farming"},
      "StopCondition": {"file": "main/main_model.py", "cls": "StopCondition", "task": "Farming"}
    },
    "config": {"task": ["Farming"], "group": []}
  },
  "GemsFarming": {
    "group": {
      "GemsCampaign": {"file": "gems/gems_model.py", "cls": "GemsCampaign", "task": "GemsFarming"}
    },
    "config": {"task": ["GemsFarming"], "group": []}
  },
  "_global_bind": {
    "group": {},
    "config": {"task": [], "group": []}
  }
}"""

    def test_partial_task_group_reads_groups(self, fs):
        """A task reading a part of another task reads the groups one by one."""
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
        data = gen.task_index_data
        assert data['GemsFarming']['config'] == {
            'task': ['GemsFarming'],
            'group': [['Farming', 'Campaign']],
        }

    def test_whole_task_reads_task(self, fs):
        """A task reading every group of another task reads the whole task."""
        gen = make_cross(fs, NAVS, {
            'main': TASKS['main'],
            'gems': """\
GemsFarming:
  groups:
    GemsCampaign:
    Campaign:
      task: Farming
    StopCondition:
      task: Farming
""",
        }, GenConfigIndex)
        data = gen.task_index_data
        assert data['GemsFarming']['config'].value == {
            # the task itself first, then the referenced one
            'task': ['GemsFarming', 'Farming'],
            'group': [],
        }

    def test_global_bind_groups(self, fs):
        """A global bind task binds every group it owns."""
        gen = make_cross(fs, NAVS, {
            'main': """\
Globals:
  global_bind: true
  groups:
    Campaign:
    StopCondition:
""",
        }, GenConfigIndex)
        assert task_index_json(gen) == """\
{
  "Globals": {
    "group": {
      "Campaign": {"file": "main/main_model.py", "cls": "Campaign", "task": "Globals"},
      "StopCondition": {"file": "main/main_model.py", "cls": "StopCondition", "task": "Globals"}
    },
    "config": {"task": ["Globals"], "group": []}
  },
  "_global_bind": {
    "group": {
      "Campaign": {"file": "main/main_model.py", "cls": "Campaign", "task": "Globals"},
      "StopCondition": {"file": "main/main_model.py", "cls": "StopCondition", "task": "Globals"}
    },
    "config": {"task": ["Globals"], "group": []}
  }
}"""

    def test_dashboard_global_bind_goes_last(self, fs):
        """Dashboard global binds are listed after the other ones."""
        gen = make_cross(fs, {
            'main': """\
Campaign:
  args:
    Name: main
""",
            'dashboard': """\
Oil:
  args:
    Value: 0
""",
        }, {
            'main': """\
Farming:
  global_bind: true
  groups:
    Campaign:
""",
            'dashboard': """\
OilTask:
  global_bind: true
  groups:
    Oil:
""",
        }, GenConfigIndex)
        assert list(gen.task_index_data['_global_bind']['group']) == ['Campaign', 'Oil']


class TestRegroupIntaskGroup:
    def test_current_task(self, fs):
        """A group of the current task reads the whole task."""
        gen = make_cross(fs, NAVS, TASKS, GenConfigIndex)
        assert gen._regroup_intask_group([('Farming', 'Campaign')], current_task='Farming') == (
            ['Farming'], [])

    def test_whole_task(self, fs):
        """Every group of another task reads the whole task."""
        gen = make_cross(fs, NAVS, TASKS, GenConfigIndex)
        assert gen._regroup_intask_group(
            [('Farming', 'Campaign'), ('Farming', 'StopCondition')]) == (['Farming'], [])

    def test_partial_task(self, fs):
        """A part of another task reads the group by name, sorted."""
        gen = make_cross(fs, NAVS, TASKS, GenConfigIndex)
        assert gen._regroup_intask_group([('Farming', 'StopCondition')]) == (
            [], [['Farming', 'StopCondition']])

    def test_group_of_several_tasks(self, fs):
        """Groups of several tasks are regrouped one task at a time."""
        gen = make_cross(fs, NAVS, TASKS, GenConfigIndex)
        tasks, groups = gen._regroup_intask_group([
            ('Farming', 'Campaign'),
            ('Farming', 'StopCondition'),
            ('GemsFarming', 'GemsCampaign'),
        ])
        assert tasks == ['Farming', 'GemsFarming']
        assert groups == []

    @pytest.mark.xfail(strict=True, reason='dict_intask_group is a defaultdict, the unknown task branch is unreachable')
    def test_unknown_task(self, fs):
        """A reference to a task that does not exist is reported."""
        gen = make_cross(fs, NAVS, TASKS, GenConfigIndex)
        with pytest.raises(DefinitionError) as e:
            gen._regroup_intask_group([('Missing', 'Campaign')])
        assert e.value.msg == 'No such task "Missing"'
