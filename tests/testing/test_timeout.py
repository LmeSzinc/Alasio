import time

import pytest

from alasio.testing.patch_time import PatchTime
from alasio.testing.timeout import AssertTimeout


def test_immediate_success():
    """Test that the loop exits immediately if the assertion passes."""
    with PatchTime(100.0):
        start = time.perf_counter()
        count = 0
        for _ in AssertTimeout(timeout=1.0):
            with _:
                count += 1
                assert True
        end = time.perf_counter()

    assert count == 1
    assert end - start < 0.1  # Should be very fast (no retries)


def test_eventual_success():
    """Test that the loop retries until success within timeout."""
    with PatchTime(100.0):
        start = time.perf_counter()
        count = 0
        target = 3

        # It will fail for the first 2 times, and pass on the 3rd time
        for _ in AssertTimeout(timeout=1.0, interval=0.01):
            with _:
                count += 1
                if count < target:
                    assert False, "Not yet"
                assert True

        end = time.perf_counter()

    assert count == target
    # Should have taken at least (target-1) * interval in mock time
    # Each of the first (target-1) iterations sleeps for interval via mock_sleep
    assert end - start >= (target - 1) * 0.01


def test_timeout_failure():
    """Test that AssertionError is raised if it never passes within timeout."""
    timeout = 0.1

    with PatchTime(100.0):
        start = time.perf_counter()

        with pytest.raises(AssertionError, match="Always fail"):
            for _ in AssertTimeout(timeout=timeout, interval=0.01):
                with _:
                    assert False, "Always fail"

        end = time.perf_counter()

    # mock time should have advanced by at least the timeout
    assert end - start >= timeout


def test_other_exception_propagation():
    """Test that non-AssertionErrors are propagated immediately."""
    with PatchTime(100.0):
        start = time.perf_counter()
        count = 0

        with pytest.raises(ValueError, match="Something wrong"):
            for _ in AssertTimeout(timeout=1.0, interval=0.01):
                with _:
                    count += 1
                    raise ValueError("Something wrong")

        end = time.perf_counter()

    assert count == 1
    assert end - start < 0.1  # Should not retry (no mock sleep advance)


def test_poll_interval_uses_the_precise_sleep(monkeypatch):
    """The poll interval is passed to the precise sleep of the project

    time.sleep() rounds the interval up to a whole millisecond on Windows and
    the wake-up is quantized to the system timer tick, so a 5ms poll can take
    three times as long as asked.
    """
    calls = []
    monkeypatch.setattr('alasio.testing.timeout.sleep', calls.append)

    count = 0
    for _ in AssertTimeout(timeout=1.0, interval=0.01):
        with _:
            count += 1
            assert count >= 3

    assert count == 3
    # The first two rounds fail, each one is followed by a poll interval
    assert calls == [0.01, 0.01]


def test_zero_interval_yields_instead_of_polling_hard(monkeypatch):
    """A zero interval is a busy poll, it yields the GIL to the polled thread

    The precise sleep returns at once on a zero time without yielding, which
    would let a polling thread starve the thread it is waiting for.
    """
    yields = []
    monkeypatch.setattr(time, 'sleep', yields.append)
    monkeypatch.setattr(
        'alasio.testing.timeout.sleep',
        lambda seconds: pytest.fail('the precise sleep must not be used for a zero interval'),
    )

    count = 0
    for _ in AssertTimeout(timeout=1.0, interval=0):
        with _:
            count += 1
            assert count >= 3

    assert count == 3
    assert yields == [0, 0]
