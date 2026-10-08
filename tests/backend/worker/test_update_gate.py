"""
Tests for the update window of WorkerManager (the start gate of the
update flow): a start accepted during an update window is queued as a
resume entry instead of being refused, and the window releases the queue
when it ends.

The manager never spawns here: the spawn tail of worker_start is replaced
by a marker, only the gate paths are exercised.
"""
import pytest

from alasio.backend.worker.manager import WorkerManager
from tests.backend.worker.test_restart_state import add_state


class NoSpawnManager(WorkerManager):
    """A manager whose process spawn is a marker (no real subprocess)."""

    def _worker_start_process(self, state, mod, config, child_conn, project_root='',
                              mod_root='', path_main=''):
        return True, 'spawned'


@pytest.fixture
def manager():
    """A fresh manager without process spawns"""
    WorkerManager.singleton_clear()
    mgr = NoSpawnManager()
    yield mgr
    try:
        mgr.close()
    except Exception as e:
        print(f'Warning: Error during cleanup: {e}')


class TestUpdateWindowGate:
    """worker_start under an update window."""

    def test_check_window_queues_the_start(self, manager):
        """A mod-level window (a check) accepts the start as a resume entry"""
        manager.update_lock_mods(['m'])

        success, msg = manager.worker_start('m', 'cfg')

        assert success is True
        assert 'queued' in msg
        state = manager.state['cfg']
        assert state.state == 'resuming'
        assert state.update_queued is True
        assert state.mod == 'm'

    def test_transaction_window_queues_every_start(self, manager):
        """An instance-level window (an update transaction) queues any mod"""
        manager.update_begin_transaction('m')

        assert manager.worker_start('other', 'cfg')[0] is True

        assert manager.state['cfg'].state == 'resuming'
        manager.update_end_transaction()
        assert manager.worker_start('other', 'cfg2')[0] is True
        assert manager.state['cfg2'].state == 'starting'

    def test_start_of_another_mod_is_not_gated(self, manager):
        """A window of mod m does not touch the configs of other mods"""
        manager.update_lock_mods(['m'])

        success, msg = manager.worker_start('other', 'cfg')

        assert (success, msg) == (True, 'spawned')
        assert manager.state['cfg'].state == 'starting'

    def test_running_config_is_not_queued_twice(self, manager):
        """An already running config is not the window's business"""
        add_state(manager, 'cfg', 'running')
        manager.update_lock_mods(['m'])

        success, msg = manager.worker_start('m', 'cfg')

        assert success is False
        assert 'already running' in msg
        assert manager.state['cfg'].state == 'running'

    def test_queued_config_stays_queued(self, manager):
        """A second click on a queued config is accepted, not duplicated"""
        manager.update_lock_mods(['m'])

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

    def test_plain_restart_parks_restarting(self, manager):
        """A restart outside an update transaction keeps "restarting"""
        manager.update_lock_mods(['m'])
        assert manager.worker_start('m', 'cfg')[0] is True

        manager.restart_begin()

        assert manager.state['cfg'].state == 'restarting'
        assert manager.restart_wait(0.1) == (True, ['cfg'])


class TestUpdateQueueRelease:
    """release_update_queue and the start of the released configs."""

    def test_release_lists_only_update_entries(self, manager):
        manager.update_lock_mods(['m'])
        manager.worker_start('m', 'cfg')
        manager.update_unlock_mods(['m'])
        # an entry of the auto-resume queue of a restart is not released
        assert manager.mark_resume(['other']) == ['other']

        assert manager.release_update_queue({'m'}) == ['cfg']
        assert manager.release_update_queue({'unknown'}) == []

    def test_release_then_resume(self, manager):
        """The released config starts through worker_resume, the flag is
        consumed with the entry"""
        manager.update_lock_mods(['m'])
        manager.worker_start('m', 'cfg')
        manager.update_unlock_mods(['m'])

        assert manager.release_update_queue({'m'}) == ['cfg']
        success, msg = manager.worker_resume('m', 'cfg')

        assert success, msg
        assert manager.state['cfg'].state == 'starting'
        assert manager.state['cfg'].update_queued is False
        assert manager.release_update_queue({'m'}) == []

    def test_release_without_a_window_is_empty(self, manager):
        assert manager.release_update_queue() == []
