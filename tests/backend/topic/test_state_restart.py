"""
Tests for the restart / force_restart RPC entry (alasio/backend/topic/state.py)

The rpc only validates and schedules the orchestration: the orchestration
itself is covered by tests/backend/test_restart_resume.py.
"""
import builtins

import pytest

from alasio.backend import restart
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
        assert restart_state.started == [(restart.run_graceful_restart, ())]

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
