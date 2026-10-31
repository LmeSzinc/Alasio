"""
Tests of queue.index.json (GenQueueIndex.queue_index_data).

The file is the translation of the task queue: only the tasks having a
Scheduler group are listed, an existing translation is kept and a new task
defaults to its own name.
"""
import json

from alasio.config_dev.gen.gen_queue_index import GenQueueIndex
from alasio.ext.file.jsonfile import json_dumps_custom_indent
from alasio.testing.filesystem import fs  # noqa: F401
from tests.config_dev.gen.helpers import make_cross

NAVS = {
    'main': """\
Scheduler:
  args:
    Enable: true
Campaign:
  args:
    Name: main
""",
}

TASKS = {
    'main': """\
Farming:
  groups:
    Scheduler:
    Campaign:
Manual:
  groups:
    Campaign:
""",
}


def build(fs, navs=NAVS, tasks=TASKS, queue=None):
    """
    Build a GenQueueIndex, optionally with an existing queue.index.json.

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture
        navs (dict[str, str]): key: nav name, value of {nav}.args.yaml
        tasks (dict[str, str]): key: nav name, value of {nav}.tasks.yaml
        queue (dict, optional): Content of the existing queue.index.json

    Returns:
        GenQueueIndex:
    """
    gen = make_cross(fs, navs, tasks, GenQueueIndex)
    if queue is not None:
        fs.create_file(gen.queue_index_file, contents=json.dumps(queue, ensure_ascii=False))
    return gen


class TestQueueIndexData:
    def test_only_scheduler_tasks(self, fs):
        """A task without Scheduler group is not in the queue."""
        gen = build(fs)
        assert json_dumps_custom_indent(gen.queue_index_data).decode() == """\
{
  "Farming": {
    "zh-CN": "Farming",
    "en-US": "Farming"
  }
}"""

    def test_existing_translation_is_kept(self, fs):
        """A manual translation of a task name survives the generation."""
        gen = build(fs, queue={'Farming': {'zh-CN': '刷宝石', 'en-US': 'Gems'}})
        assert gen.queue_index_data == {'Farming': {'zh-CN': '刷宝石', 'en-US': 'Gems'}}

    def test_missing_language_falls_back_to_the_name(self, fs):
        """A language missing in the existing file is filled with the task name."""
        gen = build(fs, queue={'Farming': {'zh-CN': '刷宝石'}})
        assert gen.queue_index_data == {'Farming': {'zh-CN': '刷宝石', 'en-US': 'Farming'}}

    def test_task_of_another_nav(self, fs):
        """The queue merges the tasks of every nav."""
        gen = build(fs, navs={
            'main': NAVS['main'],
            'gems': 'SchedulerGems:\n  args:\n    Enable: true\n',
        }, tasks={
            'main': TASKS['main'],
            'gems': 'GemsFarming:\n  groups:\n    SchedulerGems:\n',
        })
        assert list(gen.queue_index_data) == ['Farming', 'GemsFarming']
