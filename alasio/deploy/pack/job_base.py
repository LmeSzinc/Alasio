from alasio.ext import env


class JobBase:
    """
    Base class of deploy jobs.

    The pack data is passed in __init__, the workspace is the folder
    where the job stores its temporary files, both are relative to
    env.PROJECT_ROOT.
    """

    # pack structure, relative to the app root folder (env.PROJECT_ROOT)
    INDEX_PACK = '.pack/index.pack'
    WORKSPACE = '.pack/workspace'
    JOB_FILE = '.pack/workspace/job.pack'

    def __init__(self, data):
        """
        Args:
            data (bytes): Pack data
        """
        self._data = data
        self.workspace = env.PROJECT_ROOT.joinpath(self.WORKSPACE)

    def run(self):
        """
        Execute the job, each subclass implements its own run().

        Raises:
            NotImplementedError: Subclasses must implement run()
        """
        raise NotImplementedError
