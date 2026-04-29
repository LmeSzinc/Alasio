import threading

import pytest

from alasio.base.scheduler.task_record import (
    TaskRecord,
    TaskTooManyExecutionsError,
    TaskTooManyFailuresError,
)
from alasio.testing.patch_time import PatchTime


class TestTaskRecord:
    """Test TaskRecord class that replaces old FailureRecord."""

    def setup_method(self):
        """Clear singleton cache before each test to start with a fresh instance."""
        TaskRecord.singleton_clear()

    def test_success_resets_failure_count(self):
        """
        Test that a successful execution resets the failure count to 0.
        Same behavior as old FailureRecord.
        """
        record = TaskRecord()

        # Fail twice
        count = record.mark_task_result(task="test_task", success=False)
        assert count == 1
        count = record.mark_task_result(task="test_task", success=False)
        assert count == 2

        # Success resets to 0
        count = record.mark_task_result(task="test_task", success=True)
        assert count == 0

    def test_failure_increments_count(self):
        """
        Test that consecutive failures increment the count.

        With MAX_EXECUTIONS=3 within 2s, we can only do 2 fast failures safely.
        The 3rd failure within the window would trigger either execution or failure error.
        So this test uses 2 fast failures to verify incremental counting.
        """
        record = TaskRecord()

        count = record.mark_task_result(task="test_task", success=False)
        assert count == 1

        count = record.mark_task_result(task="test_task", success=False)
        assert count == 2

    def test_multiple_tasks_independence(self):
        """
        Test that records for different tasks are independent.

        task_a fails 3 times -> 3rd raises TaskTooManyFailuresError.
        task_b fails once.
        """
        record = TaskRecord()

        count_a1 = record.mark_task_result(task="task_a", success=False)
        assert count_a1 == 1

        count_a2 = record.mark_task_result(task="task_a", success=False)
        assert count_a2 == 2

        count_b1 = record.mark_task_result(task="task_b", success=False)
        assert count_b1 == 1

        # task_a 3rd failure triggers exception
        with pytest.raises(TaskTooManyFailuresError):
            record.mark_task_result(task="task_a", success=False)

        # task_b unaffected - success returns 0
        count_b2 = record.mark_task_result(task="task_b", success=True)
        assert count_b2 == 0

    def test_task_too_many_failures_exception(self):
        """
        Test that TaskTooManyFailuresError is raised after MAX_FAILURES failures.
        Wait between calls to avoid execution frequency limit.
        """
        with PatchTime(100.0) as pt:
            record = TaskRecord()

            record.mark_task_result(task="test_task", success=False)  # failures=1
            pt.shift(record.EXECUTION_WINDOW + 0.1)
            record.mark_task_result(task="test_task", success=False)  # failures=2
            pt.shift(record.EXECUTION_WINDOW + 0.1)

            # Third failure should raise
            with pytest.raises(TaskTooManyFailuresError) as exc_info:
                record.mark_task_result(task="test_task", success=False)

            assert exc_info.value.task == "test_task"
            assert exc_info.value.actual == 3
            assert exc_info.value.limit == 3

    def test_task_too_many_executions_exception(self):
        """
        Test that TaskTooManyExecutionsError is raised when a task is executed
        more than MAX_EXECUTIONS times within EXECUTION_WINDOW seconds.
        """
        record = TaskRecord()

        # Execute 4 times quickly (MAX_EXECUTIONS is 3, so 4 > 3 triggers the error)
        record.mark_task_result(task="test_task", success=False)  # 1
        record.mark_task_result(task="test_task", success=False)  # 2
        record.mark_task_result(task="test_task", success=True)  # 3

        # 4th execution should raise TaskTooManyExecutionsError
        with pytest.raises(TaskTooManyExecutionsError) as exc_info:
            record.mark_task_result(task="test_task", success=True)

        assert exc_info.value.task == "test_task"
        assert exc_info.value.actual == 4
        assert exc_info.value.limit == 3

    def test_execution_count_resets_after_window(self):
        """
        Test that execution count resets after EXECUTION_WINDOW seconds,
        so no error is raised for new executions outside the window.
        """
        with PatchTime(100.0) as pt:
            record = TaskRecord()

            # Execute 4 times quickly
            record.mark_task_result(task="test_task", success=True)  # 1
            record.mark_task_result(task="test_task", success=True)  # 2
            record.mark_task_result(task="test_task", success=True)  # 3

            with pytest.raises(TaskTooManyExecutionsError):
                record.mark_task_result(task="test_task", success=True)  # 4 - error

            # Advance time past the window
            pt.shift(record.EXECUTION_WINDOW + 0.1)

            # Now it should succeed again (old entries are pruned)
            count = record.mark_task_result(task="test_task", success=True)
            assert count == 0  # all succeeded

    def test_clear_task(self):
        """
        Test that clear_task removes records for a specific task.
        """
        with PatchTime(100.0) as pt:
            record = TaskRecord()

            record.mark_task_result(task="task_a", success=False)  # failures=1
            pt.shift(record.EXECUTION_WINDOW + 0.1)
            record.mark_task_result(task="task_b", success=False)  # failures=1
            record.clear_task("task_a")

            # After clearing, task_a should start fresh
            count = record.mark_task_result(task="task_a", success=False)  # failures=1
            assert count == 1

            # task_b is unaffected
            count = record.mark_task_result(task="task_b", success=False)  # failures=2
            assert count == 2

    def test_clear(self):
        """
        Test that clear removes all records.
        """
        with PatchTime(100.0) as pt:
            record = TaskRecord()

            record.mark_task_result(task="task_a", success=False)
            pt.shift(record.EXECUTION_WINDOW + 0.1)
            record.mark_task_result(task="task_b", success=False)
            record.clear()

            # Both tasks should start fresh
            count_a = record.mark_task_result(task="task_a", success=False)
            assert count_a == 1

            count_b = record.mark_task_result(task="task_b", success=False)
            assert count_b == 1

    def test_clear_failure_resets_failure_count(self):
        """
        Test that clear_failure resets failure count for a task
        while keeping execution history intact.
        """
        record = TaskRecord()

        # Fail once
        count = record.mark_task_result(task="test_task", success=False)
        assert count == 1

        # Clear only the failure record
        record.clear_failure("test_task")

        # After clearing failure, fail again - should start from 1
        count = record.mark_task_result(task="test_task", success=False)
        assert count == 1

    def test_clear_failure_keeps_execution_history(self):
        """
        Test that clear_failure does NOT reset execution history,
        so execution frequency checks still apply.
        """
        record = TaskRecord()

        # Execute 3 times quickly (2 failures + 1 success to avoid failure limit)
        record.mark_task_result(task="test_task", success=False)  # 1: failures=1
        record.mark_task_result(task="test_task", success=False)  # 2: failures=2
        record.mark_task_result(task="test_task", success=True)  # 3: failures reset to 0

        # Clear failures only (already 0, but just to be explicit)
        record.clear_failure("test_task")

        # Execution count is still 3 in the window, so 4th call should trigger
        # TaskTooManyExecutionsError (execution count 4 > 3)
        with pytest.raises(TaskTooManyExecutionsError):
            record.mark_task_result(task="test_task", success=True)

    def test_clear_failure_nonexistent_task(self):
        """
        Test that clear_failure on a task with no failure record
        does not raise any error.
        """
        record = TaskRecord()

        # Should not raise
        record.clear_failure("nonexistent_task")

        # Should still work normally after
        count = record.mark_task_result(task="nonexistent_task", success=False)
        assert count == 1

    def test_clear_failure_does_not_affect_other_tasks(self):
        """
        Test that clear_failure only affects the specified task.
        """
        record = TaskRecord()

        record.mark_task_result(task="task_a", success=False)  # failures=1
        record.mark_task_result(task="task_b", success=False)  # failures=1

        record.clear_failure("task_a")

        # task_a failures reset
        count_a = record.mark_task_result(task="task_a", success=False)
        assert count_a == 1

        # task_b unaffected
        count_b = record.mark_task_result(task="task_b", success=False)
        assert count_b == 2

    def test_thread_safety(self):
        """
        Test that TaskRecord is thread-safe under concurrent access.
        Each thread uses a unique task name and sleeps enough between calls
        to stay within the execution frequency limit (3 calls per 2 seconds).
        """
        with PatchTime(100.0) as pt:
            record = TaskRecord()
            errors = []
            lock = threading.Lock()

            def worker(thread_id):
                try:
                    for i in range(10):
                        record.mark_task_result(task=f"thread_task_{thread_id}", success=True)
                        pt.shift(0.7)  # ~1.4 calls/sec, well within 3 calls/2s limit
                except Exception as e:
                    with lock:
                        errors.append(e)

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(errors) == 0, f"Thread safety errors: {errors}"

    def test_success_resets_failure_count_before_limit(self):
        """
        Test that a success after some failures resets the failure count,
        then subsequent failures start from 1 again (not reaching threshold).
        """
        with PatchTime(100.0) as pt:
            record = TaskRecord()

            record.mark_task_result(task="test_task", success=False)  # failures=1
            record.mark_task_result(task="test_task", success=False)  # failures=2
            # Advance past execution window
            pt.shift(record.EXECUTION_WINDOW + 0.1)
            record.mark_task_result(task="test_task", success=True)  # failures=0

            # Now fail again - should be back to 1
            count = record.mark_task_result(task="test_task", success=False)  # failures=1
            assert count == 1

    def test_max_executions_custom_values(self):
        """Test with modified class constants to verify flexibility."""

        class CustomTaskRecord(TaskRecord):
            EXECUTION_WINDOW = 60.0  # Long window to ensure all calls are counted
            MAX_EXECUTIONS = 2
            MAX_FAILURES = 5

        record = CustomTaskRecord()

        # Execute 3 times (MAX_EXECUTIONS=2, so 3 > 2 triggers error)
        record.mark_task_result(task="test_task", success=True)  # 1
        record.mark_task_result(task="test_task", success=True)  # 2

        with pytest.raises(TaskTooManyExecutionsError) as exc_info:
            record.mark_task_result(task="test_task", success=True)  # 3rd - error

        assert exc_info.value.actual == 3
        assert exc_info.value.limit == 2

    def test_mixed_success_failure_execution_check(self):
        """
        Test that the execution frequency check counts all executions
        (both successes and failures) within the time window.
        """
        record = TaskRecord()

        # Mix successes and failures - 4 total executions
        record.mark_task_result(task="test_task", success=True)  # 1
        record.mark_task_result(task="test_task", success=False)  # 2
        record.mark_task_result(task="test_task", success=True)  # 3

        # 4th should trigger too many executions
        with pytest.raises(TaskTooManyExecutionsError):
            record.mark_task_result(task="test_task", success=False)

    def test_task_record_error_base(self):
        """Test that custom exceptions inherit from Exception."""
        assert issubclass(TaskTooManyExecutionsError, Exception)
        assert issubclass(TaskTooManyFailuresError, Exception)

    def test_execution_frequency_takes_priority(self):
        """
        Test that execution frequency error is raised before failure error
        when both conditions would be met in the same call.
        Use a higher MAX_FAILURES to ensure the failure check doesn't trigger first.
        """

        class CustomTaskRecord(TaskRecord):
            EXECUTION_WINDOW = 60.0
            MAX_EXECUTIONS = 3
            MAX_FAILURES = 10  # High enough that failures don't trigger before executions

        record = CustomTaskRecord()

        # 3 fast failures
        record.mark_task_result(task="test_task", success=False)  # 1: failures=1
        record.mark_task_result(task="test_task", success=False)  # 2: failures=2
        record.mark_task_result(task="test_task", success=False)  # 3: failures=3
        # With MAX_EXECUTIONS=3, 4th call would exceed limit
        # With MAX_FAILURES=10, failures are at 3, well below the limit

        # 4th - should raise execution error, not failure error
        with pytest.raises(TaskTooManyExecutionsError):
            record.mark_task_result(task="test_task", success=False)

    def test_concurrent_fast_failures_within_window(self):
        """
        Test that 3 fast failures within the execution window raises
        TaskTooManyFailuresError (since all 3 happen within the window,
        the execution count check passes because it's exactly 3, not > 3).
        """
        record = TaskRecord()

        # 3 fast failures - exactly MAX_EXECUTIONS=3, so no execution error
        record.mark_task_result(task="test_task", success=False)  # 1: failures=1
        record.mark_task_result(task="test_task", success=False)  # 2: failures=2

        # 3rd failure: execution count=3 (== MAX_EXECUTIONS, not >), but failures=3 (>= MAX_FAILURES)
        with pytest.raises(TaskTooManyFailuresError) as exc_info:
            record.mark_task_result(task="test_task", success=False)
        assert exc_info.value.task == "test_task"
        assert exc_info.value.actual == 3
        assert exc_info.value.limit == 3

    def test_fourth_execution_triggers_after_failure_exception(self):
        """
        Test that after TaskTooManyFailuresError is raised on the 3rd failure,
        the 4th call triggers TaskTooManyExecutionsError (execution count=4 > 3).
        """
        record = TaskRecord()

        # 3 failures in a row - 3rd one triggers failure error
        record.mark_task_result(task="test_task", success=False)  # 1
        record.mark_task_result(task="test_task", success=False)  # 2

        # 3rd: failures=3 triggers TaskTooManyFailuresError
        with pytest.raises(TaskTooManyFailuresError):
            record.mark_task_result(task="test_task", success=False)  # 3

        # After the failure exception, the execution state still has 3 records.
        # 4th call: executions=4 > 3 -> TaskTooManyExecutionsError
        with pytest.raises(TaskTooManyExecutionsError):
            record.mark_task_result(task="test_task", success=True)
