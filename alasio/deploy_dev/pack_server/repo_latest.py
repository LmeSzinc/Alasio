"""
Publish the version of the repo tree (step 7).

The version a client runs is the one packrepo/latest.pack names:

    {workspace}/packrepo/latest.pack

The file carries the latest version and the checksum of its index pack (see
alasio.deploy.pack.packdep). It is the publication barrier of the run: every
pack of the version is on the disk before it is written, and a client that reads
a new latest.pack finds the complete packs of the version it switches to. A
failed run never writes it, so the previous version keeps serving the clients
until a run completes.

The folders of the versions that are not the latest one are removed after the
barrier: the folder of the version that was the latest one in an earlier run is
the leftover of the run, no latest.pack links its packs anymore, and the folder
would pile up on the disk for every version of the repo.

The dependency channels are published the same way: repo_dep removes the packs
of the versions out of the window, and repo_wheel the wheels the provided pins
do not ask for. Those removals also run after this barrier, see repo.

Usage:
    from alasio.deploy_dev.pack_server.repo_base import RepoBase
    from alasio.deploy_dev.pack_server.repo_latest import RepoLatest
    from alasio.deploy_dev.pack_server.repo_pack import RepoPack

    base = RepoBase(repo, config.data)
    pack = RepoPack(base, dep)
    pack.run()
    latest = RepoLatest(base, pack)
    latest.run()
"""

from alasio.ext.path.atomic import atomic_write
from alasio.logger import logger


class RepoLatest:
    """
    Publish the version of the repo tree, see the module docstring.

    Usage:
        latest = RepoLatest(base, pack)
        latest.run()
        latest.commit()
    """

    def __init__(self, base, pack):
        """
        Args:
            base (RepoBase): Workspace and declarations of the repo
            pack (RepoPack): The repo tree packs of the run after its run(),
                its pack is the version to publish
        """
        self.base = base
        self.pack = pack
        # whether run() completed, the precondition of commit(): a failed
        # publication must not remove the version the clients still run
        self._generated = False

    def run(self):
        """
        Write latest.pack: the latest version and its index pack checksum.

        The file is written on every run: it is the small file the clients read
        the published version from. A run that kept the full pack of an earlier
        run passes the checksum of the kept file, see RepoPack.run().

        Raises:
            RuntimeError: If the repo tree packs were not generated
            OSError: If latest.pack cannot be written
        """
        pack = self.pack.pack
        if pack is None:
            raise RuntimeError('RepoPack.run() must be called before RepoLatest.run()')
        self._generated = False
        file = self.base.packrepo_folder.joinpath('latest.pack')
        logger.info(f'Writing latest info: version={pack.current_version}, "{file}"')
        atomic_write(file, pack.latest_pack(self.pack.kept_checksum))
        self._generated = True

    def commit(self):
        """
        Remove the folders of the versions the publication replaced.

        Raises:
            RuntimeError: If run() was not called
        """
        if not self._generated:
            raise RuntimeError('RepoLatest.run() must be called before commit()')
        self.pack.commit()
        logger.info(f'Packs of "{self.base.workspace.name}" committed, '
                    f'latest={self.pack.pack.current_version}')
