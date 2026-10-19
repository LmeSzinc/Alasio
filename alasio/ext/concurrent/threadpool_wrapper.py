from typing import TYPE_CHECKING, Callable

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


class BatchJobsWrapper(WaitJobsWrapper):
    """
    Wrapper class to submit jobs in batches

    The jobs submitted to this wrapper do not run immediately. They queue
    until the total size of the queued jobs reaches ``batch``, then the queue
    is sent to the thread pool at once. ``flush()`` sends the queue at any
    time, and leaving the ``with`` block sends the queue and waits for every
    job of the wrapper.

    Examples:
        with THREAD_POOL.batch_jobs(batch=10) as batch:
            batch.submit(func, *args, size=1)
    """

    def __init__(self, pool: "ThreadPool", batch: int = 10):
        super().__init__(pool)
        if batch < 1:
            raise ValueError(f'batch must be at least 1, got {batch}')
        self.batch = batch
        self.total_size = 0
        self.pending: "list[tuple[Callable, tuple, dict]]" = []

    def submit(self, func, *args, size=1, **kwargs):
        """
        Queue a job to run with the next batch

        Args:
            func (Callable[..., ResultT]): Function to run
            *args: Arguments of the function
            size (int): Size the job adds to the queued batch, the queue is
                sent to the pool when the total size reaches ``batch``.
                Defaults to 1.
            **kwargs: Keyword arguments of the function

        Note:
            ``size`` is consumed by the wrapper and is not passed to ``func``.
        """
        if size < 1:
            raise ValueError(f'size must be at least 1, got {size}')
        self.pending.append((func, args, kwargs))
        self.total_size += size
        if self.total_size >= self.batch:
            self.flush()

    def flush(self):
        """
        Send the queued jobs to the thread pool now
        """
        if not self.pending:
            return
        pending = self.pending
        self.pending = []
        self.total_size = 0
        self.jobs.extend(
            self.pool.start_thread_soon(func, *args, **kwargs) for func, args, kwargs in pending
        )

    def get(self):
        """
        Send the queued jobs and wait for every job of the wrapper
        """
        self.flush()
        super().get()

    def __enter__(self):
        super().__enter__()
        self.pending.clear()
        self.total_size = 0
        return self
