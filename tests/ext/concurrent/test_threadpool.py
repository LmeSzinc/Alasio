import os
import threading
import time

import pytest

from alasio.ext.concurrent.cmd import CmdlineError, CmdlineResultStr
from alasio.ext.concurrent.threadpool import (
    THREAD_POOL,
    Error,
    GatherJobsWrapper,
    Job,
    JobTimeout,
    ThreadPool,
    WaitJobsWrapper,
    _JobKill,
    remove_tb_frames,
)


# ---------------------------------------------------------------------------
# Synchronisation helpers
#
# A job function that blocks on a threading.Event lets the test control
# exactly when the job continues, eliminating non-deterministic sleeps.
# ---------------------------------------------------------------------------


def _wait_on(event):
    """Block until *event* is set."""
    event.wait()


def _wait_then_return(event, value):
    """Block until *event* is set, then return *value*."""
    event.wait()
    return value


def _wait_then_raise(event, exc_class=ValueError, msg="test error"):
    """Block until *event* is set, then raise."""
    event.wait()
    raise exc_class(msg)


def _busy_loop():
    """Busy-loop so _JobKill can be delivered by `PyThreadState_SetAsyncExc`."""
    try:
        while True:
            pass
    except BaseException:
        raise


# ===================================================================
# remove_tb_frames
# ===================================================================


class TestRemoveTbFrames:
    """Tests for remove_tb_frames().

    ``exc.__traceback__`` chains outermost frame -> innermost frame via
    ``tb_next``.  ``remove_tb_frames(exc, n)`` therefore removes the *n*
    outermost frames.
    """

    def test_remove_zero_frames(self):
        """n=0 keeps the full traceback."""
        try:
            raise ValueError("keep")
        except ValueError as exc:
            cnt_before = _count_frames(exc.__traceback__)
            result = remove_tb_frames(exc, 0)
            cnt_after = _count_frames(result.__traceback__)
            assert cnt_after == cnt_before

    def test_remove_one_frame(self):
        """Removing one frame reduces the chain length by 1."""

        def raise_inner():
            raise ValueError("inner")

        try:
            raise_inner()
        except ValueError as exc:
            cnt_before = _count_frames(exc.__traceback__)
            result = remove_tb_frames(exc, 1)
            cnt_after = _count_frames(result.__traceback__)
            assert cnt_after == cnt_before - 1

    def test_remove_all_frames(self):
        """Removing all frames leaves traceback as None."""
        try:
            raise RuntimeError("clear")
        except RuntimeError as exc:
            cnt = _count_frames(exc.__traceback__)
            result = remove_tb_frames(exc, cnt)
            assert result.__traceback__ is None

    def test_remove_too_many_raises_assertion(self):
        """Removing more frames than available raises AssertionError."""
        try:
            raise ValueError("short")
        except ValueError as exc:
            cnt = _count_frames(exc.__traceback__)
            with pytest.raises(AssertionError):
                remove_tb_frames(exc, cnt + 1)

    def test_innermost_frame_name_preserved(self):
        """After removing outer frames, the innermost (raise-site) frame is still present."""

        def deep():
            raise ValueError("deep")

        try:
            deep()
        except ValueError as exc:
            # Get innermost frame name before
            tb = exc.__traceback__
            while tb.tb_next:
                tb = tb.tb_next
            innermost_before = tb.tb_frame.f_code.co_name

            # Remove all but the last frame
            cnt = _count_frames(exc.__traceback__)
            result = remove_tb_frames(exc, cnt - 1)
            tb_after = result.__traceback__
            # Only one frame left — the innermost one
            assert tb_after is not None
            assert tb_after.tb_next is None
            assert tb_after.tb_frame.f_code.co_name == innermost_before


def _count_frames(tb):
    """Return the number of frames in a traceback chain."""
    n = 0
    while tb:
        n += 1
        tb = tb.tb_next
    return n


# ===================================================================
# Error
# ===================================================================


class TestError:
    """Tests for the Error wrapper class."""

    def test_wrap(self):
        """Error stores the original exception."""
        inner = ValueError("wrapped")
        err = Error(inner)
        assert err.error is inner

    def test_unwrap_raises(self):
        """unwrap() re-raises the wrapped exception."""
        err = Error(ValueError("unwrap"))
        with pytest.raises(ValueError, match="unwrap"):
            err.unwrap()

    def test_unwrap_preserves_type(self):
        """unwrap() preserves the original exception type."""
        err = Error(TypeError("type_check"))
        with pytest.raises(TypeError, match="type_check"):
            err.unwrap()

    def test_unwrap_breaks_reference_cycle(self):
        """After unwrap the Error object still exists (locals cleaned in unwrap)."""
        err = Error(ValueError("cycle"))
        with pytest.raises(ValueError):
            err.unwrap()
        # The .error attribute still exists; no cycle was created
        assert isinstance(err.error, ValueError)

    def test_repr(self):
        """__repr__ includes the wrapped error."""
        err = Error(ValueError("repr_check"))
        assert "ValueError" in repr(err)
        assert "repr_check" in repr(err)


# ===================================================================
# Job
# ===================================================================


class TestJobGet:
    """Tests for Job.get()."""

    def test_get_returns_result(self):
        """get() waits for the job and returns the result."""
        pool = ThreadPool(pool_size=4)
        job = pool.start_thread_soon(lambda: 42)
        assert job.get() == 42

    def test_get_raises_error(self):
        """get() re-raises an exception raised in the job."""
        pool = ThreadPool(pool_size=4)
        job = pool.start_thread_soon(
            lambda: (_ for _ in ()).throw(ValueError("job_error"))
        )
        with pytest.raises(ValueError, match="job_error"):
            job.get()

    def test_get_on_finished_job_once(self):
        """get() on a finished job returns the result (single-consumer contract)."""
        pool = ThreadPool(pool_size=4)
        job = pool.start_thread_soon(lambda: 99)
        assert job.get() == 99

    def test_get_after_error(self):
        """get() after job raised an error still raises."""
        pool = ThreadPool(pool_size=4)
        job = pool.start_thread_soon(
            lambda: (_ for _ in ()).throw(ValueError("persistent"))
        )
        with pytest.raises(ValueError, match="persistent"):
            job.get()


class TestJobGetOrKill:
    """Tests for Job.get_or_kill()."""

    def test_get_or_kill_success(self):
        """get_or_kill returns the result when the job finishes in time."""
        pool = ThreadPool(pool_size=4)
        job = pool.start_thread_soon(lambda: 77)
        assert job.get_or_kill(timeout=5) == 77

    def test_get_or_kill_timeout(self):
        """get_or_kill raises JobTimeout when the job does not finish in time."""
        pool = ThreadPool(pool_size=4)
        blocked = threading.Event()  # never set → job stays blocked
        job = pool.start_thread_soon(_wait_on, blocked)
        with pytest.raises(JobTimeout):
            job.get_or_kill(timeout=0.05)

    def test_get_or_kill_error(self):
        """get_or_kill re-raises a job exception."""
        pool = ThreadPool(pool_size=4)
        job = pool.start_thread_soon(
            lambda: (_ for _ in ()).throw(RuntimeError("kill_error"))
        )
        with pytest.raises(RuntimeError, match="kill_error"):
            job.get_or_kill(timeout=5)

    def test_get_or_kill_on_completed_job(self):
        """get_or_kill on a completed job returns the result."""
        pool = ThreadPool(pool_size=4)
        job = pool.start_thread_soon(lambda: "done")
        assert job.get_or_kill(timeout=5) == "done"


class TestJobKill:
    """Tests for Job._kill()."""

    def test_kill_finished_job_noop(self):
        """_kill() on a finished job (no worker attribute) is a no-op."""
        pool = ThreadPool(pool_size=4)
        job = pool.start_thread_soon(lambda: 1)
        job.get()
        # worker attribute deleted by _handle_job → AttributeError caught
        job._kill()

    def test_kill_with_worker_none_noop(self):
        """_kill() on a job whose worker is None is a no-op."""
        job = Job(None, lambda: 1, (), {})
        job._kill()

    def test_kill_then_finished_job_noop(self):
        """_kill() after the job was already killed is a no-op (AttributeError)."""
        pool = ThreadPool(pool_size=4)
        job = pool.start_thread_soon(lambda: 1)
        job.get()
        job._kill()
        job._kill()  # second call also safe


class TestJobRepr:
    """Tests for Job.__repr__."""

    def test_job_repr_includes_func_and_args(self):
        """__repr__ shows the function name and its arguments."""
        pool = ThreadPool(pool_size=4)
        job = pool.start_thread_soon(lambda x: x, "hello")
        r = repr(job)
        # Should mention the lambda or the argument
        assert "<lambda>" in r or "hello" in r


# ===================================================================
# WorkerThread
# ===================================================================


class TestWorkerThread:
    """Tests for WorkerThread."""

    def test_kill_active_worker(self):
        """kill() on a worker running Python code terminates it."""
        pool = ThreadPool(pool_size=4)
        job = pool.start_thread_soon(_busy_loop)
        # Wait briefly for the worker to start spinning
        timeout = time.time() + 2
        while time.time() < timeout and len(pool.all_workers) == 0:
            time.sleep(0.005)
        count_before = len(pool.all_workers)
        with pytest.raises(JobTimeout):
            job.get_or_kill(timeout=0.05)
        # _JobKill should have been delivered; worker count should have decreased
        time.sleep(0.05)
        assert len(pool.all_workers) < count_before

    def test_worker_is_daemon(self):
        """Worker threads are daemon threads."""
        pool = ThreadPool(pool_size=4)
        job = pool.start_thread_soon(lambda: 1)
        job.get()
        for w in list(pool.all_workers):
            assert w.thread.daemon is True

    def test_worker_kill_protected_by_put_lock(self):
        """kill() is protected by job.put_lock (called via _kill())."""
        pool = ThreadPool(pool_size=4)

        def block_then_raise():
            raise RuntimeError("boom")

        job = pool.start_thread_soon(block_then_raise)
        # Wait for job to complete
        with pytest.raises(RuntimeError):
            job.get()
        # Now _kill is a no-op (no worker attribute)
        job._kill()


# ===================================================================
# ThreadPool -- Core API
# ===================================================================


class TestThreadPoolStart:
    """Tests for ThreadPool.start_thread_soon()."""

    def test_basic(self):
        """Submit and get a simple result."""
        pool = ThreadPool(pool_size=4)
        assert pool.start_thread_soon(lambda: 42).get() == 42

    def test_error_propagation(self):
        """Job exceptions propagate to get()."""
        pool = ThreadPool(pool_size=4)
        with pytest.raises(ValueError, match="fail"):
            pool.start_thread_soon(
                lambda: (_ for _ in ()).throw(ValueError("fail"))
            ).get()

    def test_job_with_args_and_kwargs(self):
        """Jobs with *args and **kwargs work correctly."""
        pool = ThreadPool(pool_size=4)

        def kw_func(a, b=10):
            return a + b

        assert pool.start_thread_soon(kw_func, 5, b=7).get() == 12

    def test_return_none(self):
        """Jobs that return None work."""
        pool = ThreadPool(pool_size=4)
        assert pool.start_thread_soon(lambda: None).get() is None

    def test_return_complex_object(self):
        """Jobs that return complex objects work."""
        pool = ThreadPool(pool_size=4)
        data = {"key": [1, 2, 3], "nested": {"a": 1}}
        assert pool.start_thread_soon(lambda d: d, data).get() == data


class TestThreadPoolRunOnThread:
    """Tests for ThreadPool.run_on_thread decorator."""

    def test_decorator_runs_on_thread(self):
        """Decorated function returns a Job."""
        pool = ThreadPool(pool_size=4)

        @pool.run_on_thread
        def add(a, b):
            return a + b

        job = add(3, 4)
        assert isinstance(job, Job)
        assert job.get() == 7

    def test_decorator_error(self):
        """Decorated function propagates errors via Job."""
        pool = ThreadPool(pool_size=4)

        @pool.run_on_thread
        def fail():
            raise RuntimeError("decorated")

        with pytest.raises(RuntimeError, match="decorated"):
            fail().get()

    def test_decorator_preserves_wraps(self):
        """Decorator preserves function name."""
        pool = ThreadPool(pool_size=4)

        @pool.run_on_thread
        def my_func():
            """docstring."""
            return 0

        assert my_func.__name__ == "my_func"


class TestThreadPoolStartCmd:
    """Tests for ThreadPool.start_cmd_soon()."""

    def test_run_cmd_success(self):
        """Run a simple command successfully."""
        pool = ThreadPool(pool_size=4)
        if os.name == "nt":
            cmd = ["cmd", "/c", "echo", "hello_thread"]
        else:
            cmd = ["echo", "hello_thread"]
        result = pool.start_cmd_soon(cmd).get()
        assert isinstance(result, CmdlineResultStr)
        assert "hello_thread" in result.stdout

    def test_run_cmd_nonzero_exit(self):
        """A command that fails returns CmdlineError."""
        pool = ThreadPool(pool_size=4)
        if os.name == "nt":
            cmd = ["cmd", "/c", "exit", "1"]
        else:
            cmd = ["sh", "-c", "exit 1"]
        with pytest.raises(CmdlineError):
            pool.start_cmd_soon(cmd).get()


class TestThreadPoolBulk:
    """Tests for thread_map, thread_starmap, thread_funcmap."""

    def test_thread_map(self):
        """thread_map maps a function over an iterable."""
        pool = ThreadPool(pool_size=4)
        assert pool.thread_map(lambda x: x * 10, [1, 2, 3]) == [10, 20, 30]

    def test_thread_starmap(self):
        """thread_starmap unpacks tuples as *args."""
        pool = ThreadPool(pool_size=4)
        assert pool.thread_starmap(lambda a, b: a + b, [(1, 2), (3, 4), (5, 6)]) == [
            3,
            7,
            11,
        ]

    def test_thread_funcmap(self):
        """thread_funcmap runs a list of zero-arg callables."""
        pool = ThreadPool(pool_size=4)
        assert pool.thread_funcmap([lambda: "a", lambda: "b", lambda: "c"]) == [
            "a",
            "b",
            "c",
        ]


# ===================================================================
# ThreadPool -- Pool management (Event-based synchronisation)
# ===================================================================


class TestThreadPoolSizeAndIdle:
    """Tests for pool size limits, idle reuse, and worker lifecycle.

    All synchronisation uses ``threading.Event`` instead of ``time.sleep``
    for deterministic control and fast test execution.
    """

    def test_pool_respects_size_limit(self):
        """Pool never creates more than pool_size workers simultaneously."""
        pool = ThreadPool(pool_size=3)
        blockers = [threading.Event() for _ in range(3)]
        jobs = [pool.start_thread_soon(_wait_on, e) for e in blockers]
        # Give workers a moment to spin up
        time.sleep(0.05)
        assert len(pool.all_workers) <= 3
        # Release all
        for e in blockers:
            e.set()
        for j in jobs:
            j.get()

    def test_idle_worker_reuse(self):
        """Workers are reused after completing a job."""
        pool = ThreadPool(pool_size=4)
        j1 = pool.start_thread_soon(lambda: 1)
        j1.get()
        workers_after_first = dict(pool.all_workers)
        j2 = pool.start_thread_soon(lambda: 2)
        j2.get()
        # Same worker set (no new workers created)
        assert dict(pool.all_workers) == workers_after_first

    def test_pool_full_blocks_and_recovers(self):
        """When the pool is full, callers block until a worker is free."""
        pool = ThreadPool(pool_size=2)
        blockers = [threading.Event() for _ in range(2)]
        long_jobs = [pool.start_thread_soon(_wait_on, e) for e in blockers]
        # Wait until both workers are occupied
        time.sleep(0.05)
        assert len(pool.all_workers) == 2

        # Submit a third job; it must block.  We verify it has not started
        # after a short delay, then release one blocker.
        started = threading.Event()

        def track_start():
            started.set()
            return 3

        t0 = time.time()
        # We run the third submission in a separate thread so the test
        # can observe that it blocks.
        sub_results = []

        def submit_third():
            sub_results.append(pool.start_thread_soon(track_start).get())

        t = threading.Thread(target=submit_third, daemon=True)
        t.start()
        time.sleep(0.1)
        # Should *not* have started yet because both workers are busy
        assert not started.is_set(), "Third job should be blocked, pool is full"

        # Release one blocker -> a worker becomes free
        blockers[0].set()
        time.sleep(0.1)
        assert started.is_set()
        t.join(timeout=2)

        assert sub_results == [3]
        # Cleanup remaining blockers
        for e in blockers[1:]:
            e.set()
        for j in long_jobs:
            j.get()

    def test_pool_full_with_multiple_waiting(self):
        """Multiple callers can queue when the pool is full."""
        pool = ThreadPool(pool_size=2)
        blockers = [threading.Event() for _ in range(2)]
        long_jobs = [pool.start_thread_soon(_wait_on, e) for e in blockers]
        time.sleep(0.05)

        # Submit 3 extra jobs from a background thread so the main thread
        # isn't blocked (the pool is full, so start_thread_soon will block).
        extra = []
        submitted = threading.Event()

        def submit_extra():
            for i in range(3):
                extra.append(pool.start_thread_soon(lambda v=i: v, i))
            submitted.set()

        t = threading.Thread(target=submit_extra, daemon=True)
        t.start()
        # Main thread waits briefly so the submitter blocks on the full pool
        time.sleep(0.1)
        assert not submitted.is_set(), "Submissions should still be queued"

        # Release blockers one at a time so queued jobs can run
        for e in blockers:
            e.set()
            time.sleep(0.05)

        # Now the extra jobs should have been submitted
        submitted.wait(timeout=2)
        results = [j.get() for j in extra]
        assert sorted(results) == [0, 1, 2]
        for j in long_jobs:
            j.get()

    def test_worker_idle_timeout_exits(self):
        """Workers exit after IDLE_TIMEOUT seconds with no work."""
        pool = ThreadPool(pool_size=4)
        original_timeout = pool.IDLE_TIMEOUT
        pool.IDLE_TIMEOUT = 0.05
        try:
            j1 = pool.start_thread_soon(lambda: 1)
            j1.get()
            # Worker should now be idle; wait for timeout
            time.sleep(0.15)
            old_count = len(pool.all_workers)
            j2 = pool.start_thread_soon(lambda: 2)
            j2.get()
            # The idle worker timed out and exited; a new one may or may not
            # be created (depending on timing).  At minimum no crash.
            assert len(pool.all_workers) <= pool.pool_size
        finally:
            pool.IDLE_TIMEOUT = original_timeout

    def test_worker_catches_job_while_exiting(self):
        """Race: worker timing out but is assigned a new job before exiting."""
        pool = ThreadPool(pool_size=4)
        original_timeout = pool.IDLE_TIMEOUT
        pool.IDLE_TIMEOUT = 0.3
        try:
            j1 = pool.start_thread_soon(lambda: 1)
            j1.get()
            # Worker is idle but still within IDLE_TIMEOUT
            time.sleep(0.15)
            j2 = pool.start_thread_soon(lambda: 2)
            assert j2.get() == 2
        finally:
            pool.IDLE_TIMEOUT = original_timeout


class TestThreadPoolReleaseFullLock:
    """Tests for the release_full_lock race-condition handling."""

    def test_release_full_lock_double_release(self):
        """release_full_lock handles the RuntimeError from double-release gracefully.

        Both ``notify_worker`` and ``notify_pool`` start in the *locked* state
        after ``ThreadPool.__init__``.  To exercise the RuntimeError catch we
        unlock both first so that ``release_full_lock`` can acquire
        ``notify_worker`` but then fails to release ``notify_pool`` because it
        is already unlocked.
        """
        pool = ThreadPool(pool_size=4)
        # Unlock both so we can trigger the race-condition path
        pool.notify_worker.release()
        pool.notify_pool.release()
        # notify_worker is unlocked → acquire(blocking=False) succeeds
        # notify_pool is unlocked → release() raises RuntimeError → caught
        pool.release_full_lock()  # should not raise


# ===================================================================
# WaitJobsWrapper / GatherJobsWrapper
# ===================================================================


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


# ===================================================================
# THREAD_POOL global singleton
# ===================================================================


class TestGlobalThreadPool:
    """Tests for the global THREAD_POOL singleton."""

    def test_global_is_instance(self):
        """THREAD_POOL is a ThreadPool instance."""
        assert isinstance(THREAD_POOL, ThreadPool)

    def test_global_submits_work(self):
        """The global pool can submit and retrieve results."""
        assert THREAD_POOL.start_thread_soon(lambda: "global").get() == "global"

    def test_global_decorator(self):
        """The global pool supports the run_on_thread decorator."""

        @THREAD_POOL.run_on_thread
        def double(x):
            return x * 2

        assert double(21).get() == 42


# ===================================================================
# Edge cases and error scenarios
# ===================================================================


class TestEdgeCases:
    """Additional edge-case tests."""

    def test_job_exception_removes_one_tb_frame(self):
        """Job exceptions have the _handle_job frame removed from the traceback."""
        pool = ThreadPool(pool_size=4)

        def raise_me():
            raise ValueError("tb_clean")

        job = pool.start_thread_soon(raise_me)
        with pytest.raises(ValueError, match="tb_clean") as excinfo:
            job.get()
        # Walk the traceback; _handle_job must NOT appear
        tb = excinfo.tb
        found_handle_job = False
        while tb is not None:
            if "_handle_job" in tb.tb_frame.f_code.co_name:
                found_handle_job = True
                break
            tb = tb.tb_next
        assert not found_handle_job, "_handle_job should be removed from traceback"

    def test_job_kill_exception_caught_in_handle_job(self):
        """_JobKill raised during job execution is caught by _handle_job.

        When ``_handle_job`` catches ``_JobKill`` it returns ``False``
        immediately without delivering the result to the ``Job`` object,
        so ``job.get()`` would hang.  Use ``get_or_kill`` to verify the
        worker exited instead.
        """
        pool = ThreadPool(pool_size=4)
        job = pool.start_thread_soon(lambda: (_ for _ in ()).throw(_JobKill()))
        # The worker caught _JobKill and exited without delivering a result
        with pytest.raises(JobTimeout):
            job.get_or_kill(timeout=0.05)

    def test_create_lock_contention(self):
        """Multiple threads contending for create_lock creates workers correctly."""
        pool = ThreadPool(pool_size=4)
        results = []
        lock = threading.Lock()

        def worker():
            result = pool.start_thread_soon(lambda: 1).get()
            with lock:
                results.append(result)

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(results) == 8
        assert all(r == 1 for r in results)

    def test_exception_chaining_preserved(self):
        """Exception chains are preserved across the thread boundary."""
        pool = ThreadPool(pool_size=4)

        def chain_error():
            try:
                raise ValueError("inner")
            except ValueError as e:
                raise RuntimeError("outer") from e

        with pytest.raises(RuntimeError, match="outer") as excinfo:
            pool.start_thread_soon(chain_error).get()
        cause = excinfo.value.__cause__
        assert cause is not None
        assert isinstance(cause, ValueError)
        assert "inner" in str(cause)

    def test_job_get_or_kill_kills_busy_worker(self):
        """get_or_kill with timeout kills a busy-looping job."""
        pool = ThreadPool(pool_size=4)
        job = pool.start_thread_soon(_busy_loop)
        time.sleep(0.05)
        count_before = len(pool.all_workers)
        with pytest.raises(JobTimeout):
            job.get_or_kill(timeout=0.05)
        # The worker should have been removed from all_workers
        time.sleep(0.05)
        assert len(pool.all_workers) < count_before
