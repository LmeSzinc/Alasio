"""
Tests of ConfigGenerator.generate() as a whole, the order of the writes and
the files staged to git.

The content of each generated file is covered by test_model_py.py,
test_i18n_json.py, test_args_yaml.py and test_tasks_yaml.py.
"""

import pytest

from alasio.config_dev.parse.base import DefinitionError
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.config_dev.gen.helpers import GitAddSpy, make_generator

# not formatted on purpose: the blank line after the comment and the blank
# line inside the group are dropped by yaml_formatter
YAML = """\
# A group comment

Group:
  args:

    Mode:
      value: normal
      option: [normal, hard]



"""

TASKS = """\
Task:
  groups:
    Group:



Other:
  groups:
    Group:
"""


class TestGenerateOrder:
    def test_write_order(self, fs):
        """The tasks yaml is created first, then the generated files, then the yaml."""
        gen = make_generator(fs, yaml=YAML)
        with logger.mock_capture_writer() as capture:
            gen.generate()
        assert capture.concat() == f"""\
INFO | Write file {gen.tasks_file}
INFO | Write file {gen.model_file}
INFO | Write file {gen.i18n_file}
INFO | Write file {gen.file}
"""
        assert gen.file.atomic_read_text() == """\
# A group comment

Group:
  args:
    Mode:
      value: normal
      option: [normal, hard]
"""

    def test_staged_files(self, fs):
        """The files are staged to git in the same order as they are written."""
        gen = make_generator(fs, yaml=YAML)
        gitadd = GitAddSpy()
        gen.generate(gitadd=gitadd)
        assert gitadd.files == [
            str(gen.tasks_file),
            str(gen.model_file),
            str(gen.i18n_file),
            str(gen.file),
        ]

    def test_tasks_formatted_last(self, fs):
        """An existing tasks yaml is formatted after the generated files."""
        gen = make_generator(fs, yaml=YAML, tasks=TASKS)
        gitadd = GitAddSpy()
        with logger.mock_capture_writer() as capture:
            gen.generate(gitadd=gitadd)
        assert capture.concat() == f"""\
INFO | Write file {gen.model_file}
INFO | Write file {gen.i18n_file}
INFO | Write file {gen.file}
INFO | Write file {gen.tasks_file}
"""
        assert gitadd.files == [
            str(gen.model_file),
            str(gen.i18n_file),
            str(gen.file),
            str(gen.tasks_file),
        ]

    def test_inforef_nav(self, fs):
        """A nav of inforef groups has no model, only i18n and the yaml."""
        gen = make_generator(fs, yaml="Info:\n\n\n")
        with logger.mock_capture_writer() as capture:
            gen.generate()
        assert capture.concat() == f"""\
INFO | Write file {gen.tasks_file}
INFO | Write file {gen.i18n_file}
INFO | Write file {gen.file}
"""
        assert not gen.model_file.exists()
        assert gen.i18n_file.atomic_read_text() == """\
{
  "Info": {
    "_info": {
      "zh-CN": {"name": "Info", "help": ""},
      "en-US": {"name": "Info", "help": ""}
    }
  }
}"""
        assert gen.file.atomic_read_text() == "Info:\n"

    def test_empty_nav(self, fs):
        """An empty args yaml only creates the tasks yaml."""
        gen = make_generator(fs, yaml='')
        with logger.mock_capture_writer() as capture:
            gen.generate()
        assert capture.concat() == f"""\
INFO | Write file {gen.tasks_file}
"""
        assert not gen.model_file.exists()
        assert not gen.i18n_file.exists()
        assert gen.file.atomic_read_text() == ''


class TestGenerateIdempotent:
    def test_second_run_writes_nothing(self, fs):
        """The same input generates the same files, the second run is a no-op."""
        first = make_generator(fs, yaml=YAML)
        first.generate()
        contents = {
            'yaml': first.file.atomic_read_text(),
            'model': first.model_file.atomic_read_text(),
            'i18n': first.i18n_file.atomic_read_text(),
            'tasks': first.tasks_file.atomic_read_text(),
        }
        gitadd = GitAddSpy()
        gen = make_generator(fs, yaml=None)
        with logger.mock_capture_writer() as capture:
            gen.generate(gitadd=gitadd)
        assert capture.concat() == ''
        assert gitadd.files == []
        assert gen.file.atomic_read_text() == contents['yaml']
        assert gen.model_file.atomic_read_text() == contents['model']
        assert gen.i18n_file.atomic_read_text() == contents['i18n']
        assert gen.tasks_file.atomic_read_text() == contents['tasks']

    def test_model_error_stops_before_i18n(self, fs):
        """A broken model file aborts the generation, the i18n is not written."""
        gen = make_generator(fs, yaml=YAML, model="""\
class Group(:
    pass
""")
        with pytest.raises(DefinitionError):
            gen.generate()
        assert not gen.i18n_file.exists()
