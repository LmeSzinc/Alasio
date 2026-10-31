"""
Tests of the {nav}.args.yaml formatting done by ConfigGenerator.generate().

The args yaml is a manual file: generate() never rewrites its content, it only
applies the layout of yaml_formatter (one blank line between root level
groups, one trailing newline). A file that is already formatted is not
written.
"""

from alasio.config_dev.gen.gen_config import ConfigGenerator
from alasio.ext.path import PathStr
from alasio.ext.path.calc import joinpath
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.config_dev.gen.helpers import GitAddSpy, make_entry, make_generator, nav_dir


class TestArgsYamlFormat:
    def test_blank_lines_between_groups(self, fs):
        """Blank lines inside a group are dropped, root groups are separated."""
        gen = make_generator(fs, yaml="""\
A:
  args:
    X: 1



B:
  args:
    Y: 2
""")
        gen.generate()
        assert gen.file.atomic_read_text() == """\
A:
  args:
    X: 1

B:
  args:
    Y: 2
"""

    def test_comments_are_kept(self, fs):
        """The comments of the args yaml are kept as they are."""
        gen = make_generator(fs, yaml="""\
# standalone

# group comment
# second line
A:
  args:
    X: 1
# trailing


B:
  args:
    Y: 2
""")
        gen.generate()
        assert gen.file.atomic_read_text() == """\
# standalone

# group comment
# second line
A:
  args:
    X: 1

# trailing

B:
  args:
    Y: 2
"""

    def test_unicode_is_kept(self, fs):
        """The non-ascii text of the args yaml is not escaped."""
        gen = make_generator(fs, yaml="""\
# 主线图设置
A:
  args:
    X: 主线
""")
        gen.generate()
        assert gen.file.atomic_read_text() == """\
# 主线图设置
A:
  args:
    X: 主线
"""

    def test_formatted_file_is_not_written(self, fs):
        """A file that is already formatted is not written again."""
        make_generator(fs, yaml="""\
A:
  args:
    X: 1

B:
  args:
    Y: 2
""").generate()
        gitadd = GitAddSpy()
        gen = make_generator(fs, yaml=None)
        with logger.mock_capture_writer() as capture:
            gen.generate(gitadd=gitadd)
        assert capture.concat() == ''
        assert gitadd.files == []

    def test_empty_file_stays_empty(self, fs):
        """An empty args yaml is valid and is not filled with any content."""
        gen = make_generator(fs, yaml='')
        with logger.mock_capture_writer() as capture:
            gen.generate()
        assert gen.file.atomic_read_text() == ''
        assert not capture.fd.any_contains(f'Write file {gen.file}')

    def test_missing_args_yaml_does_nothing(self, fs):
        """A nav whose args yaml disappeared is skipped as a whole."""
        entry = make_entry(fs)
        file = joinpath(nav_dir(fs, 'testnav'), 'testnav.args.yaml')
        gen = ConfigGenerator(entry, PathStr.new(file))
        with logger.mock_capture_writer() as capture:
            gen.generate()
        assert capture.concat() == ''
        assert not gen.tasks_file.exists()
        assert not gen.model_file.exists()
        assert not gen.i18n_file.exists()
