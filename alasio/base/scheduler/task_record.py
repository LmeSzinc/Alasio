import threading
import time
from typing import Dict, List

from alasio.ext.singleton import Singleton


class TaskRecordError(Exception):
    """Base exception for TaskRecord errors."""
    pass


class TaskTooManyExecutionsError(TaskRecordError):
    """Raised when a task is executed too many times within the time window."""

    def __init__(self, task: str, actual: int, limit: int):
        self.task = task
        self.actual = actual
        self.limit = limit
        super().__init__(
            f"Task `{task}` executed {actual} times within "
            f"{TaskRecord.EXECUTION_WINDOW} seconds"
        )


class TaskTooManyFailuresError(TaskRecordError):
    """Raised when a task has failed too many times."""

    def __init__(self, task: str, actual: int, limit: int):
        self.task = task
        self.actual = actual
        self.limit = limit
        super().__init__(
            f"Task `{task}` failed {actual} or more times"
        )


class TaskRecord(metaclass=Singleton):
    """
    Records task execution history and enforces constraints.

    Constraints:
    1. A task cannot be executed more than MAX_EXECUTIONS times within EXECUTION_WINDOW seconds.
    2. A task cannot fail more than MAX_FAILURES times cumulatively.

    This replaces the old FailureRecord class.
    """
    EXECUTION_WINDOW = 2.0  # seconds
    MAX_EXECUTIONS = 3
    MAX_FAILURES = 3

    def __init__(self):
        self._lock = threading.Lock()
        # Cumulative failure count per task (same as old FailureRecord)
        self._failures: Dict[str, int] = {}
        # Execution timestamps per task (for frequency check)
        self._executions: Dict[str, List[float]] = {}

    def clear(self):
        """Clear all records for all tasks."""
        with self._lock:
            self._failures.clear()
            self._executions.clear()

    def clear_task(self, task: str):
        """Clear all records for a specific task (failures and execution history)."""
        with self._lock:
            self._failures.pop(task, None)
            self._executions.pop(task, None)

    def clear_failure(self, task: str):
        """Clear only the failure record for a specific task, keeping execution history."""
        with self._lock:
            self._failures.pop(task, None)

    def mark_task_result(self, task: str, success: bool) -> int:
        """
        Record the result of a task execution and enforce constraints.

        Args:
            task: Name of the task.
            success: Whether the task succeeded.

        Returns:
            int: Cumulative failure count for the task.

        Raises:
            TaskTooManyExecutionsError: If the task was executed more than MAX_EXECUTIONS
                times within EXECUTION_WINDOW seconds.
            TaskTooManyFailuresError: If the task has failed MAX_FAILURES or more times.
        """
        with self._lock:
            now = time.perf_counter()

            # --- Execution frequency check (new feature) ---
            if task not in self._executions:
                self._executions[task] = []
            self._executions[task].append(now)

            # Prune old timestamps outside the window
            cutoff = now - self.EXECUTION_WINDOW
            self._executions[task] = [
                t for t in self._executions[task] if t >= cutoff
            ]

            recent_count = len(self._executions[task])
            if recent_count > self.MAX_EXECUTIONS:
                raise TaskTooManyExecutionsError(
                    task, recent_count, self.MAX_EXECUTIONS
                )

            # --- Failure tracking (replaces old FailureRecord) ---
            if success:
                # On success, clear failure count (same as old FailureRecord)
                self._failures.pop(task, None)
                return 0
            else:
                # On failure, increment failure count
                count = self._failures.get(task, 0)
                count += 1
                self._failures[task] = count

                if count >= self.MAX_FAILURES:
                    raise TaskTooManyFailuresError(
                        task, count, self.MAX_FAILURES
                    )
                return count
