"""
Tests of the {nav}.tasks.yaml handling done by ConfigGenerator.generate().

The tasks yaml is a manual file. A mod nav gets an empty one on the first
generation, so the author starts from a valid file. The navs of the alasio
entry do not, because most of them have no task at all. The file is only
formatted, never filled.
"""

from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.config_dev.gen.helpers import GitAddSpy, make_alasio_generator, make_generator


class TestTasksYamlCreate:
    def test_created_for_mod_nav(self, fs):
        """The first generation of a mod nav creates an empty tasks yaml."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    X: 1
""")
        gitadd = GitAddSpy()
        with logger.mock_capture_writer() as capture:
            gen.generate(gitadd=gitadd)
        assert gen.tasks_file.exists()
        assert gen.tasks_file.atomic_read_text() == ''
        assert capture.concat().splitlines()[0] == f'INFO | Write file {gen.tasks_file}'
        assert str(gen.tasks_file) in gitadd.files

    def test_not_created_for_alasio_nav(self, fs):
        """The navs of the alasio entry do not get a tasks yaml."""
        gen = make_alasio_generator(fs, nav='device', yaml="""\
Device:
  args:
    Serial: 127.0.0.1:5555
""")
        gen.generate()
        assert not gen.tasks_file.exists()

    def test_missing_args_yaml_does_not_create_tasks_yaml(self, fs):
        """No tasks yaml is created for a nav that does not exist."""
        gen = make_generator(fs, yaml=None)
        gen.generate()
        assert not gen.file.exists()
        assert not gen.tasks_file.exists()


class TestTasksYamlFormat:
    def test_existing_tasks_yaml_is_formatted(self, fs):
        """The root level tasks of the tasks yaml are separated by one blank line."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    X: 1
""", tasks="""\
Task:
  groups:
    Group:



Other:
  groups:
    Group:
""")
        gen.generate()
        assert gen.tasks_file.atomic_read_text() == """\
Task:
  groups:
    Group:

Other:
  groups:
    Group:
"""

    def test_empty_tasks_yaml_is_not_rewritten(self, fs):
        """The empty tasks yaml created by the first run is not rewritten."""
        make_generator(fs, yaml="Group:\n  args:\n    X: 1\n").generate()
        gitadd = GitAddSpy()
        gen = make_generator(fs, yaml=None)
        with logger.mock_capture_writer() as capture:
            gen.generate(gitadd=gitadd)
        assert capture.concat() == ''
        assert gitadd.files == []
