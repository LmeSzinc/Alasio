import itertools
import queue
from threading import Lock, Thread
from typing import Callable, TypeVar

from typing_extensions import ParamSpec

from alasio.ext.concurrent.threadpool import Error, Job, WorkerThread, _JobKill, remove_tb_frames

ParamP = ParamSpec("ParamP")
ResultT = TypeVar("ResultT")


class PriorityWorkerThread(WorkerThread):
    def __init__(self, thread_pool, index):
        """
        Args:
            thread_pool (PriorityThreadPool):
            index (int): Thread index, starting from 0
        """
        self.job: "Job | None" = None
        self.thread_pool = thread_pool

        self.default_name = f"Alasio priority thread {index}"

        self.thread = Thread(target=self._work, name=self.default_name, daemon=True)

    def _handle_job(self, job: Job) -> None:
        # Capture func result
        try:
            result = job.func(*job.args, **job.kwargs)
        except BaseException as exc:
            exc = remove_tb_frames(exc, 1)
            result = Error(exc)

            # Check if job killed, must before marking self idle
            if type(result.error) is _JobKill:
                return

        # Job finished, putin result and notify
        # logger.info('deliver job')
        with job.put_lock:
            job.result = result
            del job.worker
            job.notify_get.release()

    def _work(self):
        pool = self.thread_pool
        while True:
            # get job directly
            try:
                _, _, job = pool.task_queue.get(block=False)
            except queue.Empty:
                pass
            else:
                pool.idle_workers.pop(self, None)
                job.worker = self
                self._handle_job(job)
                continue
            # mark self as idle, wait new job
            pool.idle_workers[self] = None
            try:
                _, _, job = pool.task_queue.get(timeout=pool.IDLE_TIMEOUT)
            except queue.Empty:
                pass
            else:
                pool.idle_workers.pop(self, None)
                job.worker = self
                self._handle_job(job)
                continue
            # Timeout getting job, so we can probably exit. But,
            # there's a race condition: we might be assigned a job *just*
            # as we're about to exit. So we have to check.
            with pool.create_lock:
                pool.idle_workers.pop(self, None)
                if pool.task_queue.empty():
                    pool.all_workers.pop(self, None)
                    return
                else:
                    # just got a job
                    continue


class PriorityThreadPool:
    """
    A thread pool with priority
    """

    # Thread exits after 10s idling.
    IDLE_TIMEOUT = 10

    def __init__(self, pool_size: int = 8):
        # Pool has 8 threads at max.
        # Alasio is for local low-frequency access so default pool size is small
        self.pool_size = pool_size
        self.counter = itertools.count()
        self.task_queue = queue.PriorityQueue()

        self.all_workers: "dict[PriorityWorkerThread, None]" = {}
        self.idle_workers: "dict[PriorityWorkerThread, None]" = {}

        self.create_lock = Lock()

    def _ensure_worker(self):
        if self.idle_workers:
            return
        count = len(self.all_workers)
        if count >= self.pool_size:
            return

        with self.create_lock:
            # double lock check
            if self.idle_workers:
                return
            count = len(self.all_workers)
            if count >= self.pool_size:
                return

            # Create thread
            worker = PriorityWorkerThread(self, count)
            self.all_workers[worker] = None
            worker.thread.start()
            # logger.info(f'New worker thread: {worker.default_name}')

    def enqueue(
            self, func: Callable[[ParamP], ResultT], priority, *args: ParamP.args, **kwargs: ParamP.kwargs
    ) -> Job[ResultT]:
        """
        Args:
            func:
            priority: Lower priority to run first
            *args:
            **kwargs:
        """
        job = Job(None, func, args, kwargs)
        # sort by priority and count to be FIFO
        self.task_queue.put((priority, next(self.counter), job))
        self._ensure_worker()
        return job

    def wait_jobs(self):
        """
        Auto wait all jobs finished

        Returns:
            PriorityWaitJobsWrapper:
        """
        return PriorityWaitJobsWrapper(self)

    def gather_jobs(self):
        """
        Auto wait all jobs finished and gather results

        Returns:
            PriorityGatherJobsWrapper:
        """
        return PriorityGatherJobsWrapper(self)


class PriorityWaitJobsWrapper:
    """
    Wrapper class to wait all jobs
    """

    def __init__(self, pool):
        """
        Args:
            pool (PriorityThreadPool):
        """
        self.pool = pool
        self.jobs = []

    def get(self):
        for job in self.jobs:
            job.get()
        self.jobs.clear()

    def __enter__(self):
        self.jobs.clear()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.get()

    def enqueue(self, func, priority, *args, **kwargs):
        """
        Run a function on thread,
        result can be got from `job` object

        Args:
            func (Callable):
            priority (Any):
            *args:
            **kwargs:

        Returns:
            Job:
        """
        job = self.pool.enqueue(func, priority, *args, **kwargs)
        self.jobs.append(job)
        return job


class PriorityGatherJobsWrapper(PriorityWaitJobsWrapper):
    """
    Wrapper class to gather all jobs
    """

    def __init__(self, pool):
        """
        Args:
            pool (PriorityThreadPool):
        """
        super().__init__(pool)
        self.results = []

    def get(self):
        for job in self.jobs:
            result = job.get()
            self.results.append(result)
        self.jobs.clear()

    def __enter__(self):
        self.jobs.clear()
        self.results.clear()
        return self
