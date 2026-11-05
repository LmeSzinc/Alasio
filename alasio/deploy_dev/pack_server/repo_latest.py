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

The files of the versions that are not the latest one are removed after the
barrier: the folder of the version that was the latest one in an earlier run is
the leftover of the run, no latest.pack links its packs anymore, and the folder
would pile up on the disk for every version of the repo. The removal is the one
of the workspace (RepoBase.cleanup()), it runs right after this file is
written, see repo.

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

from alasio.deploy.pack.server_file import LatestInfo
from alasio.ext.path.atomic import atomic_write
from alasio.logger import logger

# the name of the barrier in the packrepo folder of the workspace
LATEST_FILE = 'latest.pack'


class RepoLatest:
    """
    Publish the version of the repo tree, see the module docstring.

    Usage:
        latest = RepoLatest(base, pack)
        latest.run()
        latest.info   # the version and checksum the file carries
        latest.file   # the file that was written
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
        # the barrier of the last run(), the input of repo_manifest: the
        # manifest names the version a client reads, without reading the file
        # back. None until then.
        self.info = None

    def run(self):
        """
        Write latest.pack: the latest version and its index pack checksum.

        The file is written on every run: it is the small file the clients read
        the published version from. A run that kept the full pack of an earlier
        run passes the checksum of the kept file, see RepoPack.run().

        The info of the file is read back from the bytes just written, not from
        the encoder: latest_pack() takes its checksum from the encoder or from
        the kept file, and the manifest of the run must name exactly what a
        client reads.

        Returns:
            LatestInfo: The version and the index pack checksum the file carries

        Raises:
            RuntimeError: If the repo tree packs were not generated
            OSError: If latest.pack cannot be written
        """
        pack = self.pack.pack
        if pack is None:
            raise RuntimeError('RepoPack.run() must be called before RepoLatest.run()')
        file = self.base.packrepo_folder.joinpath(LATEST_FILE)
        data = pack.latest_pack(self.pack.kept_checksum)
        self.base.touch(file)
        logger.info(f'Writing latest info: version={pack.current_version}, "{file}"')
        atomic_write(file, data)
        self.info = LatestInfo.parse(data)
        return self.info
