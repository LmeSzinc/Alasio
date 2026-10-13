"""
Tests for the entries and the run of the restart manager (restart_manager.py)

The flow bodies are fakes over the real slot: the manager owns the slot, the
accept / refuse semantics, the cancellation windows, the daily bookkeeping and
the run, not the work of the flows. Every fake body signals its start, holds
until the test releases it and signals its end, so the tests drive the slot
from the outside.
"""
import datetime

import pytest
import trio
import trio.testing

from alasio.backend.app.restart.daily import DailyState
from alasio.backend.app.restart.restart_base import FLOW_NAMES, INIT_FLOW, STARTING_UP, Flow, FlowRefused
from alasio.backend.app.restart.restart_flow import FLOW_CLASSES
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
    The manager with body-shaped fake flows: the slot, the entries, the flow
    state machine and run() are the real ones, the bodies only signal the test
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

    def create_flow(self, name):
        """
        A flow of the real state machine (the window, the cancel) whose body
        holds inside its cancellable window until the test releases it
        """
        manager = self

        class FakeBody(Flow):
            async def run(self):
                manager.started[name].set()
                with self.cancel_window('the handover started'):
                    await manager.hold[name].wait()
                manager.records.append(('cancelled' if self.cancelled else 'done', name))
                manager.finished[name].set()

        FakeBody.name = name
        return FakeBody(self)


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
    async def test_a_failing_body_releases_the_slot(self):
        class Failing(FakeManager):
            def create_flow(self, name):
                class BrokenBody(Flow):
                    async def run(self):
                        raise ValueError('the body failed')

                BrokenBody.name = name
                return BrokenBody(self)

        async with trio.open_nursery() as nursery:
            manager = Failing()
            nursery.start_soon(manager.run)
            await trio.testing.wait_all_tasks_blocked()
            # the failure was reported, the slot is free and the manager keeps
            # serving the requests
            assert manager.flow_name == ''
            manager.request_restart()
            assert manager.flow_name == 'restart'
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
    """The manager creates the flow object when it claims the slot"""

    def test_the_flow_object_is_created_by_name(self):
        manager = RestartManager()
        for name, cls in FLOW_CLASSES.items():
            flow = manager.create_flow(name)
            assert isinstance(flow, cls)
            assert flow.manager is manager
            assert flow.name == name

    def test_an_unknown_flow_name_is_an_internal_error(self):
        with pytest.raises(RuntimeError, match='No flow class'):
            RestartManager().create_flow('nope')

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
        manager.current = manager.create_flow('restart')
        manager.frozen = True
        manager.nursery = object()
        manager.reset()
        assert manager.current is None
        assert manager.frozen is False
        assert manager.nursery is None
