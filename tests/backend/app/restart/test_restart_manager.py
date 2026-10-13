"""
Tests for the entries and the run of the restart manager (restart_manager.py)

The flow bodies are fakes over the real slot: a fixture replaces ``Flow.run``
of the four classes (they signal the test instead of doing the work), the rest
of the manager -- the entries, the claim, the flow state machine and run() --
is the real one. Every fake body signals its start, holds until the test
releases it and signals its end, so the tests drive the slot from the outside.
"""
import datetime

import pytest
import trio
import trio.testing

from alasio.backend.app.restart.daily import DailyState
from alasio.backend.app.restart.restart_base import FLOW_NAMES, INIT_FLOW, STARTING_UP, FlowRefused
from alasio.backend.app.restart.restart_flow import FlowDaily, FlowRestart, FlowStartup, FlowUpdate
from alasio.backend.app.restart.restart_manager import RestartManager

NOW = datetime.datetime(2026, 10, 13, 4, 0)


class FakeWorkerManager:
    """
    Worker manager stand-in: the manager only wakes its graceful wait on a cancel
    """

    def __init__(self):
        self.abort_calls = 0

    def restart_abort(self):
        self.abort_calls += 1


class FakeManager(RestartManager):
    """
    The manager under test with the Restart topic push recorded

    The slot, the entries, the claim, the flow state machine and run() are the
    real ones: only the flow bodies are replaced (the ``fake_bodies`` fixture)
    and the topic push is recorded instead of published.
    """

    def __init__(self, daily=None, worker_manager=None):
        self.records = []
        self.started = {name: trio.Event() for name in FLOW_NAMES}
        self.hold = {name: trio.Event() for name in FLOW_NAMES}
        self.finished = {name: trio.Event() for name in FLOW_NAMES}
        self.views = []
        super().__init__(worker_manager=worker_manager if worker_manager is not None else FakeWorkerManager(),
                         daily=daily if daily is not None else DailyState(target=(3, 50)))

    async def push_view(self):
        """The Restart topic push, recorded instead of published"""
        self.views.append(self.view())


@pytest.fixture(autouse=True)
def fake_bodies(monkeypatch):
    """
    The body of every flow class replaced by one that signals the test

    The manager tests own the slot and the flow state machine, not the work of
    the flows: the fake body sets the start event of its flow, holds inside its
    cancellation window until the test releases it and records how the flow
    ended.
    """

    async def fake_run(self):
        manager = self.manager
        manager.started[self.name].set()
        with self.cancel_window('the handover started'):
            await manager.hold[self.name].wait()
        manager.records.append(('cancelled' if self.cancelled else 'done', self.name))
        manager.finished[self.name].set()

    for flow_cls in (FlowStartup, FlowRestart, FlowUpdate, FlowDaily):
        monkeypatch.setattr(flow_cls, 'run', fake_run)


class Harness:
    """
    A running manager with fake flow bodies

    Attributes:
        manager (FakeManager): The manager under test
        nursery (trio.Nursery): The nursery run() was started in
    """

    def __init__(self, nursery, daily=None):
        self.nursery = nursery
        self.manager = FakeManager(daily=daily)

    @classmethod
    async def start(cls, nursery, daily=None):
        """
        Run the manager and wait for it to claim the startup flow
        """
        h = cls(nursery, daily=daily)
        nursery.start_soon(h.manager.run)
        await trio.testing.wait_all_tasks_blocked()
        return h

    @property
    def records(self):
        return self.manager.records

    def release(self, name):
        self.manager.hold[name].set()

    async def run_idle(self):
        """
        Let the startup flow end: the backend is idle afterwards
        """
        await self.manager.started['startup'].wait()
        self.release('startup')
        await self.manager.finished['startup'].wait()
        await trio.testing.wait_all_tasks_blocked()
        assert self.manager.flow_name == ''


class TestRun:
    """The lifespan entry: the init slot and the manager nursery"""

    @pytest.mark.trio
    async def test_run_enters_init_and_claims_the_startup_flow(self):
        async with trio.open_nursery() as nursery:
            h = Harness(nursery)
            assert h.manager.flow_name == ''
            nursery.start_soon(h.manager.run)
            # run() enters its nursery, the init slot and the startup flow
            # claim in one synchronous section: the process is served from its
            # first instruction on
            await trio.testing.wait_all_tasks_blocked()
            assert h.manager.flow_name == 'startup'
            assert h.manager.current.name == 'startup'
            h.release('startup')
            await h.manager.finished['startup'].wait()
            await trio.testing.wait_all_tasks_blocked()
            assert h.manager.flow_name == ''
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_run_ends_with_the_nursery(self):
        manager = FakeManager()
        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            await trio.testing.wait_all_tasks_blocked()
            assert manager.nursery is not None
            nursery.cancel_scope.cancel()
        # the manager released its state on the way out
        assert manager.nursery is None

    @pytest.mark.trio
    async def test_requests_before_run_are_refused(self):
        manager = FakeManager()
        # the backend does not serve flows yet: the user answer is the startup
        # window, an internal entry is an error
        with pytest.raises(FlowRefused) as e:
            manager.request_restart()
        assert str(e.value) == STARTING_UP
        with pytest.raises(FlowRefused) as e:
            manager.request_update()
        assert str(e.value) == STARTING_UP
        assert manager.request_daily(now=NOW) is None
        with pytest.raises(RuntimeError, match='does not run'):
            manager.request_startup()
        with pytest.raises(RuntimeError, match='does not run'):
            manager.spawn(_never)

    @pytest.mark.trio
    async def test_spawn_runs_in_the_manager_nursery(self):
        async with trio.open_nursery() as nursery:
            h = await Harness.start(nursery)
            started = trio.Event()

            async def task():
                started.set()

            h.manager.spawn(task)
            await started.wait()
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_a_failing_body_releases_the_slot(self, monkeypatch):
        async def broken(self):
            raise ValueError('the body failed')

        # one body of the fixture replaced: the failure is reported and the
        # manager keeps serving the requests
        monkeypatch.setattr(FlowRestart, 'run', broken)
        async with trio.open_nursery() as nursery:
            h = await Harness.start(nursery)
            await h.run_idle()
            h.manager.request_restart()
            assert h.manager.flow_name == 'restart'
            await trio.testing.wait_all_tasks_blocked()
            # the slot is free again and the next request is served as usual
            assert h.manager.flow_name == ''
            assert h.manager.request_restart() is not None
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_abandon_startup_releases_the_init_slot(self):
        manager = FakeManager()
        manager.enter_init()
        assert manager.flow_name == INIT_FLOW
        manager.abandon_startup()
        assert manager.flow_name == ''
        # a second call is a no-op
        manager.abandon_startup()
        assert manager.flow_name == ''


async def _never(*args):
    """Task that does nothing (the manager is not running in that test)"""


class TestRefusals:
    """The refusal matrix of the user entries"""

    @pytest.mark.trio
    @pytest.mark.parametrize("current, requested, message", [
        ('startup', 'restart', STARTING_UP),
        ('startup', 'update', STARTING_UP),
        ('restart', 'restart', 'A restart is already in progress'),
        ('restart', 'update', 'A restart is in progress; the updates are checked and applied right after it'),
        ('update', 'update', 'An update is already in progress'),
        ('update', 'restart', 'An update is in progress; the backend restarts by itself when it finishes'),
        ('daily', 'restart', 'The daily maintenance is in progress (it includes the restart)'),
        ('daily', 'update', 'The daily maintenance is in progress (it includes the full update)'),
    ])
    async def test_refusal_matrix(self, current, requested, message):
        async with trio.open_nursery() as nursery:
            h = await Harness.start(nursery)
            if current != 'startup':
                await h.run_idle()
                getattr(h.manager, f'request_{current}')()
            await h.manager.started[current].wait()
            with pytest.raises(FlowRefused) as e:
                getattr(h.manager, f'request_{requested}')()
            assert str(e.value) == message
            # the flow in flight is untouched by the refused request
            assert h.manager.flow_name == current
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_a_claimed_flow_keeps_the_slot_until_its_body_ends(self):
        async with trio.open_nursery() as nursery:
            h = await Harness.start(nursery)
            await h.run_idle()
            h.manager.request_update()
            await h.manager.started['update'].wait()
            h.release('update')
            await h.manager.finished['update'].wait()
            await trio.testing.wait_all_tasks_blocked()
            assert h.manager.flow_name == ''
            assert h.records == [('done', 'startup'), ('done', 'update')]
            nursery.cancel_scope.cancel()


class TestCancel:
    """The cancellation windows of the cancellable flows"""

    @pytest.mark.trio
    async def test_cancel_restart_interrupts_the_flow(self):
        async with trio.open_nursery() as nursery:
            h = await Harness.start(nursery)
            await h.run_idle()
            h.manager.request_restart()
            await h.manager.started['restart'].wait()
            assert h.manager.cancellable is True
            await h.manager.cancel_restart()
            await h.manager.finished['restart'].wait()
            await trio.testing.wait_all_tasks_blocked()
            assert h.records == [('done', 'startup'), ('cancelled', 'restart')]
            assert h.manager.flow_name == ''
            assert h.manager.cancellable is False
            assert h.manager.worker_manager.abort_calls == 1
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_cancel_before_the_window_opened_is_refused(self):
        async with trio.open_nursery() as nursery:
            h = await Harness.start(nursery)
            await h.run_idle()
            h.manager.request_restart()
            # no await since the claim: the body did not open its window yet
            assert h.manager.flow_name == 'restart'
            assert h.manager.cancellable is False
            with pytest.raises(FlowRefused) as e:
                await h.manager.cancel_restart()
            assert str(e.value) == 'The restart has no cancellable phase to interrupt'
            # the flow is untouched and runs to its end
            h.release('restart')
            await h.manager.finished['restart'].wait()
            await trio.testing.wait_all_tasks_blocked()
            assert h.records == [('done', 'startup'), ('done', 'restart')]
            assert h.manager.flow_name == ''
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_cancel_refused_without_a_flow(self):
        async with trio.open_nursery() as nursery:
            h = await Harness.start(nursery)
            await h.run_idle()
            for cancel in (h.manager.cancel_restart, h.manager.cancel_update):
                with pytest.raises(FlowRefused) as e:
                    await cancel()
                assert 'is in progress' in str(e.value)
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_cancel_refused_for_another_flow(self):
        async with trio.open_nursery() as nursery:
            h = await Harness.start(nursery)
            await h.run_idle()
            h.manager.request_update()
            await h.manager.started['update'].wait()
            with pytest.raises(FlowRefused) as e:
                await h.manager.cancel_restart()
            assert str(e.value) == 'No restart is in progress'
            await h.manager.cancel_update()
            await h.manager.finished['update'].wait()
            await trio.testing.wait_all_tasks_blocked()
            assert ('cancelled', 'update') in h.records
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_cancel_refused_twice(self):
        async with trio.open_nursery() as nursery:
            h = await Harness.start(nursery)
            await h.run_idle()
            flow = h.manager.request_restart()
            await h.manager.started['restart'].wait()
            # the first cancel was accepted (the flow is winding down): the
            # second one is refused with the "already cancelled" message (the
            # flow is then gone and the refusal becomes "no restart")
            flow.cancelled = True
            with pytest.raises(FlowRefused) as e:
                await h.manager.cancel_restart()
            assert str(e.value) == 'The restart was already cancelled'
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_cancel_refused_after_the_upgrade(self):
        async with trio.open_nursery() as nursery:
            h = await Harness.start(nursery)
            await h.run_idle()
            flow = h.manager.request_restart()
            flow.upgrade_to_daily()
            assert flow.upgraded is True
            assert h.manager.upgraded is True
            assert h.manager.cancellable is False
            with pytest.raises(FlowRefused) as e:
                await h.manager.cancel_restart()
            assert str(e.value) == 'The daily maintenance (upgraded from this flow) cannot be cancelled'
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_cancel_refused_past_the_point_of_no_return(self):
        async with trio.open_nursery() as nursery:
            h = await Harness.start(nursery)
            await h.run_idle()
            h.manager.request_update()
            await h.manager.started['update'].wait()
            h.manager.current.close_window()
            with pytest.raises(FlowRefused) as e:
                await h.manager.cancel_update()
            assert str(e.value) == ('The update is past its point of no return '
                                    '(the handover started) and cannot be cancelled')
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_interrupt_stops_any_flow(self):
        async with trio.open_nursery() as nursery:
            h = await Harness.start(nursery)
            flow = h.manager.current
            await flow.interrupt()
            assert flow.cancelled is True
            assert flow.cancellable is False
            await h.manager.finished['startup'].wait()
            await trio.testing.wait_all_tasks_blocked()
            assert ('cancelled', 'startup') in h.records
            assert h.manager.flow_name == ''
            nursery.cancel_scope.cancel()


class TestDailyRequest:
    """The daily entry: claim an idle slot, skip (and mark) the others"""

    @pytest.mark.trio
    async def test_daily_claims_an_idle_slot(self):
        async with trio.open_nursery() as nursery:
            h = await Harness.start(nursery)
            await h.run_idle()
            flow = h.manager.request_daily(now=NOW)
            assert flow is not None and flow.name == 'daily'
            assert h.manager.cancellable is False
            await h.manager.started['daily'].wait()
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    @pytest.mark.parametrize("current, marked", [
        ('restart', False),
        ('update', False),
        ('daily', False),
        ('startup', True),
    ])
    async def test_daily_skipped_while_a_flow_runs(self, current, marked):
        async with trio.open_nursery() as nursery:
            h = await Harness.start(nursery)
            if current != 'startup':
                await h.run_idle()
                if current == 'daily':
                    h.manager.request_daily(now=NOW)
                else:
                    getattr(h.manager, f'request_{current}')()
            await h.manager.started[current].wait()
            assert h.manager.request_daily(now=NOW) is None
            assert h.manager.daily.is_done(NOW) is marked
            assert h.manager.flow_name == current
            nursery.cancel_scope.cancel()


class TestView:
    """The flow view published on the Restart topic"""

    @pytest.mark.trio
    async def test_the_view_follows_the_slot(self):
        async with trio.open_nursery() as nursery:
            h = await Harness.start(nursery)
            await h.run_idle()
            h.manager.request_restart()
            await trio.testing.wait_all_tasks_blocked()
            assert h.manager.views[-1] == {'flow': 'restart', 'cancellable': True, 'upgraded': False}
            h.manager.current.upgrade_to_daily()
            h.release('restart')
            await h.manager.finished['restart'].wait()
            await trio.testing.wait_all_tasks_blocked()
            assert h.manager.views[-1] == {'flow': '', 'cancellable': False, 'upgraded': False}
            nursery.cancel_scope.cancel()


class TestFlowCreation:
    """The claim builds the object of the flow class the entry names"""

    @pytest.mark.parametrize("entry, flow_cls", [
        ('request_startup', FlowStartup),
        ('request_restart', FlowRestart),
        ('request_update', FlowUpdate),
        ('request_daily', FlowDaily),
    ])
    def test_every_entry_claims_its_own_flow_class(self, entry, flow_cls):
        manager = RestartManager()
        # the daily entry claims an idle slot only while the manager runs
        manager.nursery = object()
        claimed = []
        manager._claim = lambda cls: claimed.append(cls)
        getattr(manager, entry)()
        assert claimed == [flow_cls]

    @pytest.mark.trio
    async def test_the_claim_builds_the_object_of_the_class(self):
        async with trio.open_nursery() as nursery:
            h = await Harness.start(nursery)
            flow = h.manager.current
            assert type(flow) is FlowStartup
            assert flow.manager is h.manager
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_every_claim_creates_a_new_flow(self):
        async with trio.open_nursery() as nursery:
            h = await Harness.start(nursery)
            await h.run_idle()
            first = h.manager.request_restart()
            await h.manager.started['restart'].wait()
            h.release('restart')
            await h.manager.finished['restart'].wait()
            await trio.testing.wait_all_tasks_blocked()
            # a flow object belongs to one run: the next claim builds a new one
            second = h.manager.request_restart()
            assert second is not first
            assert second.cancelled is False
            nursery.cancel_scope.cancel()


class TestStates:
    """The plain state the sync world reads"""

    def test_fresh_manager_is_idle(self):
        manager = RestartManager()
        assert manager.current is None
        assert manager.frozen is False
        assert manager.nursery is None
        assert manager.flow_name == ''
        assert manager.flow_running is False

    def test_reset(self):
        manager = RestartManager()
        manager.current = FlowRestart(manager)
        manager.frozen = True
        manager.nursery = object()
        manager.reset()
        assert manager.current is None
        assert manager.frozen is False
        assert manager.nursery is None
