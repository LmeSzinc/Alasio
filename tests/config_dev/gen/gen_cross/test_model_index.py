"""
Tests of the model registry of a mod: dict_group_ref, model_data and tasks_data.

dict_group_ref says where the msgspec model of a group is generated, model_data
is the same reference per task and group with the task the value is read from,
tasks_data merges the tasks of every nav and rejects a duplicate name.
"""
import pytest

from alasio.config_dev.parse.base import DefinitionError
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
Global:
  args:
    Flag: true
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
Globals:
  global_bind: true
  groups:
    Global:
""",
    'gems': """\
GemsFarming:
  groups:
    GemsCampaign:
    Campaign:
      task: Farming
""",
}


class TestDictGroupRef:
    def test_points_to_the_declaring_nav(self, fs):
        """A variant group is generated in its own nav, not in the parent nav."""
        gen = make_cross(fs, NAVS, TASKS)
        assert gen.dict_group_ref == {
            'Campaign': {'file': 'main/main_model.py', 'cls': 'Campaign'},
            'StopCondition': {'file': 'main/main_model.py', 'cls': 'StopCondition'},
            'Global': {'file': 'main/main_model.py', 'cls': 'Global'},
            'GemsCampaign': {'file': 'gems/gems_model.py', 'cls': 'GemsCampaign'},
        }


class TestModelData:
    def test_reference_per_task_group(self, fs):
        """A cross-task group keeps the task the value is read from."""
        gen = make_cross(fs, NAVS, TASKS)
        assert gen.model_data == {
            'Farming': {
                'Campaign': {'file': 'main/main_model.py', 'cls': 'Campaign', 'task': 'Farming'},
                'StopCondition': {'file': 'main/main_model.py', 'cls': 'StopCondition', 'task': 'Farming'},
            },
            'Globals': {
                'Global': {'file': 'main/main_model.py', 'cls': 'Global', 'task': 'Globals'},
            },
            'GemsFarming': {
                'GemsCampaign': {'file': 'gems/gems_model.py', 'cls': 'GemsCampaign', 'task': 'GemsFarming'},
                # a cross task reference keeps the referenced task
                'Campaign': {'file': 'main/main_model.py', 'cls': 'Campaign', 'task': 'Farming'},
            },
            '_global_bind': {
                'Global': {'file': 'main/main_model.py', 'cls': 'Global', 'task': 'Globals'},
            },
        }

    def test_unknown_model(self, fs):
        """A task binding a group that does not exist is reported."""
        with pytest.raises(DefinitionError) as e:
            make_cross(fs, {'main': NAVS['main']}, {
                'main': """\
Farming:
  displays: null
  groups:
    Missing:
""",
            }).model_data
        assert e.value.msg == 'No such group model "Missing"'
        assert e.value.keys == ['Farming', 'groups', 'Missing']

    def test_unknown_cross_task_group(self, fs):
        """A cross task reference to a group that does not exist is reported."""
        with pytest.raises(DefinitionError) as e:
            make_cross(fs, NAVS, {
                'main': TASKS['main'],
                'gems': """\
GemsFarming:
  groups:
    GemsCampaign:
    Campaign:
      task: MissingTask
""",
            })
        assert e.value.msg == 'Invalid cross-task group reference: MissingTask.Campaign, no such group'

    def test_global_bind_group_used_twice(self, fs):
        """A global bind group can not be bound by a normal task as well."""
        with pytest.raises(DefinitionError) as e:
            make_cross(fs, NAVS, {
                'main': TASKS['main'],
                'gems': """\
GemsFarming:
  groups:
    GemsCampaign:
    Global:
      task: Globals
""",
            }).model_data
        assert e.value.msg == 'Group "Global" is already global bind, maybe remove the use of non global bind?'

    def test_global_bind_groups_go_last(self, fs):
        """Dashboard global binds are moved to the end of _global_bind."""
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
        })
        assert list(gen.model_data['_global_bind']) == ['Campaign', 'Oil']


class TestTasksData:
    def test_merges_navs(self, fs):
        """The tasks of every nav are collected in the filesystem order."""
        gen = make_cross(fs, NAVS, TASKS)
        assert list(gen.tasks_data) == ['Farming', 'Globals', 'GemsFarming']

    def test_duplicate_task_name(self, fs):
        """A task name used by two navs is reported."""
        with pytest.raises(DefinitionError) as e:
            make_cross(fs, {'main': NAVS['main'], 'gems': NAVS['gems']}, {
                'main': 'Farming:\n  groups:\n    Campaign:\n',
                'gems': 'Farming:\n  groups:\n    GemsCampaign:\n',
            }).tasks_data
        assert e.value.msg == 'Duplicate task name: "Farming"'
        assert e.value.file.endswith('gems/gems.tasks.yaml')


class TestResolveInfoI18nGroup:
    def test_variant_uses_its_ancestor(self, fs):
        """A variant without override_i18n shows the title of its ancestor."""
        gen = make_cross(fs, NAVS, TASKS)
        assert gen._resolve_info_i18ngroup(gen.groups_data['GemsCampaign']) == 'Campaign'

    def test_nearest_override_ancestor_wins(self, fs):
        """The nearest ancestor with override_i18n provides the title."""
        gen = make_cross(fs, {'main': """\
Campaign:
  args:
    Name: main
Middle:
  parent: Campaign
  override_i18n: true
  args:
    Name: middle
Leaf:
  parent: Middle
  args:
    Name: leaf
"""}, {'main': 'Farming:\n  groups:\n    Campaign:\n    Middle:\n    Leaf:\n'})
        # the group itself is skipped, the search starts from its parent
        assert gen._resolve_info_i18ngroup(gen.groups_data['Leaf']) == 'Middle'
