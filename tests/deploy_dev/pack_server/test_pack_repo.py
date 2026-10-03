"""
Tests for PackRepo: clone and update the git repo of a pack config.

The tests run in the memory file system and fake the git command line: the
fake records every command and applies its effect on the memory file system
(the clone creates the repo, update-ref copies the ref file), so the class is
checked through the commands it runs and through the refs it leaves behind.
The commands are asserted verbatim: the flags of clone and fetch are the
contract with git, a wrong flag would only show up on a real remote.
"""
import os
from hashlib import sha1

import pytest

from alasio.deploy_dev.pack_server import pack_repo
from alasio.deploy_dev.pack_server.gate import RunDirError
from alasio.deploy_dev.pack_server.model import LookbackConfig, PackRepoModel, RepoConfig
from alasio.deploy_dev.pack_server.pack_repo import GIT_NETWORK_TIMEOUT, REPO_FOLDER, GitCmdline, PackRepo
from alasio.ext import env
from alasio.ext.concurrent.cmd import CmdlineError, CmdlineResultStr
from alasio.ext.path import PathStr
from alasio.git.repo import GitRepo
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401

REMOTE = 'https://github.com/Author/Repo'

CONFIG = PackRepoModel(
    Repo=RepoConfig(Remote=REMOTE, Author='Author', Repo='Repo', Branch='master'),
)


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


def sha1_of(name):
    """
    The fake sha1 of a branch of the fake remote, stable across the tests.

    Args:
        name (str): Branch name

    Returns:
        str: 40 characters of hex
    """
    return sha1(name.encode('utf-8')).hexdigest()


def ref_path(root, ref):
    """
    The path of a loose ref file of a repo, e.g. .git/refs/heads/master.

    Args:
        root (str): Folder of the repo
        ref (str): Ref name, e.g. refs/heads/master

    Returns:
        PathStr: Path of the file
    """
    return join_path(root, *f'.git/{ref}'.split('/'))


def read_ref(root, ref):
    """
    The content of a loose ref file, empty string if it does not exist.

    Args:
        root (str): Folder of the repo
        ref (str): Ref name, e.g. refs/heads/master

    Returns:
        str: Sha1 of the ref
    """
    try:
        with open(ref_path(root, ref), encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        return ''


def write_ref(root, ref, content):
    """
    Write a loose ref file, create the folders of the ref.

    Args:
        root (str): Folder of the repo
        ref (str): Ref name, e.g. refs/heads/master
        content (str): Sha1 to write
    """
    path = ref_path(root, ref)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)


def cmd_result(cmd, returncode, stdout=''):
    """
    The result of a fake git command, the way run_cmd returns it.

    Returns:
        CmdlineResultStr:
    """
    return CmdlineResultStr(cmd=cmd, returncode=returncode, stdout=stdout, stderr='')


class FakeGit:
    """
    The fake git command line of the tests.

    Every command is recorded, and its effect on the repo is applied to the
    memory file system, so PackRepo is checked through the commands it runs
    and the refs it writes:

    - the fake remote has the branches of `branches`, the head of a branch is
      in `branch_sha` and can be moved with move_branch(); the clone and the
      fetch write the remote tracking refs of every branch of the remote,
      like git does, and a fetch first removes the refs of the branches that
      are gone, like a fetch with prune
    - update-ref copies the content of the target ref, a missing target fails
      like git
    - the subcommands in `fail` raise CmdlineError, like a failed command
    """

    def __init__(self, fs, branches=('master',), fail=()):
        """
        Args:
            fs: Fake filesystem of the test
            branches (Iterable[str]): Branches of the fake remote.
                Defaults to ('master',)
            fail (Iterable[str]): Subcommands that raise CmdlineError, e.g.
                ('clone',). Defaults to ()
        """
        self.fs = fs
        self.branches = list(branches)
        self.branch_sha = {name: sha1_of(name) for name in self.branches}
        self.fail = set(fail)
        self.commands = []
        self.kwargs = []

    def move_branch(self, name, sha1):
        """
        Move the head of a branch of the fake remote, e.g. a commit was pushed.

        Args:
            name (str): Branch to move
            sha1 (str): New sha1 of the head
        """
        self.branch_sha[name] = sha1

    def make_clone(self, run_dir, name='Author_Repo'):
        """
        Create the folder of an existing clone of the fake remote.

        Args:
            run_dir (str): Run directory of the test
            name (str): Name of the folder. Defaults to 'Author_Repo'.

        Returns:
            PathStr: Folder of the clone
        """
        folder = join_path(run_dir, REPO_FOLDER, name)
        self.fs.create_dir(join_path(folder, '.git', 'objects'))
        self.write_remote_refs(folder)
        return folder

    def write_remote_refs(self, folder):
        """
        Write the remote tracking refs of the fake remote into a repo.

        The refs of the branches that are not in `branches` are removed
        first, like a fetch that prunes the refs of the deleted branches.

        Args:
            folder (str): Folder of the repo
        """
        origin = join_path(folder, '.git', 'refs', 'remotes', 'origin')
        if origin.exists():
            self.fs.rmtree(origin)
        for name in self.branches:
            write_ref(folder, f'refs/remotes/origin/{name}', self.branch_sha[name])

    def __call__(self, cmd, **kwargs):
        """
        Run a fake git command, see the class docstring.

        Returns:
            CmdlineResultStr: Result of the command

        Raises:
            CmdlineError: If the subcommand is in `fail`, or the state of the
                fake repo makes a real git command fail
        """
        self.commands.append([str(part) for part in cmd])
        self.kwargs.append(dict(kwargs))
        check = kwargs.get('check', True)

        if cmd[1] == '-C':
            root = str(cmd[2])
            sub = cmd[3]
            args = cmd[4:]
        else:
            root = ''
            sub = cmd[1]
            args = cmd[2:]

        if sub in self.fail:
            raise CmdlineError(cmd=cmd, msg=f'fake failure of "{sub}"', returncode=1)
        if sub == 'clone':
            return self._clone(cmd, args)
        if sub == 'remote':
            # set-url / add, the fake keeps no remotes
            return cmd_result(cmd, 0)
        if sub == 'fetch':
            self.write_remote_refs(root)
            return cmd_result(cmd, 0)
        if sub == 'rev-parse':
            content = read_ref(root, args[-1])
            if not content and check:
                raise CmdlineError(cmd=cmd, msg=f'fake failure: no such ref "{args[-1]}"', returncode=1)
            return cmd_result(cmd, 0 if content else 1, stdout=content)
        if sub == 'update-ref':
            ref, target = args
            content = read_ref(root, target)
            if not content:
                raise CmdlineError(cmd=cmd, msg=f'fake failure: no such ref "{target}"', returncode=128)
            write_ref(root, ref, content)
            return cmd_result(cmd, 0)
        raise AssertionError(f'Unexpected git command: {cmd}')

    def _clone(self, cmd, args):
        """
        The fake of "git clone": create the repo of the clone in the folder.

        Args:
            cmd (list[str]): Command of the test
            args (list[str]): Arguments of the clone

        Returns:
            CmdlineResultStr:
        """
        # git clone --no-checkout --origin origin --branch {branch} {remote} {folder}
        branch = args[args.index('--branch') + 1]
        folder = args[-1]
        if branch not in self.branches:
            raise CmdlineError(
                cmd=cmd, msg=f'fake failure: no branch "{branch}" at the remote', returncode=128)
        self.fs.create_dir(join_path(folder, '.git', 'objects'))
        self.write_remote_refs(folder)
        write_ref(folder, f'refs/heads/{branch}', self.branch_sha[branch])
        return cmd_result(cmd, 0)


def patch_git(monkeypatch, fs, branches=('master',), fail=()):
    """
    Replace the command runner of the module with a fake git command line.

    Args:
        monkeypatch (MonkeyPatch): Patcher of the test
        fs: Fake filesystem of the test
        branches (Iterable[str]): Branches of the fake remote. Defaults to ('master',)
        fail (Iterable[str]): Subcommands that raise CmdlineError. Defaults to ()

    Returns:
        FakeGit: The fake, with the commands of the test
    """
    fake = FakeGit(fs, branches=branches, fail=fail)
    monkeypatch.setattr(pack_repo, 'run_cmd', fake)
    return fake


class Recorder:
    """
    Replacement of run_cmd that records the command and its kwargs.
    """

    def __init__(self, returncode=0):
        """
        Args:
            returncode (int): Exit code of every command. Defaults to 0.
        """
        self.calls = []
        self.returncode = returncode

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        return cmd_result(cmd, self.returncode)


class TestGitCmdline:
    """The command line of one git command."""

    def test_run_with_root(self, monkeypatch):
        """A command of a repo runs "git -C root", without a timeout, run_cmd uses its default."""
        recorder = Recorder()
        monkeypatch.setattr(pack_repo, 'run_cmd', recorder)
        GitCmdline().run('folder', 'status')
        assert recorder.calls == [(['git', '-C', 'folder', 'status'], {'check': True})]

    def test_run_without_root(self, monkeypatch):
        """A command without a repo runs git directly, without a timeout, run_cmd uses its default."""
        recorder = Recorder()
        monkeypatch.setattr(pack_repo, 'run_cmd', recorder)
        GitCmdline().run('', 'version')
        assert recorder.calls == [(['git', 'version'], {'check': True})]

    def test_run_with_network_timeout(self, monkeypatch):
        """A command that calls the network passes its own timeout through to run_cmd."""
        recorder = Recorder()
        monkeypatch.setattr(pack_repo, 'run_cmd', recorder)
        GitCmdline().run('folder', 'fetch', timeout=GIT_NETWORK_TIMEOUT)
        assert recorder.calls == [
            (['git', '-C', 'folder', 'fetch'], {'timeout': GIT_NETWORK_TIMEOUT, 'check': True})]

    def test_clone_command(self, monkeypatch):
        """A clone is not checked out, the pack generation reads git objects only."""
        recorder = Recorder()
        monkeypatch.setattr(pack_repo, 'run_cmd', recorder)
        GitCmdline().clone('remote', 'folder', 'master')
        assert recorder.calls == [
            (['git', 'clone', '--no-checkout', '--origin', 'origin', '--branch', 'master', 'remote', 'folder'],
             {'timeout': GIT_NETWORK_TIMEOUT, 'check': True})]

    def test_set_remote_command(self, monkeypatch):
        """An existing remote "origin" is pointed to the url of the config."""
        recorder = Recorder()
        monkeypatch.setattr(pack_repo, 'run_cmd', recorder)
        GitCmdline().set_remote('folder', 'url')
        assert recorder.calls == [
            (['git', '-C', 'folder', 'remote', 'set-url', 'origin', 'url'], {'check': False})]

    def test_set_remote_without_origin(self, monkeypatch):
        """A repo without a remote "origin" gets one, the set-url failure is not fatal."""
        calls = []

        def run_cmd(cmd, **kwargs):
            calls.append(cmd)
            return cmd_result(cmd, 1 if 'set-url' in cmd else 0)

        monkeypatch.setattr(pack_repo, 'run_cmd', run_cmd)
        GitCmdline().set_remote('folder', 'url')
        assert calls == [
            ['git', '-C', 'folder', 'remote', 'set-url', 'origin', 'url'],
            ['git', '-C', 'folder', 'remote', 'add', 'origin', 'url'],
        ]

    def test_fetch_command(self, monkeypatch):
        """Every branch and tag is fetched, a rewritten or deleted ref of the remote is followed."""
        recorder = Recorder()
        monkeypatch.setattr(pack_repo, 'run_cmd', recorder)
        GitCmdline().fetch('folder')
        assert recorder.calls == [
            (['git', '-C', 'folder', 'fetch', '--prune', 'origin',
              '+refs/heads/*:refs/remotes/origin/*', '+refs/tags/*:refs/tags/*'],
             {'timeout': GIT_NETWORK_TIMEOUT, 'check': True})]

    def test_update_ref_command(self, monkeypatch):
        """A local ref is set to the ref it should point to."""
        recorder = Recorder()
        monkeypatch.setattr(pack_repo, 'run_cmd', recorder)
        GitCmdline().update_ref('folder', 'refs/heads/master', 'refs/remotes/origin/master')
        assert recorder.calls == [
            (['git', '-C', 'folder', 'update-ref', 'refs/heads/master', 'refs/remotes/origin/master'],
             {'check': True})]

    def test_ref_exists(self, monkeypatch):
        """The ref is looked up with rev-parse, a missing ref is not an error."""
        recorder = Recorder(returncode=0)
        monkeypatch.setattr(pack_repo, 'run_cmd', recorder)
        assert GitCmdline().ref_exists('folder', 'refs/heads/master') is True
        assert recorder.calls == [
            (['git', '-C', 'folder', 'rev-parse', '--verify', '--quiet', 'refs/heads/master'],
             {'check': False})]

        recorder = Recorder(returncode=1)
        monkeypatch.setattr(pack_repo, 'run_cmd', recorder)
        assert GitCmdline().ref_exists('folder', 'refs/heads/dev') is False


class TestConfigAndFolder:
    """Config validation and the run directory paths."""

    def test_folder_with_path_separator(self, fs, run_dir):
        """An Author that would nest the clone inside another config is refused."""
        config = PackRepoModel(Repo=RepoConfig(Remote=REMOTE, Author='Author/extra', Repo='Repo', Branch='master'))
        with pytest.raises(ValueError):
            PackRepo(config)

    def test_repo_folder(self, fs, run_dir):
        """The folder of the clone is named by the config, under the repo folder of the run directory."""
        repo = PackRepo(CONFIG)
        assert repo.repo_folder == join_path(run_dir, REPO_FOLDER, 'Author_Repo')

    def test_branch_list(self, fs, run_dir):
        """The branch to pack comes first, the lookback branches follow without duplicates."""
        config = PackRepoModel(
            Repo=RepoConfig(Remote=REMOTE, Author='Author', Repo='Repo', Branch='master'),
            Lookback=LookbackConfig(LookbackBranch=['dev', 'master', '', 'bug_fix', 'dev']),
        )
        assert PackRepo(config).branch_list == ['master', 'dev', 'bug_fix']

    def test_run_dir_is_a_mod(self, fs, run_dir, monkeypatch):
        """The class refuses to run in a mod, like the config reader and the generator."""
        fake = patch_git(monkeypatch, fs)
        fs.create_file(join_path(run_dir, 'module', 'main.py'), contents='')
        with pytest.raises(RunDirError):
            PackRepo(CONFIG).run()
        assert fake.commands == []


class TestClone:
    """The first run of a repo clones it, without a working tree."""

    def test_clone_command(self, fs, run_dir, monkeypatch):
        """A missing repo is cloned without a checkout, then its branch is set."""
        fake = patch_git(monkeypatch, fs)
        folder = join_path(run_dir, REPO_FOLDER, 'Author_Repo')
        with logger.mock_capture_writer() as capture:
            repo = PackRepo(CONFIG).run()
        assert capture.fd.any_contains('Cloning repo')
        assert fake.commands == [
            ['git', 'clone', '--no-checkout', '--origin', 'origin', '--branch', 'master', REMOTE, str(folder)],
            ['git', '-C', str(folder), 'rev-parse', '--verify', '--quiet', 'refs/remotes/origin/master'],
            ['git', '-C', str(folder), 'update-ref', 'refs/heads/master', 'refs/remotes/origin/master'],
        ]
        # the clone is a network command, the local commands keep the run_cmd default
        assert fake.kwargs == [
            {'timeout': GIT_NETWORK_TIMEOUT, 'check': True},
            {'check': False},
            {'check': True},
        ]
        # the repo is returned opened for reading, from the folder of the config
        assert isinstance(repo, GitRepo)
        assert repo.path == str(folder)
        # read_lazy() ran, the object index is built
        assert repo.loose is not None
        # and the local branch points at the head of the remote
        assert read_ref(folder, 'refs/heads/master') == sha1_of('master')

    def test_clone_sets_lookback_branch(self, fs, run_dir, monkeypatch):
        """The lookback branches are local branches of the clone too."""
        fake = patch_git(monkeypatch, fs, branches=('master', 'dev'))
        config = PackRepoModel(
            Repo=RepoConfig(Remote=REMOTE, Author='Author', Repo='Repo', Branch='master'),
            Lookback=LookbackConfig(LookbackBranch=['dev']),
        )
        PackRepo(config).run()
        folder = join_path(run_dir, REPO_FOLDER, 'Author_Repo')
        assert read_ref(folder, 'refs/heads/master') == sha1_of('master')
        assert read_ref(folder, 'refs/heads/dev') == sha1_of('dev')

    def test_second_run_fetches(self, fs, run_dir, monkeypatch):
        """The run after the clone fetches the cloned repo instead of cloning it again."""
        fake = patch_git(monkeypatch, fs)
        PackRepo(CONFIG).run()
        fake.commands.clear()
        PackRepo(CONFIG).run()
        assert all('clone' not in cmd for cmd in fake.commands)

    def test_clone_failed(self, fs, run_dir, monkeypatch):
        """A failed clone is raised to the caller, no clone is left behind."""
        fake = patch_git(monkeypatch, fs, fail=('clone',))
        with pytest.raises(CmdlineError):
            PackRepo(CONFIG).run()
        assert len(fake.commands) == 1
        assert not join_path(run_dir, REPO_FOLDER, 'Author_Repo', '.git').exists()

    def test_empty_folder_is_cloned(self, fs, run_dir, monkeypatch):
        """An empty folder is the leftover of an interrupted run, the clone fills it."""
        fake = patch_git(monkeypatch, fs)
        fs.create_dir(join_path(run_dir, REPO_FOLDER, 'Author_Repo'))
        PackRepo(CONFIG).run()
        assert fake.commands[0][1] == 'clone'
        assert read_ref(join_path(run_dir, REPO_FOLDER, 'Author_Repo'), 'refs/heads/master') == sha1_of('master')

    def test_folder_without_git_is_refused(self, fs, run_dir, monkeypatch):
        """A folder that holds something else is not removed, the run is refused."""
        fake = patch_git(monkeypatch, fs)
        fs.create_file(join_path(run_dir, REPO_FOLDER, 'Author_Repo', 'readme.txt'), contents='keep me')
        with pytest.raises(ValueError, match='not a git repo'):
            PackRepo(CONFIG).run()
        assert fake.commands == []
        # the file of the operator is kept
        assert join_path(run_dir, REPO_FOLDER, 'Author_Repo', 'readme.txt').exists()

    def test_folder_is_a_file_refused(self, fs, run_dir, monkeypatch):
        """A file at the path of the clone is refused, the pack server removes nothing."""
        fake = patch_git(monkeypatch, fs)
        fs.create_file(join_path(run_dir, REPO_FOLDER, 'Author_Repo'), contents='')
        with pytest.raises(ValueError, match='not a folder'):
            PackRepo(CONFIG).run()
        assert fake.commands == []


class TestUpdate:
    """A later run fetches the existing clone and moves its local branches."""

    def test_update_command(self, fs, run_dir, monkeypatch):
        """The remote of the config, the refs of the remote and the branches decide the commands."""
        fake = patch_git(monkeypatch, fs)
        folder = fake.make_clone(run_dir)
        with logger.mock_capture_writer() as capture:
            PackRepo(CONFIG).run()
        assert capture.fd.any_contains('Updating repo')
        assert fake.commands == [
            ['git', '-C', str(folder), 'remote', 'set-url', 'origin', REMOTE],
            ['git', '-C', str(folder), 'fetch', '--prune', 'origin',
             '+refs/heads/*:refs/remotes/origin/*', '+refs/tags/*:refs/tags/*'],
            ['git', '-C', str(folder), 'rev-parse', '--verify', '--quiet', 'refs/remotes/origin/master'],
            ['git', '-C', str(folder), 'update-ref', 'refs/heads/master', 'refs/remotes/origin/master'],
        ]

    def test_update_moves_the_local_branch(self, fs, run_dir, monkeypatch):
        """A branch that moved at the remote is moved in the clone too."""
        fake = patch_git(monkeypatch, fs)
        folder = fake.make_clone(run_dir)
        write_ref(folder, 'refs/heads/master', '0' * 40)
        fake.move_branch('master', 'a' * 40)
        PackRepo(CONFIG).run()
        assert read_ref(folder, 'refs/heads/master') == 'a' * 40

    def test_update_with_lookback_branch(self, fs, run_dir, monkeypatch):
        """Every lookback branch is set to its remote branch, the branch to pack included."""
        fake = patch_git(monkeypatch, fs, branches=('master', 'dev'))
        folder = fake.make_clone(run_dir)
        config = PackRepoModel(
            Repo=RepoConfig(Remote=REMOTE, Author='Author', Repo='Repo', Branch='master'),
            Lookback=LookbackConfig(LookbackBranch=['dev']),
        )
        PackRepo(config).run()
        assert read_ref(folder, 'refs/heads/master') == sha1_of('master')
        assert read_ref(folder, 'refs/heads/dev') == sha1_of('dev')

    def test_update_missing_branch_of_the_config(self, fs, run_dir, monkeypatch):
        """The branch to pack must exist at the remote, the head of the packs is read from it."""
        fake = patch_git(monkeypatch, fs, branches=('dev',))
        folder = fake.make_clone(run_dir)
        with pytest.raises(ValueError, match='No such branch "master"'):
            PackRepo(CONFIG).run()
        assert not ref_path(folder, 'refs/heads/master').exists()

    def test_update_missing_lookback_branch(self, fs, run_dir, monkeypatch):
        """A lookback branch that does not exist at the remote is skipped with a warning."""
        fake = patch_git(monkeypatch, fs, branches=('master',))
        folder = fake.make_clone(run_dir)
        config = PackRepoModel(
            Repo=RepoConfig(Remote=REMOTE, Author='Author', Repo='Repo', Branch='master'),
            Lookback=LookbackConfig(LookbackBranch=['dev']),
        )
        with logger.mock_capture_writer() as capture:
            PackRepo(config).run()
        assert capture.fd.any_contains('No such lookback branch "dev"')
        # the branch of the config is still set, the stale one is not created
        assert read_ref(folder, 'refs/heads/master') == sha1_of('master')
        assert not ref_path(folder, 'refs/heads/dev').exists()

    def test_network_timeout(self, fs, run_dir, monkeypatch):
        """Only the fetch of an update calls the network, it carries GIT_NETWORK_TIMEOUT."""
        fake = patch_git(monkeypatch, fs)
        fake.make_clone(run_dir)
        PackRepo(CONFIG).run()
        # the commands are the remote set-url, the fetch, the rev-parse of the
        # remote branch and the update-ref of the local branch
        assert fake.kwargs == [
            {'check': False},
            {'timeout': GIT_NETWORK_TIMEOUT, 'check': True},
            {'check': False},
            {'check': True},
        ]
