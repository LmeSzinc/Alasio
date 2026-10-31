"""
Generate the packs of the repo tree (step 2).

The repo tree is the mod a client runs: its packs are the files of the version,
encoded by the pack encoder of the library (see alasio.deploy_dev.pack). Every
version of the window gets a folder of its own:

    {workspace}/packrepo/{commit}/full.pack
    {workspace}/packrepo/{commit}/update_{old}.pack

Only the latest commit has a full pack, the rule the git flow keeps for the
latest comment: a client downloads the full pack of the version it updates to
once and updates with update packs afterwards, so an old version only needs an
update path to the latest one.

The full pack of the version carries the generated files of the tree: the
history of the version, and the dependency snapshot of repo_dep encoded as
.pack/packdep.pack. The snapshot is an input of the version besides the commit:
a kept full pack, or a kept update pack, that was built without it (or with
another snapshot) is not the pack of this run, even when the pack format and
the version pair match, see kept_pack.

The bytes of a pack only depend on the version it packs, so a pack that already
exists is kept as it is: the run of an output folder that is already complete
builds nothing again. A file of another format, of another version pair, or a
file that cannot be read as a pack is written again.

The version is not published here: repo_latest writes packrepo/latest.pack
after every pack of the version is on the disk, it is the barrier a client
sees the version switch at. An error of a pack build is raised to the caller:
the previous latest.pack keeps serving the clients and the next run starts
over, the atomic writes never leave a truncated pack at its final path.

Usage:
    from alasio.deploy_dev.pack_server.repo_base import RepoBase
    from alasio.deploy_dev.pack_server.repo_dep import RepoDep
    from alasio.deploy_dev.pack_server.repo_pack import RepoPack

    base = RepoBase(repo, config.data)
    pack = RepoPack(base, dep)
    pack.run()
"""

from hashlib import sha1

from alasio.deploy.pack.packdep import PACKDEP_PATH
from alasio.deploy_dev.pack.pack_full import PackFull
from alasio.deploy_dev.pack.pack_update import PackUpdate
from alasio.deploy_dev.pack_server.pack_check import kept_pack
from alasio.ext.cache import cached_property
from alasio.ext.path.atomic import atomic_write_stream
from alasio.logger import logger


class RepoPack:
    """
    Generate the packs of the repo tree, see the module docstring.

    Usage:
        pack = RepoPack(base, dep)
        pack.run()
        pack.pack      # the encoder, the input of repo_latest
    """

    def __init__(self, base, dep):
        """
        Args:
            base (RepoBase): Workspace and declarations of the repo
            dep (RepoDep): The dependency packs of the run after its run(), the
                published snapshots are encoded into packdep by PackFull when
                the version extras are built
        """
        self.base = base
        self.dep = dep
        # the pack and the kept checksum of the last run(), the input of
        # repo_latest: latest.pack is written only from a completed run
        self.pack = None
        self.kept_checksum = None
        # whether run() completed, the precondition of commit()

    @cached_property
    def version_folder(self):
        """
        Folder of the packs of the latest version: {packrepo}/{commit}

        Returns:
            PathStr: Absolute path of the folder
        """
        return self.base.packrepo_folder.joinpath(self.base.latest_commit)

    @cached_property
    def full_pack_file(self):
        """
        File of the full pack of the latest version: {version folder}/full.pack

        Returns:
            PathStr: Absolute path of the file
        """
        return self.version_folder.joinpath('full.pack')

    def run(self):
        """
        Generate the packs of the latest version.

        Writes the full pack of the latest commit and an update pack from every
        lookback commit to it. A pack of that version that already exists is
        kept, see kept_pack: the kept check also verifies the generated
        .pack/packdep.pack record, so a changed (or removed) dependency
        snapshot writes the packs again.

        latest.pack is not written here: repo_latest writes it after every pack
        of the version is on the disk, and uses it as the publication barrier
        of the version.

        Raises:
            ValueError: If the repo has no branch to pack, or a pack fails to
                build
        """
        self.pack = None
        self.kept_checksum = None
        latest = self.base.latest_commit
        # the lookback commits are computed before the packs are built: it
        # also reads the repo index the pack encoder looks the objects up in,
        # see PackRepoLookback
        lookback = self.base.lookback_commit
        logger.info(
            f'Generating packs of "{self.base.workspace.name}": '
            f'{len(lookback) + 1} versions, latest={latest}'
        )

        # the pack of the latest version is shared by every update pack, its
        # index pack and its encodings are cached on it, see PackUpdate
        pack = PackFull(self.base.repo, latest, dep_gen=self.dep)

        # 1. the full pack of the latest commit, the only full pack
        kept_checksum = self._write_full_pack(pack)
        # 2. an update pack from every lookback commit to the latest one
        self._write_update_packs(pack)
        # the inputs of repo_latest: latest.pack is written only after both
        # steps above completed
        self.pack = pack
        self.kept_checksum = kept_checksum
        logger.info(f'Packs of "{self.base.workspace.name}" generated, latest={latest}')

    def _expected_packdep(self, pack):
        """
        The generated file record a kept pack must carry.

        The packdep file is an input of the version besides the commit: a kept
        full pack or update pack that was built without it (or with another
        snapshot) is not the pack of this run, even when the pack format and
        the version pair match. A None value means the file must be absent, so
        a run that publishes no dependency channel also removes the file from
        the kept pack.

        Args:
            pack (PackFull): The version being packed, its packdep is encoded
                on first use and shared by every kept check of the run

        Returns:
            dict[str, tuple[int, bytes] | None]: {PACKDEP_PATH: (size, sha1)}
                or {PACKDEP_PATH: None}
        """
        packdep = pack.packdep
        if not packdep:
            return {PACKDEP_PATH: None}
        return {PACKDEP_PATH: (len(packdep), sha1(packdep).digest())}

    def _write_full_pack(self, pack):
        """
        Write the full pack of the latest version to the version folder.

        The full pack of an earlier run is kept as it is, see kept_pack: the
        encoding of a full pack is the most expensive step of a run, and the
        bytes of a pack only depend on the version. latest.pack is built from
        the checksum of the kept file, see repo_latest.

        Args:
            pack (PackFull): Pack of the latest version

        Returns:
            bytes | None: Index pack checksum of the kept file, None when the
                pack was written by this run: latest.pack takes the checksum
                cached on the encoder then, see PackEncodeBase.latest_pack
        """
        file = self.full_pack_file
        self.base.touch(file)
        kept = kept_pack(
            file, pack.pack_version, pack.current_version, '',
            expected=self._expected_packdep(pack))
        if kept is not None:
            logger.info(f'Full pack exists, skipped: "{file}"')
            return kept.index_checksum
        logger.info(f'Writing full pack: "{file}"')
        atomic_write_stream(file, pack.iter_pack_data())
        return None

    def _write_update_packs(self, pack):
        """
        Write the update pack of every lookback commit to the version folder.

        An update pack of an earlier run is kept like the full pack, see
        kept_pack: only the packs of the version that are missing are built.
        Every update pack is logged with its position in the lookback window, so
        the overall progress of a run with many lookback commits is visible.

        Args:
            pack (PackFull): Pack of the latest version, shared by every update
                pack
        """
        commit_list = self.base.lookback_commit
        total = len(commit_list)
        for index, old in enumerate(commit_list, start=1):
            file = self.version_folder.joinpath(f'update_{old}.pack')
            self.base.touch(file)
            kept = kept_pack(
                file, pack.pack_version, pack.current_version, old,
                expected=self._expected_packdep(pack))
            if kept is not None:
                logger.info(f'[{index}/{total}] Update pack exists, skipped: "{file}"')
                continue
            logger.info(f'[{index}/{total}] Packing update pack from {old}')
            update = PackUpdate(pack, old)
            logger.info(f'Writing update pack: {old} -> {pack.current_version}, "{file}"')
            atomic_write_stream(file, update.iter_pack_data())
