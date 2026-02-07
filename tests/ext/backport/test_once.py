import threading
import time

from alasio.ext.backport.once import patch_once, run_once


class TestPatchOnce:
    def test_patch_once_basic(self):
        count = 0

        def increment():
            nonlocal count
            count += 1

        patched = patch_once(increment)
        patched()
        patched()
        patched()

        assert count == 1

    def test_patch_once_thread_safe(self):
        count = 0
        lock = threading.Lock()

        def increment():
            nonlocal count
            # Simulate some work
            time.sleep(0.01)
            with lock:
                count += 1

        patched = patch_once(increment)

        threads = [threading.Thread(target=patched) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert count == 1

    def test_patch_once_arguments(self):
        results = []

        def append_val(val):
            results.append(val)

        patched = patch_once(append_val)
        patched(1)
        patched(2)

        assert results == [1]


class TestRunOnce:
    def test_run_once_basic(self):
        count = 0

        def increment():
            nonlocal count
            count += 1

        decorated = run_once(increment)
        decorated()
        decorated()

        assert count == 1
        assert decorated.has_run is True

    def test_run_once_reset(self):
        count = 0

        def increment():
            nonlocal count
            count += 1

        decorated = run_once(increment)
        decorated()
        assert count == 1

        decorated.has_run = False
        decorated()
        assert count == 2
        assert decorated.has_run is True

    def test_run_once_thread_safe(self):
        count = 0
        lock = threading.Lock()

        def increment():
            nonlocal count
            time.sleep(0.01)
            with lock:
                count += 1

        decorated = run_once(increment)

        threads = [threading.Thread(target=decorated) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert count == 1

    def test_run_once_arguments(self):
        results = []

        def append_val(val):
            results.append(val)

        decorated = run_once(append_val)
        decorated("a")
        decorated("b")

        assert results == ["a"]
