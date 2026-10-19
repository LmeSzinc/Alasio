from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alasio.ext.concurrent.threadpool import Job, ResultT, ThreadPool


class WaitJobsWrapper:
    """
    Wrapper class to wait all jobs
    """

    def __init__(self, pool: "ThreadPool"):
        self.pool: "ThreadPool" = pool
        self.jobs: "list[Job[ResultT]]" = []

    def get(self):
        for job in self.jobs:
            job.get()
        self.jobs.clear()

    def __enter__(self):
        self.jobs.clear()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.get()

    def start_thread_soon(self, func, *args, **kwargs):
        """
        Run a function on thread,
        result can be got from ``job`` object

        Args:
            func (Callable[..., ResultT]):
            *args:
            **kwargs:

        Returns:
            Job[ResultT]:
        """
        job = self.pool.start_thread_soon(func, *args, **kwargs)
        self.jobs.append(job)
        return job


class GatherJobsWrapper(WaitJobsWrapper):
    """
    Wrapper class to gather all jobs
    """

    def __init__(self, pool: "ThreadPool"):
        super().__init__(pool)
        self.results: "list[ResultT]" = []

    def get(self):
        for job in self.jobs:
            result = job.get()
            self.results.append(result)
        self.jobs.clear()

    def __enter__(self):
        self.jobs.clear()
        self.results.clear()
        return self
