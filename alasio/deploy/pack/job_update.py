from hashlib import sha1

import trio

from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy.pack.job_base import JobBase, PendingFile
from alasio.deploy.pack.job_reset import ResetJob
from alasio.deploy.pack.pack_model import IdxInfo
from alasio.ext.cache import InstanceCacheOperation
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


class UpdateJob(JobBase):
    """
    An update pack unpack task, interruptible and resumable.

    The pack data is passed in __init__, the caller stores it to the
    job file ({ledger}/workspace/job.pack) with write() before
    unpacking, so a killed process leaves the record of the task in
    flight:

        job = UpdateJob(data, server=server)
        await job.run()

    The update pack upgrades the local working tree from the old
    version to the new version, every file follows the same flow:
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
    3. replace() moves every tmp file to the target path atomically,
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

    def __init__(self, data, server=None, root=None, name=''):
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
        self.error: "list[PendingFile]" = []
        # paths of the records of the update pack, the filter base of
        # _validate_remaining(): the nested reset validates exactly the
        # files the update pack does not cover
        self._pack_paths: "set[str]" = set()
        # version of the update pack, used to download the index pack
        self._version = ''
        # the data was read back from the job file (the kept pack the server
        # confirmed, see DeployJob._get_update_pack): write() keeps the file
        # as-is instead of rewriting the same bytes
        self.job_file_kept = False

    async def run(self):
        """
        Execute the full update flow.

        Writes the job file first, then unpacks, downloads the failed records, verifies the
        remaining local files and replaces all files in one pass. The
        new index pack is committed last and only when every record
        landed, see replace_index(). The network phases await on the
        event loop, the local phases run in a worker thread (see
        JobBase.run()). On failure the workspace is kept so the next
        run resumes the job: errors during write() and unpack() are
        safe because no real file was written and are logged as
        warning, errors during replace() leave partially replaced
        files and are logged as error. The workspace is cleaned up
        only when every record landed, no record stayed in self.error
        and the new index pack was committed.

        Returns:
            bool: True if every file is updated, False otherwise
        """
        try:
            # the records of the job file of a previous run join the
            # deletion base (R19): read them before write() replaces it
            await trio.to_thread.run_sync(self._warm_residue)
            await self.tmp_manager_init()
            await trio.to_thread.run_sync(self.write)
            logger.info(f'Updating files to {self.described_target}')
            await trio.to_thread.run_sync(self.unpack)
            await self.download()
            await self._validate_remaining()
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
        if not decoder.old_version:
            raise ValueError('UpdateJob requires an update pack, got a full pack')
        # rewrite the pack area paths of the pack into the ledger
        # folder of the target before any comparison
        self.localize(decoder)
        self._version = decoder.current_version
        self._pack_paths = set(decoder.fileinfo)
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
                new_index = await server.get_index_pack(self._version)
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

    async def _validate_remaining(self):
        """
        Verify the local files not covered by the update pack.

        The records of the update pack were verified in unpack(), they
        are filtered out of the index view passed to ResetJob: the
        local index pack is committed by replace_index() after the
        data files, so the new index is passed in directly with only
        the remaining files. The repaired records are merged into
        self.pending, replace_data() applies them together with the
        update records, so the real files are touched only once. The
        missing files are downloaded by the nested ResetJob, its
        download() awaits on the event loop like this job's own
        download().

        The deletion base of the flow also covers the records of the job
        file of a previous run (R19, see residue_fileinfo()): a file
        that only an intermediate version has - neither the local index
        nor the update pack describes it - is removed with the leftover
        files of the old version.

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
        # the new index pack: the fixed tmp file prepared in unpack()
        # or download(), or the local index when the index record was
        # skipped (it is already the new one then)
        try:
            data = await trio.to_thread.run_sync(
                atomic_read_bytes, self.workspace.joinpath(self.NEW_INDEX))
        except FileNotFoundError:
            try:
                data = await trio.to_thread.run_sync(atomic_read_bytes, self.index_file)
            except FileNotFoundError:
                return
        new_index = self.localize(PackDecodeBase(data))
        # the new index records every file of the new version, the
        # emptiness base of replace(). The dict is bound before the
        # decoder cache is replaced with the filtered view below: the
        # set() rebinds the cached attribute to another dict, the one
        # bound here keeps the full records
        self.new_fileinfo = new_index.fileinfo
        # the records of the job file of a previous run (R19): the paths
        # it may have written that the new version does not have are
        # removed, like the leftover files of the old version (the local
        # index part of the base is already covered by the pack records)
        self.pending += self._leftover_deletions(
            self.residue_fileinfo, self.new_fileinfo)
        # the records of the update pack were verified in unpack(),
        # keep only the remaining files in the index view, so ResetJob
        # validates exactly them
        fileinfo = {
            path: info for path, info in new_index.fileinfo.items()
            if path not in self._pack_paths
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
