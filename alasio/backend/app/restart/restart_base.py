"""
State of the backend maintenance flows and the context managers that write it

The backend has exactly one maintenance flow at a time. A flow is a coroutine
that owns the backend for its duration: it stops the workers, does its work
(update / none) and either hands the process over to a new one (a resume file
plus a supervisor restart) or wraps up and releases the backend again. The four
flows are:

- ``startup``: the process startup (consume the resume file, full update when
  the previous process did not just run one, start the update checks, release
  the resume queue);
- ``restart``: a user restart (stop the workers, hand over);
- ``update``: a user update (stop the workers, update the mods that have an
  update, hand over);
- ``daily``: the daily maintenance (stop the workers, full update, hand over).

This module owns the state of that slot and the context managers that write it.
The state is plain Python attributes: the sync world reads them directly (the
worker manager decides a start inside its own lock, the rpc layer answers a
request), no lock and no second copy of a flag to keep in sync -- the worker
start gate this replaces mirrored exactly these attributes. Only the trio
thread writes them.

The process startup enters the slot as ``init`` first: the only transition out
of it is the startup flow, so no user flow can sneak in between the process
start and the startup flow (a restart claimed there would refuse the startup
and leave the process without its update checks).

The rest of the package builds on this module: ``restart_step`` (the single
steps of a flow), ``restart_flow`` (the four flow classes, the bodies) and
``restart_manager`` (the entries and ``run()``, the lifespan entry).
"""
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Dict, Optional

import trio

from alasio.logger import logger

if TYPE_CHECKING:
    # For IDE typehint: the worker manager imports this package, so importing
    # it back at runtime would be a cycle
    from alasio.backend.worker.manager import WORKER_PARK_STATE

# The placeholder flow of the process startup: the slot is entered as init
# when the manager starts and the only transition out of it is the startup flow
INIT_FLOW = 'init'
# The four flows, mutually exclusive
FLOW_NAMES = ('startup', 'restart', 'update', 'daily')
# User-facing refusal of a flow request landing on an occupied slot
STARTING_UP = 'The backend is starting up, retry in a moment'
REFUSALS = {
    'restart': {
        INIT_FLOW: STARTING_UP,
        'startup': STARTING_UP,
        'restart': 'A restart is already in progress',
        'update': 'An update is in progress; the backend restarts by itself when it finishes',
        'daily': 'The daily maintenance is in progress (it includes the restart)',
    },
    'update': {
        INIT_FLOW: STARTING_UP,
        'startup': STARTING_UP,
        'update': 'An update is already in progress',
        'restart': 'A restart is in progress; the updates are checked and applied right after it',
        'daily': 'The daily maintenance is in progress (it includes the full update)',
    },
}


class FlowRefused(RuntimeError):
    """
    Raised when a flow request cannot be accepted right now, carrying the
    user-facing reason. The rpc layer translates it into a RpcValueError (the
    message reaches the frontend as-is); the daily task treats it as "not now"
    and lets the next evaluation retry.
    """


class Flow:
    """
    One maintenance flow: the state behind the slot, the body and the
    cancellable part of it

    A flow object is created when the flow is claimed and lives until its
    ``run()`` returned: the concrete flows (``restart_flow``) implement the body
    and declare the *cancellable phase* of it themselves (only a part of a flow
    may be interrupted, see ``cancel_window``).

    Class attributes (per flow):
        name (str): Flow name, one of FLOW_NAMES ('' on this base)
        PARK_STATE (WORKER_PARK_STATE): The state the workers stopped for this
            flow are parked in ('updating', 'restarting' for a plain restart):
            the frontend shows what the maintenance is doing

    Attributes (the state the sync world reads):
        started_at (float): Claim time (time.time())
        cancelled (bool): A cancel (or a force restart) was accepted: the flow
            must not continue past its next checkpoint
        cancellable (bool): Whether a cancel() would be accepted right now:
            the flow is inside its cancellation window
        upgraded (bool): Whether the flow was upgraded into the daily
            maintenance (it covers the daily action with its own handover)
    """
    name = ''
    PARK_STATE: "WORKER_PARK_STATE" = 'updating'

    def __init__(self, manager=None):
        """
        Args:
            manager (RestartStep): The manager the body drives (its steps) and
                the cancel wakes (via the worker manager); None in the tests
                that only exercise the flow state
        """
        self.manager = manager
        self.started_at = time.time()
        self.cancelled = False
        self.upgraded = False
        # whether the cancellation window is open (a cancel is accepted) and
        # whether it was left (the flow is past its point of no return); both
        # are False on a claimed flow that did not reach its window yet
        self._open = False
        self._irreversible = False
        # the reason a later cancel is refused with, given by the window
        self._past_reason = ''
        # the cancel scope of the cancellable phase, None outside the window
        self._window: "Optional[trio.CancelScope]" = None

    async def run(self):
        """
        Run the flow (the body, implemented by the concrete flows)

        The manager calls this as the body of the flow task: the flow stops the
        workers, does its work and either hands the process over (never
        returning in a way that matters, the process shuts down) or releases
        what it took before returning.
        """
        raise NotImplementedError(f'The flow "{self.name}" has no body')

    # ------------------------------------------------------------------
    # Read side (the rpc layer, the worker manager, the Restart topic)
    # ------------------------------------------------------------------

    @property
    def cancellable(self) -> bool:
        """
        Returns:
            bool: Whether a cancel would be accepted right now (the flow is
                inside its cancellation window and was not cancelled yet)
        """
        return bool(self._open and not self.cancelled)

    # ------------------------------------------------------------------
    # The cancellation window (run() opens it, cancel() interrupts it)
    # ------------------------------------------------------------------

    @contextmanager
    def cancel_window(self, past_reason: str = ''):
        """
        Open the cancellation window of this flow (the cancellable phase)

        The flow decides which part of its body may be interrupted::

            with self.cancel_window('the handover started'):
                ...the part a cancel may interrupt...

        The cancel scope of the phase belongs to the flow: a cancel() landing
        while the window is open interrupts the ``await`` in flight (the body
        continues after the ``with`` block and wraps up). Leaving the window --
        or closing it early (``close_window``, the point of no return inside
        the phase) -- makes the flow irreversible: a later cancel is refused.
        A flow with no cancellable part at all (the startup and the daily
        ones) opens no window and can never be cancelled.

        The flow view is published on both ends: the frontend offers the cancel
        entry only while the window is open.

        Args:
            past_reason (str): The reason a later cancel is refused with (the
                window is gone, the flow is past its point of no return, e.g.
                'the handover started'); '' leaves it out of the message
        """
        scope = trio.CancelScope()
        self._window = scope
        self._open = True
        self._past_reason = past_reason
        self.publish_view()
        try:
            with scope:
                yield
        finally:
            # the body left the window (or the process is going down)
            self._close(past_reason)
            if self._window is scope:
                self._window = None

    def close_window(self):
        """
        Close the cancellation window early (the point of no return inside the
        phase: the mods are about to be written)

        The window context manager closes the window on its way out too; this
        is for a body that must close it before the end of the ``with`` block.
        """
        self._close(self._past_reason)

    def _close(self, past_reason: str):
        """
        Mark the flow irreversible: a cancel from here on is refused

        Args:
            past_reason (str): The reason the refusal carries
        """
        if not self._open and self._irreversible:
            # already closed: keep the reason of the first close
            return
        self._open = False
        self._irreversible = True
        self._past_reason = past_reason or self._past_reason
        self.publish_view()

    def publish_view(self):
        """
        Ask the manager to publish the flow view (the cancel entry of the
        frontend follows the window); a flow without a manager has no topic
        """
        if self.manager is not None:
            self.manager.publish_view()

    def upgrade_to_daily(self):
        """
        Upgrade this flow into the daily maintenance (crossing the target time)

        The upgrade closes the cancellation window: the flow now covers the
        daily action (the same stop + full update + handover), interrupting it
        would leave the day unserved (the schedule would retrigger, but the
        workers were already stopped for nothing -- a daily action is done in
        one go).
        """
        self.upgraded = True
        self.close_window()

    # ------------------------------------------------------------------
    # Cancel (the user entry and the force restart path)
    # ------------------------------------------------------------------

    async def cancel(self):
        """
        Cancel this flow (the user cancel entry)

        Only a cancel landing inside the cancellation window is accepted (see
        ``cancel_window``): outside it the flow is either not doing anything
        cancellable yet or past its point of no return.

        Raises:
            FlowRefused: When the cancel cannot be accepted right now (the flow
                was already cancelled, it was upgraded into the daily
                maintenance, it did not reach its window yet, or it is past its
                point of no return)
        """
        refusal = self._cancel_refusal()
        if refusal:
            raise FlowRefused(refusal)
        await self.interrupt()

    async def interrupt(self):
        """
        Interrupt this flow: mark it cancelled and end its cancellable phase

        No validation (the force restart path interrupts whatever is in
        flight). The wait of the flow lives in a worker thread (the graceful
        stop can last minutes), a trio cancel cannot end it: the abort event of
        the worker manager is what wakes it, the cancel scope only ends the
        flow task once the wait returned. The wrap-up (the workers it stopped,
        the checks it paused) belongs to ``run()``, which observes the cancel
        at its next checkpoint.
        """
        self.cancelled = True
        self.close_window()
        if self.manager is not None:
            try:
                await trio.to_thread.run_sync(self.manager.worker_manager.restart_abort)
            except Exception as e:
                logger.warning(f'[Restart] Failed to wake the worker manager on a cancel: {e}')
        scope = self._window
        if scope is not None:
            scope.cancel()
        if self.manager is not None:
            await self.manager.push_view()

    def _cancel_refusal(self) -> str:
        """
        Returns:
            str: The user-facing reason a cancel is refused right now, '' when
                the cancel is accepted
        """
        if self.cancelled:
            return f'The {self.name} was already cancelled'
        if self.upgraded:
            return 'The daily maintenance (upgraded from this flow) cannot be cancelled'
        if not self._open:
            if self._irreversible:
                detail = f' ({self._past_reason})' if self._past_reason else ''
                return (f'The {self.name} is past its point of no return{detail} and cannot be '
                        f'cancelled')
            return f'The {self.name} has no cancellable phase to interrupt'
        return ''


class InitFlow(Flow):
    """
    The placeholder of the process startup

    The slot is entered as init when the manager starts: the only transition
    out of it is the startup flow, so no user flow can sneak in between the
    process start and the startup flow (a restart claimed there would refuse the
    startup and leave the process without its update checks).
    """
    name = INIT_FLOW


class RestartBase:
    """
    State of the maintenance flows of one backend process

    Plain Python attributes, readable from any thread (the trio thread is the
    only writer):

    Attributes:
        current (Flow | None): The flow in flight, None when the backend is
            idle. INIT_FLOW's Flow while the process is starting up (before
            the startup flow claims it)
        frozen (bool): True from the handover of a flow on: the resume intent
            is published and the process is about to be replaced, a worker
            started now could never be resumed (the worker manager refuses the
            start). Never cleared in the process it was set in (the next
            process starts with a fresh state), only a failed publication
            clears it (see handover_intent)
        nursery (trio.Nursery | None): The nursery run() owns the flow tasks
            with, None while the manager does not run (no flow can be claimed
            then)
    """

    def __init__(self):
        self.current: "Optional[Flow]" = None
        self.frozen = False
        self.nursery = None

    def reset(self):
        """
        Return to the initial state (tests: a fresh process has a fresh state)
        """
        self.current = None
        self.frozen = False
        self.nursery = None

    # ------------------------------------------------------------------
    # Read side: the rpc layer, the worker manager and the Restart topic
    # ------------------------------------------------------------------

    @property
    def flow_name(self) -> str:
        """
        Returns:
            str: Name of the flow in flight, '' when the backend is idle
        """
        flow = self.current
        return flow.name if flow is not None else ''

    @property
    def flow_running(self) -> bool:
        """
        Returns:
            bool: Whether a flow owns the backend right now (init included)
        """
        return self.current is not None

    @property
    def cancellable(self) -> bool:
        """
        Returns:
            bool: Whether the flow in flight may still be cancelled (the flow
                answers it: its window is open and it was not cancelled yet)
        """
        flow = self.current
        return bool(flow is not None and flow.cancellable)

    @property
    def upgraded(self) -> bool:
        """
        Returns:
            bool: Whether the flow in flight was upgraded into the daily
                maintenance
        """
        flow = self.current
        return bool(flow is not None and flow.upgraded)

    def view(self) -> "Dict[str, object]":
        """
        The flow view published on the Restart topic: the frontend disables the
        update / restart entries from it and shows the cancel entry of a
        cancellable flow.

        Returns:
            dict: {'flow', 'cancellable', 'upgraded'}
        """
        return {
            'flow': self.flow_name,
            'cancellable': self.cancellable,
            'upgraded': self.upgraded,
        }

    # ------------------------------------------------------------------
    # State context managers (trio thread)
    # ------------------------------------------------------------------

    def claim(self, flow: Flow):
        """
        Take the slot with `flow` (the entry of a request)

        One synchronous section: the judgment of a request and this claim never
        interleave with another request, so two concurrent rpc calls can never
        both be accepted.

        Args:
            flow (Flow): The flow claiming the slot
        """
        self.current = flow
        self.publish_view()
        logger.info(f'[Restart] Flow "{flow.name}" accepted')

    def release(self, flow: Flow):
        """
        Release the slot at the end of a flow

        Only the flow that still holds the slot releases it: a flow that was
        taken over (a force restart) finds `current` already moved on and
        leaves it alone.

        Args:
            flow (Flow): The flow that ended
        """
        if self.current is not flow:
            return
        self.current = None
        self.publish_view()
        logger.info(f'[Restart] Flow "{flow.name}" finished')

    @contextmanager
    def slot(self, flow: Flow):
        """
        Hold the slot with `flow` and release it on the way out

        The body of a flow runs inside this context manager: whatever it does
        (return early, fail, be cancelled), the slot is free afterwards -- the
        claim belongs to the request (``claim``, taken before the body task
        even starts), so an already claimed flow is not claimed twice here.

        Args:
            flow (Flow): The flow holding the slot
        """
        if self.current is not flow:
            self.claim(flow)
        try:
            yield flow
        finally:
            self.release(flow)

    @contextmanager
    def handover_intent(self):
        """
        Publish the resume intent: no worker start is accepted from here on

        The flag is cleared when the publication fails (the caller then
        withdraws the file and the backend keeps running): a frozen state
        without a handover would refuse every start forever.
        """
        self.frozen = True
        self.publish_view()
        try:
            yield
        except BaseException:
            self.frozen = False
            self.publish_view()
            raise

    # ------------------------------------------------------------------
    # View publishing (hook: the state does not know the Restart topic)
    # ------------------------------------------------------------------

    def publish_view(self):
        """
        Publish the flow view (hook)

        The state layer announces every change of the slot; the topic push
        belongs to the step layer (``RestartStep.publish_view`` spawns the
        task), the concrete managers inherit it. The base alone has nothing
        to publish.
        """
