from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy.pack.job_base import JobBase
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

    The unfinished job is finished inside unpack(), the caller does not
    need to care about it.
    """

    @staticmethod
    def get_unfinished_job(server=None):
        """
        Check if there is an unfinished job, read it and create the
        job object of the corresponding type.

        The job type is decided by the job file content: the REST
        marker is a validation job (ResetJob), a pack with an index
        update part is an update pack (UpdateJob), a pack without it
        is a full pack (UnpackJob). A corrupted job file is cleaned up
        with a warning.

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
        try:
            decoder = PackDecodeBase(data)
        except PackDecodeError as e:
            # the job file is corrupted, clean it up
            logger.warning(f'Failed to read the unfinished job: {e}')
            atomic_rmtree(workspace)
            return None
        if decoder._index_update:
            # an update pack, resume the update job
            return UpdateJob(data, server=server, resume=True)
        return UnpackJob(data, resume=True)

    @staticmethod
    def unpack(data):
        """
        Unpack a full pack, unified wrapper of UnpackJob.

        Finishes the unfinished job first, then unpacks the new data,
        the caller only needs to call run() of each job.

        Args:
            data (bytes): Full pack data
        """
        # finish the unfinished job first, its run() skips write()
        job = DeployJob.get_unfinished_job()
        if job is not None:
            job.run()
        # unpack the new data
        UnpackJob(data).run()
