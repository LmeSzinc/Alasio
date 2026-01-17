import sys
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from alasio.ext.backport import patch_threadpool_executor_maxworker


@pytest.fixture(scope="module", autouse=True)
def apply_patch():
    """
    Automatically apply the patch before all tests start.
    """
    patch_threadpool_executor_maxworker()
    yield


def is_target_version():
    """Helper function: Determine if the current Python version is within the patch range."""
    return (3, 7) <= sys.version_info[:2] < (3, 13)


@pytest.mark.skipif(not is_target_version(), reason="Patch only applies to Python 3.7 ~ 3.12")
def test_patch_applied_successfully():
    """
    Test 1: Verify if the patch flag exists and ensure the init method is replaced.
    """
    assert getattr(ThreadPoolExecutor, "_is_defaults_patched", False) is True
    init = ThreadPoolExecutor.__init__
    assert init.__name__ == 'init_backport'


@pytest.mark.skipif(not is_target_version(), reason="Patch only applies to Python 3.7 ~ 3.12")
def test_default_logic_with_high_cpu_count():
    """
    Test 2: Mock high CPU count (60 cores).
    Verify that the upper limit is correctly locked at 32 (Python 3.13 logic).

    Comparison:
    - Py 3.7 native: 60 * 5 = 300
    - After patch: min(32, 60 + 4) = 32
    """
    # Mock both os.cpu_count and os.process_cpu_count (if they exist).
    # create=True allows mocking non-existent attributes (for older Python versions without process_cpu_count).
    with patch("os.cpu_count", return_value=60), \
            patch("os.process_cpu_count", return_value=60, create=True):
        with ThreadPoolExecutor() as executor:
            # _max_workers is an internal attribute, but this is the most direct way to verify
            assert executor._max_workers == 32


@pytest.mark.skipif(not is_target_version(), reason="Patch only applies to Python 3.7 ~ 3.12")
def test_default_logic_with_low_cpu_count():
    """
    Test 3: Mock low CPU count (4 cores).
    Verify if the calculation formula is correct: min(32, cpu + 4).

    Comparison:
    - Py 3.7 native: 4 * 5 = 20
    - After patch: min(32, 4 + 4) = 8
    """
    with patch("os.cpu_count", return_value=4), \
            patch("os.process_cpu_count", return_value=4, create=True):
        with ThreadPoolExecutor() as executor:
            assert executor._max_workers == 8


@pytest.mark.skipif(not is_target_version(), reason="Patch only applies to Python 3.7 ~ 3.12")
def test_explicit_max_workers_bypass():
    """
    Test 4: Verify if explicitly providing the max_workers parameter bypasses the default logic.
    Even with many cores, if the user specifies 10, it should be 10.
    """
    with patch("os.cpu_count", return_value=60), \
            patch("os.process_cpu_count", return_value=60, create=True):
        # Explicitly specify 10
        with ThreadPoolExecutor(max_workers=10) as executor:
            assert executor._max_workers == 10
        with ThreadPoolExecutor(10) as executor:
            assert executor._max_workers == 10

        # Explicitly specify 100 (exceeds the default limit of 32, but should be allowed as it is user-specified)
        with ThreadPoolExecutor(max_workers=100) as executor:
            assert executor._max_workers == 100


def test_missing_cpu_count_fallback():
    """
    Test 5: Simulate os.cpu_count returning None (unable to retrieve hardware information).
    Should fall back to the default 1-core logic -> min(32, 1 + 4) = 5
    """
    # Run this logic verification only on target versions.
    if not is_target_version():
        return

    with patch("os.cpu_count", return_value=None), \
            patch("os.process_cpu_count", return_value=None, create=True):
        with ThreadPoolExecutor() as executor:
            assert executor._max_workers == 5
