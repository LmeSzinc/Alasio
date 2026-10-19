from alasio.ext.concurrent.threadpool import ThreadPool
from alasio.ext.concurrent.threadpool_wrapper import GatherJobsWrapper, WaitJobsWrapper


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
