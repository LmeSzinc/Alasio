"""
Tests for the flow classes (restart_flow.py)

The steps and the check module are stand-ins: the flows only sequence them, the
order (pause, stop, update, hand over / wrap up) and the states they drive are
what these tests pin.
"""
import datetime

import pytest

from alasio.backend.app.restart.daily import DailyState
from alasio.backend.app.restart.restart_base import FLOW_NAMES
from alasio.backend.app.restart.restart_flow import FLOW_CLASSES, FlowDaily, FlowRestart, FlowStartup, FlowUpdate
from alasio.backend.app.restart.restart_step import HandoverFailed
from alasio.backend.app.restart.resume_file import ResumeRecord

NOW = datetime.datetime.now()


class FakeCheck:
    """The verdict of one DeployJob.check() request"""

    def __init__(self, uptodate, local='v1', latest='v2'):
        self._uptodate = uptodate
        self.local = local
        self.latest = latest

    def uptodate(self):
        return self._uptodate


class FakeJob:
    def __init__(self, uptodate):
        self.uptodate_result = uptodate

    async def check(self):
        return FakeCheck(self.uptodate_result)


class FakePerMod:
    """The per-mod entry the pre-check of the update flow reads"""

    def __init__(self, uptodate=False):
        self.uptodate = uptodate
        self.states = []
        # a usable update source: the pre-check skips the mods without one
        self.mirrors = object()

    async def job(self):
        return FakeJob(self.uptodate)

    def set_state(self, state, **kwargs):
        self.states.append(state)


class FakeUpdateCheck:
    """
    The check module, recorded instead of executed
    """

    def __init__(self, available=('m',), uptodate=False):
        self.calls = []
        self.required = []
        self.mods = {'m': FakePerMod(uptodate)}
        self._available = list(available)

    def bind(self, mods=None):
        self.calls.append('bind')
        return self.mods

    def set_initial_states(self):
        self.calls.append('set_initial_states')

    def names(self, managed=True):
        return ['m']

    def start(self, names=None):
        self.calls.append('start')

    def pause(self, names=None):
        self.calls.append('pause')

    def restore(self, names=None):
        self.calls.append('restore')

    def available(self):
        return list(self._available)

    def set_state(self, name, state, error=''):
        self.calls.append(('set_state', name, state))

    def mark_restart_required(self, names, error=''):
        self.required.extend(names)


class FakeManager:
    """
    The manager surface the flows drive (the single steps), recorded instead of
    executed
    """

    def __init__(self, record=None, stop_result=(True, ['cfg_a']), changed=(), handover_error=None,
                 available=('m',), uptodate=False, target=(0, 0)):
        """
        Args:
            record (ResumeRecord): The resume intent the startup flow reads
            stop_result (tuple[bool, list[str]]): The result of stop_all
            changed (list[str]): The mods the update steps report as changed
            handover_error (Exception): Raised by the handover step
            available (list[str]): The mods with an available update
            uptodate (bool): The pre-check verdict of the available mods
            target (tuple[int, int]): The daily target time
        """
        self.calls = []
        self.record = record
        self.stop_result = stop_result
        self.changed = list(changed)
        self.handover_error = handover_error
        self.handed_over = []
        self.spawned = []
        self.views = 0
        self.update_check = FakeUpdateCheck(available=available, uptodate=uptodate)
        daily = DailyState(target=target)
        daily.note_full_update(NOW.timestamp())
        # a full update of this process covers today's daily action
        self.daily = daily

    # ------------------------------------------------------------------
    # The steps: recorded instead of executed
    # ------------------------------------------------------------------

    async def read_resume_intent(self):
        self.calls.append('read_resume_intent')
        return self.record

    async def queued_resume_list(self):
        self.calls.append('queued_resume_list')
        return ['cfg_q']

    async def full_update(self, flow=None):
        self.calls.append('full_update')
        return list(self.changed)

    async def update_mods(self, names, flow=None):
        self.calls.append(('update_mods', list(names)))
        return list(self.changed)

    async def stop_all(self, flow=None, timeout=None):
        self.calls.append(('stop_all', flow.name if flow is not None else ''))
        return self.stop_result

    async def maybe_upgrade_to_daily(self, flow):
        self.calls.append('maybe_upgrade_to_daily')
        return False

    async def handover(self, flow, reason, resume_list, actions=None):
        self.calls.append(('handover', reason, list(resume_list)))
        if self.handover_error is not None:
            raise self.handover_error
        self.handed_over.append(flow.name)

    async def wrap_up(self, flow, resume_list=None):
        self.calls.append(('wrap_up', sorted(resume_list or ())))

    async def resume_queue(self):
        """The task a flow spawns (the assertion compares the bound method)"""

    def spawn(self, async_fn, *args):
        self.spawned.append((async_fn, args))

    def publish_view(self):
        """The flow view push of the manager (the window announces itself)"""
        self.views += 1


class TestFlowRestart:
    """The user restart flow"""

    @pytest.mark.trio
    async def test_happy_path(self):
        manager = FakeManager()
        await FlowRestart(manager).run()
        assert manager.calls == [
            ('stop_all', 'restart'), 'maybe_upgrade_to_daily', ('handover', 'user request', ['cfg_a'])]
        assert manager.update_check.calls == ['pause']
        assert manager.handed_over == ['restart']

    @pytest.mark.trio
    async def test_cancelled_stop_wraps_up(self):
        manager = FakeManager(stop_result=(False, []))
        await FlowRestart(manager).run()
        assert manager.calls == [('stop_all', 'restart'), ('wrap_up', [])]
        assert manager.handed_over == []

    @pytest.mark.trio
    async def test_handover_failure_wraps_up_the_workers(self):
        manager = FakeManager(handover_error=HandoverFailed('no supervisor'))
        await FlowRestart(manager).run()
        assert manager.calls == [
            ('stop_all', 'restart'), 'maybe_upgrade_to_daily',
            ('handover', 'user request', ['cfg_a']), ('wrap_up', ['cfg_a'])]
        assert manager.handed_over == []

    @pytest.mark.trio
    async def test_the_window_closes_at_the_point_of_no_return(self):
        manager = FakeManager()
        flow = FlowRestart(manager)
        inside = []

        async def stop_all(one=None, timeout=None):
            inside.append(flow.cancellable)
            return True, ['cfg_a']

        manager.stop_all = stop_all
        await flow.run()
        # the wait for the workers is cancellable, the handover is not
        assert inside == [True]
        assert flow.cancellable is False
        assert manager.views == 2

    @pytest.mark.trio
    async def test_the_cancel_window_is_closed_before_the_handover(self):
        manager = FakeManager()
        flow = FlowUpdate(manager)
        seen = []

        async def handover(one, reason, resume_list, actions=None):
            seen.append(one.cancellable)

        manager.handover = handover
        await flow.run()
        assert seen == [False]

    @pytest.mark.trio
    async def test_the_upgrade_closes_the_window(self):
        manager = FakeManager()

        async def upgrade(flow):
            flow.upgrade_to_daily()
            return True

        manager.maybe_upgrade_to_daily = upgrade
        flow = FlowRestart(manager)
        await flow.run()
        assert flow.upgraded is True
        assert flow.cancellable is False


class TestFlowUpdate:
    """The user update flow"""

    @pytest.mark.trio
    async def test_noop_when_the_pre_check_finds_everything_up_to_date(self):
        manager = FakeManager(uptodate=True)
        await FlowUpdate(manager).run()
        assert manager.calls == [('wrap_up', [])]
        assert ('set_state', 'm', 'uptodate') in manager.update_check.calls
        assert manager.handed_over == []

    @pytest.mark.trio
    async def test_applies_and_hands_over(self):
        manager = FakeManager(changed=['m'])
        await FlowUpdate(manager).run()
        assert manager.calls == [
            ('stop_all', 'update'), ('update_mods', ['m']), 'maybe_upgrade_to_daily',
            ('handover', 'update', ['cfg_a'])]
        assert ('set_state', 'm', 'queued') in manager.update_check.calls
        assert ('set_state', 'm', 'stopping') in manager.update_check.calls
        assert manager.handed_over == ['update']

    @pytest.mark.trio
    async def test_noop_without_an_available_update(self):
        manager = FakeManager(available=())
        await FlowUpdate(manager).run()
        # no mod of the scope: no pre-check, the maintenance degenerates into
        # the stop + handover of a plain restart
        assert manager.calls == [
            ('stop_all', 'update'), ('update_mods', []), 'maybe_upgrade_to_daily',
            ('handover', 'update', ['cfg_a'])]

    @pytest.mark.trio
    async def test_handover_failure_marks_the_written_mods(self):
        manager = FakeManager(changed=['m'], handover_error=HandoverFailed('no supervisor'))
        await FlowUpdate(manager).run()
        assert manager.update_check.required == ['m']
        assert manager.calls[-1] == ('wrap_up', ['cfg_a'])

    @pytest.mark.trio
    async def test_the_pre_check_failure_does_not_stop_the_update(self):
        manager = FakeManager()

        class BrokenJob(FakeJob):
            async def check(self):
                raise OSError('mirror is down')

        async def broken_job():
            return BrokenJob(False)

        manager.update_check.mods['m'] = FakePerMod()
        manager.update_check.mods['m'].job = broken_job
        await FlowUpdate(manager).run()
        assert ('stop_all', 'update') in manager.calls


class TestFlowDaily:
    """The daily maintenance flow"""

    @pytest.mark.trio
    async def test_stops_updates_and_hands_over(self):
        manager = FakeManager(changed=['m'])
        await FlowDaily(manager).run()
        assert manager.calls == [
            ('stop_all', 'daily'), 'full_update', ('handover', 'daily', ['cfg_a'])]
        assert manager.update_check.calls == ['pause']
        assert manager.handed_over == ['daily']

    @pytest.mark.trio
    async def test_stop_cancelled_wraps_up(self):
        manager = FakeManager(stop_result=(False, []))
        await FlowDaily(manager).run()
        assert manager.calls == [('stop_all', 'daily'), ('wrap_up', [])]


class TestFlowStartup:
    """The process startup flow"""

    @pytest.mark.trio
    async def test_without_a_record_runs_the_full_update_and_starts_the_checks(self):
        manager = FakeManager()
        await FlowStartup(manager).run()
        assert manager.calls == ['read_resume_intent', 'full_update']
        assert manager.update_check.calls == ['bind', 'set_initial_states', 'start']
        assert manager.spawned and manager.spawned[0][0] == manager.resume_queue

    @pytest.mark.trio
    async def test_a_record_skips_the_full_update(self):
        record = ResumeRecord(ts=0.0, owner='restart', configs=['cfg_a'], actions=[],
                              updated_all_at=NOW.timestamp())
        manager = FakeManager(record=record)
        await FlowStartup(manager).run()
        assert manager.calls == ['read_resume_intent']
        # the previous process served the daily action of today
        assert manager.daily.is_done() is True
        assert manager.update_check.calls == ['bind', 'set_initial_states', 'start']

    @pytest.mark.trio
    async def test_a_convergence_hands_the_process_over(self):
        manager = FakeManager(changed=['m'])
        await FlowStartup(manager).run()
        assert manager.calls == ['read_resume_intent', 'full_update', 'queued_resume_list',
                                 ('handover', 'startup convergence', ['cfg_q'])]
        # nothing is started before the handover: the new process owns the checks
        assert manager.update_check.calls == ['bind', 'set_initial_states']
        assert manager.spawned == []

    @pytest.mark.trio
    async def test_a_failed_convergence_marks_and_wraps_up(self):
        manager = FakeManager(changed=['m'], handover_error=HandoverFailed('no supervisor'))
        await FlowStartup(manager).run()
        assert manager.update_check.required == ['m']
        assert manager.calls[-1] == ('wrap_up', [])

    @pytest.mark.trio
    async def test_a_failed_flow_wraps_up(self):
        manager = FakeManager()

        async def broken(flow=None):
            raise OSError('the disk is gone')

        manager.full_update = broken
        await FlowStartup(manager).run()
        assert manager.calls[-1] == ('wrap_up', [])


class TestRegistry:
    """The classes the manager creates the flow objects with"""

    def test_the_flow_classes_are_registered(self):
        assert FLOW_CLASSES == {
            'startup': FlowStartup,
            'restart': FlowRestart,
            'update': FlowUpdate,
            'daily': FlowDaily,
        }
        for name, cls in FLOW_CLASSES.items():
            assert cls.name == name
            assert name in FLOW_NAMES

    def test_cancellability_is_not_a_class_property(self):
        # only the window a body opens makes a flow cancellable: no flow class
        # declares it
        for cls in FLOW_CLASSES.values():
            assert not hasattr(cls, 'CANCELLABLE')
