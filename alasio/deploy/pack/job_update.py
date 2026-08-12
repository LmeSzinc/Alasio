from hashlib import sha1

import httpx

from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy.pack.job_base import JobBase, PendingFile
from alasio.deploy.pack.pack_model import IdxInfo
from alasio.ext import env
from alasio.ext.path.atomic import atomic_read_bytes, atomic_write
from alasio.logger import logger


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
    job file .pack/workspace/job.pack with write() before unpacking,
    so an interrupted run can be resumed by the next run:

        job = DeployJob.get_unfinished_job(server)
        if job is not None:
            job.run()
        job = UpdateJob(data, server=server)
        job.write()
        job.run()

    The update pack upgrades the local working tree from the old
    version to the new version, every file follows the same flow:
    read - verify - decompress to a tmp file, file content never
    stays in memory. The index pack .pack/index.pack is a normal
    record of the update:

    1. unpack() decompresses every file to .pack/workspace/
       {size}_{sha1}_{index}.tmp, real files are untouched. Records
       are computed from the update pack data and the old files
       recorded in refinfo: A records are decompressed directly, C
       (copied) records copy the source blob (read from the tmp file
       of the earlier new file, or from the verified working tree
       file), M / RM records decompress a zstd patch from the old
       file, R (renamed) records move the old file. The record of
       the index pack is an M record from the old index to the new
       index: the local .pack/index.pack is verified against the
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
       writes the new index pack like any other file.

    On failure the workspace is kept, the next run resumes from it.

    Note: the exclusive lock on .pack/index.pack in the draft is shared
    by the whole update flow (full pack, update pack and file check),
    the caller is responsible for it.
    """

    def __init__(self, data, server=None, resume=False):
        """
        Args:
            data (bytes): Update pack data
            server (ServerFile, optional): Server to download the
                index pack and the failed records. Defaults to None.
            resume (bool): True if the data was read from the job file,
                run() does not write the job file again then
        """
        super().__init__(data)
        self.server = server
        self._resume = resume
        self.error: "list[PendingFile]" = []
        # {path: index} of fileinfo, used to build the tmp file names
        self._file_index: "dict[str, int]" = {}
        # version of the update pack, used to download the index pack
        self._version = ''
        # tmp file of the index record prepared in unpack(), the new
        # index pack, only the path is kept, the content is on disk
        self._index_tmp = ''

    def run(self):
        """
        Execute the full update flow.

        Writes the job file first unless the job was resumed from it,
        then unpacks, downloads the failed records and replaces all
        files. On failure the workspace is cleaned up: errors during
        write() and unpack() are safe because no real file was written
        and are logged as warning, errors during replace() leave
        partially replaced files and are logged as error.

        Returns:
            bool: True if every file is updated, False if some records
                stay in self.error
        """
        try:
            if not self._resume:
                self.write()
            logger.info(f'Updating files to "{env.PROJECT_ROOT}"')
            self.unpack()
            self.download()
        except Exception as e:
            # no real file was written, safe to clean up
            logger.warning(f'Failed to update: {e}')
            self.cleanup()
            return False
        try:
            logger.info(f'Replacing files to "{env.PROJECT_ROOT}"')
            self.replace()
        except Exception as e:
            # real files may be partially replaced
            logger.error(f'Failed to replace file: {e}')
            self.cleanup()
            return False
        # all changes applied, clean the workspace atomically
        self.cleanup()
        logger.info(f'Update done')
        return not self.error

    def write(self):
        """
        Write the data to the job file, so that a future run can resume
        from it if this run gets interrupted.
        """
        atomic_write(env.PROJECT_ROOT.joinpath(self.JOB_FILE), self._data)

    def unpack(self):
        """
        Prepare all files in the workspace, real files are untouched.

        Computes every record of the update pack: files that exist and
        pass the size + sha1 check are skipped, the others are
        decompressed to tmp files, filling self.pending with the
        changes to apply in replace(). Every file follows the same
        flow: read the source, verify it, decompress to a tmp file,
        the content never stays in memory. The index pack is updated
        like a normal file, its local copy is verified against the
        refinfo. Records whose source fails the size + sha1 check are
        kept in self.error, download() fetches their content from the
        new full pack instead.
        """
        decoder = PackDecodeBase(self._data)
        decoder.validate()
        if not decoder.refinfo:
            raise ValueError('UpdateJob requires an update pack, got a full pack')
        self._version = decoder.version
        self._file_index = {path: index for index, (path, _) in enumerate(decoder.fileinfo.items())}
        self.error = []
        self._index_tmp = ''

        pending = []
        for index, (path, info) in enumerate(decoder.fileinfo.items()):
            target = env.PROJECT_ROOT.joinpath(path)
            if info.edit == 2:
                # deleted marker, its target is removed in replace()
                pending.append(PendingFile(info=info, tmp='', current_mode=0))
                continue
            # R / RM records move the source file, its deletion is
            # scheduled in replace() on every path
            deleted = info.source_path if info.edit == 3 else ''
            current = self._read_current(target)
            result = self._matches(info, current)
            tmp = self.workspace.joinpath(f'{info.size}_{info.sha1}_{index}.tmp')
            if result.match:
                # the target file exists and passes the size + sha1 check
                if deleted:
                    self._append_deleted(pending, deleted)
                if self._mode_matches(info, current):
                    continue
                # only the mode differs, the content is verified:
                # write the current content to the tmp file, replace()
                # fixes the mode
                if not self._matches(info, self._read_current(tmp)).match:
                    atomic_write(tmp, current.data)
                pending.append(PendingFile(info=info, tmp=tmp, current_mode=0o666))
                continue
            if result.match_data:
                # only the EOL differs, write the converted content
                # to the tmp file without reading the sources
                atomic_write(tmp, result.match_data)
                if deleted:
                    self._append_deleted(pending, deleted)
                pending.append(PendingFile(info=info, tmp=tmp, current_mode=0o666))
                continue
            try:
                content = self._read_file(decoder, info)
            except SourceError:
                # the source fails the size + sha1 check, download()
                # fetches the content of the record instead
                self.error.append(PendingFile(info=info, tmp='', current_mode=0))
                continue
            if not self._matches(info, self._read_current(tmp)).match:
                # decompress and write to the tmp file
                atomic_write(tmp, content)
            if path == self.INDEX_PACK:
                # the tmp file of the index record is the new index
                # pack, download() reads it from the disk for the
                # offsets of the failed records
                self._index_tmp = tmp
            if deleted:
                self._append_deleted(pending, deleted)
            # the file is written by python with the default mode 666,
            # a 755 record is chmod-ed in replace()
            pending.append(PendingFile(info=info, tmp=tmp, current_mode=0o666))

        self.pending = pending

    def download(self):
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
        Records that cannot be downloaded or fail the size + sha1
        check stay in self.error, this is an unsolvable problem per
        the draft of PackEncodeBase.
        """
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
        new_index_data = None
        for item in self.error:
            if item.info.path != self.INDEX_PACK:
                continue
            info = item.info
            index = self._file_index[info.path]
            tmp = self.workspace.joinpath(f'{info.size}_{info.sha1}_{index}.tmp')
            if self._matches(info, self._read_current(tmp)).match:
                # a leftover tmp file passes the size + sha1 check, reuse it
                pending.append(PendingFile(info=info, tmp=tmp, current_mode=0o666))
                continue
            try:
                # the index pack is self-validating, the trailing
                # checksum covers the header, the length and the whole
                # index section
                new_index_data = server.get_index_pack(self._version)
                PackDecodeBase(new_index_data).validate_index()
            except (PackDecodeError, httpx.HTTPError) as e:
                # cannot be downloaded or fails the size + sha1 check,
                # the record stays in error, this is unsolvable
                logger.warning(f'Failed to download {self.INDEX_PACK}: {e}')
                failed.append(item)
            else:
                atomic_write(tmp, new_index_data)
                pending.append(PendingFile(info=info, tmp=tmp, current_mode=0o666))
            break
        # the other records, downloaded from the new full pack with
        # the offsets of the new index records
        if new_index_data is None:
            # the new index pack: the tmp file prepared in unpack(),
            # or the local index (already the new one when the index
            # record was skipped on a resumed run)
            if self._index_tmp:
                try:
                    new_index_data = atomic_read_bytes(self._index_tmp)
                except FileNotFoundError:
                    pass
        if new_index_data is None:
            try:
                new_index_data = atomic_read_bytes(env.PROJECT_ROOT.joinpath(self.INDEX_PACK))
            except FileNotFoundError:
                # the local index is missing and could not be
                # downloaded, the offsets are unavailable
                failed += [item for item in self.error if item.info.path != self.INDEX_PACK]
                self.pending += pending
                self.error = failed
                return
        new_index = PackDecodeBase(new_index_data)
        for item in self.error:
            if item.info.path == self.INDEX_PACK:
                continue
            info = item.info
            index = self._file_index[info.path]
            tmp = self.workspace.joinpath(f'{info.size}_{info.sha1}_{index}.tmp')
            if self._matches(info, self._read_current(tmp)).match:
                # a leftover tmp file passes the size + sha1 check, reuse it
                pending.append(PendingFile(info=info, tmp=tmp, current_mode=0o666))
                continue
            new_info = new_index.fileinfo.get(info.path)
            if new_info is None:
                # the new index does not record the file, this should
                # not happen
                failed.append(item)
                continue
            try:
                # data_start is an offset into the new full pack file,
                # range requests use it directly
                data = server.get_file_content(
                    new_index.version, new_info.data_start, new_info.data_size)
                content = new_index.decode_content(new_info, data)
            except (PackDecodeError, httpx.HTTPError) as e:
                # cannot be downloaded or fails the size + sha1 check,
                # the record stays in error, this is unsolvable
                logger.warning(f'Failed to download {info.path}: {e}')
                failed.append(item)
                continue
            atomic_write(tmp, content)
            pending.append(PendingFile(info=info, tmp=tmp, current_mode=0o666))
        self.pending += pending
        self.error = failed

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
            bytes: Working tree content of the file

        Raises:
            SourceError: If the source of the record fails the size +
                sha1 check
        """
        if info.edit == 0 and info.source_lookback:
            # C (copied), the content is the source blob with the own eol
            return PackDecodeBase.apply_eol(self._read_source_blob(decoder, info), info.eol)
        if info.edit == 3 and info.data_size == 0:
            # R (renamed), the content is the source blob with the own eol
            return PackDecodeBase.apply_eol(self._read_source_blob(decoder, info), info.eol)
        if info.algo == 2 and info.source_lookback:
            # zstd patch from the old blob, the source is the dictionary
            source = self._read_source_blob(decoder, info)
            return decoder.decode_content(info, decoder.catdata(info), source=source)
        # A (added), or M / RM with plain data
        return decoder.catfile(info)

    def _read_source_blob(self, decoder, info):
        """
        Read the LF blob of the source of a record, verified against
        the update pack.

        A C (copied) record may reference an earlier new file: its
        content is already written to the tmp file by the earlier
        record, it is read from there and verified against the
        record. The source of a C / M / RM / R record is otherwise an
        old file, read from the working tree and verified against the
        refinfo of the update pack (the pack is self-describing, no
        old index pack is needed): the size and sha1 of the LF blob
        must match, an EOL mismatch is converted. A file that fails
        the check raises SourceError, the caller fetches the record
        content from the server instead.

        Args:
            decoder (PackDecodeBase): Decoder of the update pack
            info (IdxInfo): Record whose source is read

        Returns:
            bytes: LF blob of the source file

        Raises:
            SourceError: If the source fails the size + sha1 check
        """
        source_path = info.source_path
        fileinfo = decoder.fileinfo
        if info.edit == 0 and source_path in fileinfo:
            # C (copied): the source is an earlier new file, its
            # content is in the tmp file written by the earlier record
            source_info = fileinfo[source_path]
            source_tmp = self.workspace.joinpath(
                f'{source_info.size}_{source_info.sha1}_{self._file_index[source_path]}.tmp')
            current = self._read_current(source_tmp)
            result = self._matches(source_info, current)
            if not result.match and not result.match_data:
                # the source record failed too, its tmp file is missing
                raise SourceError(source_path)
            data = result.match_data if result.match_data else current.data
            return self._to_blob(data, source_info.eol)
        # the source is an old file, verified against the refinfo
        ref = decoder.refinfo.get(source_path)
        if ref is None:
            raise SourceError(source_path)
        current = self._read_current(env.PROJECT_ROOT.joinpath(source_path))
        if not current.exist:
            raise SourceError(source_path)
        data = current.data
        # the record size and sha1 are of the LF blob, a CRLF working
        # tree file is converted before the check
        if len(data) == ref.size and (not ref.sha1 or sha1(data).hexdigest() == ref.sha1):
            return data
        converted = data.replace(b'\r\n', b'\n')
        if b'\r' in converted:
            # a lone CR, the EOL cannot be converted cleanly
            raise SourceError(source_path)
        if len(converted) != ref.size or (ref.sha1 and sha1(converted).hexdigest() != ref.sha1):
            raise SourceError(source_path)
        return converted

    @staticmethod
    def _to_blob(content, eol):
        """
        Convert working tree content back to the LF blob.

        Args:
            content (bytes | memoryview): Working tree file content
            eol (int): Line ending rule, 0 for LF, 1 for CRLF, 2 for
                binary

        Returns:
            bytes: Content in the LF blob form
        """
        content = bytes(content)
        if eol == 1:
            return content.replace(b'\r\n', b'\n')
        return content

    @staticmethod
    def _append_deleted(pending, path):
        """
        Schedule the deletion of a renamed source file.

        Args:
            pending (list[PendingFile]): Pending list to append to
            path (str): Path of the renamed source
        """
        pending.append(PendingFile(info=IdxInfo(path=path, edit=2), tmp='', current_mode=0))
