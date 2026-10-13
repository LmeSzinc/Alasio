"""
Restart manager: the single slot of the backend maintenance flows

The manager is the entry surface of the flow model: the rpc layer (restart /
cancel / update) and the daily task request a flow here, the lifespan runs it
with ``run()``. Requesting a flow while another one owns the backend is
*refused* with a user-facing reason (never queued, never joined): the user
retries, or the chain satisfies the request by itself (an update hands over
with a restart, a restart is followed by the startup update check). The
frontend disables the restart / update buttons while a flow is in flight, so
the refusals are the guard for the racy cases (two clicks, a click from a
second client).

The slot is the single source of truth (``restart_base``) and every entry
claims it in one synchronous section (no await between the judgment and the
claim), so two concurrent rpc calls can never both be accepted. The process
startup enters the slot as ``init`` first: only the startup flow may leave it,
so no user flow can sneak in between the process start and the startup flow.

The class layers of the package: ``RestartBase`` (the state of the slot and
its context managers) -> ``RestartStep`` (the single steps a flow drives) ->
``RestartManager`` (this module: the entries and ``run()``). The flows
(``restart_flow``) are objects the manager creates per run: each one owns its
state (the cancellation window above all) and its body (``Flow.run()``).
"""
from typing import Dict, Optional

import trio

from alasio.backend.app.restart.restart_base import INIT_FLOW, REFUSALS, STARTING_UP, Flow, FlowRefused, InitFlow
from alasio.backend.app.restart.restart_flow import FLOW_CLASSES
from alasio.backend.app.restart.restart_step import RestartStep
from alasio.logger import logger


class RestartManager(RestartStep):
    """
    The single slot of the maintenance flows, its entries and its run

    ``run()`` is the lifespan entry: it owns the nursery the flows (and the
    resume queue) run in and keeps the manager alive for the requests until the
    process shuts down. The flows are objects the manager creates when it
    claims the slot (``create_flow``).
    """

    async def run(self):
        """
        Run the maintenance flows of this process (the lifespan entry)

        The manager owns its nursery: the flow bodies (and the resume queue a
        flow spawns) are tasks of it, a failing flow ends alone and the lifespan
        nursery is never taken down by the maintenance. The slot enters ``init``
        in the same synchronous section as the nursery, then the startup flow is
        claimed -- the process is served by that flow from its first
        instruction.

        Returns when the process shuts down (the lifespan nursery is
        cancelled); a failure of the nursery is reported, the backend keeps
        running without a flow (the next restart / update request claims the
        slot normally).
        """
        try:
            async with trio.open_nursery() as nursery:
                # one synchronous section: no request can slip in between the
                # nursery and the init slot
                self.nursery = nursery
                self.enter_init()
                try:
                    self.request_startup()
                except Exception as e:
                    # the gate must never stay shut: without the startup flow
                    # the backend runs with the configs startable by hand
                    logger.error(f'[Restart] Failed to start the startup flow: {e}')
                    logger.exception(e)
                    self.abandon_startup()
                # the requests claim flows from any task of the backend (the
                # rpc layer, the daily task) while this task keeps the nursery
                await trio.sleep_forever()
        except trio.Cancelled:
            # the process is going down (the lifespan nursery was cancelled)
            raise
        except Exception as e:
            logger.error(f'[Restart] The restart manager stopped: {e}')
            logger.exception(e)
        finally:
            self.nursery = None

    def enter_init(self):
        """
        Enter the init slot (the process startup)

        Only the startup flow may leave it: a user flow claimed there would
        refuse the startup and leave the process without its update checks.
        """
        if self.current is None:
            self.claim(InitFlow())

    def abandon_startup(self):
        """
        Release the init slot when the startup flow could not start

        The backend then runs without a flow: the configs are startable by hand
        and the next restart / update request claims the slot normally.
        """
        if self.current is not None and self.current.name == INIT_FLOW:
            self.current = None
            self.publish_view()
            logger.warning('[Restart] The startup flow is not running, the backend has no '
                           'maintenance flow')

    # ------------------------------------------------------------------
    # Entries (trio thread: the judgment and the claim are one sync section)
    # ------------------------------------------------------------------

    def request_startup(self) -> Flow:
        """
        Claim the slot for the startup flow (the lifespan entry)

        The only transition out of the init slot; a startup requested while
        anything else owns the backend is an internal error (``run()`` is the
        only caller and it does that before every other entry).

        Returns:
            Flow: The claimed flow

        Raises:
            FlowRefused: When the slot is not in init
        """
        current = self.current
        if current is not None and not (current.name == INIT_FLOW):
            raise FlowRefused(f'The backend is already running the "{current.name}" flow')
        return self._claim('startup')

    def request_restart(self) -> Flow:
        """
        Claim the slot for a user restart

        Returns:
            Flow: The claimed flow

        Raises:
            FlowRefused: When another flow owns the backend (the message is the
                user-facing reason of REFUSALS['restart'])
        """
        return self._claim('restart', REFUSALS['restart'])

    def request_update(self) -> Flow:
        """
        Claim the slot for a user update

        The caller checks the precondition (at least one mod has an update, the
        Update topic knows it) before requesting; the manager owns the slot
        only.

        Returns:
            Flow: The claimed flow

        Raises:
            FlowRefused: When another flow owns the backend (the message is the
                user-facing reason of REFUSALS['update'])
        """
        return self._claim('update', REFUSALS['update'])

    def request_daily(self, now=None) -> "Optional[Flow]":
        """
        Claim the slot for the daily maintenance (the daily task entry)

        The daily action is a *due* decision (see
        ``alasio.backend.app.restart.daily``): an occupied slot is not an error.

        Args:
            now (datetime.datetime): Local time of the evaluation, defaults to
                the current time

        Returns:
            Flow | None: The claimed flow, None when the action is skipped --
                restart / update in flight upgrade themselves before their
                handover, a startup (or the init slot) means the day is already
                served by the starting process, a daily flow already runs
        """
        current = self.current
        if current is None:
            if self.nursery is None:
                # the process is not serving flows yet (the next evaluation
                # retries): nothing may be claimed before the startup flow
                return None
            return self._claim('daily')
        if current.name in (INIT_FLOW, 'startup'):
            # the process just started: its startup full update and its young
            # workers already serve both purposes of the daily action
            self.daily.mark_done(now)
            logger.info('[Restart] Daily action skipped: the backend is starting up')
        return None

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    async def cancel_restart(self):
        """
        Cancel the restart flow in flight (the wait for the workers to stop)

        The flow object owns the cancellation: it decides whether the cancel is
        still accepted (the window is open, the flow was not upgraded) and
        interrupts its own cancellable phase. The wrap-up (restore the queue,
        restart the checks) belongs to the flow task, which observes the
        cancellation at its next checkpoint.

        Raises:
            FlowRefused: When no restart is in flight, when it was already
                cancelled, or when it is past its point of no return
        """
        await self._require_flow('restart').cancel()

    async def cancel_update(self):
        """
        Cancel the update flow in flight (the stop and the download phase)

        Past the point of no return (the first mod is being written to disk) and
        after a daily upgrade the cancel is refused: the file replacement must
        not be interrupted half way, its own handover ends the flow.

        Raises:
            FlowRefused: When no update is in flight, when it was already
                cancelled, or when it is past its point of no return
        """
        await self._require_flow('update').cancel()

    # ------------------------------------------------------------------
    # Tasks of the manager nursery
    # ------------------------------------------------------------------

    def spawn(self, async_fn, *args):
        """
        Run a task in the manager nursery (the flows outside the slot)

        The resume queue is the task that matters here: a flow spawns it before
        releasing the slot, so the drain of the queue is not part of the flow
        (a new flow may take the slot over while it still runs).

        Args:
            async_fn (Callable): Coroutine function to run
            args: Arguments of the call

        Raises:
            RuntimeError: When the manager does not run
        """
        nursery = self.nursery
        if nursery is None:
            raise RuntimeError('The restart manager does not run (run() was not started)')
        nursery.start_soon(async_fn, *args)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _claim(self, name: str, refusal: "Optional[Dict[str, str]]" = None) -> Flow:
        """
        Take the slot for a flow and spawn its body (one synchronous section)

        Args:
            name (str): Flow name
            refusal (dict[str, str]): {current flow name: user-facing refusal}
                of this request, None for the internal entries

        Returns:
            Flow: The claimed flow

        Raises:
            FlowRefused: When the slot is occupied, or when the backend does not
                serve flows yet (no run())
        """
        current = self.current
        if current is not None:
            # the only transition out of init is the startup flow
            if not (current.name == INIT_FLOW and name == 'startup'):
                if refusal is not None:
                    message = refusal.get(current.name, STARTING_UP)
                else:
                    message = f'The backend is already running the "{current.name}" flow'
                raise FlowRefused(message)
        nursery = self.nursery
        if nursery is None:
            if refusal is None:
                raise RuntimeError('The restart manager does not run (run() was not started)')
            # the process is not serving flows yet (run() did not run): the
            # user entry answers like any startup window
            raise FlowRefused(STARTING_UP)
        flow = self.create_flow(name)
        self.claim(flow)
        nursery.start_soon(self._run_flow, flow)
        return flow

    def create_flow(self, name: str) -> Flow:
        """
        Create the flow object of a name (the claim runs it)

        The flow owns its state and its body: the manager only creates it, takes
        the slot with it and runs it (see ``alasio.backend.app.restart.
        restart_flow``).

        Args:
            name (str): Flow name (INIT_FLOW has no object: the placeholder is
                a plain Flow)

        Returns:
            Flow: The flow object

        Raises:
            RuntimeError: When the name has no flow class
        """
        cls = FLOW_CLASSES.get(name, None)
        if cls is None:
            raise RuntimeError(f'No flow class is registered for the flow "{name}"')
        return cls(self)

    async def _run_flow(self, flow: Flow):
        """
        Run the body of a flow under the slot, whatever happens

        The task boundary is the last line of defence of the backend: the
        nursery is the manager's own, an escaped error would end the flows for
        the rest of the process (the lifespan keeps running). The wrap-up of a
        failure (workers, checks, queue) belongs to the flow body, which runs it
        before propagating; a failure reaching here is reported and releases
        the slot.

        Args:
            flow (Flow): The flow to run
        """
        with self.slot(flow):
            try:
                await flow.run()
            except trio.Cancelled:
                # the process is going down (the manager nursery was cancelled)
                raise
            except Exception as e:
                logger.error(f'[Restart] Flow "{flow.name}" failed: {e}')
                logger.exception(e)

    def _require_flow(self, name: str) -> Flow:
        """
        The flow in flight with that name (the cancel entry)

        Args:
            name (str): Flow name the cancel addresses

        Returns:
            Flow: The flow to cancel

        Raises:
            FlowRefused: When no flow of that name is in flight (the flow
                itself answers why its cancel is refused otherwise, see
                ``Flow.cancel()``)
        """
        flow = self.current
        if flow is None or flow.name != name:
            raise FlowRefused(f'No {name} is in progress')
        return flow


# The process-wide manager, run by the lifespan
RESTART_MANAGER = RestartManager()
