from hashlib import sha1

import trio

from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy.pack.job_base import JobBase, PendingFile
from alasio.deploy.pack.job_reset import ResetJob
from alasio.deploy.pack.pack_model import IdxInfo
from alasio.ext.cache import InstanceCacheOperation, cached_property_async
from alasio.ext.path.atomic import atomic_read_bytes, atomic_write, file_write
from alasio.logger import logger

# httpx2 is imported lazily in the places that use it: the package resolves
# its own version with importlib.metadata at import time, which the in-memory
# filesystem of the tests cannot answer


class SourceError(Exception):
    """
    Raised when the source file of a record fails the size + sha1 check.

    The record is kept in self.error, download() fetches its content
    from the new full pack instead.
    """


class IndexChecksumError(ValueError):
    """
    Raised when the prepared index pack cannot be bound to the trusted one.

    The update pack is not a resume base for the rebuild that follows: the
    workspace is discarded instead of leaving its records as a deletion base.
    """


class UpdateRecordError(ValueError):
    """
    Raised when the update pack records do not match the trusted index.

    The update pack is not a resume base for the rebuild that follows: the
    workspace is discarded instead of leaving its records as a deletion base.
    """


class UpdateJob(JobBase):
    """
    An update pack unpack task, interruptible and resumable.

    The pack data is passed in __init__, the caller stores it to the
    job file ({ledger}/workspace/job.pack) with write() before
    unpacking, so a killed process leaves the record of the task in
    flight:

        job = UpdateJob(
            data, server=server,
            expected_old_version='c1', expected_new_version='c2',
        )
        await job.run()

    The update pack upgrades the local working tree from the old
    version to the new version; the caller can name that pair with
    expected_old_version / expected_new_version and a pack of another pair is
    rejected before unpack() prepares any target record. DeployJob.update()
    always names the pair it checked, so the path of the server cannot decide
    the versions on its own. Every file follows the same flow:
    read - verify - decompress to a tmp file, file content never
    stays in memory. The index pack (index.pack in the ledger folder
    of the target) is a normal record of the update:

    1. unpack() decompresses every file to {ledger}/workspace/
       {size}_{sha1}_{rank}.tmp, real files are untouched. The tmp
       files are named by the tmp manager of the workspace (the rank
       is the occurrence index of the content among the records that
       need a tmp file, see TmpManager), so a leftover tmp file of an
       interrupted run is found again and reused. Records are computed
       from the update pack data and the old files
       recorded in refinfo: A records are decompressed directly, C
       (copied) records copy the source blob (read from the tmp file
       of the earlier new file, or from the verified working tree
       file), M / RM records decompress a zstd patch from the old
       file, R (renamed) records move the old file. The record of
       the index pack is an M record from the old index to the new
       index: the local index pack is verified against the
       refinfo like any other old file, a self-consistent but wrong
       local index fails the check. A source that fails the size +
       sha1 check (missing, wrong content or unfixable EOL) is kept
       in self.error.
    2. download() fetches the content of the failed records from the
       full pack of the new version (the new index pack records
       carry the offsets), like ResetJob, and writes their tmp
       files. The index pack record is downloaded as a whole index
       pack, it is not a file of the new full pack. The local source
       files are not repaired: the update brings the records of the
       update pack to the new version, the other files are checked
       by ResetJob. Records that cannot be downloaded or fail the
       size + sha1 check stay in self.error, this is an unsolvable
       problem per the draft of PackEncodeBase.
    3. _get_validated_index compares the prepared index pack with the
       caller's trusted checksum, when it names one. The prepared index
       is workspace/new_index.tmp, or the local index when the index record
       was skipped. _validate_update_records() then checks the new file
       records, deletions and refinfo against the trusted index and the old
       index. _validate_remaining() verifies the files the update pack does
       not cover against that same cached index.
    4. replace() moves every tmp file to the target path atomically,
       removes the deleted markers and the renamed sources, and
       commits the new index pack last (replace_index()), only when
       every record landed.

    On failure the workspace is kept for the next run to resume from
    (the job file and the tmp files written so far): errors during
    write() and unpack() are safe, no real file was written, errors
    during replace() leave partially replaced files that the resumed
    run repairs. The workspace is cleaned up only when every record
    landed, no record stayed in self.error and the new index pack was
    committed, see run().

    Note: the exclusive lock of the target ledger (the lock file, see
    DeployJob.locked()) is held by DeployJob around the whole update
    flow (full pack, update pack and file check); a direct user of
    this job must take it itself.
    """

    def __init__(
            self, data, server=None, root=None, name='',
            expected_old_version='', expected_new_version=''):
        """
        Args:
            data (PackDecodeBase | bytes): Update pack data: the validated
                decoder of ServerFile.get_update_pack() (reused as it is, the
                pack is not decoded a second time), or raw bytes, which
                unpack() decodes and validates
            server (ServerFile, optional): Server to download the
                index pack and the failed records. Defaults to None.
            root (str, optional): Folder to update. Defaults to None,
                env.PROJECT_ROOT
            name (str, optional): Ledger key of the target. Defaults to
                '', the project tree target
            expected_old_version (str, optional): Version the pack must
                update from. Defaults to '', no verification
            expected_new_version (str, optional): Version the pack must
                install. Defaults to '', no verification
        """
        if isinstance(data, PackDecodeBase):
            # the pack of ServerFile.get_update_pack(), already validated: the
            # decoder is reused as it is (no second decode / checksum)
            self._decoder = data
            data = data.data
        else:
            self._decoder = None
        super().__init__(data, root=root, name=name)
        self.server = server
        # the version pair the caller expects, checked before unpack() writes
        # the records; '' skips the check, the deploy flow always names it
        self.expected_old_version = expected_old_version
        self.expected_new_version = expected_new_version
        self.error: "list[PendingFile]" = []
        # version of the update pack, used to download the index pack
        self._version = ''
        # expected index checksum of the version, '' when the caller has no
        # trusted snapshot; the fallback index download validates it and the
        # prepared index is compared with it before replace(). It must be set
        # before _get_validated_index is awaited, DeployJob sets it before run
        self.index_checksum = ''
        # the data was read back from the job file (the kept pack the server
        # confirmed, see DeployJob._get_update_pack): write() keeps the file
        # as-is instead of rewriting the same bytes
        self.job_file_kept = False

    async def run(self):
        """
        Execute the full update flow.

        Writes the job file first, then unpacks, downloads the failed
        records, binds the prepared index to the expected checksum, verifies
        the remaining local files and replaces all files in one pass. The
        new index pack is committed last and only when every record
        landed, see replace_index(). The network phases await on the
        event loop, the local phases run in a worker thread (see
        JobBase.run()). On failure the workspace is kept so the next
        run resumes the job: errors during write() and unpack() are
        safe because no real file was written and are logged as
        warning, errors during replace() leave partially replaced
        files and are logged as error. An index checksum mismatch discards
        the workspace instead: a pack that cannot be the trusted target is
        not a resume base for the rebuild that follows. The workspace is
        cleaned up only when every record landed, no record stayed in
        self.error and the new index pack was committed.

        Returns:
            bool: True if every file is updated, False otherwise
        """
        try:
            # the records of the job file of a previous run join the
            # deletion base: read them before write() replaces it
            await trio.to_thread.run_sync(self._warm_residue)
            await self.tmp_manager_init()
            await trio.to_thread.run_sync(self.write)
            logger.info(f'Updating files to {self.described_target}')
            await trio.to_thread.run_sync(self.unpack)
            await self.download()
            old_fileinfo = {}
            if self.index_checksum:
                old_fileinfo = await self._validate_update_records()
            await self._validate_remaining(old_fileinfo)
        except (IndexChecksumError, UpdateRecordError) as e:
            # the update pack cannot be trusted: no target file was written,
            # and its records must not become the deletion base of the
            # rebuild that follows
            logger.warning(f'Failed to update: {e}, discarding the workspace')
            try:
                await trio.to_thread.run_sync(self.cleanup)
            except Exception as cleanup_error:
                logger.warning(f'Failed to discard the workspace: {cleanup_error}')
            return False
        except Exception as e:
            # no real file was written: the workspace (the job file
            # and the tmp files written so far) is kept as the resume
            # base of the next run
            logger.warning(f'Failed to update: {e}, keeping the workspace for the next run')
            return False
        try:
            logger.info(f'Replacing files to {self.described_target}')
            # the new index pack is committed only when every record
            # landed: a failed flow keeps the old local version
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
                f'Failed to update: {len(self.error)} file(s) stayed in error, '
                f'the workspace is kept for the next run'
            )
            return False
        # every record landed and the new index pack is committed: the
        # workspace is done, clean it up atomically
        await trio.to_thread.run_sync(self.cleanup)
        logger.info(f'Update done')
        return True

    def _validate_version_pair(self, decoder):
        """
        Check the version pair recorded inside the update pack.

        The pack path names the pair, but the file itself is the only proof
        of what it updates; a self-consistent pack of another pair cannot be
        applied to these files. The check runs before unpack() prepares any
        record and before replace() can touch a real file.

        Args:
            decoder (PackDecodeBase): Decoder of the update pack

        Raises:
            ValueError: If the pack updates from another version or installs
                another version than the expected one
        """
        expected = self.expected_old_version
        if expected and decoder.old_version != expected:
            raise ValueError(
                f'The update pack updates from "{decoder.old_version}", '
                f'expected "{expected}"'
            )
        expected = self.expected_new_version
        if expected and decoder.current_version != expected:
            raise ValueError(
                f'The update pack installs "{decoder.current_version}", '
                f'expected "{expected}"'
            )

    @cached_property_async
    async def _get_validated_index(self):
        """
        The index pack the update prepares, checked against the trusted one.

        The index pack is the commit record of the flow. When the caller
        names the checksum of the trusted index (the packdep snapshot of a
        dependency, or the latest index of the mod tree), the index prepared
        by the update pack must be exactly that index: the update pack is
        self-consistent on its own, it can still carry another index for the
        same version. The read and validation run on the first await, after
        the index is prepared and before any target file is replaced; a
        mismatch fails the flow, the caller rebuilds from the trusted version
        and checksum. The validated decoder is cached on the instance, so
        _validate_remaining() reuses exactly the index the commit is bound to.

        The prepared index is workspace/new_index.tmp when the update pack
        has an index record. Without an index record the local index is the
        prepared one (the record was skipped because it already matches).
        index_checksum must be set before the first await, DeployJob sets it
        before run(). The returned decoder is localized to the target
        namespace and cached, so later users see the same records.

        Without a trusted checksum this is the legacy view: a missing index
        yields None and a decoded index is returned without the trusted
        checksum / version checks. _validate_remaining() only needs the
        records then.

        Returns:
            PackDecodeBase | None: The prepared index pack, localized, None
                when the caller has no trusted checksum and the file is
                missing

        Raises:
            IndexChecksumError: If a trusted checksum is set and the index
                pack is missing, malformed, records another version, or its
                checksum is not the expected one
            PackDecodeError: If no trusted checksum is set and the index pack
                cannot be decoded
        """
        expected = self.index_checksum
        if self.pending_index is not None:
            file = self.workspace.joinpath(self.NEW_INDEX)
        else:
            file = self.index_file
        try:
            data = await trio.to_thread.run_sync(atomic_read_bytes, file)
        except FileNotFoundError as e:
            if not expected:
                return None
            raise IndexChecksumError(
                f'Failed to validate the index pack of the update: '
                f'the file is missing: "{file}"') from e
        try:
            decoder = await trio.to_thread.run_sync(PackDecodeBase, data)
            if expected:
                await trio.to_thread.run_sync(decoder.validate_index)
        except PackDecodeError as e:
            if not expected:
                raise
            raise IndexChecksumError(
                f'Failed to validate the index pack of the update: {e}') from e
        if expected:
            if decoder.index_checksum != expected:
                raise IndexChecksumError(
                    f'The update pack produces an index pack with checksum '
                    f'"{decoder.index_checksum}", expected "{expected}"'
                )
            expected_version = self.expected_new_version
            if expected_version and decoder.current_version != expected_version:
                raise IndexChecksumError(
                    f'The index pack records version "{decoder.current_version}", '
                    f'expected "{expected_version}"'
                )
        return self.localize(decoder)

    def write(self):
        """
        Write the data to the job file, so that the file is the record of
        the task in flight (the next flow derives its work from the tree
        and the local index, see residue_fileinfo()).

        The job file lives in the workspace, it is written atomically: a
        killed process leaves the previous content (or none) instead of a
        truncated file, and the leftovers of the interrupted write are
        removed by the tmp manager of the next run (TmpManager). A run
        whose data was read back from the job file (job_file_kept, the kept
        pack the server confirmed) does not write it again: the file
        already holds exactly these bytes.
        """
        if self.job_file_kept:
            return
        atomic_write(self.job_file, self._data)

    def unpack(self):
        """
        Prepare all files in the workspace, real files are untouched.

        Computes every record of the update pack: files that exist and
        pass the size + sha1 check are skipped, the others are
        decompressed to tmp files, filling self.pending with the data
        changes to apply in replace_data(). Every file follows the same
        flow: read the source, verify it, decompress to a tmp file,
        the content never stays in memory. The index pack is updated
        like a normal file, its local copy is verified against the
        refinfo. Records whose source fails the size + sha1 check are
        kept in self.error, download() fetches their content from the
        new full pack instead.
        """
        decoder = self._decoder
        if decoder is None:
            decoder = PackDecodeBase(self._data)
            decoder.validate()
            # keep the decoded pack for the record check and the remaining
            # validation, a raw data job decodes once
            self._decoder = decoder
        if not decoder.old_version:
            raise ValueError('UpdateJob requires an update pack, got a full pack')
        self._validate_version_pair(decoder)
        # rewrite the pack area paths of the pack into the ledger
        # folder of the target before any comparison
        self.localize(decoder)
        self._version = decoder.current_version
        # the update pack records the changed files of the new version
        # only: a partial emptiness base of replace_data(), the new index
        # decoded in _validate_remaining() replaces it with the full
        # records when the server is available
        self.new_fileinfo = decoder.fileinfo
        self.error = []

        # the changes to apply in replace_data(), the index pack record is
        # routed out of the list at the end
        pending = []
        for path, info in decoder.fileinfo.items():
            target = self.root.joinpath(path)
            if info.edit == 2:
                # deleted marker, its target is removed in replace()
                pending.append(PendingFile(info=info, tmp=''))
                continue
            # R / RM records move the source file: its deletion is
            # scheduled here as a deleted marker and applied by
            # replace_data() after every write of the batch (the
            # pending order does not matter)
            deleted = info.source_path if info.edit == 3 else ''
            current = self._read_current(target)
            result = self._matches(info, current)
            if result.match:
                # the target file exists and passes the size + sha1 check
                if deleted:
                    self._append_deleted(pending, deleted)
                if result.mode_matched:
                    # the content and the mode are verified, the record
                    # needs no tmp file and applies for no tmp name
                    continue
                # only the mode differs, the content is verified:
                # write the current content to the tmp file, replace()
                # chmod-ed the target to the record mode
                tmp = self.tmp_file(path, info)
                if not self._matches(info, self._read_current(tmp)).match:
                    file_write(tmp, current.data)
                self.register_tmp(path, info, tmp)
                pending.append(PendingFile(
                    info=info, tmp=tmp, mode=info.mode_decoded))
                continue
            if result.match_data:
                # only the EOL differs, write the converted content
                # to the tmp file without reading the sources
                tmp = self.tmp_file(path, info)
                file_write(tmp, result.match_data)
                if deleted:
                    self._append_deleted(pending, deleted)
                self.register_tmp(path, info, tmp)
                pending.append(PendingFile(
                    info=info, tmp=tmp, mode=info.mode_decoded if info.mode == 1 else None))
                continue
            try:
                content, source_tmp = self._read_file(decoder, info)
            except SourceError:
                # the source fails the size + sha1 check, download()
                # fetches the content of the record instead and applies
                # for the tmp file there
                self.error.append(PendingFile(info=info, tmp=''))
                continue
            # the record uses the leftover its source lookup read the
            # content from when there is one (the file it read is claimed
            # by the lookup and is the tmp file it writes here), a fresh
            # name otherwise
            tmp = source_tmp or self.tmp_file(path, info)
            if not self._matches(info, self._read_current(tmp)).match:
                # decompress and write to the tmp file
                file_write(tmp, content)
            if deleted:
                self._append_deleted(pending, deleted)
            self.register_tmp(path, info, tmp)
            # the file is written by python with the default mode 666,
            # a 755 record is chmod-ed in replace()
            pending.append(PendingFile(
                info=info, tmp=tmp, mode=info.mode_decoded if info.mode == 1 else None))

        # the index pack is the commit record of the flow, replace_index()
        # commits it after every data file: route it out of pending. A
        # skipped index record (the local index is already the new one)
        # leaves pending_index as None
        index = [p for p in pending if p.info.path == self.index_rel]
        self.pending_index = index[0] if index else None
        self.pending = [p for p in pending if p.info.path != self.index_rel]

    async def download(self):
        """
        Download the content of the failed records from the server
        and write their tmp files.

        A record whose source failed the size + sha1 check cannot be
        computed locally: its content is fetched from the full pack of
        the new version (the new index pack records carry the
        offsets), decompressed, verified and written to its tmp file
        like ResetJob. The index pack record is downloaded as a whole
        index pack: it is not a file of the new full pack, and it is
        downloaded first because the other records need the new index
        for their offsets. The local source files are not repaired.
        The requests await on the event loop; the file reads and the tmp
        writes run in worker threads.
        Records that cannot be downloaded or fail the size + sha1
        check stay in self.error, this is an unsolvable problem per
        the draft of PackEncodeBase.
        """
        import httpx2
        if not self.error:
            return
        server = self.server
        if server is None:
            # the failed records are unsolvable without the server
            logger.warning(
                f'Failed to download the content of {len(self.error)} files: '
                f'no server provided'
            )
            return
        pending = []
        failed = []
        # the index pack record first: it is not a file of the new
        # full pack, the whole index pack is downloaded
        new_index = None
        for item in self.error:
            if item.info.path != self.index_rel:
                continue
            info = item.info
            tmp = self.workspace.joinpath(self.NEW_INDEX)
            current = await trio.to_thread.run_sync(self._read_current, tmp)
            if self._matches(info, current).match:
                # a leftover tmp file passes the size + sha1 check, reuse it
                self.pending_index = PendingFile(
                    info=info, tmp=tmp, mode=info.mode_decoded if info.mode == 1 else None)
                continue
            try:
                # the client validates the index pack it returns (the trailing
                # checksum covers the header, the length and the whole index
                # section) and reselects the mirror when it is broken
                new_index = await server.get_index_pack(self._version, self.index_checksum)
            except (PackDecodeError, httpx2.HTTPError) as e:
                # cannot be downloaded or fails the size + sha1 check,
                # the record stays in error, this is unsolvable
                logger.warning(f'Failed to download {self.index_rel}: {e}')
                new_index = None
                failed.append(item)
            else:
                await trio.to_thread.run_sync(file_write, tmp, new_index.data)
                self.pending_index = PendingFile(
                    info=info, tmp=tmp, mode=info.mode_decoded if info.mode == 1 else None)
            break
        # the other records, downloaded from the new full pack with
        # the offsets of the new index records
        if new_index is None:
            # the new index pack: the fixed tmp file prepared in
            # unpack() or download(), or the local index (already the
            # new one when the index record was skipped on a resumed
            # run)
            try:
                data = await trio.to_thread.run_sync(
                    atomic_read_bytes, self.workspace.joinpath(self.NEW_INDEX))
            except FileNotFoundError:
                pass
            else:
                new_index = PackDecodeBase(data)
        if new_index is None:
            try:
                data = await trio.to_thread.run_sync(atomic_read_bytes, self.index_file)
            except FileNotFoundError:
                # the local index is missing and could not be
                # downloaded, the offsets are unavailable
                failed += [item for item in self.error if item.info.path != self.index_rel]
                self.pending += pending
                self.error = failed
                return
            new_index = PackDecodeBase(data)
        new_index = self.localize(new_index)
        for item in self.error:
            if item.info.path == self.index_rel:
                continue
            info = item.info
            tmp = self.tmp_file(info.path, info)
            current = await trio.to_thread.run_sync(self._read_current, tmp)
            if self._matches(info, current).match:
                # a leftover tmp file passes the size + sha1 check, reuse it
                self.register_tmp(info.path, info, tmp)
                pending.append(PendingFile(
                    info=info, tmp=tmp, mode=info.mode_decoded if info.mode == 1 else None))
                continue
            new_info = new_index.fileinfo.get(info.path)
            if new_info is None:
                # the new index does not record the file, this should
                # not happen
                failed.append(item)
                continue
            try:
                # the client fetches the range of the record from the new full
                # pack, decodes it and validates it against the record (a bad
                # copy makes the mirror reselect and the download is retried
                # once)
                content = await server.get_file_data(new_index, new_info)
            except (PackDecodeError, httpx2.HTTPError) as e:
                # cannot be downloaded or fails the size + sha1 check,
                # the record stays in error, this is unsolvable
                logger.warning(f'Failed to download {info.path}: {e}')
                failed.append(item)
                continue
            await trio.to_thread.run_sync(file_write, tmp, content)
            self.register_tmp(info.path, info, tmp)
            pending.append(PendingFile(
                info=info, tmp=tmp, mode=info.mode_decoded if info.mode == 1 else None))
        self.pending += pending
        self.error = failed

    async def _validate_update_records(self):
        """
        Cross-check the update pack records against the trusted index.

        The update pack is self-consistent and the index it prepares can be
        the trusted one, but its file records are still its own claim. Every
        non-deleted record must match the trusted index record of the same
        path (size, sha1, mode and eol), and a deleted record must not remove
        a file the trusted index keeps. The deletion and rename targets must
        be files of the old local index or of the job file of a previous run:
        a pack never deletes a file it does not manage. The old side of the
        update (refinfo) must match the local old index as well, so the delta
        is applied to the version it was built from.

        The check runs before _validate_remaining(), which then adds the old
        files the trusted index no longer has to the deletion list; a missing
        deletion in the pack therefore cannot leave an obsolete file behind.

        Returns:
            dict[str, IdxInfo]: The old local index records, the leftover
                deletion base of _validate_remaining()

        Raises:
            UpdateRecordError: If an update pack record does not match the
                trusted index, a deletion is not a managed old file, or an
                old-side record does not match the local old index
        """
        new_index = await self._get_validated_index
        if new_index is None:
            return {}
        old_fileinfo = await trio.to_thread.run_sync(self._old_fileinfo_from_index)
        removable = {**old_fileinfo, **self.residue_fileinfo}
        trusted = new_index.fileinfo
        for path, info in self._decoder.fileinfo.items():
            if path == self.index_rel:
                continue
            expected = trusted.get(path)
            if info.edit == 2:
                if expected is not None and expected.edit != 2:
                    raise UpdateRecordError(
                        f'The update pack deletes "{path}", which the trusted index keeps')
                if path not in removable:
                    raise UpdateRecordError(
                        f'The update pack deletes "{path}", which is not a file of the old index')
                continue
            if expected is None or expected.edit == 2:
                raise UpdateRecordError(
                    f'The update pack writes "{path}", which the trusted index does not keep')
            if (info.size, info.sha1, info.mode, info.eol) != (
                    expected.size, expected.sha1, expected.mode, expected.eol):
                raise UpdateRecordError(
                    f'The update pack record of "{path}" does not match the trusted index')
            if info.edit == 3 and info.source_path:
                source = info.source_path
                expected_source = trusted.get(source)
                if expected_source is not None and expected_source.edit != 2:
                    raise UpdateRecordError(
                        f'The update pack renames "{source}", which the trusted index keeps')
                if source not in removable:
                    raise UpdateRecordError(
                        f'The update pack renames "{source}", which is not a file of the old index')
        for path, ref in self._decoder.refinfo.items():
            if path == self.index_rel:
                continue
            old = old_fileinfo.get(path)
            if old is None or old.size != ref.size or old.sha1 != ref.sha1:
                raise UpdateRecordError(
                    f'The update pack source "{path}" does not match the old index')
        return old_fileinfo

    async def _validate_remaining(self, old_fileinfo=None):
        """
        Verify the local files not covered by the update pack.

        The records of the update pack were verified in unpack(), they
        are filtered out of the index view passed to ResetJob: the
        local index pack is committed by replace_index() after the
        data files, so the new index is passed in directly with only
        the remaining files. When index_checksum was set, the index is
        the decoder _get_validated_index bound to it, not a second read of
        the prepared file. The repaired records are merged into
        self.pending, replace_data() applies them together with the
        update records, so the real files are touched only once. The
        missing files are downloaded by the nested ResetJob, its
        download() awaits on the event loop like this job's own
        download().

        The deletion base of the flow also covers the records of the job
        file of a previous run (see residue_fileinfo()) and the old index
        records passed by the caller: a file that only an intermediate
        version has - neither the local index nor the update pack describes
        it - is removed with the leftover files of the old version.

        Args:
            old_fileinfo (dict[str, IdxInfo], optional): Records of the old
                local index, the base of the leftover deletions. Defaults to
                None, no old index base

        Skipped when the update has no server, or the index pack record
        failed (the local index is not the new one then).
        """
        if self.server is None:
            return
        if self.index_rel in [item.info.path for item in self.error]:
            # the index record failed, the local index is not the new
            # one, it cannot be trusted for the remaining check
            logger.warning('Failed to validate the remaining files: '
                           'the index pack record failed')
            return
        # the new index pack: the one whose checksum was validated, the fixed
        # tmp file prepared in unpack() or download(), or the local index when
        # the index record was skipped (it is already the new one then)
        new_index = await self._get_validated_index
        if new_index is None:
            return
        # the new index records every file of the new version, the
        # emptiness base of replace(). The dict is bound before the
        # decoder cache is replaced with the filtered view below: the
        # set() rebinds the cached attribute to another dict, the one
        # bound here keeps the full records
        self.new_fileinfo = new_index.fileinfo
        # the old files the trusted index no longer has are deleted even when
        # the update pack forgot a deletion record; the paths a previous run
        # may have written join the same base. A deleted marker of the new
        # index does not keep the path alive.
        new_files = {
            path: info for path, info in self.new_fileinfo.items()
            if info.edit != 2
        }
        self.pending += self._leftover_deletions(
            {**(old_fileinfo or {}), **self.residue_fileinfo}, new_files)
        # the records of the update pack were verified in unpack(),
        # keep only the remaining files in the index view, so ResetJob
        # validates exactly them
        pack_paths = set(self._decoder.fileinfo)
        fileinfo = {
            path: info for path, info in new_index.fileinfo.items()
            if path not in pack_paths
        }
        InstanceCacheOperation.set(new_index, 'fileinfo', fileinfo)
        reset = ResetJob(self.server, root=self.root, name=self.name)
        InstanceCacheOperation.set(reset, '_index_pack', new_index)
        # the nested reset shares the tmp manager of this job: the two
        # jobs write into the same workspace, their records must never
        # share a tmp file, so the occurrence counters continue with
        # the records of this job
        reset.tmp_manager = self.tmp_manager
        await trio.to_thread.run_sync(reset.validate_files)
        await reset.download()
        # the nested reset never prepares an index record (the index is
        # filtered out of its view), pending_index stays this job's own
        self.pending += reset.pending
        self.error += reset.error

    def _read_file(self, decoder, info):
        """
        Compute the working tree content of a record.

        The content comes from the update pack data or from the source
        files: A records and M records with plain data are decompressed
        directly, C (copied) records copy the source blob, M / RM
        records decompress a zstd patch from the old file (the source
        blob is the zstd dictionary), R (renamed) records take the
        source blob. The sources are read through _read_source_blob().

        Args:
            decoder (PackDecodeBase): Decoder of the update pack
            info (IdxInfo): Record of the file

        Returns:
            tuple[bytes, str]: Working tree content of the file, and the
                tmp file the content was read from when the record can
                claim it (see _read_source_blob()), '' when there is none

        Raises:
            SourceError: If the source of the record fails the size +
                sha1 check
        """
        if info.edit == 0 and info.source_lookback:
            # C (copied), the content is the source blob with the own eol
            blob, source_tmp = self._read_source_blob(decoder, info)
            return PackDecodeBase.apply_eol(blob, info.eol), source_tmp
        if info.edit == 3 and info.data_size == 0:
            # R (renamed), the content is the source blob with the own eol
            blob, _ = self._read_source_blob(decoder, info)
            return PackDecodeBase.apply_eol(blob, info.eol), ''
        if info.algo == 2 and info.source_lookback:
            # zstd patch from the old blob, the source is the dictionary
            blob, _ = self._read_source_blob(decoder, info)
            return decoder.decode_content(info, decoder.catdata(info), source=blob), ''
        # A (added), or M / RM with plain data
        return decoder.catfile(info), ''

    def _read_source_blob(self, decoder, info):
        """
        Read the LF blob of the source of a record, verified against
        the update pack.

        A C (copied) record may reference an earlier new file: its
        content is read from the tmp files of the source content (the
        leftovers of an interrupted run and the tmp files this run
        wrote, see TmpManager.candidates()), the first one that passes
        the check is used. The leftover it was read from is claimed: the
        file the reader read is the tmp file it writes, no second name
        and no second file for the same bytes. A source record that
        needed no tmp file (its target already matched the record) wrote
        none: its content is the working tree file then. The source of a
        C / M / RM / R record is otherwise an old file, read from the
        working tree and verified against the refinfo of the update pack
        (the pack is self-describing, no old index pack is needed): the
        size and sha1 of the LF blob must match, an EOL mismatch is
        converted. A file that fails the check raises SourceError, the
        caller fetches the record content from the server instead.

        Args:
            decoder (PackDecodeBase): Decoder of the update pack
            info (IdxInfo): Record whose source is read

        Returns:
            tuple[bytes, str]: LF blob of the source file, and the tmp
                file it was read from when the record claims it (a
                leftover of the content), '' when it came from the tmp
                file of another record or the working tree

        Raises:
            SourceError: If the source fails the size + sha1 check
        """
        source_path = info.source_path
        fileinfo = decoder.fileinfo
        source_info = fileinfo.get(source_path)
        if info.edit == 0 and source_info is not None and source_info.edit != 2:
            # C (copied): the source is an earlier new file. A D (deleted)
            # record of the same path is not a copy source: the source of a C
            # record can be a file the update deletes (the file of the old
            # version under a moved folder, e.g. a .dist-info of another
            # version), its content is read from the working tree below
            manager = self.tmp_manager
            for rank, source_tmp in manager.candidates(source_info.size, source_info.sha1):
                data = self._read_matched(source_tmp, source_info)
                if data is None:
                    continue
                # the file the content was read from is claimed when it is an
                # unclaimed leftover: it becomes the tmp file of this record
                # (it is not read twice, no second file is built for the same
                # bytes). A tmp file of this run belongs to its record (rank
                # -1): it is only read, replace_data() moves it to the target
                # of that record
                if rank >= 0 and manager.claim(
                        source_info.size, source_info.sha1, rank):
                    return self._to_blob(data, source_info.eol), source_tmp
                return self._to_blob(data, source_info.eol), ''
            # no tmp file of the source content, or none that passes the
            # check: the source content is the working tree file then. A
            # source record that is not applied yet does not match either, the
            # record stays unsolvable and is downloaded
            data = self._read_matched(self.root.joinpath(source_path), source_info)
            if data is None:
                raise SourceError(source_path)
            return self._to_blob(data, source_info.eol), ''
        # the source is an old file, verified against the refinfo
        ref = decoder.refinfo.get(source_path)
        if ref is None:
            raise SourceError(source_path)
        current = self._read_current(self.root.joinpath(source_path))
        if not current.exist:
            raise SourceError(source_path)
        data = current.data
        # the record size and sha1 are of the LF blob, a CRLF working
        # tree file is converted before the check
        if len(data) == ref.size and (not ref.sha1 or sha1(data).digest() == ref.sha1):
            return data, ''
        converted = data.replace(b'\r\n', b'\n')
        if b'\r' in converted:
            # a lone CR, the EOL cannot be converted cleanly
            raise SourceError(source_path)
        if len(converted) != ref.size or (ref.sha1 and sha1(converted).digest() != ref.sha1):
            raise SourceError(source_path)
        return converted, ''

    @staticmethod
    def _to_blob(content, eol):
        """
        Convert working tree content back to the LF blob.

        Args:
            content (bytes): Working tree file content, the callers
                pass the verified file data
            eol (int): Line ending rule, 0 for LF, 1 for CRLF, 2 for
                binary

        Returns:
            bytes: Content in the LF blob form
        """
        if eol == 1:
            return content.replace(b'\r\n', b'\n')
        return content

    @staticmethod
    def _append_deleted(pending, path):
        """
        Schedule the deletion of a renamed source file.

        replace_data() applies the deletion only after every write of
        the batch (the pending order does not matter): the target of
        the rename is written before its source is removed, so an
        interruption never loses the file (a leftover source is
        removed by the next run, which finds the target in place).

        Args:
            pending (list[PendingFile]): Pending list to append to
            path (str): Path of the renamed source
        """
        pending.append(PendingFile(info=IdxInfo(path=path, edit=2), tmp=''))
