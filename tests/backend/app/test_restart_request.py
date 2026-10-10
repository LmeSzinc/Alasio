"""
Tests for the restart entries (alasio/backend/app/restart.py)

request_graceful_restart is the public entry of every external trigger: the
settings page rpc (ConnState.restart, see tests/backend/topic/test_state_restart.py)
and the daily scheduled restart (alasio/backend/app/schedule.py).
open_restart_window is the internal entry of the owner of the backend (the
update transaction and its convergence): the public entry refuses while the
holder registry is set, the internal entry is not gated by the startup window.
Both only validate the preconditions, set the re-entry flag synchronously and
hand the window over; the orchestration itself is covered by
tests/backend/app/test_restart_resume.py.
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
        """The default driver task is scheduled with the re-entry flag set"""
        nursery = FakeNursery()

        await restart.request_graceful_restart('test request', nursery=nursery)

        assert restart.GRACEFUL_RESTART.running is True
        assert nursery.started == [
            (restart.run_graceful_restart, (restart.GRACEFUL_RESTART.WORKER_MANAGER,))]

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

    @pytest.mark.trio
    async def test_rejected_while_an_update_transaction_owns_the_backend(self):
        """The holder registry refuses the external trigger: only the owner
        itself drives its restart (the internal entry), and the refusal names
        the holder"""
        nursery = FakeNursery()
        restart.GRACEFUL_RESTART.set_holder('update of "m"')

        with pytest.raises(restart.RestartUnavailable, match='update of "m" is in progress'):
            await restart.request_graceful_restart('test request', nursery=nursery)
        assert nursery.started == []

        # the transaction is over: the entry is free again
        restart.GRACEFUL_RESTART.clear_holder('update of "m"')
        await restart.request_graceful_restart('test request', nursery=nursery)
        assert len(nursery.started) == 1

    @pytest.mark.trio
    async def test_clear_holder_ignores_a_stale_close(self):
        """A stale close never drops a newer owner"""
        restart.GRACEFUL_RESTART.set_holder('update of "m"')
        restart.GRACEFUL_RESTART.clear_holder('update of "old"')
        assert restart.GRACEFUL_RESTART.holder == 'update of "m"'

    @pytest.mark.trio
    async def test_rejected_while_the_backend_is_starting_up(self):
        """The startup gate refuses every external trigger"""
        from alasio.backend.app.update_startup import UPDATE_STARTUP

        nursery = FakeNursery()
        UPDATE_STARTUP.reset()  # not initialized yet
        with pytest.raises(restart.RestartUnavailable, match='starting up'):
            await restart.request_graceful_restart('test request', nursery=nursery)
        assert nursery.started == []

        # initialized, but a registered mod is still in its first check
        UPDATE_STARTUP.update_inited.set()
        UPDATE_STARTUP.mod_event('m')
        with pytest.raises(restart.RestartUnavailable, match='starting up'):
            await restart.request_graceful_restart('test request', nursery=nursery)
        assert nursery.started == []


class TestOpenRestartWindow:
    """The internal entry of the owner of the backend (the update flow)."""

    @pytest.mark.trio
    async def test_rejected_while_a_restart_is_running(self):
        """A restart already in flight owns the backend: the second window is
        refused and the flag is left to the first one"""
        restart.GRACEFUL_RESTART.running = True

        with pytest.raises(RestartInProgress):
            await restart.open_restart_window('test window')
        assert restart.GRACEFUL_RESTART.running is True
        assert restart.GRACEFUL_RESTART.window is None

    @pytest.mark.trio
    async def test_begin_failure_releases_the_flag(self, monkeypatch):
        """A window that cannot begin (a restart already owns the manager)
        releases the rpc flag: the update transaction is still in flight and
        handles the failure itself"""
        async def failing_begin(self):
            raise RuntimeError('Restart already in progress')

        monkeypatch.setattr(restart.RestartWindow, 'begin', failing_begin)

        with pytest.raises(RuntimeError, match='Restart already in progress'):
            await restart.open_restart_window('test window')
        assert restart.GRACEFUL_RESTART.running is False

    @pytest.mark.trio
    async def test_the_startup_window_does_not_gate_the_owner(self, monkeypatch):
        """The startup window only refuses the external triggers: the update
        transaction drives its own restart while the backend is starting up
        (its convergence and first checks are exactly what it is finishing)"""
        from alasio.backend.app.update_startup import UPDATE_STARTUP

        async def empty_begin(self):
            return []

        monkeypatch.setattr(restart.RestartWindow, 'begin', empty_begin)
        UPDATE_STARTUP.reset()  # the startup window is open

        window = await restart.open_restart_window('test window')

        assert isinstance(window, restart.RestartWindow)
        assert restart.GRACEFUL_RESTART.running is True
