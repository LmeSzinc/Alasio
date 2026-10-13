"""
Tests for the state of the maintenance flows (restart_base.py)

The state is the single source of truth of the flow model: the slot, the frozen
resume intent, the context managers that write them, and the flow objects with
their cancellation windows. The sync world (the worker manager, the rpc layer)
reads the plain attributes, so the read side is what these tests pin.
"""
import pytest
import trio

from alasio.backend.app.restart.restart_base import (
    FLOW_NAMES, INIT_FLOW, STARTING_UP, Flow, FlowRefused, InitFlow, RestartBase
)


class FakeWorkerManager:
    """The worker manager side the cancel wakes (the wait runs in a thread)"""

    def __init__(self):
        self.aborts = 0

    def restart_abort(self):
        self.aborts += 1


class FakeManager:
    """The manager surface the flows use: the view push"""

    def __init__(self):
        self.worker_manager = FakeWorkerManager()
        self.views = 0

    def publish_view(self):
        """The synchronous entry of the step layer (it spawns the push there)"""
        self.views += 1

    async def push_view(self):
        self.views += 1


class FakeFlow(Flow):
    """A flow of the base tests declaring a cancellable phase (the real classes
    live in restart_flow)"""
    name = 'restart'
    PARK_STATE = 'restarting'

    def __init__(self, manager=None, past_reason='the handover started'):
        super().__init__(manager)
        self.past_reason = past_reason
        self.ran = False

    async def run(self):
        self.ran = True


class TestConstants:
    """The flow names and the exception of a refused request"""

    def test_flow_names(self):
        assert FLOW_NAMES == ('startup', 'restart', 'update', 'daily')
        assert INIT_FLOW not in FLOW_NAMES

    def test_flow_refused_is_a_runtime_error(self):
        assert issubclass(FlowRefused, RuntimeError)
        assert STARTING_UP


class TestFlow:
    """The flow object: the state and the body"""

    def test_the_base_has_no_body(self):
        base = Flow()
        assert base.name == ''
        with pytest.raises(NotImplementedError):
            trio.run(base.run)

    def test_defaults_of_a_cancellable_flow(self):
        flow = FakeFlow()
        assert flow.name == 'restart'
        assert flow.PARK_STATE == 'restarting'
        assert flow.upgraded is False
        assert flow.cancelled is False
        # a claimed flow is not cancellable yet: the body has not opened its
        # cancellation window
        assert flow.cancellable is False
        assert flow.manager is None

    def test_a_flow_that_is_never_cancellable(self):
        class Daily(Flow):
            name = 'daily'

        flow = Daily()
        assert flow.cancellable is False

    def test_the_default_park_state(self):
        assert Flow.PARK_STATE == 'updating'

    def test_the_base_carries_no_refusal(self):
        # the user entries (restart / update) answer a refusal of their own;
        # the base and the internal entries never do
        assert Flow.REFUSALS == {}

    def test_there_is_no_cancellable_flag_on_the_flow(self):
        # whether a flow may be cancelled is not a property of the flow kind:
        # only the window its body opens makes it cancellable
        assert not hasattr(Flow, 'CANCELLABLE')
        assert not hasattr(Flow, '_may_cancel')

    @pytest.mark.trio
    async def test_the_body_runs(self):
        flow = FakeFlow()
        await flow.run()
        assert flow.ran is True


class TestWindow:
    """The cancellation window: only the body of a flow declares it"""

    @pytest.mark.trio
    async def test_a_cancel_cancels_the_phase_in_flight(self):
        flow = FakeFlow(FakeManager())
        reached = []

        async def body():
            with flow.cancel_window('the handover started'):
                reached.append('entered')
                await trio.sleep_forever()
            reached.append('left')

        async with trio.open_nursery() as nursery:
            nursery.start_soon(body)
            await trio.testing.wait_all_tasks_blocked()
            await flow.cancel()
            await trio.testing.wait_all_tasks_blocked()
        # the cancel interrupted the sleep, the body continued after the window
        assert reached == ['entered', 'left']
        assert flow.cancelled is True

    @pytest.mark.trio
    async def test_a_cancel_outside_the_window_is_refused(self):
        flow = FakeFlow(FakeManager())
        # the flow was just claimed: nothing cancellable runs yet
        with pytest.raises(FlowRefused) as e:
            await flow.cancel()
        assert str(e.value) == 'The restart has no cancellable phase to interrupt'
        assert flow.cancelled is False

    @pytest.mark.trio
    async def test_leaving_the_window_is_irreversible(self):
        flow = FakeFlow(FakeManager())
        with flow.cancel_window('the handover started'):
            assert flow.cancellable is True
        # the body left its cancellable phase: the flow is past the point of
        # no return
        assert flow.cancellable is False
        with pytest.raises(FlowRefused) as e:
            await flow.cancel()
        assert str(e.value) == ('The restart is past its point of no return '
                                '(the handover started) and cannot be cancelled')

    @pytest.mark.trio
    async def test_close_window_is_the_point_of_no_return(self):
        flow = FakeFlow(FakeManager(), past_reason='the mods are being written')
        with flow.cancel_window(flow.past_reason):
            flow.close_window()
            assert flow.cancellable is False
            with pytest.raises(FlowRefused, match='the mods are being written'):
                await flow.cancel()
        # leaving the window later changes nothing
        assert flow.cancellable is False

    @pytest.mark.trio
    async def test_the_view_follows_the_window(self):
        manager = FakeManager()
        flow = FakeFlow(manager)
        with flow.cancel_window('the handover started'):
            pass
        # the cancel entry appears when the window opens and disappears when
        # it closes
        assert manager.views == 2

    @pytest.mark.trio
    async def test_the_window_closes_on_a_failure(self):
        flow = FakeFlow(FakeManager())
        with pytest.raises(ValueError):
            with flow.cancel_window('the handover started'):
                raise ValueError('the body failed')
        assert flow.cancellable is False

    @pytest.mark.trio
    async def test_a_cancelled_window_is_not_a_cancellable_flow_any_more(self):
        flow = FakeFlow(FakeManager())
        with flow.cancel_window('the handover started'):
            await flow.cancel()
        assert flow.cancellable is False
        assert flow.cancelled is True


class TestCancel:
    """The cancel entry of the flow object"""

    @pytest.mark.trio
    async def test_cancel_marks_and_wakes(self):
        manager = FakeManager()
        flow = FakeFlow(manager)
        entered = trio.Event()

        async def body():
            with flow.cancel_window('the handover started'):
                entered.set()
                await trio.sleep_forever()

        async with trio.open_nursery() as nursery:
            nursery.start_soon(body)
            await entered.wait()
            assert flow.cancellable is True
            await flow.cancel()
            await trio.testing.wait_all_tasks_blocked()
        assert flow.cancelled is True
        assert flow.cancellable is False
        assert manager.worker_manager.aborts == 1
        # the view was pushed as the window opened and as the cancel landed
        assert manager.views >= 1

    @pytest.mark.trio
    async def test_cancel_refused_twice(self):
        flow = FakeFlow(FakeManager())
        with flow.cancel_window('the handover started'):
            await flow.cancel()
            with pytest.raises(FlowRefused) as e:
                await flow.cancel()
            assert str(e.value) == 'The restart was already cancelled'

    @pytest.mark.trio
    async def test_cancel_refused_after_the_upgrade(self):
        flow = FakeFlow(FakeManager())
        with flow.cancel_window('the handover started'):
            flow.upgrade_to_daily()
            assert flow.upgraded is True
            with pytest.raises(FlowRefused) as e:
                await flow.cancel()
            assert str(e.value) == ('The daily maintenance (upgraded from this flow) '
                                    'cannot be cancelled')

    @pytest.mark.trio
    async def test_cancel_refused_past_the_point_of_no_return(self):
        class Update(Flow):
            name = 'update'

        flow = Update(FakeManager())
        with flow.cancel_window('the mods are being written'):
            flow.close_window()
        with pytest.raises(FlowRefused) as e:
            await flow.cancel()
        assert str(e.value) == ('The update is past its point of no return '
                                '(the mods are being written) and cannot be cancelled')

    @pytest.mark.trio
    async def test_cancel_refused_for_a_flow_without_a_window(self):
        class Daily(Flow):
            name = 'daily'

        flow = Daily(FakeManager())
        with pytest.raises(FlowRefused) as e:
            await flow.cancel()
        assert str(e.value) == 'The daily has no cancellable phase to interrupt'

    @pytest.mark.trio
    async def test_interrupt_without_validation(self):
        manager = FakeManager()
        flow = FakeFlow(manager)
        entered = trio.Event()
        left = trio.Event()

        async def body():
            with flow.cancel_window('the handover started'):
                entered.set()
                await trio.sleep_forever()
            left.set()

        async with trio.open_nursery() as nursery:
            nursery.start_soon(body)
            await entered.wait()
            # the force restart path does not ask whether the flow may be
            # cancelled: it ends the phase in flight
            await flow.interrupt()
            await left.wait()
        assert flow.cancelled is True
        assert manager.worker_manager.aborts == 1

    @pytest.mark.trio
    async def test_interrupt_without_a_manager(self):
        flow = FakeFlow()
        await flow.interrupt()
        assert flow.cancelled is True


class TestReadSide:
    """The plain attributes the sync world reads"""

    def test_idle(self):
        base = RestartBase()
        assert base.current is None
        assert base.frozen is False
        assert base.nursery is None
        assert base.flow_name == ''
        assert base.flow_running is False
        assert base.cancellable is False
        assert base.upgraded is False
        assert base.view() == {'flow': '', 'cancellable': False, 'upgraded': False}

    def test_init_slot(self):
        base = RestartBase()
        base.current = InitFlow()
        assert base.flow_name == INIT_FLOW
        assert base.flow_running is True
        assert base.cancellable is False

    @pytest.mark.trio
    async def test_cancellable_and_upgraded(self):
        base = RestartBase()
        flow = FakeFlow()
        base.current = flow
        # a claimed flow is not cancellable until its body opens the window
        assert base.cancellable is False
        with flow.cancel_window('the handover started'):
            assert base.cancellable is True
            assert base.view() == {'flow': 'restart', 'cancellable': True, 'upgraded': False}
        assert base.cancellable is False
        assert base.upgraded is False
        flow.upgrade_to_daily()
        assert base.upgraded is True
        assert base.cancellable is False
        assert base.view() == {'flow': 'restart', 'cancellable': False, 'upgraded': True}

    def test_reset(self):
        base = RestartBase()
        base.current = FakeFlow()
        base.frozen = True
        base.nursery = object()
        base.reset()
        assert base.current is None
        assert base.frozen is False
        assert base.nursery is None


class TestSlot:
    """The claim / release of the slot"""

    def test_claim_and_release(self):
        base = RestartBase()
        flow = FakeFlow()
        base.claim(flow)
        assert base.current is flow
        assert base.flow_name == 'restart'
        base.release(flow)
        assert base.current is None

    def test_release_only_its_own_flow(self):
        base = RestartBase()
        first = FakeFlow()
        second = FakeFlow()
        base.claim(first)
        # a flow that lost the slot (a force restart) does not release the one
        # that took it over
        base.claim(second)
        base.release(first)
        assert base.current is second

    def test_slot_releases_on_the_way_out(self):
        base = RestartBase()
        flow = FakeFlow()
        with base.slot(flow):
            assert base.current is flow
        assert base.current is None

    def test_slot_releases_on_a_failure(self):
        base = RestartBase()
        flow = FakeFlow()
        with pytest.raises(ValueError):
            with base.slot(flow):
                raise ValueError('the body failed')
        assert base.current is None

    def test_slot_claims_a_flow_that_was_not_claimed(self):
        base = RestartBase()
        flow = FakeFlow()
        with base.slot(flow):
            assert base.current is flow
        assert base.current is None

    def test_claim_publishes_the_view(self):
        published = []

        class Base(RestartBase):
            def publish_view(self):
                published.append(self.view())

        base = Base()
        flow = FakeFlow()
        base.claim(flow)
        base.release(flow)
        # the flow is not cancellable yet (its window is closed) and the slot is
        # free again afterwards
        assert published == [
            {'flow': 'restart', 'cancellable': False, 'upgraded': False},
            {'flow': '', 'cancellable': False, 'upgraded': False},
        ]


class TestHandoverIntent:
    """The freeze of the resume intent"""

    def test_success_keeps_the_state_frozen(self):
        base = RestartBase()
        with base.handover_intent():
            assert base.frozen is True
        # the process is being replaced: the flag stays
        assert base.frozen is True

    def test_failure_clears_it(self):
        base = RestartBase()
        with pytest.raises(ValueError):
            with base.handover_intent():
                raise ValueError('the restart cannot be requested')
        # the backend keeps running: a start must be accepted again
        assert base.frozen is False

    def test_publication_failure_clears_it(self):
        base = RestartBase()

        class WriteFailure(Exception):
            pass

        with pytest.raises(WriteFailure):
            with base.handover_intent():
                raise WriteFailure('the resume file cannot be written')
        assert base.frozen is False

    @pytest.mark.trio
    async def test_cancellation_clears_it(self):
        base = RestartBase()
        with trio.CancelScope() as scope:
            with base.handover_intent():
                # a cancellation is a failure like any other: the process is
                # not being replaced, the starts must be accepted again
                scope.cancel()
                await trio.sleep(0)
        assert scope.cancelled_caught is True
        assert base.frozen is False
