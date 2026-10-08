"""
Tests for request_graceful_restart (alasio/backend/app/restart.py)

The shared entry point of every restart trigger: the settings page rpc
(ConnState.restart, see tests/backend/topic/test_state_restart.py) and the
daily scheduled restart (alasio/backend/app/schedule.py). It only validates
the preconditions, sets the re-entry flag synchronously and schedules the
orchestration, which is covered by tests/backend/app/test_restart_resume.py.
"""
import builtins

import pytest

from alasio.backend.app import restart
from alasio.backend.app.restart import RestartInProgress


class FakeNursery:
    """Nursery stand-in recording the scheduled tasks"""

    def __init__(self):
        self.started = []

    def start_soon(self, async_fn, *args):
        self.started.append((async_fn, args))


@pytest.fixture(autouse=True)
def restart_state(monkeypatch):
    """Reset the restart module state and provide a pipe"""
    restart.GRACEFUL_RESTART.reset()
    monkeypatch.setattr(builtins, '__mpipe_conn__', object(), raising=False)
    yield
    restart.GRACEFUL_RESTART.reset()


class TestRequestGracefulRestart:
    @pytest.mark.trio
    async def test_starts_the_orchestration_on_the_given_nursery(self):
        """The orchestration task is scheduled with the re-entry flag set"""
        nursery = FakeNursery()

        await restart.request_graceful_restart('test request', nursery=nursery)

        assert restart.GRACEFUL_RESTART.running is True
        assert nursery.started == [
            (restart.run_graceful_restart, (restart.GRACEFUL_RESTART.WORKER_MANAGER, None))]

    @pytest.mark.trio
    async def test_hooks_are_handed_to_the_orchestration(self):
        """The hooks of the in-app update flow travel with the task"""
        nursery = FakeNursery()
        hooks = restart.RestartHooks(actions=['test'])

        await restart.request_graceful_restart('test request', nursery=nursery, hooks=hooks)

        assert nursery.started == [
            (restart.run_graceful_restart, (restart.GRACEFUL_RESTART.WORKER_MANAGER, hooks))]

    @pytest.mark.trio
    async def test_rejected_while_running(self):
        """A second request is refused and schedules nothing"""
        nursery = FakeNursery()
        restart.GRACEFUL_RESTART.running = True

        with pytest.raises(RestartInProgress):
            await restart.request_graceful_restart('test request', nursery=nursery)
        assert nursery.started == []

    @pytest.mark.trio
    async def test_rejected_without_supervisor(self, monkeypatch):
        """Without a supervisor the restart can never come back: explicit refusal"""
        monkeypatch.delattr(builtins, '__mpipe_conn__', raising=False)
        nursery = FakeNursery()

        with pytest.raises(PermissionError, match='without supervisor'):
            await restart.request_graceful_restart('test request', nursery=nursery)
        # the rejection must not leave the re-entry flag set
        assert restart.GRACEFUL_RESTART.running is False
        assert nursery.started == []
