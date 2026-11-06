"""
Write the manifest of a workspace (step 8).

The record of a generation is written in two files, each one for its reader,
see alasio.deploy_dev.pack_server.manifest:

    {workspace}/manifest.json    the published contract: what a mirror serves
    {workspace}/.manifest.json   the local record: what stays on the disk

The published contract names the version latest.pack serves and the files a
mirror publishes (Manifest). The local record names the same version and the
files that never leave the disk of the generator, e.g. the wheel cache
(LocalManifest). The two are paired by the version and the checksum, and both
come from the barrier step, not from the file it wrote: RepoLatest reads them
back from the bytes it published, so the record names exactly what a client
reads, without a second read of the workspace.

The dot of the local file marks it private by the same rule as the cache
folder: the workspace mirrors to the server as it is, and the local record is
not one of the files a mirror serves.

Both files are written after the publication barrier (repo_latest), so the
version they name is the version latest.pack serves, and before the cleanup of
the run (RepoBase.cleanup), so the cleanup keeps the files it just wrote. The
local record goes first: the published contract is the last file of a
generation to land on the disk, here and on a mirror.

The two lists of the record are the two halves of the same truth the cleanup
uses: the files of the run that are published, and the files of the run that
stay on the disk of the generator. Nothing is derived a second time here: the
paths come from RepoBase.repo_manifest(), the set the steps touched.

Usage:
    from alasio.deploy_dev.pack_server.repo_base import RepoBase
    from alasio.deploy_dev.pack_server.repo_latest import RepoLatest
    from alasio.deploy_dev.pack_server.repo_manifest import RepoManifest

    base = RepoBase(repo, config.data)
    latest = RepoLatest(base, pack)
    latest.run()
    manifest = RepoManifest(base, latest)
    manifest.run()
"""

from alasio.deploy_dev.pack_server.manifest import LOCAL_MANIFEST_FILE, MANIFEST_FILE, LocalManifest, Manifest
from alasio.ext.cache import cached_property
from alasio.logger import logger


class RepoManifest:
    """
    Write the record of the workspace, see the module docstring.

    Usage:
        manifest = RepoManifest(base, latest)
        manifest.run()
        manifest.manifest        # the published contract of the run
        manifest.local_manifest  # the local record of the run
    """

    def __init__(self, base, latest):
        """
        Args:
            base (RepoBase): Workspace and declarations of the repo
            latest (RepoLatest): The publication barrier of the run after its
                run(): its info is the version and checksum the record names
        """
        self.base = base
        self.latest = latest
        # the published contract written by run(), None until then
        self.manifest = None
        # the local record written by run(), None until then
        self.local_manifest = None

    @cached_property
    def file(self):
        """
        File of the published contract in the workspace: {workspace}/manifest.json

        Returns:
            PathStr: Absolute path of the file
        """
        return self.base.workspace.joinpath(MANIFEST_FILE)

    @cached_property
    def local_file(self):
        """
        File of the local record in the workspace: {workspace}/.manifest.json

        Returns:
            PathStr: Absolute path of the file
        """
        return self.base.workspace.joinpath(LOCAL_MANIFEST_FILE)

    def run(self):
        """
        Write the record: the version of the barrier and the files of the run.

        The version and the checksum are the ones RepoLatest published, so the
        record names exactly what a client reads from latest.pack, whether the
        full pack of the run was built or kept from an earlier run.

        Returns:
            Manifest: The published contract of the run

        Raises:
            RuntimeError: If the barrier of the run was not published yet, see
                repo.Repo.commit()
            OSError: If a file of the record cannot be written
        """
        info = self.latest.info
        if info is None:
            raise RuntimeError('RepoLatest.run() must be called before RepoManifest.run()')
        files, private = self.base.repo_manifest()
        # the local record first: the published contract is the last file of a
        # generation to land on the disk, here and on a mirror. A write that
        # fails fails the run, so the two files of a generation never disagree
        local = LocalManifest.build(
            channel=self.base.workspace.name,
            version=info.version,
            checksum=info.checksum,
            private=private,
        )
        local_written = local.write(self.local_file)
        manifest = Manifest.build(
            channel=self.base.workspace.name,
            version=info.version,
            checksum=info.checksum,
            files=files,
        )
        written = manifest.write(self.file)
        # the two files are files of the run like every other one: the cleanup
        # of the version keeps what the run touches, whether it wrote the file
        # or found it current
        self.base.touch(self.local_file)
        self.base.touch(self.file)
        self.local_manifest = local
        self.manifest = manifest
        logger.info(
            f'Manifest of "{self.base.workspace.name}": version={manifest.version}, '
            f'{len(manifest.files)} files, {len(local.private)} private'
        )
        if local_written:
            logger.info(f'Write local manifest: "{self.local_file}"')
        else:
            logger.info(f'Local manifest exists, skipped: "{self.local_file}"')
        if written:
            logger.info(f'Write manifest: "{self.file}"')
        else:
            logger.info(f'Manifest exists, skipped: "{self.file}"')
        return manifest
