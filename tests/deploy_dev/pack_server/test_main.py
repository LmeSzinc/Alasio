"""
Tests for PackServer: generate the packs of every config of the run directory.

The steps of the flow (PackRepo, PackRepoGen and DepGen) are faked on the
module, the config reader is real, so the tests check the chain that main.py
builds and the behavior of the run: the configs are run one by one, a config
that fails does not stop the other ones, and a failed run is reported at the
end.
"""
import os
import sys

import pytest

from alasio.deploy_dev.pack_server import main
from alasio.deploy_dev.pack_server.gate import RunDirError, check_run_dir
from alasio.deploy_dev.pack_server.model import PackRepoConfig
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401

FILE = 'LmeSzinc_AzurLaneAutoScript_master.yaml'
OTHER = 'LmeSzinc_StarRailCopilot_master.yaml'
THIRD = 'LmeSzinc_Alasio_master.yaml'


@pytest.fixture
def run_dir(fs, monkeypatch):
    """
    A run directory of the pack server, set as env.PROJECT_ROOT.

    Returns:
        PathStr: Absolute path of the run directory
    """
    root = PathStr.new(fs.root_dir.path).joinpath('pack_server')
    fs.create_dir(root)
    monkeypatch.setattr(env, 'PROJECT_ROOT', root)
    return root


def join_path(root, *parts):
    """
    Join path parts one by one, PathStr.joinpath takes one part only.

    Args:
        root (str): Root path
        *parts (str): Parts to join

    Returns:
        PathStr: Joined path
    """
    path = PathStr.new(root)
    for part in parts:
        path = path.joinpath(part)
    return path


def config_content(repo='AzurLaneAutoScript'):
    """
    The content of a valid config file of a repo.

    Args:
        repo (str): Repo name. Defaults to 'AzurLaneAutoScript'

    Returns:
        str: Yaml text
    """
    return (
        f'Repo:\n'
        f'  Remote: https://github.com/LmeSzinc/{repo}\n'
        f'  Author: LmeSzinc\n'
        f'  Repo: {repo}\n'
        f'  Branch: master\n'
    )


def write_config(run_dir, name, content):
    """
    Write a config file into the config folder of the run directory.

    Args:
        run_dir (str): Run directory of the test
        name (str): File name of the config
        content (str): Yaml text of the config
    """
    folder = join_path(run_dir, 'config')
    os.makedirs(folder, exist_ok=True)
    with open(join_path(folder, name), 'w', encoding='utf-8') as f:
        f.write(content)


def make_config(run_dir, name=FILE, repo='AzurLaneAutoScript'):
    """
    Write a valid config file for a repo.

    Args:
        run_dir (str): Run directory of the test
        name (str): File name of the config. Defaults to FILE
        repo (str): Repo name. Defaults to 'AzurLaneAutoScript'
    """
    write_config(run_dir, name, config_content(repo))


def fake_repo(calls, fail=()):
    """
    Build a fake of PackRepo, the step that clones or fetches the repo.

    Every run is recorded as ('repo', config, repo), a repo whose name is in
    `fail` raises instead.

    Args:
        calls (list): Record of the steps of the test
        fail (Iterable[str]): Repo names whose run fails. Defaults to ()

    Returns:
        type: Fake class to replace main.PackRepo
    """

    class FakeRepo:
        def __init__(self, config):
            self.config = config

        def run(self):
            name = self.config.Repo.Repo
            if name in fail:
                raise RuntimeError(f'fake failure of the repo "{name}"')
            repo = f'repo {name}'
            calls.append(('repo', self.config, repo))
            return repo

    return FakeRepo


def fake_gen(calls, fail=()):
    """
    Build a fake of PackRepoGen, the step that generates the packs.

    Every run is recorded as ('gen', config, repo), the packs of a repo whose
    name is in `fail` raise instead.

    Args:
        calls (list): Record of the steps of the test
        fail (Iterable[str]): Repo names whose run fails. Defaults to ()

    Returns:
        type: Fake class to replace main.PackRepoGen
    """

    class FakeGen:
        def __init__(self, repo, config):
            self.repo = repo
            self.config = config

        def run(self):
            name = self.config.Repo.Repo
            if name in fail:
                raise RuntimeError(f'fake failure of the packs of "{name}"')
            calls.append(('gen', self.config, self.repo))

    return FakeGen


def fake_dep_gen(calls, fail=()):
    """
    Build a fake of DepGen, the step that generates the dependency packs.

    Every run is recorded as ('dep', config, repo), the packs of a repo whose
    name is in `fail` raise instead.

    Args:
        calls (list): Record of the steps of the test
        fail (Iterable[str]): Repo names whose run fails. Defaults to ()

    Returns:
        type: Fake class to replace main.DepGen
    """

    class FakeDepGen:
        def __init__(self, repo, config):
            self.repo = repo
            self.config = config

        def run(self):
            name = self.config.Repo.Repo
            if name in fail:
                raise RuntimeError(f'fake failure of the dependency packs of "{name}"')
            calls.append(('dep', self.config, self.repo))

    return FakeDepGen


class TestIterConfig:
    """The config files of the run directory."""

    def test_config_files(self, fs, run_dir):
        """Only the *.yaml files are config files, sorted by name is the order of the run."""
        make_config(run_dir, THIRD, repo='Alasio')
        make_config(run_dir, FILE)
        make_config(run_dir, OTHER, repo='StarRailCopilot')
        # a file that is not a yaml file, e.g. a note of the operator
        fs.create_file(join_path(run_dir, 'config', 'notes.txt'), contents='')
        # the tmp file of an interrupted write, an incomplete config
        fs.create_file(join_path(run_dir, 'config', f'{FILE}.abcd1234.tmp'), contents='')
        # a folder that is named like a config
        fs.create_dir(join_path(run_dir, 'config', 'folder.yaml'))
        assert list(main.PackServer().iter_config()) == [THIRD, FILE, OTHER]

    def test_no_folder(self, fs, run_dir):
        """A config folder that does not exist has no config, the operator has not set a repo."""
        assert list(main.PackServer().iter_config()) == []

    def test_template_is_not_a_config(self, fs, run_dir):
        """The template is not run as a config, the operator copies it to one and fills it."""
        PackRepoConfig.write_template()
        make_config(run_dir, FILE)
        assert list(main.PackServer().iter_config()) == [FILE]


class TestRunConfig:
    """The flow of one config."""

    def test_chain(self, fs, run_dir, monkeypatch):
        """The config of the file is read, then the repo is prepared, then the packs are generated."""
        make_config(run_dir)
        calls = []
        monkeypatch.setattr(main, 'PackRepo', fake_repo(calls))
        monkeypatch.setattr(main, 'PackRepoGen', fake_gen(calls))
        monkeypatch.setattr(main, 'DepGen', fake_dep_gen(calls))
        main.PackServer().run_config(FILE)

        assert [(step, config.Repo.Repo, repo) for step, config, repo in calls] == [
            ('repo', 'AzurLaneAutoScript', 'repo AzurLaneAutoScript'),
            ('gen', 'AzurLaneAutoScript', 'repo AzurLaneAutoScript'),
            ('dep', 'AzurLaneAutoScript', 'repo AzurLaneAutoScript'),
        ]
        # the generators get the repo object of PackRepo.run and the config
        # the reader returned, not a copy of it
        assert calls[1][2] == calls[0][2]
        assert calls[1][1] is calls[0][1]
        assert calls[2][2] == calls[0][2]
        assert calls[2][1] is calls[0][1]

    def test_invalid_config(self, fs, run_dir, monkeypatch):
        """A config with an empty value fails in the reader, no step is run."""
        write_config(run_dir, FILE, 'Repo:\n  Remote: https://github.com/LmeSzinc/Repo\n  Repo: Repo\n  Branch: master\n')
        calls = []
        monkeypatch.setattr(main, 'PackRepo', fake_repo(calls))
        monkeypatch.setattr(main, 'PackRepoGen', fake_gen(calls))
        with pytest.raises(ValueError, match='Empty Author'):
            main.PackServer().run_config(FILE)
        assert calls == []


class TestPackServerRun:
    """The run of every config of the run directory."""

    def test_all_configs(self, fs, run_dir, monkeypatch):
        """Every config is run, in the sorted order of the files."""
        make_config(run_dir, FILE)
        make_config(run_dir, OTHER, repo='StarRailCopilot')
        calls = []
        monkeypatch.setattr(main, 'PackRepo', fake_repo(calls))
        monkeypatch.setattr(main, 'PackRepoGen', fake_gen(calls))
        monkeypatch.setattr(main, 'DepGen', fake_dep_gen(calls))
        with logger.mock_capture_writer() as capture:
            main.PackServer().run()

        assert [step for step, _, _ in calls] == [
            'repo', 'gen', 'dep', 'repo', 'gen', 'dep']
        assert [config.Repo.Repo for _, config, _ in calls] == [
            'AzurLaneAutoScript', 'AzurLaneAutoScript', 'AzurLaneAutoScript',
            'StarRailCopilot', 'StarRailCopilot', 'StarRailCopilot']
        assert capture.fd.any_contains('Ran 2 configs: 2 done, 0 failed')

    def test_failed_config_does_not_stop_the_rest(self, fs, run_dir, monkeypatch):
        """A config that fails is skipped, the other configs still run, the run fails at the end."""
        make_config(run_dir, THIRD, repo='Alasio')
        make_config(run_dir, FILE)
        make_config(run_dir, OTHER, repo='StarRailCopilot')
        calls = []
        # the repo of the second config cannot be fetched, the packs of the third cannot be built
        monkeypatch.setattr(main, 'PackRepo', fake_repo(calls, fail=('AzurLaneAutoScript',)))
        monkeypatch.setattr(main, 'PackRepoGen', fake_gen(calls, fail=('StarRailCopilot',)))
        monkeypatch.setattr(main, 'DepGen', fake_dep_gen(calls))
        with logger.mock_capture_writer() as capture:
            with pytest.raises(main.PackRunError) as e:
                main.PackServer().run()

        # both failures are reported with the file of the config
        assert [name for name, _ in e.value.errors] == [FILE, OTHER]
        assert all(isinstance(error, RuntimeError) for _, error in e.value.errors)
        assert 'Failed to run 2 configs' in str(e.value)
        # the repo of the third config is fetched, then its packs fail; the
        # dependency packs of the first config are generated after its packs
        assert [(step, config.Repo.Repo) for step, config, _ in calls] == [
            ('repo', 'Alasio'), ('gen', 'Alasio'), ('dep', 'Alasio'), ('repo', 'StarRailCopilot')]
        assert capture.fd.any_contains(f'Config "{FILE}" failed')
        assert capture.fd.any_contains(f'Config "{OTHER}" failed')
        assert capture.fd.any_contains('Ran 3 configs: 1 done, 2 failed')

    def test_no_config(self, fs, run_dir, monkeypatch):
        """A run directory without a config file writes the template and runs nothing."""
        calls = []
        monkeypatch.setattr(main, 'PackRepo', fake_repo(calls))
        monkeypatch.setattr(main, 'PackRepoGen', fake_gen(calls))
        with logger.mock_capture_writer() as capture:
            main.PackServer().run()
        assert calls == []
        assert capture.fd.any_contains('Write config')
        assert capture.fd.any_contains('No config file')
        # the operator has the template to copy
        assert join_path(run_dir, 'config', PackRepoConfig.TEMPLATE_FILE).isfile()

    def test_template_on_every_run(self, fs, run_dir, monkeypatch):
        """The template is written on the start of a run, a run that finds it up to date writes nothing."""
        make_config(run_dir, FILE)
        calls = []
        monkeypatch.setattr(main, 'PackRepo', fake_repo(calls))
        monkeypatch.setattr(main, 'PackRepoGen', fake_gen(calls))
        monkeypatch.setattr(main, 'DepGen', fake_dep_gen(calls))
        with logger.mock_capture_writer() as capture:
            main.PackServer().run()
        assert capture.fd.any_contains('Write config')
        assert join_path(run_dir, 'config', PackRepoConfig.TEMPLATE_FILE).isfile()
        # the second run finds the template up to date
        with logger.mock_capture_writer() as capture:
            main.PackServer().run()
        assert not capture.fd.any_contains('Write config')
        assert [step for step, _, _ in calls] == [
            'repo', 'gen', 'dep', 'repo', 'gen', 'dep']

    def test_run_dir_is_a_mod(self, fs, run_dir, monkeypatch):
        """The run refuses a mod directory, like the modules it chains."""
        fs.create_file(join_path(run_dir, 'module', 'main.py'), contents='')
        # check_run_dir runs once per process (init_once), the bare check runs
        # the gate for the run directory of this test
        monkeypatch.setattr(main, 'check_run_dir', check_run_dir.__wrapped__)
        with pytest.raises(RunDirError):
            main.PackServer().run()


class TestMain:
    """The command line entry."""

    def test_main_root(self, fs, run_dir, monkeypatch):
        """--root sets the run directory of the run."""
        make_config(run_dir)
        calls = []
        monkeypatch.setattr(main, 'PackRepo', fake_repo(calls))
        monkeypatch.setattr(main, 'PackRepoGen', fake_gen(calls))
        monkeypatch.setattr(main, 'DepGen', fake_dep_gen(calls))
        monkeypatch.setattr(sys, 'argv', ['main', '--root', str(run_dir)])
        main.main()
        assert env.PROJECT_ROOT == str(run_dir)
        assert [step for step, _, _ in calls] == ['repo', 'gen', 'dep']

    def test_main_bad_args(self, fs, run_dir, monkeypatch):
        """An argument that is not known raises SystemExit."""
        monkeypatch.setattr(sys, 'argv', ['main', '--nope'])
        with pytest.raises(SystemExit):
            main.main()

    def test_root_before_first_log(self, fs, run_dir, monkeypatch):
        """--root is set before the first log: the log file of the process is
        decided by the first write, see LogWriter.file."""
        calls = []

        class FakeServer:
            def run(self):
                calls.append('run')

        monkeypatch.setattr(main.env, 'set_project_root', lambda root: calls.append('root'))
        monkeypatch.setattr(main.logger, 'hr', lambda *args, **kwargs: calls.append('log'))
        monkeypatch.setattr(main, 'PackServer', FakeServer)
        monkeypatch.setattr(sys, 'argv', ['main', '--root', str(run_dir)])
        main.main()
        assert calls == ['root', 'log', 'run']
