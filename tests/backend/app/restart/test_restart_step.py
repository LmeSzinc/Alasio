"""
Tests for the single steps of the flows (restart_step.py)

The worker manager, the check module and the resume file IO are stand-ins: the
steps only drive their interface, the sequences (stop, hand over, resume the
queue, wrap up) are what these tests pin.
"""
import datetime

import pytest

from alasio.backend.app.restart import restart_step as step_module
from alasio.backend.app.restart.daily import DailyState
from alasio.backend.app.restart.restart_base import FlowRefused
from alasio.backend.app.restart.restart_flow import FlowDaily, FlowRestart, FlowUpdate
from alasio.backend.app.restart.restart_manager import RestartManager
from alasio.backend.app.restart.restart_step import HandoverFailed
from alasio.backend.app.restart.resume_file import OWNER_RESTART
from alasio.backend.topic.restart import RestartSource

NOW = datetime.datetime.now()


class FakeWorkerManager:
    """
    Worker manager stand-in: the stop sequence and the resume queue
    """

    def __init__(self):
        self.restarting = False
        self.park_state = 'restarting'
        self.begin_calls = 0
        self.wait_timeouts = []
        self.abort_calls = 0
        self.cancel_calls = 0
        self.begin_error = None
        self.wait_result = (True, ['cfg_running'])
        self.queued = []
        self.parked = []
        self.resumed = []
        self.killed = []
        self.dropped = []
        self.marked = []
        # stop the queue after the first start (let it be interrupted by a flow)
        self.stop_after_first = False

    def set_park_state(self, state):
        self.park_state = state

    def restart_begin(self):
        self.begin_calls += 1
        if self.begin_error is not None:
            raise self.begin_error
        self.restarting = True
        return ['cfg_running']

    def restart_wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        return self.wait_result

    def restart_abort(self):
        self.abort_calls += 1

    def restart_cancel(self):
        self.cancel_calls += 1
        self.restarting = False

    def parked_configs(self):
        return list(self.parked)

    def resume_intent(self):
        return list(self.queued)

    def mark_resume(self, configs):
        self.marked.extend(configs)
        return list(configs)

    def drop_resume(self, configs):
        self.dropped.extend(configs)
        return list(configs)

    def worker_resume(self, mod, config):
        self.resumed.append((mod, config))
        if self.stop_after_first:
            # a new flow took the backend over while the queue was draining
            self.restarting = True
        return True, ''

    def worker_force_kill(self, config, restart_resume=False):
        self.killed.append(config)
        return True, ''


class FakeFiles:
    """
    Resume file stand-in: records the publications and the withdrawals
    """

    def __init__(self):
        self.writes = []
        self.withdraws = 0
        self.write_error = None

    async def write(self, configs, owner=OWNER_RESTART, actions=None, updated_all_at=0.0):
        if self.write_error is not None:
            raise self.write_error
        self.writes.append({
            'configs': list(configs), 'owner': owner, 'actions': actions,
            'updated_all_at': updated_all_at})
        return 'token-checksum'

    async def withdraw(self):
        self.withdraws += 1
        return None


class FakeUpdateCheck:
    """
    Check module stand-in: the apply steps and the pause / restore discipline
    """

    def __init__(self, mods=None):
        self.mods = mods if mods is not None else {}
        self.states = []
        self.restores = 0
        self.releases = 0
        self.required = []

    def names(self, managed=True):
        return list(self.mods)

    def set_state(self, name, state, error=''):
        self.states.append((name, state, error))

    def restore(self, names=None):
        self.restores += 1

    def release_states(self):
        self.releases += 1

    def mark_restart_required(self, names, error=''):
        self.required.extend(names)


class FakeScan:
    """
    ConfigScanSource stand-in: fixed data, all refreshes recorded
    """

    data = {}
    forces = []

    def __init__(self):
        pass

    async def reinit(self, force=False):
        FakeScan.forces.append(force)


async def _no_restart():
    """The process restart, for the tests that never reach it"""
    return None


def make_manager(worker_manager=None, update_check=None, daily=None, files=None, restart_fn=None):
    """
    A manager of the real steps with injected stand-ins
    """
    return RestartManager(
        worker_manager=worker_manager if worker_manager is not None else FakeWorkerManager(),
        update_check=update_check if update_check is not None else FakeUpdateCheck(),
        daily=daily if daily is not None else DailyState(target=(0, 0)),
        files=files if files is not None else FakeFiles(),
        restart_fn=restart_fn if restart_fn is not None else _no_restart,
    )


@pytest.fixture(autouse=True)
def clean_state():
    """Drop the Restart topic and the fake scan state around every test"""
    RestartSource.singleton_clear()
    FakeScan.data = {}
    FakeScan.forces = []
    yield
    RestartSource.singleton_clear()


def phase():
    """
    The phase currently on the Restart topic
    """
    return RestartSource().data.get('phase', '')


class TestPushPhases:
    """The topic pushes, in and out of the guard of the flows"""

    @pytest.mark.trio
    async def test_push_and_clear(self):
        manager = make_manager()
        await manager.push_restart_phase('stopping')
        assert phase() == 'stopping'
        await manager.push_restart_phase('')
        assert RestartSource().data == {}

    @pytest.mark.trio
    async def test_queue_phase_guards_against_a_flow(self):
        manager = make_manager()
        assert await manager.push_queue_phase('resuming') is True
        assert phase() == 'resuming'
        # a stop in flight drops the queue phase
        manager.worker_manager.restarting = True
        assert await manager.push_queue_phase('done') is False
        assert phase() == 'resuming'
        # a handover in flight drops it too
        manager.worker_manager.restarting = False
        manager.frozen = True
        assert await manager.push_queue_phase('') is False
        assert phase() == 'resuming'


class TestStopAll:
    """The graceful stop of the workers"""

    @pytest.mark.trio
    async def test_stop_all_pushes_the_phase_and_returns_the_list(self):
        manager = make_manager()
        success, resume_list = await manager.stop_all(timeout=12.0)
        assert success is True
        assert resume_list == ['cfg_running']
        assert manager.worker_manager.begin_calls == 1
        assert manager.worker_manager.wait_timeouts == [12.0]
        # a flow that is not a restart parks the workers in "updating"
        assert manager.worker_manager.park_state == 'restarting'
        assert phase() == 'stopping'

    @pytest.mark.trio
    async def test_stop_all_parks_in_the_state_of_the_flow(self):
        manager = make_manager()
        await manager.stop_all(FlowUpdate(manager), timeout=1.0)
        assert manager.worker_manager.park_state == 'updating'
        await manager.stop_all(FlowRestart(manager), timeout=1.0)
        assert manager.worker_manager.park_state == 'restarting'

    @pytest.mark.trio
    async def test_stop_all_reports_a_manager_already_restarting(self):
        manager = make_manager()
        manager.worker_manager.begin_error = RuntimeError('Restart already in progress')
        success, resume_list = await manager.stop_all(timeout=1.0)
        assert (success, resume_list) == (False, [])
        assert phase() == ''

    @pytest.mark.trio
    async def test_stop_all_reports_a_cancelled_wait(self):
        manager = make_manager()
        manager.worker_manager.wait_result = (False, [])
        success, resume_list = await manager.stop_all(timeout=1.0)
        assert (success, resume_list) == (False, [])


class TestHandover:
    """The publication and the process restart"""

    @pytest.mark.trio
    async def test_handover_publishes_and_marks_the_daily_action(self):
        files = FakeFiles()
        daily = DailyState(target=(0, 0))
        daily.note_full_update(NOW.timestamp())
        calls = []

        async def restart_fn():
            calls.append('restart')

        manager = make_manager(daily=daily, files=files, restart_fn=restart_fn)
        await manager.handover(FlowRestart(manager), 'user request', ['cfg_a'])
        assert calls == ['restart']
        # the process is being replaced: the starts are refused from here on
        assert manager.frozen is True
        assert files.writes == [{
            'configs': ['cfg_a'], 'owner': OWNER_RESTART, 'actions': None,
            'updated_all_at': daily.updated_all_at}]
        assert daily.is_done() is True
        assert phase() == 'shutting-down'

    @pytest.mark.trio
    async def test_handover_without_a_full_update_marks_nothing(self):
        files = FakeFiles()
        daily = DailyState(target=(0, 0))
        manager = make_manager(daily=daily, files=files)
        await manager.handover(FlowDaily(manager), 'daily', [])
        assert daily.done_day is None
        assert files.writes[0]['updated_all_at'] == 0.0

    @pytest.mark.trio
    async def test_handover_failure_withdraws_and_unfreezes(self):
        files = FakeFiles()
        daily = DailyState(target=(0, 0))

        async def restart_fn():
            raise PermissionError('Cannot restart backend running without supervisor')

        manager = make_manager(daily=daily, files=files, restart_fn=restart_fn)
        with pytest.raises(HandoverFailed):
            await manager.handover(FlowUpdate(manager), 'update', ['cfg_a'])
        assert files.withdraws == 1
        # the backend keeps running: the starts must be accepted again
        assert manager.frozen is False
        assert len(files.writes) == 1

    @pytest.mark.trio
    async def test_a_publication_failure_unfreezes(self):
        files = FakeFiles()
        files.write_error = OSError('the log folder is gone')
        manager = make_manager(files=files)
        with pytest.raises(OSError):
            await manager.handover(FlowRestart(manager), 'user request', [])
        # nothing was published and the process is not being replaced
        assert manager.frozen is False
        assert files.withdraws == 0


class TestResumeQueue:
    """The auto-resume queue of the new backend"""

    @pytest.mark.trio
    async def test_empty_queue_is_a_noop(self):
        manager = make_manager()
        await manager.resume_queue()
        assert phase() == ''
        assert manager.worker_manager.resumed == []

    @pytest.mark.trio
    async def test_a_frozen_state_stops_the_queue(self):
        manager = make_manager()
        manager.worker_manager.queued = ['cfg_a']
        manager.frozen = True
        await manager.resume_queue()
        assert manager.worker_manager.resumed == []
        assert phase() == ''

    @pytest.mark.trio
    async def test_starts_the_queued_configs(self, monkeypatch):
        monkeypatch.setattr(step_module, 'WORKER_START_INTERVAL', 0.01)
        monkeypatch.setattr(step_module, 'ConfigScanSource', FakeScan)
        monkeypatch.setattr('alasio.backend.topic.worker.get_mod', lambda config: f'mod_{config}')
        FakeScan.data = {'cfg_a': True, 'cfg_b': True}
        manager = make_manager()
        manager.worker_manager.queued = ['cfg_a', 'cfg_b']
        await manager.resume_queue()
        assert manager.worker_manager.resumed == [('mod_cfg_a', 'cfg_a'), ('mod_cfg_b', 'cfg_b')]
        assert manager.worker_manager.dropped == []
        # one forced refresh decides whether the configs exist
        assert FakeScan.forces == [True]
        # the terminal phases are pushed (the topic is cleared at the end)
        assert RestartSource().data == {}

    @pytest.mark.trio
    async def test_missing_configs_are_dropped(self, monkeypatch):
        monkeypatch.setattr(step_module, 'WORKER_START_INTERVAL', 0.01)
        monkeypatch.setattr(step_module, 'ConfigScanSource', FakeScan)
        monkeypatch.setattr('alasio.backend.topic.worker.get_mod', lambda config: f'mod_{config}')
        FakeScan.data = {'cfg_a': True}
        manager = make_manager()
        manager.worker_manager.queued = ['cfg_a', 'cfg_gone']
        await manager.resume_queue()
        assert manager.worker_manager.resumed == [('mod_cfg_a', 'cfg_a')]
        assert manager.worker_manager.dropped == ['cfg_gone']

    @pytest.mark.trio
    async def test_a_new_flow_interrupts_the_queue(self, monkeypatch):
        monkeypatch.setattr(step_module, 'WORKER_START_INTERVAL', 0.01)
        monkeypatch.setattr(step_module, 'ConfigScanSource', FakeScan)
        monkeypatch.setattr('alasio.backend.topic.worker.get_mod', lambda config: f'mod_{config}')
        FakeScan.data = {'cfg_a': True, 'cfg_b': True}
        manager = make_manager()
        manager.worker_manager.queued = ['cfg_a', 'cfg_b']
        manager.worker_manager.stop_after_first = True
        await manager.resume_queue()
        # the first config started, the second belongs to the new flow
        assert manager.worker_manager.resumed == [('mod_cfg_a', 'cfg_a')]
        assert phase() == 'resuming'

    @pytest.mark.trio
    async def test_scan_failure_falls_back_to_the_current_data(self, monkeypatch):
        monkeypatch.setattr(step_module, 'WORKER_START_INTERVAL', 0.01)
        monkeypatch.setattr('alasio.backend.topic.worker.get_mod', lambda config: f'mod_{config}')

        class FailingScan(FakeScan):
            async def reinit(self, force=False):
                raise OSError('scan is broken')

        FakeScan.data = {'cfg_a': True}
        monkeypatch.setattr(step_module, 'ConfigScanSource', FailingScan)
        manager = make_manager()
        manager.worker_manager.queued = ['cfg_a']
        await manager.resume_queue()
        assert manager.worker_manager.resumed == [('mod_cfg_a', 'cfg_a')]


class FakeDeployJob:
    """DeployJob stand-in: reports the phases and the given outcome"""

    def __init__(self, changed=False, completed=True, error=None):
        self.changed = changed
        self.completed = completed
        self.error = error

    async def update(self, on_phase=None):
        for phase in ('checking', 'downloading', 'updating'):
            assert await on_phase(phase) is True
        if self.error is not None:
            raise self.error
        return self.completed


class FakePerMod:
    """ModUpdateCheck stand-in of the apply step"""

    def __init__(self, name, job, states):
        self.name = name
        self._job = job
        self._states = states
        # the update source: None makes the mod unmanaged (the step skips it)
        self.mirrors = object() if job is not None else None

    async def job(self):
        return self._job

    def set_state(self, state, error='', **kwargs):
        self._states.append((self.name, state, error))


class TestUpdateMods:
    """The apply step: one deploy job per mod, phases, failure isolation"""

    @staticmethod
    def _make(jobs):
        """
        A manager whose check module carries the given jobs

        Args:
            jobs (dict[str, FakeDeployJob | None]): The job of every mod, None
                for a mod without a usable update source

        Returns:
            tuple: (the manager, the recorded states)
        """
        update_check = FakeUpdateCheck()
        for name, job in jobs.items():
            update_check.mods[name] = FakePerMod(name, job, update_check.states)
        return make_manager(update_check=update_check), update_check.states

    @pytest.mark.trio
    async def test_success_drives_the_phases_and_reports_the_change(self):
        manager, states = self._make({'m': FakeDeployJob(changed=True)})
        flow = FlowUpdate(manager)
        changed = await manager.update_mods(['m'], flow=flow)
        assert changed == ['m']
        assert states == [('m', 'checking', ''), ('m', 'downloading', ''), ('m', 'updating', ''),
                          ('m', 'uptodate', '')]

    @pytest.mark.trio
    async def test_the_first_write_closes_the_cancel_window(self):
        manager, _ = self._make({'m': FakeDeployJob(changed=True)})
        flow = FlowUpdate(manager)
        with flow.cancel_window('the mods are being written'):
            assert flow.cancellable is True
            await manager.update_mods(['m'], flow=flow)
            # the job reported its 'updating' phase: the real files are about
            # to be replaced and the flow cannot be cancelled any more
            assert flow.cancellable is False
        # leaving the window does not make it cancellable again
        assert flow.cancellable is False
        with pytest.raises(FlowRefused) as e:
            await flow.cancel()
        assert 'the mods are being written' in str(e.value)

    @pytest.mark.trio
    async def test_a_job_without_change_still_reports_uptodate(self):
        manager, states = self._make({'m': FakeDeployJob(changed=False)})
        assert await manager.update_mods(['m']) == []
        assert states[-1] == ('m', 'uptodate', '')

    @pytest.mark.trio
    async def test_a_failed_mod_is_isolated(self):
        manager, states = self._make({
            'a': FakeDeployJob(error=OSError('mirror is down')),
            'b': FakeDeployJob(changed=True),
        })
        changed = await manager.update_mods(['a', 'b'])
        assert changed == ['b']
        assert ('a', 'error', 'mirror is down') in states

    @pytest.mark.trio
    async def test_an_incomplete_job_is_an_error(self):
        manager, states = self._make({'m': FakeDeployJob(completed=False)})
        assert await manager.update_mods(['m']) == []
        assert states[-1] == ('m', 'error', 'Some files failed to update, retry the update')

    @pytest.mark.trio
    async def test_an_unknown_or_unmanaged_mod_is_skipped(self):
        manager, states = self._make({'m': None})
        await manager.update_mods(['m', 'nope'])
        assert states == []

    @pytest.mark.trio
    async def test_an_aborted_job_returns_the_mod_to_available(self):
        manager, states = self._make({'m': FakeDeployJob(error=step_module.UpdateAborted('cancelled'))})
        assert await manager.update_mods(['m']) == []
        assert states[-1] == ('m', 'available', '')

    @pytest.mark.trio
    async def test_full_update_notes_the_completion(self):
        manager, _ = self._make({'m': FakeDeployJob(changed=True)})
        assert await manager.full_update() == ['m']
        assert manager.daily.updated_all_at > 0

    @pytest.mark.trio
    async def test_the_daily_upgrade_runs_the_full_update_once(self):
        manager, _ = self._make({'m': FakeDeployJob(changed=True)})
        flow = FlowRestart(manager)
        assert await manager.maybe_upgrade_to_daily(flow) is True
        assert flow.upgraded is True
        first = manager.daily.updated_all_at
        assert first > 0
        # a second crossing does not upgrade again (and does not repeat the
        # full update of this process)
        assert await manager.maybe_upgrade_to_daily(flow) is False
        assert manager.daily.updated_all_at == first

    @pytest.mark.trio
    async def test_the_upgrade_is_skipped_when_the_day_is_served(self):
        manager, _ = self._make({'m': FakeDeployJob(changed=True)})
        manager.daily.mark_done()
        flow = FlowRestart(manager)
        assert await manager.maybe_upgrade_to_daily(flow) is False
        assert flow.upgraded is False


class TestWrapUp:
    """The release path of a flow that did not hand over"""

    @pytest.mark.trio
    async def test_wrap_up_restores_the_checks_and_spawns_the_resume_queue(self):
        manager = make_manager()
        spawned = []
        manager.spawn = lambda fn, *args: spawned.append((fn, args))
        await manager.wrap_up(FlowUpdate(manager), ['cfg_a'])
        assert manager.update_check.restores == 1
        assert manager.worker_manager.cancel_calls == 1
        assert manager.worker_manager.marked == ['cfg_a']
        # the queue is a task of the nursery, not part of the flow: the wrap-up
        # only asks for it
        assert spawned == [(manager.resume_queue, ())]

    @pytest.mark.trio
    async def test_wrap_up_releases_the_states_of_the_flow_in_flight(self):
        manager = make_manager()
        manager.spawn = lambda fn, *args: None
        await manager.wrap_up(FlowUpdate(manager), ['cfg_a'])
        # the pending states of the update steps are released before the check
        # sequences restart (the update of the last check is still pending)
        assert manager.update_check.releases == 1
        assert manager.update_check.restores == 1

    @pytest.mark.trio
    async def test_wrap_up_collects_the_parked_and_queued_configs(self):
        worker_manager = FakeWorkerManager()
        worker_manager.parked = ['cfg_parked']
        worker_manager.queued = ['cfg_queued']
        manager = make_manager(worker_manager=worker_manager)
        manager.spawn = lambda fn, *args: None
        await manager.wrap_up(FlowRestart(manager), ['cfg_stopped'])
        assert worker_manager.marked == ['cfg_parked', 'cfg_queued', 'cfg_stopped']


class TestForceRestart:
    """The emergency exit"""

    @pytest.mark.trio
    async def test_force_restart_interrupts_freezes_and_restarts(self):
        worker_manager = FakeWorkerManager()
        files = FakeFiles()
        calls = []

        async def restart_fn():
            calls.append('restart')

        manager = make_manager(worker_manager=worker_manager, files=files, restart_fn=restart_fn)
        reminder = FlowRestart(manager)
        manager.current = reminder
        await manager.force_restart()
        assert reminder.cancelled is True
        assert worker_manager.cancel_calls == 1
        assert manager.frozen is True
        assert files.withdraws == 1
        assert calls == ['restart']
        assert phase() == 'shutting-down'

    @pytest.mark.trio
    async def test_force_restart_without_a_supervisor_raises(self):
        files = FakeFiles()

        async def restart_fn():
            raise PermissionError('Cannot restart backend running without supervisor')

        manager = make_manager(files=files, restart_fn=restart_fn)
        with pytest.raises(PermissionError):
            await manager.force_restart()
        # the publication was withdrawn before the attempt
        assert files.withdraws == 1

    @pytest.mark.trio
    async def test_abort_releases_the_flow_and_clears_the_phase(self):
        files = FakeFiles()
        manager = make_manager(files=files)
        manager.current = FlowUpdate(manager)
        await manager.abort()
        assert manager.current.cancelled is True
        assert manager.worker_manager.cancel_calls == 1
        assert manager.frozen is True
        assert files.withdraws == 1
        # no phase survives the abort (the flow view is re-pushed as the flow
        # task ends)
        assert 'phase' not in RestartSource().data
