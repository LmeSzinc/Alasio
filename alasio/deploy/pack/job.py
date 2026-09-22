import httpx2

from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy.pack.job_base import JobBase
from alasio.deploy.pack.job_rebuild import RebuildJob
from alasio.deploy.pack.job_reset import ResetJob
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy.pack.job_update import UpdateJob
from alasio.ext import env
from alasio.ext.path.atomic import atomic_read_bytes, atomic_rmtree
from alasio.logger import logger


class DeployJob:
    """
    Unified entry of deploy jobs.

        DeployJob.unpack(data)
        DeployJob.update(server)

    The unfinished job is finished inside unpack() and update(), the
    caller does not need to care about it.
    """

    @classmethod
    def get_unfinished_job(cls, server=None):
        """
        Check if there is an unfinished job, read it and create the
        job object of the corresponding type.

        The job type is decided by the job file content: the REST
        marker is a validation job (ResetJob), the RBIL marker is a
        rebuild job (RebuildJob), a pack with refinfo is an update
        pack (UpdateJob), a pack without it is a full pack
        (UnpackJob). A corrupted job file is cleaned up with a
        warning.

        Args:
            server (ServerFile, optional): Server to download the
                missing files for a resumed validation job

        Returns:
            JobBase: The unfinished job, or None if there is no
                unfinished job
        """
        workspace = env.PROJECT_ROOT.joinpath(JobBase.WORKSPACE)
        try:
            data = atomic_read_bytes(env.PROJECT_ROOT.joinpath(JobBase.JOB_FILE))
        except FileNotFoundError:
            return None
        if data == ResetJob.MARK:
            # a validation job, its data comes from the local index pack
            return ResetJob(server, resume=True)
        if data == RebuildJob.MARK:
            # a rebuild job, its data comes from the local index pack
            return RebuildJob(server, resume=True)
        try:
            decoder = PackDecodeBase(data)
        except PackDecodeError as e:
            # the job file is corrupted, clean it up
            logger.warning(f'Failed to read the unfinished job: {e}')
            atomic_rmtree(workspace)
            return None
        try:
            is_update = bool(decoder.refinfo)
        except PackDecodeError:
            # the index data is malformed, the unpack job fails on
            # validation and cleans up
            is_update = False
        if is_update:
            # an update pack, resume the update job
            return UpdateJob(data, server=server, resume=True)
        return UnpackJob(data, resume=True)

    @classmethod
    def unpack(cls, data):
        """
        Unpack a full pack, unified wrapper of UnpackJob.

        Unpacking is a local rebuild: the leftover files of the old
        version (recorded in the old local index pack, not in this
        pack) are removed, the new index pack replaces .pack/index.pack
        last. No server is involved. Finishes the unfinished job first,
        then unpacks the new data, the caller only needs to call run()
        of each job.

        Args:
            data (bytes): Full pack data
        """
        # finish the unfinished job first, its run() skips write()
        job = cls.get_unfinished_job()
        if job is not None:
            logger.info(f'Found unfinished job: {job.__class__}')
            job.run()
        # unpack the new data
        UnpackJob(data).run()

    @classmethod
    def _local_version(cls):
        """
        The version of the local index pack .pack/index.pack.

        Returns:
            str: The version of the local index pack, '' when it is
                missing or malformed
        """
        try:
            decoder = PackDecodeBase(
                atomic_read_bytes(env.PROJECT_ROOT.joinpath(JobBase.INDEX_PACK)))
        except (FileNotFoundError, PackDecodeError):
            return ''
        return decoder.version

    @classmethod
    def update(cls, server):
        """
        Check the latest version on the server and update the local
        working tree to it.

        The unified entry of the file check flow in the draft of
        PackEncodeBase:

        1. the latest version and its index pack checksum are read
           from latest.pack, the local version comes from the local
           index pack .pack/index.pack
        2. a version mismatch downloads the update pack
           /{new_version}/from_{old_version}.pack and applies it with
           UpdateJob, a missing update pack (out of the update window
           or removed) falls back to RebuildJob, a failed update also
           falls back to RebuildJob
        3. the same version continues with ResetJob: the local index
           pack is checked against the latest checksum (an outdated
           self-consistent index is downloaded again), then every
           recorded file is verified and repaired

        A missing or malformed local index pack has an unknown
        version, the update cannot be incremental: RebuildJob
        downloads the latest index unconditionally and rebuilds the
        working tree from it. The unfinished job is finished inside,
        the caller does not need to care about it.

        Args:
            server (ServerFile): Server to check and download from

        Returns:
            bool: True if every file is up to date, False if some
                records stay in error
        """
        # finish the unfinished job first, its run() skips write()
        job = cls.get_unfinished_job(server)
        if job is not None:
            logger.info(f'Found unfinished job: {job.__class__}')
            job.run()

        local = cls._local_version()
        logger.attr('CurrentVersion', local)
        info = server.get_latest_info()
        logger.attr('LatestVersion', info.version)

        if not local:
            # the local index is missing or malformed, the version is
            # unknown: rebuild from the latest index
            logger.warning('Failed to read the local version, rebuilding from the latest index')
            job = RebuildJob(server)
            return job.run()
        if local != info.version:
            # a version mismatch, apply the update pack incrementally
            try:
                data = server.get_update_pack(local, info.version)
            except httpx2.HTTPStatusError as e:
                # the update pack of the local version is not on the
                # server (out of the update window or removed), the
                # incremental path is broken: rebuild from the latest index
                logger.warning(
                    f'Failed to get the update pack {local} -> {info.version}: {e}, '
                    f'rebuilding from the latest index'
                )
                job = RebuildJob(server)
                return job.run()
            job = UpdateJob(data, server=server)
            if job.run():
                return True
            # the update pack failed to apply, rebuild from the latest
            # index: a corrupt pack is bypassed, the latest index and
            # the files are downloaded directly
            logger.warning('Failed to apply the update pack, rebuilding from the latest index')
            job = RebuildJob(server)
            return job.run()
        # the same version, check the index and the files
        job = ResetJob(server)
        return job.run()
