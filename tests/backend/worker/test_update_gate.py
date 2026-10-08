"""
Tests for the update gate of WorkerManager (the start gate of the update
flows): a start accepted while the gate holds the instance is queued as a
resume entry instead of being refused, and the holder of the gate releases
the queue when it ends.

The gate has two sources: the update transaction (downloading / updating)
and the update startup events (the first update check of the mod is not
over, see alasio.backend.app.update_startup). The manager never spawns here:
the spawn tail of worker_start is replaced by a marker, only the gate paths
are exercised.
"""
import pytest

from alasio.backend.app.update_startup import UPDATE_STARTUP
from alasio.backend.worker.manager import WorkerManager
from tests.backend.worker.test_restart_state import add_state


class NoSpawnManager(WorkerManager):
    """A manager whose process spawn is a marker (no real subprocess)."""

    def _worker_start_process(self, state, mod, config, child_conn, project_root='',
                              mod_root='', path_main=''):
        return True, 'spawned'


@pytest.fixture
def manager():
    """A fresh manager without process spawns (the shared conftest fixture
    opens the update startup gate by default; a test of the shut gate resets
    the events itself)"""
    WorkerManager.singleton_clear()
    mgr = NoSpawnManager()
    yield mgr
    try:
        mgr.close()
    except Exception as e:
        print(f'Warning: Error during cleanup: {e}')


class TestUpdateWindowGate:
    """worker_start under the update gate."""

    def test_transaction_window_queues_every_start(self, manager):
        """The transaction window queues the starts of every mod"""
        manager.update_begin_transaction('m')

        assert manager.worker_start('other', 'cfg')[0] is True

        assert manager.state['cfg'].state == 'resuming'
        manager.update_end_transaction('m')
        assert manager.worker_start('other', 'cfg2')[0] is True
        assert manager.state['cfg2'].state == 'starting'

    def test_startup_gate_queues_the_start(self, manager):
        """While the first update check of the mod is not over, a start is
        queued as a resume (released by the startup orchestration)"""
        UPDATE_STARTUP.reset()

        success, msg = manager.worker_start('m', 'cfg')

        assert success is True
        assert 'queued' in msg
        state = manager.state['cfg']
        assert state.state == 'resuming'
        assert state.update_queued is True
        assert state.mod == 'm'

    def test_startup_gate_opens_when_the_mod_is_ready(self, manager):
        """Once the mod had its first check, the starts run normally"""
        UPDATE_STARTUP.reset()
        assert manager.worker_start('m', 'cfg')[0] is True
        assert manager.state['cfg'].state == 'resuming'

        UPDATE_STARTUP.update_inited.set()
        UPDATE_STARTUP.mod_event('m').set()

        success, msg = manager.worker_start('m', 'cfg2')
        assert (success, msg) == (True, 'spawned')

    def test_start_outside_a_gate_is_not_gated(self, manager):
        """Without a gate a start runs normally"""
        success, msg = manager.worker_start('m', 'cfg')

        assert (success, msg) == (True, 'spawned')
        assert manager.state['cfg'].state == 'starting'

    def test_running_config_is_not_queued_twice(self, manager):
        """An already running config is not the gate's business"""
        add_state(manager, 'cfg', 'running')
        manager.update_begin_transaction('m')

        success, msg = manager.worker_start('m', 'cfg')

        assert success is False
        assert 'already running' in msg
        assert manager.state['cfg'].state == 'running'

    def test_queued_config_stays_queued(self, manager):
        """A second click on a queued config is accepted, not duplicated"""
        manager.update_begin_transaction('m')

        assert manager.worker_start('m', 'cfg')[0] is True
        assert manager.worker_start('m', 'cfg')[0] is True

        assert manager.state['cfg'].state == 'resuming'
        assert len(manager.state) == 1

    def test_frozen_restart_refuses(self, manager):
        """After the resume list is frozen a queued entry could never be
        resumed: the start is refused instead of being lost silently"""
        manager.update_begin_transaction('m')
        manager.restart_begin()
        assert manager.restart_wait(0.1) == (True, [])

        success, msg = manager.worker_start('m', 'cfg')

        assert success is False
        assert 'point of no return' in msg
        assert 'cfg' not in manager.state

    def test_queued_entry_is_collected_by_the_restart(self, manager):
        """An entry queued before the wait joins the resume list"""
        manager.update_begin_transaction('m')
        assert manager.worker_start('m', 'cfg')[0] is True

        manager.restart_begin()
        # the restart belongs to an update transaction: the workers are
        # parked in "updating" (the frontend shows 更新中), not "restarting"
        assert manager.state['cfg'].state == 'updating'
        assert manager.restart_wait(0.1) == (True, ['cfg'])

    def test_startup_gate_restart_parks_restarting(self, manager):
        """A restart outside an update transaction keeps "restarting" """
        UPDATE_STARTUP.reset()
        assert manager.worker_start('m', 'cfg')[0] is True

        manager.restart_begin()

        assert manager.state['cfg'].state == 'restarting'
        assert manager.restart_wait(0.1) == (True, ['cfg'])


class TestUpdateQueueRelease:
    """release_update_queue and the start of the released configs."""

    def test_release_lists_only_update_entries(self, manager):
        UPDATE_STARTUP.reset()
        manager.worker_start('m', 'cfg')
        # an entry of the auto-resume queue of a restart is not released
        assert manager.mark_resume(['other']) == ['other']

        assert manager.release_update_queue() == ['cfg']

    def test_release_then_resume(self, manager):
        """The released config starts through worker_resume, the flag is
        consumed with the entry"""
        UPDATE_STARTUP.reset()
        manager.worker_start('m', 'cfg')

        assert manager.release_update_queue() == ['cfg']
        success, msg = manager.worker_resume('m', 'cfg')

        assert success, msg
        assert manager.state['cfg'].state == 'starting'
        assert manager.state['cfg'].update_queued is False
        assert manager.release_update_queue() == []

    def test_release_without_a_window_is_empty(self, manager):
        assert manager.release_update_queue() == []


class TestUpdateWindowClose:
    """The close paths of the transaction window."""

    def test_stale_transaction_close_is_ignored(self, manager):
        """A close naming another mod must not close a newer window"""
        manager.update_begin_transaction('m')

        manager.update_end_transaction('other')

        assert manager.update_transaction == 'm'
        manager.update_end_transaction('m')
        assert manager.update_transaction == ''
