import multiprocessing
import os
import threading
import time

import pytest

from alasio.ext.concurrent.processpool import ProcessPool
from alasio.testing.timeout import AssertTimeout


# ===========================
# Helper Functions (Must be picklable)
# ===========================

def worker_add(a, b):
    return a + b


def worker_sleep(t):
    time.sleep(t)
    return t


def worker_pid(x=None):
    return os.getpid()


def worker_raise(msg):
    raise ValueError(msg)


def worker_echo(x):
    return x


def worker_system_exit():
    import sys
    sys.exit(1)


# ===========================
# Tests
# ===========================

def test_submit_and_get():
    with ProcessPool(worker_add, max_workers=1) as pool:
        job = pool.submit(1, 2)
        assert job.get() == 3
        assert job._status == 'FINISHED'


def test_concurrency():
    # Use more workers than tasks to ensure parallelism if possible,
    # or use enough tasks to saturate workers.
    # Here we check if we can run multiple tasks.
    count = 4
    with ProcessPool(worker_pid, max_workers=2) as pool:
        jobs = [pool.submit() for _ in range(count)]
        results = [job.get() for job in jobs]

        assert len(results) == count
        # PIDs should be unique per worker process. 
        # Since we have 2 workers, we expect at most 2 unique PIDs usually,
        # but if a worker is recycled, it keeps PID.
        unique_pids = set(results)
        assert len(unique_pids) <= 2
        assert len(unique_pids) > 0


def test_worker_exception():
    with ProcessPool(worker_raise, max_workers=1) as pool:
        job = pool.submit("test_error")
        with pytest.raises(ValueError, match="test_error"):
            job.get()
        assert job._status == 'ERROR'


def test_serialization_error():
    # Define a local function or object that can't be pickled
    # But ProcessPool uses 'worker_func' defined in init.
    # The args passed to submit must be picklable.

    # We use worker_echo, and pass an unpicklable arg.

    class Unpicklable:
        def __getstate__(self):
            raise TypeError("Cannot pickle me")

    with ProcessPool(worker_echo, max_workers=1) as pool:
        job = pool.submit(Unpicklable())

        # Depending on implementation, submit might raise or return failed job.
        # The code says:
        # except Exception as e: ... job._set_error(e) ... return job

        with pytest.raises(Exception):  # TypeError or PickleError
            job.get()

        assert job._status == 'ERROR'

        # Verify pool is still working
        job2 = pool.submit("hello")
        assert job2.get() == "hello"


def test_worker_crash_retry():
    # max_retry=2
    # We use SystemExit to bypass the 'except Exception' in worker_loop,
    # causing the worker process to exit, closing the pipe, triggering _handle_crash.
    with ProcessPool(worker_system_exit, max_workers=1, max_retry=2) as pool:
        job = pool.submit()

        # It should retry 2 times (total 3 attempts) and then fail.
        # The final exception comes from _handle_crash -> _finalize_job.
        # It is likely a BrokenPipeError or similar indicating the worker died.
        with pytest.raises((BrokenPipeError, EOFError, OSError)):
            job.get()

        assert job.retries > 0
        assert job._status == 'ERROR'

    # Check for leaks
    # Give a brief moment for cleanup
    for _ in AssertTimeout(2):
        with _:
            leaked_procs = [p for p in multiprocessing.active_children() if p.name.startswith("ProcessPool-")]
            leaked_threads = [t for t in threading.enumerate() if t.name.startswith("ProcessPool-")]
            assert not leaked_procs, f"Leaked processes: {leaked_procs}"
            assert not leaked_threads, f"Leaked threads: {leaked_threads}"


def test_backpressure():
    # max_workers=1
    # Submit 1 slow task.
    # Submit 2nd task -> should block until 1st finishes.

    with ProcessPool(worker_sleep, max_workers=1) as pool:
        # Task 1: sleep 0.5s
        job1 = pool.submit(0.5)

        start_time = time.time()

        # Task 2: sleep 0.1s
        # This submit should block until job1 is done (approx 0.5s)
        # because max_workers=1 and job1 occupies the only slot.
        job2 = pool.submit(0.1)

        submit_duration = time.time() - start_time

        # Verify that submit took at least some time (waiting for job1)
        # Ideally close to 0.5s.
        # Note: 'submit' blocks acquiring semaphore.
        # Once job1 finishes, listener thread releases semaphore.
        # Then job2 submit proceeds.

        assert submit_duration >= 0.4  # Allow some buffer

        job1.get()
        job2.get()


def test_shutdown():
    # Verify __exit__ waits for tasks
    with ProcessPool(worker_sleep, max_workers=2) as pool:
        job1 = pool.submit(0.5)
        job2 = pool.submit(0.5)

    # If we are here, context manager exited.
    # Tasks should be done.
    assert job1._status == 'FINISHED'
    assert job2._status == 'FINISHED'


def test_resource_leak():
    with ProcessPool(worker_sleep, max_workers=2) as pool:
        j1 = pool.submit(0.1)
        j2 = pool.submit(0.1)
        j1.get()
        j2.get()

    for _ in AssertTimeout(2):
        with _:
            leaked_procs = [p for p in multiprocessing.active_children() if p.name.startswith("ProcessPool-")]
            leaked_threads = [t for t in threading.enumerate() if t.name.startswith("ProcessPool-")]
            assert not leaked_procs, f"Leaked processes: {leaked_procs}"
            assert not leaked_threads, f"Leaked threads: {leaked_threads}"


def test_no_tasks():
    with ProcessPool(worker_add, max_workers=1) as pool:
        pass

    for _ in AssertTimeout(2):
        with _:
            leaked_procs = [p for p in multiprocessing.active_children() if p.name.startswith("ProcessPool-")]
            leaked_threads = [t for t in threading.enumerate() if t.name.startswith("ProcessPool-")]
            assert not leaked_procs, f"Leaked processes: {leaked_procs}"
            assert not leaked_threads, f"Leaked threads: {leaked_threads}"


def test_no_retry():
    with ProcessPool(worker_system_exit, max_workers=1, max_retry=0) as pool:
        job = pool.submit()
        with pytest.raises((BrokenPipeError, EOFError, OSError)):
            job.get()
        assert job.retries == 0
        assert job._status == 'ERROR'
