"""
Tests for the restart / cancel_restart / force_restart RPC entry
(alasio/backend/topic/state.py)

The rpc only validates and schedules the orchestration: the orchestration
itself is covered by tests/backend/app/test_restart_resume.py.
"""
import builtins

import pytest
import trio

from alasio.backend.app import restart
from alasio.backend.reactive.event import RpcValueError
from alasio.backend.topic.state import ConnState
from alasio.backend.ws.context import GLOBAL_CONTEXT


class FakeNursery:
    """Nursery stand-in recording the scheduled tasks"""

    def __init__(self):
        self.started = []

    def start_soon(self, async_fn, *args):
        self.started.append((async_fn, args))


@pytest.fixture
def state():
    """A ConnState topic of a fake connection"""
    yield ConnState('conn_test', None)
    ConnState.singleton_clear()


@pytest.fixture(autouse=True)
def restart_state(monkeypatch):
    """Reset the restart module state and provide a pipe + nursery"""
    restart.GRACEFUL_RESTART.reset()
    monkeypatch.setattr(builtins, '__mpipe_conn__', object(), raising=False)
    nursery = FakeNursery()
    monkeypatch.setattr(GLOBAL_CONTEXT, 'global_nursery', nursery)
    yield nursery
    restart.GRACEFUL_RESTART.reset()


class TestRestartRpc:
    @pytest.mark.trio
    async def test_restart_starts_orchestration(self, state, restart_state):
        await state.restart()

        assert restart.GRACEFUL_RESTART.running is True
        assert restart_state.started == [
            (restart.run_graceful_restart, (restart.GRACEFUL_RESTART.WORKER_MANAGER,))]

    @pytest.mark.trio
    async def test_restart_rejected_when_already_running(self, state):
        restart.GRACEFUL_RESTART.running = True

        with pytest.raises(RpcValueError, match='Restart already in progress'):
            await state.restart()

    @pytest.mark.trio
    async def test_restart_rejected_without_supervisor(self, state, monkeypatch):
        monkeypatch.delattr(builtins, '__mpipe_conn__', raising=False)

        with pytest.raises(PermissionError, match='without supervisor'):
            await state.restart()
        # the rejection must not leave the re-entry flag set
        assert restart.GRACEFUL_RESTART.running is False


class TestCancelRestartRpc:
    @pytest.mark.trio
    async def test_cancel_restart_cancels_the_transaction(self, state, restart_state, monkeypatch):
        calls = []

        async def fake_cancel(reason=''):
            calls.append(('cancel', reason))

        monkeypatch.setattr(restart, 'cancel_graceful_restart', fake_cancel)
        restart.GRACEFUL_RESTART.running = True

        await state.cancel_restart()

        assert calls == [('cancel', 'user cancel')]

    @pytest.mark.trio
    async def test_cancel_restart_rejected_without_restart(self, state):
        # Nothing to cancel: an explicit error, not a silent success (the
        # button only shows during the wait, a stale page gets told so)
        with pytest.raises(RpcValueError, match='No restart in progress'):
            await state.cancel_restart()

    @pytest.mark.trio
    async def test_cancel_restart_leaves_the_resume_queue_alone(self, state, monkeypatch):
        # The auto-resume queue of the new backend shares the cancel entry
        # point of the restart module, but it is not a restart: this rpc must
        # not drop the queue
        async def fake_cancel(reason=''):
            raise AssertionError('cancel_graceful_restart must not be called')

        monkeypatch.setattr(restart, 'cancel_graceful_restart', fake_cancel)
        restart.GRACEFUL_RESTART.resume_scope = trio.CancelScope()

        with pytest.raises(RpcValueError, match='No restart in progress'):
            await state.cancel_restart()

    @pytest.mark.trio
    async def test_cancel_restart_refused_during_an_applying_update(self, state, monkeypatch):
        # The restart of an update transaction in its applying phase is the
        # update itself (the workers are stopping for the file replace): it
        # is not cancellable, the rpc must refuse before touching the restart
        from alasio.backend.app import update as update_app

        async def fake_cancel(reason=''):
            raise AssertionError('cancel_graceful_restart must not be called')

        monkeypatch.setattr(restart, 'cancel_graceful_restart', fake_cancel)
        monkeypatch.setattr(type(update_app.UPDATE_MANAGER), 'applying', property(lambda self: True))
        restart.GRACEFUL_RESTART.running = True

        with pytest.raises(RpcValueError, match='cannot be cancelled'):
            await state.cancel_restart()


class TestForceRestartRpc:
    @pytest.mark.trio
    async def test_force_restart_cancels_first(self, state, monkeypatch):
        calls = []

        async def fake_cancel(reason=''):
            calls.append(('cancel', reason))

        async def fake_lifespan_restart():
            calls.append(('restart', ''))

        monkeypatch.setattr(restart, 'cancel_graceful_restart', fake_cancel)
        monkeypatch.setattr('alasio.backend.topic.state.lifespan_restart', fake_lifespan_restart)

        await state.force_restart()

        # the in-flight graceful restart is cancelled before the force restart
        assert calls == [('cancel', 'force restart'), ('restart', '')]
