"""
Tests for SchedulerTask, the task interface of the scheduler.

Every scheduler task is called by the task name, either scheduled from the
config (e.g. RestartGame) or called internally (e.g. GotoMain), so the MOD
must override all of them.
"""

import inspect

import pytest

from alasio.base.scheduler.scheduler import AlasioScheduler
from alasio.base.scheduler.scheduler_task import SchedulerTask

# scheduler tasks of alasio, sorted by name
SCHEDULER_TASK = [
    'GotoMain',
    'RestartDevice',
    'RestartGame',
    'StopDevice',
    'StopGame',
]


class TestSchedulerTask:
    """Tests for the default implementation of the scheduler tasks."""

    @pytest.mark.parametrize('task', SCHEDULER_TASK)
    def test_not_implemented(self, task):
        """Scheduler task raises NotImplementedError when the MOD does not override it."""
        scheduler_task = SchedulerTask()
        with pytest.raises(NotImplementedError):
            getattr(scheduler_task, task)()

    @pytest.mark.parametrize('task', SCHEDULER_TASK)
    def test_task_has_no_argument(self, task):
        """Scheduler task is called without any argument."""
        params = inspect.signature(getattr(SchedulerTask, task)).parameters
        assert list(params) == ['self']

    @pytest.mark.parametrize('task', SCHEDULER_TASK)
    def test_task_is_documented(self, task):
        """Scheduler task is documented, so the MOD developer knows what to implement."""
        doc = getattr(SchedulerTask, task).__doc__
        assert doc
        assert 'NotImplementedError' in doc


class TestAlasioSchedulerInheritSchedulerTask:
    """Tests for the inheritance between AlasioScheduler and SchedulerTask."""

    def test_scheduler_inherit_scheduler_task(self):
        """AlasioScheduler inherits the scheduler tasks."""
        assert issubclass(AlasioScheduler, SchedulerTask)

    @pytest.mark.parametrize('task', SCHEDULER_TASK)
    def test_scheduler_does_not_override_task(self, task):
        """AlasioScheduler does not override any scheduler task, the MOD does."""
        assert getattr(AlasioScheduler, task) is getattr(SchedulerTask, task)
