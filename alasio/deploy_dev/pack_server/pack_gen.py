"""
Generate the packs of a repo for the pack server.

The generator reads a git repo and writes the packs of the run directory,
the output layout is fixed by PackRepoModel:

1. the full pack of the latest commit to
   pack/{Author}_{Repo}_{Branch}/{commit}/full_{commit}.pack
   only the latest commit has a full pack: a client downloads a full pack
   once and updates with update packs afterwards, the packs of the older
   versions do not need one
2. an update pack from every lookback commit to
   pack/{Author}_{Repo}_{Branch}/{commit}/update_{old}.pack
3. the latest info to pack/{Author}_{Repo}_{Branch}/latest.pack
   the latest version and the checksum of its index pack
4. the folders that do not match the latest commit are removed

Every pack of a version lives in the folder of the latest commit, an older
folder is the folder of the version that was the latest one in an earlier
run.

latest.pack is written after every pack of the version is on the disk, so
the clients only switch to a version whose packs are complete. An error of
a pack build is raised to the caller: the previous latest.pack keeps
serving the clients and the next run starts over, the atomic writes never
leave a truncated pack at its final path.

The repo of a config is an input of the generator, not cloned here: the
caller opens the git repo of the config and passes it in, the generation
only reads it, like PackRepoLookback. The packs themselves are encoded by
PackFull and PackUpdate, see alasio/deploy_dev/pack.

Usage:
    from alasio.deploy_dev.pack_server.model import PackRepoConfig
    from alasio.deploy_dev.pack_server.pack_gen import PackRepoGen
    from alasio.git.repo import GitRepo

    config = PackRepoConfig('LmeSzinc_AzurLaneAutoScript_master.yaml')
    repo = GitRepo(repo_path).read_lazy()
    PackRepoGen(repo, config.data).run()
"""

from alasio.deploy_dev.pack.pack_full import PackFull
from alasio.deploy_dev.pack.pack_update import PackUpdate
from alasio.deploy_dev.pack_server.gate import check_run_dir
from alasio.deploy_dev.pack_server.lookback import PackRepoLookback
from alasio.ext import env
from alasio.ext.cache import cached_property
from alasio.ext.path.atomic import atomic_failure_cleanup, atomic_rmtree, atomic_write, atomic_write_stream
from alasio.ext.path.validate import validate_filename
from alasio.logger import logger

# folder of the packs in the run directory, see PackRepoModel
PACK_FOLDER = 'pack'


class PackRepoGen:
    """
    Generate the packs of one repo, see PackRepoModel for the layout.

    The commits of the repo are read once and cached on the instance: build
    a new instance for every run, the next run must see the new commits of
    the repo.

    Usage:
        gen = PackRepoGen(repo, config)
        gen.run()
    """

    def __init__(self, repo, config):
        """
        Args:
            repo (GitRepo | MockGitRepo): Git repo to pack, every version is
                read from it, the generation does not write to it
            config (PackRepoModel): Config of the repo, read from a
                PackRepoConfig: the config reader checks that Author, Repo
                and Branch are not empty

        Raises:
            ValueError: If the config makes a folder name that is not a
                single safe path component (see pack_folder), the output
                folder of such a config would mix with the other repos
        """
        self.repo = repo
        self.config = config
        # the folder name is checked before anything is written, see pack_folder
        _ = self.pack_folder
        self.lookback = PackRepoLookback(repo, config)

    @cached_property
    def pack_folder(self):
        """
        Folder of the packs of the repo: pack/{Author}_{Repo}_{Branch}

        The name must be a single path component: a Branch like 'feature/x'
        would nest the folder of this repo inside the folder of another
        config, and the cleanup of the stale folders of that config could
        remove the packs of this one. See validate_filename.

        Returns:
            PathStr: Absolute path of the folder
        """
        name = f'{self.config.Repo.Author}_{self.config.Repo.Repo}_{self.config.Repo.Branch}'
        validate_filename(name)
        return env.PROJECT_ROOT.joinpath(PACK_FOLDER).joinpath(name)

    @cached_property
    def version_folder(self):
        """
        Folder of the packs of the latest version: {pack folder}/{commit}

        Returns:
            PathStr: Absolute path of the folder
        """
        return self.pack_folder.joinpath(self.lookback.latest_commit)

    def run(self):
        """
        Generate and publish the packs of the latest version.

        Writes the full pack of the latest commit, an update pack from every
        lookback commit to it, then latest.pack, then removes the folders of
        the other versions. latest.pack is written after every pack of the
        version, and an exception is raised to the caller instead of
        publishing a partial version: the previous latest.pack keeps serving
        the clients and the next run starts over, see the module docstring.

        Raises:
            RunDirError: If env.PROJECT_ROOT is not a run directory of the
                pack server, see check_run_dir
            ValueError: If the repo has no branch to pack, or a pack fails to
                build
        """
        check_run_dir()
        latest = self.lookback.latest_commit
        # the lookback commits are computed before the packs are built: it
        # also reads the repo index the pack encoder looks the objects up in,
        # see PackRepoLookback
        lookback = self.lookback.lookback_commit
        logger.info(
            f'Generating packs of "{self.pack_folder.name}": '
            f'{len(lookback) + 1} versions, latest={latest}'
        )
        # the tmp files of an interrupted run are ours to clean up, the real
        # files of the previous run keep serving the clients
        atomic_failure_cleanup(self.pack_folder, recursive=True)

        # the pack of the latest version is shared by every update pack, its
        # index pack and its encodings are cached on it, see PackUpdate
        pack = PackFull(self.repo, latest)

        # 1. the full pack of the latest commit, the only full pack
        self._write_full_pack(pack)
        # 2. an update pack from every lookback commit to the latest one
        self._write_update_packs(pack)
        # 3. latest.pack, after every pack of the version is written, so the
        # clients only switch to a version whose packs are complete
        self._write_latest_pack(pack)
        # 4. the folders that do not match the latest commit
        self._remove_stale_folders()
        logger.info(f'Packs of "{self.pack_folder.name}" generated, latest={latest}')

    def _write_full_pack(self, pack):
        """
        Write the full pack of the latest version to the version folder.

        Args:
            pack (PackFull): Pack of the latest version
        """
        file = self.version_folder.joinpath(f'full_{pack.current_version}.pack')
        logger.info(f'Writing full pack: "{file}"')
        atomic_write_stream(file, pack.iter_pack_data())

    def _write_update_packs(self, pack):
        """
        Write the update pack of every lookback commit to the version folder.

        Args:
            pack (PackFull): Pack of the latest version, shared by every
                update pack
        """
        for old in self.lookback.lookback_commit:
            update = PackUpdate(pack, old)
            file = self.version_folder.joinpath(f'update_{old}.pack')
            logger.info(f'Writing update pack: {old} -> {pack.current_version}, "{file}"')
            atomic_write_stream(file, update.iter_pack_data())

    def _write_latest_pack(self, pack):
        """
        Write latest.pack: the latest version and its index pack checksum.

        Args:
            pack (PackFull): Pack of the latest version
        """
        file = self.pack_folder.joinpath('latest.pack')
        logger.info(f'Writing latest info: version={pack.current_version}, "{file}"')
        atomic_write(file, pack.latest_pack())

    def _remove_stale_folders(self):
        """
        Remove the folders that do not match the latest commit.

        The stale folder is the folder of the version that was the latest
        one in an earlier run: no latest.pack links its packs anymore, the
        folder would pile up on the disk for every version of the repo.
        The removal is best effort: it runs after the version is published,
        a folder that cannot be removed (e.g. a file of it is held by
        another process) is logged and retried by the next run.
        """
        latest = self.lookback.latest_commit
        pack_folder = self.pack_folder
        for name in pack_folder.iter_foldernames():
            if name == latest:
                continue
            folder = pack_folder.joinpath(name)
            logger.info(f'Removing stale pack folder: "{folder}"')
            try:
                atomic_rmtree(folder)
            except OSError as e:
                logger.warning(f'Failed to remove stale pack folder "{folder}": {e}')
