"""
Tests for PackServer: generate the packs of every config of the run directory.

The steps of the flow (PackRepo and Repo) are faked on the module, the config
reader is real, so the tests check the chain that main.py builds and the
behavior of the run: the configs are run one by one, a config that fails does
not stop the other ones, and a failed run is reported at the end.
"""
import os
import sys

import pytest

from alasio.deploy_dev.pack_server import main, run as run_module
from alasio.deploy_dev.pack_server.gate import RunDirError, check_run_dir
from alasio.deploy_dev.pack_server.model import ConfigError, PackRepoConfig
from alasio.deploy_dev.pack_server.publish.lock import ChannelLock
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack_server.conftest import use_memory_lock

pytestmark = [
    pytest.mark.parallel,
]

FILE = 'LmeSzinc_AzurLaneAutoScript_master.yaml'
OTHER = 'LmeSzinc_StarRailCopilot_master.yaml'
THIRD = 'LmeSzinc_Alasio_master.yaml'


@pytest.fixture
def run_dir(fs, monkeypatch):
    """
    A run directory of the pack server, set as env.PROJECT_ROOT.

    The lock of a channel is swapped for the in-memory lock of the tests, see
    the conftest of the pack server.

    Returns:
        PathStr: Absolute path of the run directory
    """
    root = PathStr.new(fs.root_dir.path).joinpath('pack_server')
    fs.create_dir(root)
    monkeypatch.setattr(env, 'PROJECT_ROOT', root)
    use_memory_lock(monkeypatch)
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


def log_events(log):
    """
    The event lines of a log as one text, without the traceback panels.

    The body of a rich traceback panel (the source context and the locals of a
    frame) is rendered from the live frames and the line cache of the process:
    whether it shows up depends on the rich version and on what the process has
    cached, not on the pack server. A failure is asserted through its log line
    and the exception reported by PackRunError.

    Args:
        log (str): Captured log text

    Returns:
        str: INFO / ERROR / WARNING lines and the hr separators, in order,
            joined by a newline
    """
    return '\n'.join(
        line for line in log.splitlines()
        if line.startswith(('INFO | ', 'ERROR | ', 'WARNING | ', '='))
    )


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
    Build a fake of Repo, the flow that generates the workspace of a repo.

    Every run is recorded as ('gen', config, repo), the generation of a repo
    whose name is in `fail` raises instead.

    Args:
        calls (list): Record of the steps of the test
        fail (Iterable[str]): Repo names whose run fails. Defaults to ()

    Returns:
        type: Fake class to replace main.Repo
    """

    class FakeGen:
        def __init__(self, repo, config, client=None):
            self.repo = repo
            self.config = config

        def run(self):
            name = self.config.Repo.Repo
            if name in fail:
                raise RuntimeError(f'fake failure of the packs of "{name}"')
            calls.append(('gen', self.config, self.repo))

        def commit(self):
            calls.append(('gen-commit', self.config, self.repo))

    return FakeGen


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
        """The config is read, the repo is prepared, then its workspace is generated and published."""
        make_config(run_dir)
        calls = []
        monkeypatch.setattr(main, 'PackRepo', fake_repo(calls))
        monkeypatch.setattr(main, 'Repo', fake_gen(calls))
        main.PackServer().run_config(FILE)

        assert [(step, config.Repo.Repo, repo) for step, config, repo in calls] == [
            ('repo', 'AzurLaneAutoScript', 'repo AzurLaneAutoScript'),
            ('gen', 'AzurLaneAutoScript', 'repo AzurLaneAutoScript'),
            ('gen-commit', 'AzurLaneAutoScript', 'repo AzurLaneAutoScript'),
        ]
        # the flow gets the repo object of PackRepo.run and the config the
        # reader returned, not a copy of it
        assert calls[1][2] == calls[0][2]
        assert calls[1][1] is calls[0][1]
        assert calls[2][2] == calls[0][2]
        assert calls[2][1] is calls[0][1]

    def test_generation_failure_skips_the_publication(self, fs, run_dir, monkeypatch):
        """A failed generation publishes nothing, the config fails."""
        make_config(run_dir)
        calls = []
        monkeypatch.setattr(main, 'PackRepo', fake_repo(calls))
        monkeypatch.setattr(main, 'Repo', fake_gen(calls, fail=('AzurLaneAutoScript',)))

        with pytest.raises(RuntimeError, match='fake failure of the packs'):
            main.PackServer().run_config(FILE)
        assert [step for step, *_ in calls] == ['repo']

    def test_invalid_config(self, fs, run_dir, monkeypatch):
        """A config with an empty value fails in the reader, no step is run."""
        write_config(run_dir, FILE, 'Repo:\n  Remote: https://github.com/LmeSzinc/Repo\n  Repo: Repo\n  Branch: master\n')
        calls = []
        monkeypatch.setattr(main, 'PackRepo', fake_repo(calls))
        monkeypatch.setattr(main, 'Repo', fake_gen(calls))
        # a value the operator has to fill in, reported as a config error so the
        # CLI gives it the exit code of a file to edit
        with pytest.raises(ConfigError, match='Empty Author'):
            main.PackServer().run_config(FILE)
        assert calls == []

    def test_the_generation_holds_the_lock_of_the_channel(self, fs, run_dir, monkeypatch):
        """The generation runs under the lock, and the lock is released after it.

        A publication of the same workspace, e.g. the one of PackRun, must never
        read it halfway through a run.
        """
        make_config(run_dir)
        calls = []
        held = []
        repo_class = fake_repo(calls)
        workspace = 'LmeSzinc_AzurLaneAutoScript_master'

        class LockedRepo(repo_class):
            def __init__(self, config):
                super().__init__(config)
                held.append(ChannelLock(workspace).is_locked)

        monkeypatch.setattr(main, 'PackRepo', LockedRepo)
        monkeypatch.setattr(main, 'Repo', fake_gen(calls))
        main.PackServer().run_config(FILE)
        assert held == [True]
        assert [step for step, *_ in calls] == ['repo', 'gen', 'gen-commit']
        assert not ChannelLock(workspace).is_locked


class TestPackServerRun:
    """The run of every config of the run directory."""

    def test_all_configs(self, fs, run_dir, monkeypatch):
        """Every config is run, in the sorted order of the files."""
        make_config(run_dir, FILE)
        make_config(run_dir, OTHER, repo='StarRailCopilot')
        calls = []
        monkeypatch.setattr(main, 'PackRepo', fake_repo(calls))
        monkeypatch.setattr(main, 'Repo', fake_gen(calls))
        with logger.mock_capture_writer() as capture:
            main.PackServer().run()

        assert [step for step, *_ in calls] == [
            'repo', 'gen', 'gen-commit',
            'repo', 'gen', 'gen-commit']
        assert [config.Repo.Repo for _, config, *_ in calls] == [
            'AzurLaneAutoScript', 'AzurLaneAutoScript', 'AzurLaneAutoScript',
            'StarRailCopilot', 'StarRailCopilot', 'StarRailCopilot']
        assert capture.concat() == f"""\
INFO | Write config {run_dir}/config/template.yaml
INFO | Running 2 configs of "{run_dir}"
+==================================================================================================+
|                     RUNNING CONFIG "LMESZINC_AZURLANEAUTOSCRIPT_MASTER.YAML"                     |
+==================================================================================================+
INFO | RUNNING CONFIG "LMESZINC_AZURLANEAUTOSCRIPT_MASTER.YAML"
============================================= GENERATE =============================================
INFO | GENERATE
INFO | Config "LmeSzinc_AzurLaneAutoScript_master.yaml" done
+==================================================================================================+
|                      RUNNING CONFIG "LMESZINC_STARRAILCOPILOT_MASTER.YAML"                       |
+==================================================================================================+
INFO | RUNNING CONFIG "LMESZINC_STARRAILCOPILOT_MASTER.YAML"
============================================= GENERATE =============================================
INFO | GENERATE
INFO | Config "LmeSzinc_StarRailCopilot_master.yaml" done
INFO | Ran 2 configs: 2 done, 0 failed
"""

    def test_failed_config_does_not_stop_the_rest(self, fs, run_dir, monkeypatch):
        """A config that fails is skipped, the other configs still run, the run fails at the end."""
        make_config(run_dir, THIRD, repo='Alasio')
        make_config(run_dir, FILE)
        make_config(run_dir, OTHER, repo='StarRailCopilot')
        calls = []
        # the repo of the second config cannot be fetched, the workspace of
        # the third cannot be generated
        monkeypatch.setattr(main, 'PackRepo', fake_repo(calls, fail=('AzurLaneAutoScript',)))
        monkeypatch.setattr(main, 'Repo', fake_gen(calls, fail=('StarRailCopilot',)))
        with logger.mock_capture_writer() as capture:
            with pytest.raises(main.PackRunError) as e:
                main.PackServer().run()

        # both failures are reported with the file of the config
        assert [name for name, _ in e.value.errors] == [FILE, OTHER]
        assert all(isinstance(error, RuntimeError) for _, error in e.value.errors)
        assert 'Failed to run 2 configs' in str(e.value)
        # the repo of the third config is fetched, then its generation fails
        assert [(step, config.Repo.Repo) for step, config, *_ in calls] == [
            ('repo', 'Alasio'), ('gen', 'Alasio'), ('gen-commit', 'Alasio'),
            ('repo', 'StarRailCopilot')]
        log = capture.concat()
        # both failures are logged with a traceback of the failing config
        assert log.count('Traceback (most recent call last)') == 2
        assert 'RuntimeError: fake failure of the repo "AzurLaneAutoScript"' in log
        assert 'RuntimeError: fake failure of the packs of "StarRailCopilot"' in log
        # the failures do not stop the last config, the run fails at the end
        assert log_events(log) == f"""\
INFO | Write config {run_dir}/config/template.yaml
INFO | Running 3 configs of "{run_dir}"
INFO | RUNNING CONFIG "LMESZINC_ALASIO_MASTER.YAML"
============================================= GENERATE =============================================
INFO | GENERATE
INFO | Config "LmeSzinc_Alasio_master.yaml" done
INFO | RUNNING CONFIG "LMESZINC_AZURLANEAUTOSCRIPT_MASTER.YAML"
============================================= GENERATE =============================================
INFO | GENERATE
ERROR | Config "LmeSzinc_AzurLaneAutoScript_master.yaml" failed
INFO | RUNNING CONFIG "LMESZINC_STARRAILCOPILOT_MASTER.YAML"
============================================= GENERATE =============================================
INFO | GENERATE
ERROR | Config "LmeSzinc_StarRailCopilot_master.yaml" failed
INFO | Ran 3 configs: 1 done, 2 failed"""

    def test_no_config(self, fs, run_dir, monkeypatch):
        """A run directory without a config file writes the template and runs nothing."""
        calls = []
        monkeypatch.setattr(main, 'PackRepo', fake_repo(calls))
        monkeypatch.setattr(main, 'Repo', fake_gen(calls))
        with logger.mock_capture_writer() as capture:
            main.PackServer().run()
        assert calls == []
        assert capture.concat() == f"""\
INFO | Write config {run_dir}/config/template.yaml
WARNING | No config file in "{run_dir}/config", copy "template.yaml" to a config file and fill it
"""
        # the operator has the template to copy
        assert join_path(run_dir, 'config', PackRepoConfig.TEMPLATE_FILE).isfile()

    def test_template_on_every_run(self, fs, run_dir, monkeypatch):
        """The template is written on the start of a run, a run that finds it up to date writes nothing."""
        make_config(run_dir, FILE)
        calls = []
        monkeypatch.setattr(main, 'PackRepo', fake_repo(calls))
        monkeypatch.setattr(main, 'Repo', fake_gen(calls))
        with logger.mock_capture_writer() as capture:
            main.PackServer().run()
        assert capture.concat() == f"""\
INFO | Write config {run_dir}/config/template.yaml
INFO | Running 1 config of "{run_dir}"
+==================================================================================================+
|                     RUNNING CONFIG "LMESZINC_AZURLANEAUTOSCRIPT_MASTER.YAML"                     |
+==================================================================================================+
INFO | RUNNING CONFIG "LMESZINC_AZURLANEAUTOSCRIPT_MASTER.YAML"
============================================= GENERATE =============================================
INFO | GENERATE
INFO | Config "LmeSzinc_AzurLaneAutoScript_master.yaml" done
INFO | Ran 1 config: 1 done, 0 failed
"""
        assert join_path(run_dir, 'config', PackRepoConfig.TEMPLATE_FILE).isfile()
        # the second run finds the template up to date
        with logger.mock_capture_writer() as capture:
            main.PackServer().run()
        assert capture.concat() == f"""\
INFO | Running 1 config of "{run_dir}"
+==================================================================================================+
|                     RUNNING CONFIG "LMESZINC_AZURLANEAUTOSCRIPT_MASTER.YAML"                     |
+==================================================================================================+
INFO | RUNNING CONFIG "LMESZINC_AZURLANEAUTOSCRIPT_MASTER.YAML"
============================================= GENERATE =============================================
INFO | GENERATE
INFO | Config "LmeSzinc_AzurLaneAutoScript_master.yaml" done
INFO | Ran 1 config: 1 done, 0 failed
"""
        assert [step for step, *_ in calls] == [
            'repo', 'gen', 'gen-commit',
            'repo', 'gen', 'gen-commit']

    def test_run_dir_is_a_mod(self, fs, run_dir, monkeypatch):
        """The run refuses a mod directory, like the modules it chains."""
        fs.create_file(
            join_path(run_dir, 'module', 'config', '_index', 'mod_entry.yaml'), contents='name: m\n')
        # check_run_dir runs once per process (init_once), the bare check runs
        # the gate for the run directory of this test
        monkeypatch.setattr(main, 'check_run_dir', check_run_dir.__wrapped__)
        with pytest.raises(RunDirError):
            main.PackServer().run()


class TestMain:
    """The command line entry."""

    def test_main_root(self, fs, run_dir, monkeypatch):
        """--root sets the run directory of the run.

        The generation of the config goes through PackRun: the command line
        runs the whole channel, generation and publication alike.
        """
        make_config(run_dir)
        calls = []
        monkeypatch.setattr(run_module, 'PackRepo', fake_repo(calls))
        monkeypatch.setattr(run_module, 'Repo', fake_gen(calls))
        monkeypatch.setattr(sys, 'argv', ['main', '--root', str(run_dir)])
        main.main()
        assert env.PROJECT_ROOT == str(run_dir)
        # the config of the test publishes nowhere: the run is the generation
        assert [step for step, *_ in calls] == ['repo', 'gen', 'gen-commit']

    def test_main_bad_args(self, fs, run_dir, monkeypatch):
        """An argument that is not known raises SystemExit."""
        monkeypatch.setattr(sys, 'argv', ['main', '--nope'])
        with pytest.raises(SystemExit):
            main.main()

    def test_root_before_first_log(self, fs, run_dir, monkeypatch):
        """--root is set before the first log: the log file of the process is
        decided by the first write, see LogWriter.file."""
        calls = []

        class FakeRun:
            async def run(self, dry_run=False):
                calls.append('run')

        monkeypatch.setattr(main, 'PackRun', FakeRun)
        monkeypatch.setattr(sys, 'argv', ['main', '--root', str(run_dir)])
        with logger.mock_capture_writer() as capture:
            main.main()
        assert calls == ['run']
        # the first line of the log is the start header of the run, written
        # after env.PROJECT_ROOT was set
        assert 'INFO | START' in capture.concat()
