"""
Tests of model.index.json (CrossNavGenerator.model_index_data).

The file is the loading plan of the model files of a mod: every model file on
disk, with the classes a loader must inject into it and the file they come
from. A nav inheriting a base class of another nav imports it with a guarded
relative import, so a loader that injects the class skips the import and keeps
the other model module out of sys.modules.

The order is a dependency order: a file always comes after the files it
imports from, so a loader may follow the key order as well.
"""
import pytest

from alasio.config_dev.gen.gen_cross import CrossNavGenerator
from alasio.config_dev.parse.base import DefinitionError
from alasio.ext.file.jsonfile import json_dumps_custom_indent
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.config_dev.gen.helpers import GitAddSpy, make_cross

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

GemsStopCondition:
  parent: StopCondition
  args:
    Count: 3
""",
    'other': """\
Fleet:
  args:
    Number: 1
""",
    'mixed': """\
Mixed:
  parent: [Campaign, Fleet]
  args:
    Name: mixed
""",
    'deep': """\
Deep:
  parent: Mixed
  args:
    Name: deep
""",
}


def build(fs, navs=NAVS, tasks=None):
    """
    Build a CrossNavGenerator and generate the model file of every nav.

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture
        navs (dict[str, str]): key: nav name, value of {nav}.args.yaml
        tasks (dict[str, str], optional): key: nav name, value of {nav}.tasks.yaml

    Returns:
        CrossNavGenerator:
    """
    gen = make_cross(fs, navs, tasks or {}, CrossNavGenerator)
    for nav in gen.dict_nav_config.values():
        nav.generate()
    return gen


def rendered(gen):
    """
    Render model.index.json to text.

    Args:
        gen (CrossNavGenerator): Generator under test

    Returns:
        str: Pretty printed json
    """
    return json_dumps_custom_indent(gen.model_index_data).decode()


class TestModelIndexData:
    def test_plan_of_every_model_file(self, fs):
        """Every model file is listed, a base class of another nav is injected."""
        gen = build(fs)
        assert rendered(gen) == """\
{
  "main/main_model.py": {},
  "gems/gems_model.py": {
    "main/main_model.py": ["Campaign", "StopCondition"]
  },
  "other/other_model.py": {},
  "mixed/mixed_model.py": {
    "main/main_model.py": ["Campaign"],
    "other/other_model.py": ["Fleet"]
  },
  "deep/deep_model.py": {
    "mixed/mixed_model.py": ["Mixed"]
  }
}"""

    def test_dependency_comes_first(self, fs):
        """A file is listed after the files it imports from."""
        gen = build(fs)
        keys = list(gen.model_index_data)
        assert keys.index('main/main_model.py') < keys.index('gems/gems_model.py')
        assert keys.index('mixed/mixed_model.py') < keys.index('deep/deep_model.py')
        # the file without a dependency keeps the folder order
        assert keys == [
            'main/main_model.py',
            'gems/gems_model.py',
            'other/other_model.py',
            'mixed/mixed_model.py',
            'deep/deep_model.py',
        ]

    def test_nav_without_model_file_is_skipped(self, fs):
        """A nav of inforef groups has no model file, so it is not a step."""
        gen = build(fs, navs={'main': NAVS['main'], 'info': 'Info:\n'})
        assert list(gen.model_index_data) == ['main/main_model.py']

    def test_class_names_are_the_parent_groups(self, fs):
        """Only the classes the file really imports are injected."""
        gen = build(fs)
        row = gen.model_index_data['gems/gems_model.py']
        assert list(row) == ['main/main_model.py']
        assert row['main/main_model.py'].value == ['Campaign', 'StopCondition']

    def test_no_model_file_at_all(self, fs):
        """A mod without a model file has no plan."""
        gen = build(fs, navs={'info': 'Info:\n'})
        assert gen.model_index_data == {}

    def test_inherited_nav_without_model_file_is_reported(self, fs):
        """A base class of a nav without a model file can not be imported."""
        gen = make_cross(fs, NAVS, {}, CrossNavGenerator)
        for nav in gen.dict_nav_config.values():
            nav.generate()
        # the model of the parent nav is gone, the child still inherits it
        gen.dict_nav_config['main'].model_file.atomic_remove()
        with pytest.raises(DefinitionError) as e:
            _ = gen.model_index_data
        assert e.value.msg.startswith('Model of nav "main" does not exist')


class TestGenerateModelIndex:
    def test_writes_the_file(self, fs):
        """The plan is written and staged."""
        gen = build(fs)
        gitadd = GitAddSpy()
        with logger.mock_capture_writer() as capture:
            gen.generate_model_index(gitadd=gitadd)
        assert capture.concat() == f'INFO | Write file {gen.model_index_file}\n'
        assert gitadd.files == [str(gen.model_index_file)]
        assert gen.model_index_file.atomic_read_text() == """\
{
  "main/main_model.py": {},
  "gems/gems_model.py": {
    "main/main_model.py": ["Campaign", "StopCondition"]
  },
  "other/other_model.py": {},
  "mixed/mixed_model.py": {
    "main/main_model.py": ["Campaign"],
    "other/other_model.py": ["Fleet"]
  },
  "deep/deep_model.py": {
    "mixed/mixed_model.py": ["Mixed"]
  }
}"""

    def test_second_run_writes_nothing(self, fs):
        """The same plan does not rewrite the file."""
        gen = build(fs)
        gen.generate_model_index()
        gitadd = GitAddSpy()
        with logger.mock_capture_writer() as capture:
            gen.generate_model_index(gitadd=gitadd)
        assert capture.concat() == ''
        assert gitadd.files == []

    def test_deletes_an_orphan_file(self, fs):
        """A mod without a model file no longer has a plan."""
        gen = build(fs, navs={'info': 'Info:\n'})
        fs.create_file(gen.model_index_file, contents='{}\n')
        gitadd = GitAddSpy()
        with logger.mock_capture_writer() as capture:
            gen.generate_model_index(gitadd=gitadd)
        assert capture.concat() == f'INFO | Delete file {gen.model_index_file}\n'
        assert gitadd.files == [str(gen.model_index_file)]
        assert not gen.model_index_file.exists()

    def test_missing_file_is_not_deleted(self, fs):
        """Nothing to delete, nothing is logged."""
        gen = build(fs, navs={'info': 'Info:\n'})
        gitadd = GitAddSpy()
        with logger.mock_capture_writer() as capture:
            gen.generate_model_index(gitadd=gitadd)
        assert capture.concat() == ''
        assert gitadd.files == []
