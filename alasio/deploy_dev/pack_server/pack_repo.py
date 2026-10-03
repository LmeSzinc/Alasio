"""
Clone and update the git repo of a pack config in the run directory.

The repo of a config lives in {run directory}/repo/{Author}_{Repo}, next to
the config/ folder and the pack/ folder. The pack server clones it once and
fetches it on every later run, then opens it with the pure python git of
alasio.git and generates the packs from it, see PackRepoGen.

The clone carries no working tree: the pack generation reads the git objects
of the clone only, and a checked out working tree would be rewritten by every
fetch, which costs the time and the disk of a repo full of assets for
nothing. The git commands themselves run through the command line, see
GitCmdline: cloning and fetching a repo from the network is what git does
best, the pure python git of the client reads a repo, it is not a clone tool.

The refs of the clone follow the config on every run:

- the remote "origin" is set to RepoConfig.Remote, the config is the source
  of truth, a clone of an old url is moved to the new one
- every branch and tag of the remote is fetched, the refs the remote has
  deleted are pruned, so the branches and the tags of the clone are the ones
  of the remote, and a version is packed from the very same commit a client
  fetches
- the branch to pack (RepoConfig.Branch) and the branches of
  LookbackConfig.LookbackBranch are the local branches of the clone too: the
  lookback reads the head of a branch from refs/heads, a branch that only
  lives in refs/remotes/origin would look like a branch that does not exist,
  see PackRepoLookback

Usage:
    from alasio.deploy_dev.pack_server.model import PackRepoConfig
    from alasio.deploy_dev.pack_server.pack_repo import PackRepo

    config = PackRepoConfig('LmeSzinc_AzurLaneAutoScript_master.yaml')
    repo = PackRepo(config.data).run()
"""

import os
import stat

from alasio.deploy_dev.pack_server.gate import check_run_dir
from alasio.ext import env
from alasio.ext.cache import cached_property
from alasio.ext.concurrent.cmd import run_cmd
from alasio.ext.path.calc import joinnormpath
from alasio.ext.path.validate import validate_filename
from alasio.git.repo import GitRepo
from alasio.logger import logger

# Folder of the cloned repos in the run directory, see PackRepo.repo_folder
REPO_FOLDER = 'repo'

# Timeout in seconds of a git command that calls the network, clone and fetch.
# A clone of a repo with a long history can take minutes on a slow network, the
# timeout only catches a command that hangs. The local commands (remote
# set-url / add, rev-parse, update-ref) keep the default timeout of run_cmd.
GIT_NETWORK_TIMEOUT = 120


class GitCmdline:
    """
    The git command line of the pack server.

    Every command goes through run_cmd of alasio.ext.concurrent.cmd, a failed
    command raises CmdlineError with the exit code and the output of git. The
    network commands (clone and fetch) are killed after GIT_NETWORK_TIMEOUT,
    the local commands keep the default timeout of run_cmd. git itself is
    expected on PATH: the pack server runs on a machine of the developer.

    Usage:
        git = GitCmdline()
        git.clone('https://github.com/LmeSzinc/AzurLaneAutoScript', folder, 'master')
    """

    def run(self, root, *args, check=True, timeout=None):
        """
        Run a git command and return its result.

        Args:
            root (str): Folder of a repo, passed as "git -C root", empty
                string to run git outside of a repo, e.g. clone
            *args (str): git subcommand and its arguments
            check (bool): True to raise CmdlineError when the command exits
                non-zero. Defaults to True.
            timeout (int | float): Timeout of the command in seconds, a
                network command passes GIT_NETWORK_TIMEOUT. Defaults to None,
                the default timeout of run_cmd, the local commands only touch
                the local repo and are quick

        Returns:
            CmdlineResultStr: Result of the command

        Raises:
            CmdlineError: If git does not exist, times out, or fails while
                check is True
        """
        cmd = ['git']
        if root:
            cmd += ['-C', str(root)]
        cmd += [str(arg) for arg in args]
        logger.info(f'Git command: {" ".join(cmd)}')
        if timeout is None:
            return run_cmd(cmd, check=check)
        return run_cmd(cmd, timeout=timeout, check=check)

    def clone(self, remote, folder, branch):
        """
        Clone a remote into a folder, without a working tree.

        Args:
            remote (str): Remote url
            folder (str): Folder of the clone, created by the clone
            branch (str): Branch to set HEAD and the local branch to
        """
        # the clone is not checked out, the caller reads the git objects only,
        # see the module docstring; the clone is a network command
        self.run('', 'clone', '--no-checkout', '--origin', 'origin', '--branch', branch, remote, folder,
                 timeout=GIT_NETWORK_TIMEOUT)

    def set_remote(self, folder, remote):
        """
        Point the remote "origin" of a repo to a url.

        Args:
            folder (str): Folder of the clone
            remote (str): Remote url
        """
        result = self.run(folder, 'remote', 'set-url', 'origin', remote, check=False)
        if not result.returncode:
            return
        # the repo has no remote "origin" yet, e.g. it was created by an old
        # version of the pack server: add it, the config is the source of truth
        self.run(folder, 'remote', 'add', 'origin', remote)

    def fetch(self, folder):
        """
        Fetch every branch and tag of the remote "origin", prune the deleted ones.

        The explicit refspecs make the clone mirror the remote: a ref the
        remote has rewritten (e.g. a moved tag) is replaced, a branch or a
        tag the remote has deleted is pruned. The local branches are not
        touched: a fetch into the checked out branch of a non-bare repo is
        refused by git, PackRepo sets the local branches with update-ref
        after the fetch.

        Args:
            folder (str): Folder of the clone
        """
        self.run(
            folder, 'fetch', '--prune', 'origin',
            '+refs/heads/*:refs/remotes/origin/*',
            '+refs/tags/*:refs/tags/*',
            timeout=GIT_NETWORK_TIMEOUT,
        )

    def update_ref(self, folder, ref, target):
        """
        Set a ref of a repo to the sha1 or the ref it points to.

        Args:
            folder (str): Folder of the clone
            ref (str): Ref to set, e.g. refs/heads/master
            target (str): Sha1 or ref to set it to, e.g. refs/remotes/origin/master
        """
        self.run(folder, 'update-ref', ref, target)

    def ref_exists(self, folder, ref):
        """
        Check whether a ref exists in a repo.

        Args:
            folder (str): Folder of the clone
            ref (str): Ref to look up, e.g. refs/remotes/origin/master

        Returns:
            bool: True if the ref exists
        """
        result = self.run(folder, 'rev-parse', '--verify', '--quiet', ref, check=False)
        return result.returncode == 0


class PackRepo:
    """
    Clone and update the git repo of one pack config, see the module docstring.

    Usage:
        repo = PackRepo(config).run()
        PackRepoGen(repo, config).run()
    """

    def __init__(self, config):
        """
        Args:
            config (PackRepoModel): Config of the repo, read from a
                PackRepoConfig: the config reader checks that Author, Repo,
                Remote and Branch are not empty

        Raises:
            ValueError: If the config makes a folder name that is not a
                single safe path component (see repo_folder)
        """
        self.config = config
        # the folder name is checked before anything is run, see repo_folder
        _ = self.repo_folder

    @cached_property
    def repo_folder(self):
        """
        Folder of the clone: repo/{Author}_{Repo}

        Like the folder of the packs, the name must be a single path
        component: an Author or a Repo that carries a path separator would
        nest the clone inside the folder of another config. See
        validate_filename.

        Returns:
            PathStr: Absolute path of the folder
        """
        name = f'{self.config.Repo.Author}_{self.config.Repo.Repo}'
        validate_filename(name)
        return env.PROJECT_ROOT.joinpath(REPO_FOLDER).joinpath(name)

    @cached_property
    def branch_list(self):
        """
        Branches to keep in the local repo: the branch to pack and the
        lookback branches

        The branch to pack is the first one: the latest commit of the packs
        is the head of its local branch, see PackRepoLookback.latest_commit.
        The lookback branches come from LookbackConfig.LookbackBranch.

        Returns:
            list[str]: Branch names, empty and duplicated names are dropped
        """
        out = []
        for name in [self.config.Repo.Branch, *self.config.Lookback.LookbackBranch]:
            if name and name not in out:
                out.append(name)
        return out

    def run(self):
        """
        Clone or update the repo, then return it opened for reading.

        A repo that does not exist yet is cloned from RepoConfig.Remote, the
        branch of the config becomes the branch of the clone (without a
        working tree, see the module docstring). An existing repo is set to
        the remote of the config, fetched, and its local branches are set to
        the fetched remote branches. The repo is complete before the packs
        are generated from it, see PackRepoGen.

        Returns:
            GitRepo: The repo of the config, read lazily (read_lazy), ready
                to be packed

        Raises:
            RunDirError: If env.PROJECT_ROOT is not a run directory of the
                pack server, see check_run_dir
            ValueError: If the branch to pack does not exist at the remote,
                or the folder of the repo exists but is not a clone
            CmdlineError: If a git command fails
        """
        check_run_dir()
        folder = self.repo_folder
        git = GitCmdline()
        if self._is_clone(folder):
            self._update(git, folder)
        else:
            self._clone(git, folder)
        return GitRepo(folder).read_lazy()

    @staticmethod
    def _is_clone(folder):
        """
        Check whether a folder already holds a clone.

        Args:
            folder (str): Folder of the clone

        Returns:
            bool: True if the folder holds a clone, False if a clone can be
                created in it

        Raises:
            ValueError: If the path exists but is not the clone of a repo: a
                file, or a folder that holds something else than a git repo.
                The pack server does not remove it, the run directory is
                shared with the operator, an unexpected folder is kept for
                the operator to look at.
        """
        try:
            st = os.stat(folder)
        except FileNotFoundError:
            # the folder is created by git clone
            return False
        if not stat.S_ISDIR(st.st_mode):
            raise ValueError(f'Repo path is not a folder: "{folder}"')
        try:
            os.stat(joinnormpath(folder, '.git'))
        except (FileNotFoundError, NotADirectoryError):
            # an empty folder is the leftover of an interrupted run, git
            # clone can create the repo in it, anything else is kept
            with os.scandir(folder) as entries:
                if next(entries, None) is not None:
                    raise ValueError(
                        f'Path exists but is not a git repo: "{folder}", '
                        f'the pack server does not remove it, delete it by hand to clone again')
            return False
        return True

    def _clone(self, git, folder):
        """
        Clone the repo of the config into a folder.

        Args:
            git (GitCmdline): Runner of the git commands
            folder (str): Folder of the clone, does not exist yet
        """
        remote = self.config.Repo.Remote
        branch = self.config.Repo.Branch
        logger.info(f'Cloning repo: remote="{remote}", branch="{branch}", folder="{folder}"')
        git.clone(remote, folder, branch)
        self._set_local_branch(git, folder)

    def _update(self, git, folder):
        """
        Fetch an existing clone and set its local branches to the fetched ones.

        Args:
            git (GitCmdline): Runner of the git commands
            folder (str): Folder of the clone
        """
        remote = self.config.Repo.Remote
        logger.info(f'Updating repo: remote="{remote}", folder="{folder}"')
        git.set_remote(folder, remote)
        git.fetch(folder)
        self._set_local_branch(git, folder)

    def _set_local_branch(self, git, folder):
        """
        Set the local branch of every configured branch to its remote branch.

        The local branches are set with update-ref, not with a fetch into the
        local ref: a fetch into the checked out branch of a non-bare repo is
        refused by git, and the clone has a checked out branch even without a
        working tree, its HEAD points to the branch of the config.

        The branch to pack must exist at the remote, the heads of the packs
        are read from it. A lookback branch that does not exist at the remote
        is skipped with a warning, like the lookback itself skips it, so a
        stale LookbackBranch of the config does not stop the repo.

        Args:
            git (GitCmdline): Runner of the git commands
            folder (str): Folder of the clone

        Raises:
            ValueError: If the branch to pack does not exist at the remote
        """
        branch = self.config.Repo.Branch
        for name in self.branch_list:
            remote_ref = f'refs/remotes/origin/{name}'
            if not git.ref_exists(folder, remote_ref):
                if name == branch:
                    raise ValueError(
                        f'No such branch "{name}" at remote "{self.config.Repo.Remote}" of repo "{folder}"')
                logger.warning(f'No such lookback branch "{name}" at remote, skipped, folder="{folder}"')
                continue
            git.update_ref(folder, f'refs/heads/{name}', remote_ref)
