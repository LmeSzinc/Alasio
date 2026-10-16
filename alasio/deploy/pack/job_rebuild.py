import trio

from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy.pack.job_reset import ResetJob
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy.pack.local_probe import LocalProbe
from alasio.deploy.pack.pack_model import IdxInfo
from alasio.ext.cache import InstanceCacheOperation, cached_property
from alasio.ext.path.atomic import atomic_read_bytes, file_write
from alasio.logger import logger


class RebuildJob(ResetJob):
    """
    A rebuild task from the server: download the latest index pack,
    remove the leftover files of the old version and rebuild the
    working tree to the latest version, interruptible and resumable.

    Unlike ResetJob, the local index pack is never trusted: the latest
    index pack is downloaded unconditionally (download_index()), then a
    presence probe of the working tree (LocalProbe) decides how the files
    are rebuilt:

    - per file: the files of the new index are verified like ResetJob and
      the failed ones are downloaded with range requests of the full pack
    - full pack: the whole full pack of the version is downloaded with one
      request and applied by the unpack stage of UnpackJob

    The probe prefers the full pack when the tree supplies less than
    PRESENT_RATIO of the stored bytes or more than REQUEST_BUDGET blocks
    are missing (see LocalProbe): an empty tree or a fresh install pays
    one transfer instead of the record by record ranges, a tree that
    already holds the files keeps the per-file way. A failed full pack
    download fails the flow, there is no per-file fallback: the ranges of
    the per-file way would fetch the very same bytes from the very same
    file. The downloaded full pack is kept in the workspace
    ({ledger}/workspace/full.pack): a run that is interrupted after the
    download reuses it instead of transferring the whole file again, see
    _load_kept_full_pack().

    The leftover files are the difference between the old local index and
    the new one, so only managed files are removed, files the user placed
    by hand are untouched. The old fileinfo is read before the local index
    pack is replaced (the new index is prepared to the workspace first,
    replace_index() commits it last), and the new index pack is committed
    only when every file of it landed: an interruption
    during replace() leaves the old index pack in
    place, so a resumed run still computes the leftover deletion list
    from it.

    write() stores the RBIL marker to the job file: it is the record of
    the task in flight, a marker is never dispatched (the next flow
    derives its work from the tree and the local index, see
    JobBase.residue_fileinfo()).

    Note: the exclusive lock of the target ledger (the lock file, see
    DeployJob.locked()) is held by DeployJob around the whole update
    flow (full pack, update pack and file check); a direct user of
    this job must take it itself.
    """

    # marker of a rebuild task in the job file
    MARK = b'RBIL\x00'

    # the name of the workspace file the full pack of the flow is kept in
    # before it is applied: the resume base of an interrupted full pack
    # run (see _load_kept_full_pack()), cleaned up with the workspace when
    # the flow succeeds
    FULL_PACK = 'full.pack'

    def __init__(self, server, root=None, name=''):
        """
        Args:
            server (ServerFile): Server to download the index pack and
                the failed files
            root (str, optional): Folder to update. Defaults to None,
                env.PROJECT_ROOT
            name (str, optional): Ledger key of the target. Defaults to
                '', the project tree target
        """
        super().__init__(server, root=root, name=name)
        # {path: IdxInfo} of the old local index pack plus the records of
        # the job file of a previous run, the deletion base of the
        # leftover cleanup, {} when both are empty/missing
        self._old_fileinfo: "dict[str, IdxInfo]" = {}

    @cached_property
    def full_pack_file(self):
        """
        Returns:
            PathStr: Path of the full pack kept in the workspace, the
                resume base of an interrupted full pack run
        """
        return self.workspace.joinpath(self.FULL_PACK)

    async def run(self):
        """
        Execute the full rebuild flow.

        Writes the job marker first, then downloads the latest index pack
        unconditionally: the local
        index is never trusted, unlike ResetJob which validates it
        first. A presence probe of the working tree (LocalProbe) then
        decides how the files are rebuilt: verified and downloaded per
        file, or applied from the whole full pack in one transfer, see
        the class docstring. The new index pack is committed last by
        replace_index()
        and only when every file was rebuilt, see the class docstring.
        The network phases await on the event loop, the local phases
        run in a worker thread (see JobBase.run()). On failure the
        workspace is kept so the next run resumes the job: errors
        during write() and validation are safe because no real file
        was written and are logged as warning, errors during replace()
        leave partially replaced files and are logged as error. The
        workspace is cleaned up only when every file was rebuilt, no
        file stayed in self.error and the new index pack was
        committed.

        Returns:
            bool: True if every file is rebuilt, False otherwise
        """
        try:
            # the records of the job file of a previous run join the
            # deletion base: read them before write() replaces it
            await trio.to_thread.run_sync(self._warm_residue)
            await self.tmp_manager_init()
            await trio.to_thread.run_sync(self.write)
            logger.info(f'Rebuilding files to {self.described_target}')
            await self.download_index()
            # the presence probe of the tree decides the way the files are
            # rebuilt: the per-file way below, or the whole full pack when
            # the tree supplies too little of it (see LocalProbe)
            probe = LocalProbe(self._index_pack)
            presence = await trio.to_thread.run_sync(probe.presence, self.root)
            if presence.prefer_full_pack():
                await self.download_full_pack()
            else:
                # the deletion base of the leftover cleanup: the old local
                # index plus the records of the job file of a previous run,
                # so a file that only an intermediate version has is
                # removed too. The full pack path builds the same base in
                # its unpack stage, see download_full_pack()
                old_fileinfo = await trio.to_thread.run_sync(self._old_fileinfo_from_index)
                self._old_fileinfo = {**old_fileinfo, **self.residue_fileinfo}
                await trio.to_thread.run_sync(self.validate_files)
                await self.download()
                # the new index records every file of the new version, the
                # emptiness base of replace_data()
                self.new_fileinfo = self._index_pack.fileinfo
        except Exception as e:
            # no real file was written: the workspace (the job file
            # and the tmp files written so far) is kept as the resume
            # base of the next run
            logger.warning(f'Failed to rebuild: {e}, keeping the workspace for the next run')
            return False
        try:
            # the new index pack is committed after the rebuild and
            # only when every file of it landed: an interruption keeps
            # the old index pack in place, so a resumed run still
            # computes the leftover deletion list from it (the index
            # is prepared to pending_index, see JobBase.replace_index())
            await trio.to_thread.run_sync(self.replace, not self.error)
        except Exception as e:
            # real files may be partially replaced, the workspace is
            # kept for the next run to resume and repair the targets
            logger.error(f'Failed to replace file: {e}, keeping the workspace for the next run')
            return False
        if self.error:
            # some records could not be downloaded or verified: the new
            # index pack was not committed, the workspace is kept and
            # the next run retries the records
            logger.warning(
                f'Failed to rebuild: {len(self.error)} file(s) stayed in error, '
                f'the workspace is kept for the next run'
            )
            return False
        # every file was rebuilt and the new index pack is committed:
        # the workspace is done, clean it up atomically
        await trio.to_thread.run_sync(self.cleanup)
        logger.info(f'Rebuild done')
        return True

    async def download_full_pack(self):
        """
        Prepare every change of the new version from the full pack.

        The flow took the full pack path (the presence probe of the tree
        found too little of the pack to use, see LocalProbe): the whole
        full pack of the version is downloaded with one request, no range
        request, and applied by the unpack stage of UnpackJob in the shared
        workspace, so the pending changes of this job are the same ones the
        per-file way prepares. The pack is kept in the workspace first
        ({ledger}/workspace/full.pack): a run that is interrupted after the
        download reuses it instead of transferring the whole file again,
        see _load_kept_full_pack(). A failed download fails the flow:
        there is no per-file fallback, the range requests of the per-file
        way would fetch the very same bytes from the very same file.

        Raises:
            PackDecodeError: If the server is missing, or the full pack
                does not decode, validate or match the latest checksum
            httpx2.HTTPError: If the request fails
            AllMirrorsFailedError: If no mirror is usable
        """
        server = self.server
        if server is None:
            raise PackDecodeError('Failed to download the full pack: no server provided')
        # the latest index pack of the flow: the full pack must be the pack
        # of this version, the checksum rules out a mirror that serves
        # another pack under the same path
        version = self._index_pack.current_version
        checksum = self._index_pack.index_checksum
        kept = await trio.to_thread.run_sync(self._load_kept_full_pack, version, checksum)
        if kept is None:
            logger.info(f'Downloading the full pack of {version}')
            full = await server.get_full_pack(version, checksum)
            # keep the pack in the workspace before it is applied: an
            # interrupted run reuses it instead of downloading it again
            await trio.to_thread.run_sync(file_write, self.full_pack_file, full.data)
        else:
            logger.info(f'Reusing the full pack of {version} kept in the workspace')
            full = kept
        # the unpack stage of UnpackJob prepares the changes from the pack
        # in the shared workspace: the tmp manager is shared (the tmp files
        # of the run are named once and the ones an interrupted run left
        # are reused by content) and the residue records are the ones this
        # job read before write() replaced the job file
        unpack = UnpackJob(full, root=self.root, name=self.name)
        unpack.tmp_manager = self.tmp_manager
        InstanceCacheOperation.set(unpack, 'residue_fileinfo', self.residue_fileinfo)
        await trio.to_thread.run_sync(unpack.unpack)
        # the pending changes and the index commit record of the flow: the
        # same fields the per-file way fills, replace() applies them alike
        self.pending = unpack.pending
        self.pending_index = unpack.pending_index
        self.new_fileinfo = unpack.new_fileinfo

    def _load_kept_full_pack(self, version, checksum):
        """
        The full pack kept in the workspace, when it is the pack of the flow.

        The pack of an interrupted run (downloaded and kept before it was
        applied) is verified before it is offered for reuse: it must decode,
        validate (the checksums of both sections, one pass) and be the pack
        of the version (the version recorded in it and the index checksum of
        its front part match the requested ones). A truncated, damaged or
        foreign file is never offered, the pack is downloaded as usual.

        Args:
            version (str): Version of the flow
            checksum (str): Index pack checksum of the version, the one
                latest.pack advertised

        Returns:
            PackDecodeBase: Decoder of the kept pack, validated, None when
                there is nothing to reuse
        """
        try:
            data = atomic_read_bytes(self.full_pack_file)
        except FileNotFoundError:
            return None
        try:
            decoder = PackDecodeBase(data)
            decoder.validate()
        except PackDecodeError:
            return None
        if decoder.old_version or decoder.current_version != version:
            return None
        if checksum and decoder.index_checksum != checksum:
            return None
        return decoder

    def validate_files(self):
        """
        Collect the changes to apply against the new index.

        The records of the new index are verified like ResetJob
        (missing or mismatching files are recorded in self.error,
        fixable EOL or mode mismatches are written to tmp files
        without a download), then the leftover files are appended to
        self.pending: paths recorded in the old index (or in the job
        file of a previous run) but not in the new one become
        deleted markers, removed by replace_data(). The
        leftover deletions never enter self.error: self.error is the
        download list of download(), the leftover files are deleted,
        not downloaded. They are appended after the validation:
        ResetJob.validate_files() resets self.error at its start and
        applies for the tmp file names of the records only where they
        are needed (see tmp_file()), the appended deletions carry no
        tmp file and affect neither.

        Returns:
            bool: True if every file matches the new index, False
                otherwise
        """
        super().validate_files()
        # the leftover files are deleted, not downloaded: they go to
        # self.pending directly, self.error stays the download list
        self.pending += self._leftover_deletions(
            self._old_fileinfo, self._index_pack.fileinfo)
        return not self.error
