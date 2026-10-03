"""
Tests for the pack server config models, see PackRepoModel.

PackRepoConfig reads the config of a repo from the config folder of the run
directory, the file is created from the model with the help comments when it
does not exist, and invalid values fall back to their defaults. Author, Repo,
Remote and Branch must not be empty: every user of the config needs them, so
the reader refuses an empty one and the users do not check them again.
"""
import msgspec
import pytest

from alasio.deploy_dev.pack_server import model
from alasio.deploy_dev.pack_server.gate import RunDirError, check_run_dir
from alasio.deploy_dev.pack_server.model import LookbackConfig, PackRepoConfig, PackRepoModel, RepoConfig
from alasio.ext import env
from alasio.ext.file.yamlconfig import build_help_map
from alasio.ext.file.yamlfile import yaml_loads
from alasio.ext.path import PathStr
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401

FILE = 'LmeSzinc_AzurLaneAutoScript.yaml'


@pytest.fixture
def run_dir(fs, monkeypatch):
    """
    A run directory of the pack server, set as env.PROJECT_ROOT.

    Returns:
        str: Absolute path of the run directory
    """
    root = PathStr.new(fs.root_dir.path).joinpath('pack_server')
    fs.create_dir(root)
    monkeypatch.setattr(env, 'PROJECT_ROOT', root)
    return root


def config_file(run_dir, name=FILE):
    """
    Path of a config file in the config folder of the run directory.

    Returns:
        str:
    """
    return f'{run_dir}/config/{name}'


def read_config(file):
    """
    Read a config file of the fake filesystem

    Returns:
        Any: The parsed yaml data, in builtin types
    """
    with open(file, encoding='utf-8') as f:
        return yaml_loads(f.read().encode('utf-8'))


def iter_help_line():
    """
    Iter the help lines of PackRepoModel, the comment lines YamlConfig writes
    above the keys of the generated file

    Yields:
        str: Help text line, the text after the "# " of a comment line
    """
    for help_text in build_help_map(PackRepoModel).values():
        yield from ([help_text] if isinstance(help_text, str) else help_text)


class TestLookbackConfig:
    """The lookback restrictions of a repo."""

    def test_default(self):
        """The default restrictions are 0 / 90 / 0 / 365 days, and no branch or commit."""
        lookback = LookbackConfig()
        assert lookback.MaxCommitCount == 0
        assert lookback.MaxCommitDay == 90
        assert lookback.MaxTagCount == 0
        assert lookback.MaxTagDay == 365
        assert lookback.Parent == 'parent-all'
        assert lookback.LookbackBranch == []
        assert lookback.AdditionalCommit == []

    def test_default_not_shared(self):
        """The default list is not shared among instances, a filled one stays empty."""
        first = LookbackConfig()
        first.AdditionalCommit.append('abc')
        assert LookbackConfig().AdditionalCommit == []


class TestPackRepoModel:
    """The pack config of a repo."""

    def test_default(self):
        """The default config of a repo is empty, with the default restrictions."""
        model = PackRepoModel()
        assert model.Repo == RepoConfig()
        assert model.Lookback == LookbackConfig()


class TestPackRepoConfig:
    """Reading the config of a repo from the config folder of the run directory."""

    def test_read_missing_file(self, fs, run_dir):
        """A file that does not exist is created from the model, then the empty config is refused."""
        with logger.mock_capture_writer() as capture:
            with pytest.raises(ValueError, match='Empty Author'):
                PackRepoConfig(FILE)
        assert capture.fd.any_contains('Invalid pack config value')
        assert capture.fd.any_contains(FILE)
        # the created file holds the default config, the operator fills it
        assert read_config(config_file(run_dir)) == msgspec.to_builtins(PackRepoModel())
        # and the help comments of every field of the model, the comment text
        # is taken from the model so that a change of the help texts does not
        # need a change of this test
        with open(config_file(run_dir), encoding='utf-8') as f:
            text = f.read()
        help_lines = list(iter_help_line())
        assert help_lines
        for line in help_lines:
            assert f'# {line}' in text

    @pytest.mark.parametrize('empty, contents', [
        ('Empty Author', """\
Repo:
  Remote: https://github.com/LmeSzinc/AzurLaneAutoScript
  Repo: AzurLaneAutoScript
  Branch: master
"""),
        ('Empty Repo name', """\
Repo:
  Remote: https://github.com/LmeSzinc/AzurLaneAutoScript
  Author: LmeSzinc
  Branch: master
"""),
        ('Empty Remote', """\
Repo:
  Author: LmeSzinc
  Repo: AzurLaneAutoScript
  Branch: master
"""),
        ('Empty Branch', """\
Repo:
  Remote: https://github.com/LmeSzinc/AzurLaneAutoScript
  Author: LmeSzinc
  Repo: AzurLaneAutoScript
  Branch: ''
"""),
    ])
    def test_empty_repo_value(self, fs, run_dir, empty, contents):
        """An empty Author / Repo / Remote / Branch is refused, every user of the config needs them."""
        fs.create_file(config_file(run_dir), contents=contents)
        with pytest.raises(ValueError, match=f'{empty}.*{FILE}'):
            PackRepoConfig(FILE)

    def test_write_template(self, fs, run_dir):
        """The template of a config file is written, an up to date one is left alone."""
        file = f'{run_dir}/config/{PackRepoConfig.TEMPLATE_FILE}'
        with logger.mock_capture_writer() as capture:
            assert PackRepoConfig.write_template() is True
        assert capture.fd.any_contains('Write config')
        # the template holds the default config and the help comments of the model
        assert read_config(file) == msgspec.to_builtins(PackRepoModel())
        with open(file, encoding='utf-8') as f:
            text = f.read()
        for line in iter_help_line():
            assert f'# {line}' in text
        # the second write finds the template up to date and leaves it alone
        assert PackRepoConfig.write_template() is False

    def test_write_template_updates(self, fs, run_dir):
        """A template that is not up to date, e.g. an edit of the operator, is replaced."""
        file = f'{run_dir}/config/{PackRepoConfig.TEMPLATE_FILE}'
        fs.create_file(file, contents='Repo:\n  Author: LmeSzinc\n')
        assert PackRepoConfig.write_template() is True
        assert read_config(file) == msgspec.to_builtins(PackRepoModel())

    def test_write_template_bad_encoding(self, fs, run_dir):
        """A template that is not valid utf-8 is replaced, the file is generated again."""
        file = f'{run_dir}/config/{PackRepoConfig.TEMPLATE_FILE}'
        fs.create_file(file, contents=b'\xff\xff\xff\xff')
        assert PackRepoConfig.write_template() is True
        assert read_config(file) == msgspec.to_builtins(PackRepoModel())

    def test_read_values(self, fs, run_dir):
        """The values of the file in the config folder are read into the model."""
        fs.create_file(config_file(run_dir), contents="""\
Repo:
  Remote: https://github.com/LmeSzinc/AzurLaneAutoScript
  Author: LmeSzinc
  Repo: AzurLaneAutoScript
  Branch: dev
Lookback:
  MaxCommitCount: 5
  MaxCommitDay: 0
  MaxTagCount: 1
  MaxTagDay: 30
  LookbackBranch:
    - master
  Parent: parent-0
  AdditionalCommit:
    - 4f3a8b2c1d5e6f708192a3b4c5d6e7f8091a2b3c
""")
        config = PackRepoConfig(FILE)
        assert config.errors == []
        assert config.data.Repo.Remote == 'https://github.com/LmeSzinc/AzurLaneAutoScript'
        assert config.data.Repo.Author == 'LmeSzinc'
        assert config.data.Repo.Repo == 'AzurLaneAutoScript'
        assert config.data.Repo.Branch == 'dev'
        assert config.data.Lookback.MaxCommitCount == 5
        assert config.data.Lookback.MaxCommitDay == 0
        assert config.data.Lookback.MaxTagCount == 1
        assert config.data.Lookback.MaxTagDay == 30
        assert config.data.Lookback.LookbackBranch == ['master']
        assert config.data.Lookback.Parent == 'parent-0'
        assert config.data.Lookback.AdditionalCommit == ['4f3a8b2c1d5e6f708192a3b4c5d6e7f8091a2b3c']

    def test_read_invalid_parent_rule(self, fs, run_dir):
        """A parent rule that is not one of the model values falls back to the default."""
        fs.create_file(config_file(run_dir), contents="""\
Repo:
  Remote: https://github.com/LmeSzinc/AzurLaneAutoScript
  Author: LmeSzinc
  Repo: AzurLaneAutoScript
  Branch: master
Lookback:
  Parent: parent-1
""")
        with logger.mock_capture_writer() as capture:
            config = PackRepoConfig(FILE)
        assert capture.fd.any_contains('Invalid pack config value')
        assert capture.fd.any_contains('Invalid enum value \'parent-1\'')
        assert len(config.errors) == 1
        assert config.data.Lookback.Parent == 'parent-all'

    def test_read_valid_file_keeps_content(self, fs, run_dir):
        """A valid file is not written back, the comments are added only when needed."""
        content = """\
Repo:
  Remote: https://github.com/LmeSzinc/AzurLaneAutoScript
  Author: LmeSzinc
  Repo: AzurLaneAutoScript
  Branch: master
"""
        fs.create_file(config_file(run_dir), contents=content)
        config = PackRepoConfig(FILE)
        assert config.errors == []
        assert config.data.Repo.Author == 'LmeSzinc'
        with open(config.file, encoding='utf-8') as f:
            assert f.read() == content

    def test_write_list_style(self, fs, run_dir):
        """A list is written back as "- item", not as "[item]"."""
        fs.create_file(config_file(run_dir), contents="""\
Repo:
  Remote: https://github.com/LmeSzinc/AzurLaneAutoScript
  Author: LmeSzinc
  Repo: AzurLaneAutoScript
  Branch: master
Lookback:
  MaxCommitDay: abc
  AdditionalCommit:
    - abc
    - def
""")
        with logger.mock_capture_writer():
            config = PackRepoConfig(FILE)
        assert config.data.Lookback.AdditionalCommit == ['abc', 'def']
        with open(config.file, encoding='utf-8') as f:
            lines = f.read().splitlines()
        # the items are written as block style list items, whatever the indent is
        assert [line.strip() for line in lines if line.strip() in ('- abc', '- def')] == ['- abc', '- def']
        assert '[abc' not in '\n'.join(lines)
        # the written file is read back into the same list
        config = PackRepoConfig(FILE)
        assert config.errors == []
        assert config.data.Lookback.AdditionalCommit == ['abc', 'def']

    def test_invalid_value_falls_back(self, fs, run_dir):
        """A value that fails the validation falls back to the default and is written back."""
        fs.create_file(config_file(run_dir), contents="""\
Repo:
  Remote: https://github.com/LmeSzinc/AzurLaneAutoScript
  Author: LmeSzinc
  Repo: AzurLaneAutoScript
  Branch: master
Lookback:
  MaxCommitDay: abc
""")
        with logger.mock_capture_writer() as capture:
            config = PackRepoConfig(FILE)
        # the error is logged with the file name, the file is named by the caller
        assert capture.fd.any_contains('Invalid pack config value')
        assert capture.fd.any_contains(FILE)
        assert len(config.errors) == 1
        assert config.data.Lookback.MaxCommitDay == 90
        # the file is written back with the default value
        expected = PackRepoModel(Repo=RepoConfig(
            Remote='https://github.com/LmeSzinc/AzurLaneAutoScript',
            Author='LmeSzinc', Repo='AzurLaneAutoScript', Branch='master'))
        assert read_config(config.file) == msgspec.to_builtins(expected)

    def test_invalid_yaml_is_repaired(self, fs, run_dir):
        """A file that is not valid yaml is replaced by the model defaults, then the empty config is refused."""
        fs.create_file(config_file(run_dir), contents='Lookback: [unclosed\n')
        with logger.mock_capture_writer() as capture:
            with pytest.raises(ValueError, match='Empty Author'):
                PackRepoConfig(FILE)
        assert capture.fd.any_contains('Invalid pack config value')
        assert read_config(config_file(run_dir)) == msgspec.to_builtins(PackRepoModel())

    def test_config_of_each_file(self, fs, run_dir):
        """Every file has its own config, the reader is not bound to a single file."""
        other = 'LmeSzinc_StarRailCopilot.yaml'
        fs.create_file(config_file(run_dir), contents="""\
Repo:
  Remote: https://github.com/LmeSzinc/AzurLaneAutoScript
  Author: LmeSzinc
  Repo: AzurLaneAutoScript
  Branch: master
""")
        fs.create_file(config_file(run_dir, other), contents="""\
Repo:
  Remote: https://github.com/LmeSzinc/StarRailCopilot
  Author: LmeSzinc
  Repo: StarRailCopilot
  Branch: master
""")
        first = PackRepoConfig(FILE)
        second = PackRepoConfig(other)
        assert first.file == config_file(run_dir)
        assert second.file == config_file(run_dir, other)
        assert first.data.Repo.Repo == 'AzurLaneAutoScript'
        assert second.data.Repo.Repo == 'StarRailCopilot'

    def test_file_outside_config_folder(self, fs, run_dir):
        """A path that escapes the config folder is refused."""
        fs.create_file(f'{run_dir}/{FILE}', contents='Repo:\n  Author: LmeSzinc\n')
        with pytest.raises(ValueError):
            PackRepoConfig(f'../{FILE}')

    def test_run_dir_is_a_mod(self, fs, run_dir, monkeypatch):
        """The pack server refuses to read the config of a mod directory."""
        fs.create_file(f'{run_dir}/module/main.py', contents='')
        # check_run_dir runs once per process (init_once), the bare check runs
        # the gate for the run directory of this test
        monkeypatch.setattr(model, 'check_run_dir', check_run_dir.__wrapped__)
        with pytest.raises(RunDirError):
            PackRepoConfig(FILE)
