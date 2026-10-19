import threading

import pytest

from alasio.ext.concurrent.threadpool import ThreadPool
from alasio.ext.concurrent.threadpool_wrapper import BatchJobsWrapper, GatherJobsWrapper, WaitJobsWrapper


class TestWaitJobsWrapper:
    """Tests for WaitJobsWrapper."""

    def test_context_manager_waits_for_all(self):
        """wait_jobs waits for all jobs on context exit."""
        pool = ThreadPool(pool_size=4)
        results = []

        def track(v):
            results.append(v)
            return v

        with pool.wait_jobs() as w:
            w.start_thread_soon(track, 1)
            w.start_thread_soon(track, 2)
        assert sorted(results) == [1, 2]

    def test_get_method_clears_job_list(self):
        """wait_jobs.get() waits and clears the job list."""
        pool = ThreadPool(pool_size=4)
        w = WaitJobsWrapper(pool)
        w.start_thread_soon(lambda: "x")
        w.start_thread_soon(lambda: "y")
        w.get()
        assert len(w.jobs) == 0


class TestGatherJobsWrapper:
    """Tests for GatherJobsWrapper."""

    def test_context_manager_collects_results(self):
        """gather_jobs collects all results into .results."""
        pool = ThreadPool(pool_size=4)
        with pool.gather_jobs() as g:
            g.start_thread_soon(lambda: 10)
            g.start_thread_soon(lambda: 20)
            g.start_thread_soon(lambda: 30)
        assert sorted(g.results) == [10, 20, 30]

    def test_get_method_collects_results(self):
        """gather_jobs.get() collects results into .results."""
        pool = ThreadPool(pool_size=4)
        g = GatherJobsWrapper(pool)
        g.start_thread_soon(lambda: 100)
        g.start_thread_soon(lambda: 200)
        g.get()
        assert sorted(g.results) == [100, 200]
        assert len(g.jobs) == 0

    def test_clear_on_context_enter(self):
        """gather_jobs clears both .results and .jobs on __enter__."""
        pool = ThreadPool(pool_size=4)
        g = GatherJobsWrapper(pool)
        g.results.append("stale")
        with g:
            g.start_thread_soon(lambda: "fresh")
        assert g.results == ["fresh"]


class TestBatchJobsWrapper:
    """Tests for BatchJobsWrapper."""

    def test_default_batch(self):
        """The queue is sent when the default batch of 10 jobs is reached."""
        pool = ThreadPool(pool_size=10)
        started = threading.Semaphore(0)
        release = threading.Event()
        results = []

        def blocked(index):
            started.release()
            release.wait()
            results.append(index)

        with pool.batch_jobs() as batch:
            for index in range(9):
                batch.submit(blocked, index)
            # the queue is not sent before the batch is full
            assert not started.acquire(timeout=0.05), 'a queued job ran before the batch was full'
            batch.submit(blocked, 9)
            # the tenth job fills the batch and sends all ten jobs
            for _ in range(10):
                assert started.acquire(timeout=5), 'the full batch was not sent to the pool'
            release.set()

        assert sorted(results) == list(range(10))

    def test_size_counts_towards_batch(self):
        """The queue is sent by total size, not by the number of jobs."""
        pool = ThreadPool(pool_size=4)
        started = threading.Semaphore(0)
        release = threading.Event()
        results = []

        def blocked(index):
            started.release()
            release.wait()
            results.append(index)

        with pool.batch_jobs(batch=6) as batch:
            batch.submit(blocked, 0, size=2)
            batch.submit(blocked, 1, size=3)
            # total size is 5, below the batch of 6
            assert not started.acquire(timeout=0.05), 'a queued job ran before the batch was full'
            batch.submit(blocked, 2, size=1)
            # the third job fills the batch and sends all three jobs
            for _ in range(3):
                assert started.acquire(timeout=5), 'the batch was not sent when its size reached the batch'
            release.set()

        assert sorted(results) == [0, 1, 2]

    def test_flush(self):
        """flush sends the queued jobs before the batch is full."""
        pool = ThreadPool(pool_size=4)
        started = threading.Semaphore(0)
        release = threading.Event()
        results = []

        def blocked(index):
            started.release()
            release.wait()
            results.append(index)

        with pool.batch_jobs(batch=100) as batch:
            for index in range(3):
                batch.submit(blocked, index)
            assert not started.acquire(timeout=0.05), 'a queued job ran before flush'
            batch.flush()
            for _ in range(3):
                assert started.acquire(timeout=5), 'flush did not send the queued jobs'
            release.set()

        assert sorted(results) == [0, 1, 2]

    def test_exit_flushes_and_waits(self):
        """Leaving the context sends the queue and waits for every job."""
        pool = ThreadPool(pool_size=4)
        started = threading.Semaphore(0)
        release = threading.Event()
        results = []

        def blocked(index):
            started.release()
            release.wait()
            results.append(index)

        def run():
            with pool.batch_jobs(batch=100) as batch:
                for index in range(3):
                    batch.submit(blocked, index)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        # the queue is only sent by the exit of the context
        for _ in range(3):
            assert started.acquire(timeout=5), 'the exit did not send the queued jobs'
        # the jobs are still running, the exit has to wait for them
        assert thread.is_alive(), 'the exit returned before the jobs finished'
        release.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert sorted(results) == [0, 1, 2]

    def test_error_raises_on_exit(self):
        """An error raised by a batched job is raised when the context exits."""
        pool = ThreadPool(pool_size=4)

        def fail():
            raise RuntimeError('batch_error')

        with pytest.raises(RuntimeError, match='batch_error'):
            with pool.batch_jobs() as batch:
                batch.submit(fail)

    def test_get(self):
        """get sends the queued jobs and waits for them."""
        pool = ThreadPool(pool_size=4)
        results = []

        batch = BatchJobsWrapper(pool, batch=10)
        batch.submit(results.append, 1)
        batch.submit(results.append, 2)
        batch.get()
        assert sorted(results) == [1, 2]
        assert batch.jobs == []

    @pytest.mark.parametrize('batch_size', [0, -1])
    def test_invalid_batch(self, batch_size):
        """A batch size below 1 is rejected."""
        pool = ThreadPool(pool_size=4)
        with pytest.raises(ValueError):
            pool.batch_jobs(batch_size)

    @pytest.mark.parametrize('size', [0, -1])
    def test_invalid_size(self, size):
        """A job size below 1 is rejected."""
        pool = ThreadPool(pool_size=4)
        batch = BatchJobsWrapper(pool)
        with pytest.raises(ValueError):
            batch.submit(lambda: None, size=size)
