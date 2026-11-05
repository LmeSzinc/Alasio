"""
Write the manifest of a workspace (step 8).

The manifest is the record of what the workspace of a run holds, see
alasio.deploy_dev.pack_server.manifest:

    {workspace}/manifest.json

It is written after the publication barrier (repo_latest), so the version it
names is the version latest.pack serves, and before the cleanup of the run
(RepoBase.cleanup), so the cleanup keeps the file it just wrote.

The version and the checksum come from the barrier step, not from the file it
wrote: RepoLatest reads them back from the bytes it published, so the manifest
names exactly what a client reads, without a second read of the workspace.

The two lists of the manifest are the two halves of the same truth the cleanup
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

from alasio.deploy_dev.pack_server.manifest import MANIFEST_FILE, Manifest
from alasio.ext.cache import cached_property
from alasio.logger import logger


class RepoManifest:
    """
    Write the manifest of the workspace, see the module docstring.

    Usage:
        manifest = RepoManifest(base, latest)
        manifest.run()
        manifest.manifest   # the manifest of the run
    """

    def __init__(self, base, latest):
        """
        Args:
            base (RepoBase): Workspace and declarations of the repo
            latest (RepoLatest): The publication barrier of the run after its
                run(): its info is the version and checksum the manifest names
        """
        self.base = base
        self.latest = latest
        # the manifest written by run(), None until then
        self.manifest = None

    @cached_property
    def file(self):
        """
        File of the manifest in the workspace: {workspace}/manifest.json

        Returns:
            PathStr: Absolute path of the file
        """
        return self.base.workspace.joinpath(MANIFEST_FILE)

    def run(self):
        """
        Write the manifest: the version of the barrier and the files of the run.

        The version and the checksum are the ones RepoLatest published, so the
        manifest names exactly what a client reads from latest.pack, whether
        the full pack of the run was built or kept from an earlier run.

        Returns:
            Manifest: The manifest of the run

        Raises:
            RuntimeError: If the barrier of the run was not published yet, see
                repo.Repo.commit()
            OSError: If the manifest cannot be written
        """
        info = self.latest.info
        if info is None:
            raise RuntimeError('RepoLatest.run() must be called before RepoManifest.run()')
        files, private = self.base.repo_manifest()
        manifest = Manifest.build(
            channel=self.base.workspace.name,
            version=info.version,
            checksum=info.checksum,
            files=files,
            private=private,
        )
        written = manifest.write(self.file)
        # the manifest is a file of the run like every other one: the cleanup
        # of the version keeps what the run touches, whether it wrote the file
        # or found it current
        self.base.touch(self.file)
        self.manifest = manifest
        logger.info(
            f'Manifest of "{self.base.workspace.name}": version={manifest.version}, '
            f'{len(manifest.files)} files, {len(manifest.private)} private'
        )
        if written:
            logger.info(f'Write manifest: "{self.file}"')
        else:
            logger.info(f'Manifest exists, skipped: "{self.file}"')
        return manifest
