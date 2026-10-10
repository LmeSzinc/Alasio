"""
Graceful backend restart orchestration

The old backend stops every worker gracefully (scheduler-stopping, waiting for
the current task to finish, escalating to a kill after a timeout), writes the
resume intent to a file and asks the supervisor to restart the process. The new
backend consumes the file -- only with the one-shot credential handed over
through the supervisor -- and starts the recorded workers again.

The runtime state (GRACEFUL_RESTART) and the resume file protocol live in
alasio.backend.app.restart_context; this module drives them:

- RestartWindow / open_restart_window: one restart transaction, driven step by
  step by its caller (the default driver is run_graceful_restart);
- request_graceful_restart: the public entry of the external triggers, refused
  while an update transaction or the startup window owns the backend;
- cancel_graceful_restart: withdraw a transaction in flight;
- run_startup: the auto-resume queue of the new backend.

The auto-resume queue of the new backend can be superseded by another graceful
restart (the latest user command wins): the restart gate makes mark_resume() /
worker_resume() refuse, the queue task ends early on that refusal, and the
marks it already created are re-collected by restart_begin() into the new
restart's resume list.
"""

from typing import List

import trio

from alasio.backend.app.lifespan import SHUTDOWN_EVENT, lifespan_restart
from alasio.backend.app.restart_context import GRACEFUL_RESTART, OWNER_RESTART
from alasio.backend.app.update_startup import UPDATE_STARTUP
from alasio.backend.mpipe.mpipe_backend import mpipe_backend
from alasio.backend.topic.restart import RestartSource
from alasio.backend.topic.scan import ConfigScanSource
from alasio.backend.ws.context import GLOBAL_CONTEXT
from alasio.logger import logger

# Seconds to wait for the workers to stop gracefully before killing the
# remaining ones (R3: maximum wait of the graceful stop)
GRACEFUL_STOP_TIMEOUT = 600.0
# Interval between two worker starts of the auto-resume queue (measured from
# the previous worker_resume return; the queue exists to avoid spawning dozens
# of processes and device connections in the same instant)
WORKER_START_INTERVAL = 1.0


class RestartInProgress(RuntimeError):
    """
    Raised when a graceful restart is requested while one already owns the
    backend (the process-wide gate of GRACEFUL_RESTART.running). The refusal
    is a plain outcome of request_graceful_restart() for the scheduler; the
    rpc handler translates it into the user-facing RpcValueError
    ('Restart already in progress').
    """

    def __init__(self):
        super().__init__('Graceful restart already in progress')


class RestartUnavailable(RuntimeError):
    """
    Raised when a restart cannot start now for a reason other than another
    restart: an update transaction owns the backend (only its own apply may
    request the restart it ends in), or the backend is still starting up (the
    startup convergence and the first update checks). The rpc handler
    translates it into an RpcValueError carrying the reason; the daily
    schedule skips its trigger.
    """


# =============================================================================
# Restart topic phase
# =============================================================================

def _push_restart_phase(phase: str):
    """
    Push the restart phase to the Restart topic (blocking, thread pool only)

    Args:
        phase (str): '' clears the topic, otherwise 'stopping' /
            'shutting-down' / 'resuming' / 'done'
    """
    RestartSource().on_event(phase)


async def push_restart_phase(phase: str):
    """
    Push the restart phase to the Restart topic (async wrapper)

    The source event entry is a sync function (it locks the source and queues
    the delivery), so it runs in the trio thread pool -- the event loop never
    executes it.

    Args:
        phase (str): '' clears the topic, otherwise 'stopping' /
            'shutting-down' / 'resuming' / 'done'
    """
    try:
        await trio.to_thread.run_sync(_push_restart_phase, phase)
    except Exception as e:
        # the phase is a progress hint, never let it break the restart
        logger.warning(f'[Restart] Failed to push phase "{phase}": {e}')


async def _push_resume_phase(manager, phase: str) -> bool:
    """
    Push a phase of the auto-resume queue, unless a restart owns the backend

    The queue only ever speaks about its own phases while no restart took the
    backend over: the restart gate is checked and the phase pushed inside one
    critical section of the takeover lock, the same section the orchestration
    takes to set the gate and push its first phase. The queue's phase therefore
    either lands before the new transaction's phases or is dropped -- it can
    never overwrite them (F6), whatever the thread pool schedules.

    Args:
        manager (WorkerManager): Manager whose restart gate decides
        phase (str): 'resuming' / 'done' / '' (a queue phase)

    Returns:
        bool: True when the phase was pushed, False when a restart (or a
            cancel) owns the topic and the queue must stop here
    """
    async with GRACEFUL_RESTART._takeover_lock:
        if manager.restarting:
            return False
        await push_restart_phase(phase)
        return True


# =============================================================================
# Resume actions (carried by the resume file, run before the resume)
# =============================================================================

# Action tag -> runner. No shell: an action is a cleanup the new backend runs
# before the auto-resume (the in-app update flow injects tags through
# write_resume(..., actions=[...]) and registers their runners here).
# No builtin action is registered: replacing the .py files of an updated
# package needs no bytecode cleanup, the interpreter drops a stale .pyc on its
# own (the .pyc header records the source mtime and size, a changed source is
# recompiled).
RESUME_ACTIONS = {}


def run_resume_actions(actions):
    """
    Run the actions recorded in the resume file (best effort, no shell)

    Args:
        actions (list[str]): Action tags
    """
    for action in actions:
        runner = RESUME_ACTIONS.get(action, None)
        if runner is None:
            logger.warning(f'[Restart] Unknown resume action ignored: {action}')
            continue
        logger.info(f'[Restart] Running resume action: {action}')
        try:
            runner()
        except Exception as e:
            logger.error(f'[Restart] Resume action failed: {action}: {e}')


# =============================================================================
# Orchestration (old backend)
# =============================================================================

class RestartWindow:
    """
    One graceful restart transaction, driven step by step by its caller
:

        window = RestartWindow(manager)
        waiting = await window.begin()                # gate + stop requests + 'stopping'
        success, resume_list = await window.wait_stopped(GRACEFUL_STOP_TIMEOUT)
        await window.publish(resume_list, actions)    # resume file + credential
        # --- the caller's critical section (the update flow replaces the files
        #     here: every worker stopped, the backend still alive) ---
        await window.shutdown()                       # 'shutting-down' + backend restart

    The default driver run_graceful_restart() is exactly this sequence with an
    empty critical section; the update transaction drives the same steps itself
.

    The window is the transaction state of the orchestration: begin() opens the
    manager gate, wait_stopped() freezes the resume list (from then on the
    per-config cancels of that list are refused, F4), publish() writes the
    resume file and announces its credential inside the publication section (a
    cancel landing there is left to the withdrawal, F3), shutdown() hands the
    process over to the supervisor. cancel() withdraws the whole transaction
    (idempotent) and never interrupts a critical section already reached: a
    cancel landing after the publication lets the replace finish and marks the
    window withdrawn (its shutdown() is refused); a force restart or a backend
    stop drives the process exit itself.
    """

    def __init__(self, manager=GRACEFUL_RESTART.WORKER_MANAGER):
        """
        Args:
            manager (WorkerManager): Manager to drive, defaults to
                GRACEFUL_RESTART.WORKER_MANAGER (the process singleton,
                injectable for tests)
        """
        self.manager = manager
        # the resume list of the wait, for the shutdown log
        self.resume_list: "List[str]" = []
        # the window was withdrawn (a cancel interrupted it, or the caller
        # cancelled it): the shutdown is refused, the process keeps running
        self.aborted = False

    async def begin(self) -> "List[str]":
        """
        Open the transaction: the gate, the graceful stop requests and the
        first phase of the Restart topic.

        The takeover is one critical section of the takeover lock: the restart
        gate, the marks of a resume queue that is still being prepared or
        drained (restart_begin re-collects them) and this transaction's first
        phase. The preparation of the new backend takes the same lock, so it is
        waited for and the marks of an already consumed resume file always
        exist before the restart collects them (F6); a queue phase cannot
        overwrite the phases below either.

        Returns:
            List[str]: Sorted configs the wait will cover, for the caller's log

        Raises:
            Exception: restart_begin() failed (a restart already owns the
                manager, or nothing to begin): the caller releases the rpc
                flag and aborts
        """
        async with GRACEFUL_RESTART._takeover_lock:
            # blocking: snapshots the running workers and sends the graceful
            # stop requests over the worker pipes
            waiting = await trio.to_thread.run_sync(self.manager.restart_begin)
            await push_restart_phase('stopping')
            # the window is the transaction the cancel entry marks: registered
            # inside the section, so a cancel either lands before it (nothing
            # to mark, its abort event stops the wait instead) or finds it (the
            # critical section of the caller is left to run, the shutdown is
            # refused)
            GRACEFUL_RESTART.window = self
        return waiting

    async def wait_stopped(self, timeout=None) -> "tuple[bool, List[str]]":
        """
        Block for every worker to stop and freeze the resume list.

        The wait lives in the manager (thread-safe, no trio), the timeout
        escalation happens inside restart_wait and the abort event makes it
        return early. Its success flag is the whole verdict of the wait: a
        cancelled one (force restart / backend stop) must never write a resume
        file and never restart.

        Args:
            timeout (float): Seconds to wait for the graceful stop before
                escalating to a kill. None waits forever

        Returns:
            tuple[bool, List[str]]: (True, the frozen resume list) when the
                restart may continue, (False, []) when the wait was cancelled
        """
        return await trio.to_thread.run_sync(self.manager.restart_wait, timeout)

    async def publish(self, resume_list, actions=None):
        """
        Publish the resume intent: write the file and announce its credential.

        The file and its credential are one step inside the publication section
        of GracefulRestart.write_resume(): a cancel landing while the section is
        held waits for it and withdraws it, one landing before it cancels the
        caller at the lock acquire instead (F3). Only called after
        wait_stopped() succeeded: the list is frozen from here on and the file
        resumes it whatever a later per-config request does (F4).

        Args:
            resume_list (list[str]): Configs to auto-resume after the restart
            actions (list[str]): Optional action tags the new backend runs
                before the resume. Defaults to None
        """
        self.resume_list = list(resume_list)
        if resume_list or actions:
            await GRACEFUL_RESTART.write_resume(resume_list, OWNER_RESTART, actions)
        else:
            logger.info('[Restart] No worker to resume and no action to run, '
                        'restarting backend directly')

    async def shutdown(self) -> bool:
        """
        Enter the backend restart step: the terminal phase and the supervisor
        request.

        The success path does NOT release the gate: it stays until the process
        exits, so no worker is started (and lost) in between. The caller's
        critical section is over when this is called. A withdrawn window (a
        cancel landed while the caller was in its critical section) does not
        restart anything: the backend keeps running and the caller decides
        what happens to what it changed.

        Returns:
            bool: True when the restart was handed to the supervisor (the
                process is going down), False when the window was withdrawn
        """
        if self.aborted:
            logger.info('[Restart] The restart was withdrawn by a cancel, '
                        'the backend keeps running')
            return False
        try:
            await push_restart_phase('shutting-down')
            logger.info(f'[Restart] All workers stopped, restarting backend, '
                        f'resume list: {self.resume_list}')
            await lifespan_restart()
            return True
        finally:
            if GRACEFUL_RESTART.window is self:
                GRACEFUL_RESTART.window = None

    async def cancel(self, reason=''):
        """
        Withdraw the transaction: release the gate, remove the published resume
        file, retract its credential, clear the Restart topic (idempotent).

        The module-level cancel_graceful_restart() is the implementation (it is
        also reached by the force restart / backend stop entries); the window
        only binds it to its manager. The window is marked withdrawn: a
        shutdown after it is refused.

        Args:
            reason (str): Log message
        """
        self.aborted = True
        await cancel_graceful_restart(reason, self.manager)


async def request_graceful_restart(reason='', nursery=None):
    """
    Request a graceful restart of the backend (the public entry point)

    The single entry point of every external trigger: the settings page rpc
    (ConnState.restart) and the daily scheduled restart
    (alasio.backend.app.schedule) both go through it, so the preconditions
    and the re-entry guard exist once. The in-app update flow does not come
    through here: it drives its own window through open_restart_window(),
    the entry of the transaction that owns the backend.

    Two internal owners refuse the request: an update transaction
    in flight (GRACEFUL_RESTART.holder -- accepting an external trigger would
    race the job's 'updating' phase, which then fails against the gate) and
    the startup window of a new backend, until its own convergence / checks
    are over.

    The request only starts the orchestration task and returns immediately:
    the graceful stop can last up to GRACEFUL_STOP_TIMEOUT and the progress
    flows through the Restart and Worker topics while the rpc / scheduler
    task stays responsive.

    Args:
        reason (str): Who requests the restart, for the log
        nursery (trio.Nursery): Nursery to schedule the orchestration in.
            Defaults to the lifespan global nursery, injectable for tests

    Raises:
        PermissionError: When the backend runs without a supervisor (the
            restart could never come back)
        RestartInProgress: When a restart already owns the backend
        RestartUnavailable: When an update transaction or the startup window
            owns the backend
    """
    if not mpipe_backend:
        raise PermissionError('Cannot restart backend running without supervisor')
    # an update transaction owns the backend: only the transaction itself may
    # request the restart it ends in (through the internal entry); every
    # external trigger is refused meanwhile
    holder = GRACEFUL_RESTART.holder
    if holder:
        raise RestartUnavailable(
            f'The {holder} is in progress; '
            f'wait for it (it restarts the backend itself) or cancel it first')
    # the backend is still starting up (the startup convergence and the first
    # update checks own the instance): an external restart would race the
    # startup orchestration
    if not UPDATE_STARTUP.startup_over():
        raise RestartUnavailable('The backend is starting up, retry in a moment')
    if GRACEFUL_RESTART.running:
        raise RestartInProgress()
    if nursery is None:
        nursery = GLOBAL_CONTEXT.global_nursery
    logger.info(f'[Restart] Graceful restart requested, reason: {reason or "unspecified"}')
    # set the flag synchronously (no await in between): two concurrent
    # requests (a click and the daily schedule, or two clicks) cannot start
    # two orchestrations
    GRACEFUL_RESTART.running = True
    nursery.start_soon(run_graceful_restart, GRACEFUL_RESTART.WORKER_MANAGER)


async def open_restart_window(reason='', manager=GRACEFUL_RESTART.WORKER_MANAGER):
    """
    Open the restart window of the internal owner of the backend

    The internal entry point: the caller is the owner by construction (the
    update transaction registered in GRACEFUL_RESTART.holder, or the startup
    convergence of one), so neither the holder registry nor the startup window
    refuses it -- only a restart already in flight does. The gate, the graceful
    stop requests and the first phase of the Restart topic are done here; the
    caller drives the rest of the window itself:

        window = await open_restart_window('update of mod "m"')
        success, resume_list = await window.wait_stopped(GRACEFUL_STOP_TIMEOUT)
        await window.publish(resume_list)
        # --- the caller's critical section: the files may be replaced here ---
        await window.shutdown()

    Args:
        reason (str): Who requests the restart, for the log
        manager (WorkerManager): Manager to drive, defaults to the process
            singleton (injectable for tests)

    Returns:
        RestartWindow: The opened window (begin() succeeded)

    Raises:
        RestartInProgress: When a restart already owns the backend
        Exception: begin() failed (a restart already owns the manager, or
            nothing to begin): the rpc flag is released and the error travels
            to the caller, which withdraws its own transaction
    """
    if GRACEFUL_RESTART.running:
        raise RestartInProgress()
    logger.info(f'[Restart] Graceful restart requested, reason: {reason or "unspecified"}')
    # the flag is set synchronously (no await in between), like the public
    # entry: no other restart can start while this window lives
    GRACEFUL_RESTART.running = True
    window = RestartWindow(manager)
    try:
        await window.begin()
    except BaseException:
        # the transaction never began (or a step of the begin failed): the flag
        # must not stay set -- the caller is an update transaction still in
        # flight and its own failure handling (withdraw, state, checks) follows
        GRACEFUL_RESTART.running = False
        raise
    return window


async def run_graceful_restart(manager=GRACEFUL_RESTART.WORKER_MANAGER):
    """
    Graceful restart orchestration (old backend): the default driver

    Runs as a trio background task (the rpc returns immediately): the graceful
    stop can last up to GRACEFUL_STOP_TIMEOUT and the frontend keeps watching
    the worker states through the Worker topic while it waits.

    The default driver of the restart window: begin -> wait_stopped
    -> publish -> shutdown, with an empty critical section between the
    publication and the shutdown. The in-app update flow drives the same steps
    from its own task, with the file replacement as its critical section (see
    open_restart_window).

    The resume file is written once the wait is over -- never during it -- and
    the credential is announced inside that write. Every call into the blocking
    layer (the manager, the resume file IO, the restart topic push, the backend
    restart itself) goes through the trio thread pool, so the event loop stays
    responsive while the frontend watches the worker states through the Worker
    topic. On the success path the manager gate is kept (until the process
    exits): a worker started in the restart window would be killed at exit and
    never resumed.

    A queue of the previous restart that is still running (the backend is
    restarted again right after) is handed over by restart_begin() itself: the
    "resuming" marks it left are re-collected into this restart's resume list,
    and the queue task -- whose mark_resume() / worker_resume() are refused by
    the gate -- ends early on that refusal (the latest user command wins, F6).

    An error raised after the wait (the supervisor pipe gone) is handled
    exactly like a cancel -- the manager state is reset (the workers back to
    idle, a manual retry is possible), the resume file is withdrawn and the
    Restart topic is cleared -- and it never escapes this task: the task runs
    in the lifespan global nursery, where a raised error would cancel every
    other lifespan task and take the whole backend process down (the
    supervisor would count it as a crash instead of leaving the user with an
    idle backend). The backend stays alive and the error is reported through
    the logs (F5).

    Args:
        manager (WorkerManager): Manager to drive, defaults to
            GRACEFUL_RESTART.WORKER_MANAGER (the process singleton, injectable
            for tests)
    """
    with trio.CancelScope() as scope:
        GRACEFUL_RESTART.scope = scope
        window = RestartWindow(manager)
        try:
            try:
                waiting = await window.begin()
            except Exception as e:
                # already restarting (the rpc re-entry guard makes this
                # unreachable) or nothing to begin: release the rpc flag
                logger.error(f'[Restart] Graceful restart cannot begin: {e}')
                GRACEFUL_RESTART.running = False
                return

            # the waiting set of restart_begin() is the whole set this restart
            # waits for (the final resume list is the second element of the
            # restart_wait() result below)
            logger.info(
                f'[Restart] Graceful restart requested, '
                f'waiting up to {GRACEFUL_STOP_TIMEOUT:.0f}s for the workers to stop, '
                f'waiting for: {waiting}')

            # 1) block for every worker to stop; the wait lives in the manager
            #    (thread-safe, no trio), the timeout escalation happens inside
            #    restart_wait and the abort event makes it return early. Its
            #    success flag is the whole verdict of the wait: a cancelled one
            #    (force restart / backend stop) must never write a resume file
            #    and never restart
            success, resume_list = await window.wait_stopped(GRACEFUL_STOP_TIMEOUT)
            if not success:
                # cancelled while waiting (force restart / backend stop); the
                # cancel path already owns the state cleanup
                logger.info('[Restart] Graceful restart was cancelled')
                return

            # 2) the wait is over: publish the resume intent once (the file and
            #    its credential are one step; the credential is announced inside
            #    the write). A cancel landing while the section is held waits for
            #    it and withdraws it, one landing before it cancels this task at
            #    the lock acquire instead. The list is frozen from here on: a
            #    per-config cancel of a config of this list is refused from now
            #    on, because this file resumes it whatever the request does; a
            #    "restarting" entry outside the list carries no resume intent
            #    and stays cancellable
            await window.publish(resume_list)

            # 3) the critical section (empty here): every worker stopped, the
            #    backend still runs, the resume intent published

            # 4) all workers stopped: enter the existing backend restart step.
            #    The success path does NOT release the gate: it stays until the
            #    process exits, so no worker is started (and lost) in between
            await window.shutdown()
        except trio.Cancelled:
            # the cancel path (cancel_graceful_restart) owns the cleanup
            raise
        except Exception as e:
            # the failure is treated as a cancel, never raised: see the
            # docstring -- a raised error would travel through the lifespan
            # global nursery and kill the backend process
            logger.error(f'[Restart] Graceful restart failed: {e}')
            logger.exception(e)
            try:
                await window.cancel(f'graceful restart failed: {e}')
            except Exception as cancel_error:
                # every step of the cancel is already guarded; this only keeps
                # the orchestration task from raising under any circumstance
                logger.error(f'[Restart] Failed to cancel the failed restart: {cancel_error}')
            return
        finally:
            if GRACEFUL_RESTART.scope is scope:
                GRACEFUL_RESTART.scope = None


# =============================================================================
# Cancel
# =============================================================================

async def cancel_graceful_restart(reason: str = '', manager=GRACEFUL_RESTART.WORKER_MANAGER):
    """
    Cancel the graceful restart in progress (idempotent)

    Async: the rpc methods await it, the mpipe shutdown thread (lifespan.py)
    drives it through trio.from_thread.run(). Everything blocking inside (the
    manager reset, the resume file removal, the topic push) runs in the trio
    thread pool.

    Cancels the orchestration task (old backend) and the resume task (new
    backend), releases the manager gate, removes the resume file this
    transaction wrote (owner='restart' only: an update-owned file belongs to
    the update transaction) and clears the Restart topic.

    A cancellation is only logged when there was something to interrupt: a
    force restart (or a backend stop) of an idle backend cancels nothing and
    must not claim it cancelled a graceful restart.

    Args:
        reason (str): Log message
        manager (WorkerManager): Manager whose gate / marks are released,
            defaults to GRACEFUL_RESTART.WORKER_MANAGER
    """
    # the cleanup must complete even when the caller runs inside a cancelled
    # scope (the orchestration task being cancelled, a backend shutdown): every
    # await below is shielded
    with trio.CancelScope(shield=True):
        # the window of the transaction in flight (the driver's or the one the
        # update flow drives itself) is marked withdrawn: whatever the caller
        # does next, its shutdown() must not take the backend down any more (the
        # publication is withdrawn below, the gate released). A caller already
        # in its critical section is not interrupted: the cancel only takes the
        # restart away from it
        window = GRACEFUL_RESTART.window
        if window is not None:
            window.aborted = True
            GRACEFUL_RESTART.window = None
        # read before the cleanup below resets it: only a transaction actually
        # in flight reports the cancellation (the cleanup itself is idempotent
        # and runs unconditionally: it is the shutdown guarantee)
        in_progress = GRACEFUL_RESTART.restart_in_progress()
        # 1) cancel the orchestration / resume task: they must not continue with
        #    the restart or the resume queue. This runs on the trio thread (the
        #    rpc / trio.from_thread.run / the orchestration itself), so the
        #    cancel scopes are touched directly; a scope that already exited
        #    ignores the cancel. The waiting thread stops through the manager
        #    abort event below
        if GRACEFUL_RESTART.scope is not None:
            GRACEFUL_RESTART.scope.cancel()
        if GRACEFUL_RESTART.resume_scope is not None:
            GRACEFUL_RESTART.resume_scope.cancel()
        GRACEFUL_RESTART.running = False
        # the transaction is gone: the scope slot is dropped here (rather than
        # left to the orchestration's finally) so restart_in_progress() is false
        # as soon as the cancel ran; the finally then sees the slot moved on and
        # leaves it alone
        GRACEFUL_RESTART.scope = None
        # 2) the manager reset and the topic clear are one critical section of
        #    the takeover lock: a resume preparation still in flight finishes
        #    first (the scope was cancelled above, so the task stops at its next
        #    await inside the section and never creates a mark after the reset),
        #    a queue phase in flight lands before the clear, and a queue still
        #    waiting for the section finds the scope cancelled at its acquire.
        #    The section is bounded: the preparation is local file IO / memory
        async with GRACEFUL_RESTART._takeover_lock:
            try:
                await trio.to_thread.run_sync(manager.restart_cancel)
            except Exception as e:
                logger.warning(f'[Restart] Failed to cancel the manager state: {e}')
            # no restart in progress any more (inside the section: no queue phase
            # can land after it)
            await push_restart_phase('')
        # 3) withdraw the publication of this transaction: the lock is held by
        #    the tasks, so a publish still waiting for the section never happens
        #    (its task is cancelled at the acquire) and one in flight is left to
        #    run to completion, then removed and retracted. The retraction is the
        #    last word the supervisor hears, which makes a cancelled restart
        #    unresumable whatever the file does (F3)
        try:
            await GRACEFUL_RESTART.withdraw_resume()
        except Exception as e:
            logger.warning(f'[Restart] Failed to withdraw the resume publication: {e}')
        if in_progress:
            logger.info(f'[Restart] Graceful restart cancelled: {reason}')


# =============================================================================
# Resume (new backend)
# =============================================================================

def _resume_one(manager, config: str):
    """
    Resolve one queued config and start it (blocking, thread pool only)

    Failures are logged, not raised: one broken config must not stop the queue.

    Args:
        manager (WorkerManager): Worker manager
        config (str): Config name
    """
    try:
        from alasio.backend.topic.worker import get_mod
        mod = get_mod(config)
    except Exception as e:
        # config / mod gone: drop the queue entry (it must not block a manual
        # start), log and continue with the rest. drop_resume only touches an
        # entry still waiting: one a restart collected is not ours to drop
        logger.error(f'[Restart] Resume dropped, cannot load config "{config}": {e}')
        manager.drop_resume([config])
        return
    try:
        success, msg = manager.worker_resume(mod, config)
    except Exception as e:
        # the spawn failed: the manager finalized the entry on the way out
        # (error / restarting), so the cleanup only ends what this attempt
        # created. restart_resume=True: an entry a new restart already
        # collected is not this attempt's to cancel -- the crashed startup
        # stays in the new resume list and the new backend retries it (F9)
        logger.error(f'[Restart] Resume failed: "{config}": {e}')
        manager.worker_force_kill(config, restart_resume=True)
        return
    if not success:
        # cancelled by the user, started by someone else, or superseded by a
        # new graceful restart (the entry is collected there)
        logger.info(f'[Restart] Resume skipped: "{config}": {msg}')
        return
    logger.info(f'[Restart] Worker resumed: {config}')


async def _release_startup_queue(manager, queued):
    """
    Resolve and start the startup queue: the configs marked by the resume
    file (queued) plus the starts accepted by the startup gate.

    The configs are resolved against the config scan (one forced refresh): a
    config the scan does not expose is dropped, never started -- starting it
    would recreate its file with the default settings, which the user never
    asked for. The release waits behind the update startup events of the mods
    to resume (the manager initialization and their first update checks)
    before it starts anything. Every phase of the queue is pushed
    under the takeover lock with the restart gate checked inside it: a
    restart that took the backend over drops the phase (its phases are the
    last word) and the queue ends here (F6).

    Args:
        manager (WorkerManager): Manager to drive
        queued (list[str]): Configs already marked by mark_resume()
    """
    window_queued = await trio.to_thread.run_sync(manager.release_update_queue)
    configs = list(queued)
    for config in window_queued:
        if config not in configs:
            configs.append(config)
    if not configs:
        logger.info('[Restart] Startup queue is empty, nothing to start')
        return
    source = ConfigScanSource()
    try:
        # the disk read runs in the thread pool (inside reinit)
        await source.reinit(force=True)
    except Exception as e:
        # the scan decides whether a config exists: a failing refresh must not
        # abandon the whole queue, the resolution below falls back to the data
        # the source currently holds
        logger.error(f'[Restart] Config scan refresh failed: {e}')
    data = source.data
    resolved = [config for config in configs if config in data]
    # drop the still queued marks of the missing configs (an entry a new
    # restart already collected is not ours to drop, drop_resume leaves it
    # alone)
    missing = [config for config in configs if config not in data]
    if missing:
        logger.warning(f'[Restart] Startup queue abandoned, configs not found: {missing}')
        await trio.to_thread.run_sync(manager.drop_resume, missing)
    if not resolved:
        logger.info('[Restart] Startup queue is empty, nothing to start')
        return
    # wait behind the update startup events of the mods to resume:
    # the manager initialization and the first update check of every one of
    # them. The mods the update manager never registered are not gated
    from alasio.backend.topic.worker import get_mod
    names = []
    for config in resolved:
        try:
            name = get_mod(config)
        except Exception:
            continue
        if name and name not in names:
            names.append(name)
    if names:
        await UPDATE_STARTUP.wait_ready(names)
    if not await _push_resume_phase(manager, 'resuming'):
        logger.info('[Restart] Startup queue interrupted by a new graceful restart')
        return
    logger.info(f'[Restart] Startup queue: {len(resolved)} configs, '
                f'starting with {WORKER_START_INTERVAL}s interval')
    for config in resolved:
        if SHUTDOWN_EVENT.is_set():
            logger.info(f'[Restart] Startup queue interrupted by the shutdown: {config}')
            return
        if manager.restarting:
            # the new restart owns the queue (its phases are the last word)
            return
        # blocking (mod resolution + process spawn) -> thread pool
        await trio.to_thread.run_sync(_resume_one, manager, config)
        # interval between two starts, measured from the worker_resume return
        # (do not wait for the worker to reach "running")
        await trio.sleep(WORKER_START_INTERVAL)
    # the terminal phases, refused together when a restart took the backend
    # over while the last config was starting: 'done' is transient, "phase
    # present" means "a restart is in progress" for the frontend
    if not await _push_resume_phase(manager, 'done'):
        logger.info('[Restart] Startup queue interrupted by a new graceful restart')
        return
    await _push_resume_phase(manager, '')


async def _drop_startup_queue(manager, queued):
    """
    Drop every entry of the startup queue still waiting (the failure path).

    The resumes already started are not touched (drop_resume only handles the
    "resuming" entries); the entries a restart collected are not ours to drop
    either. The waiting entries return to idle: the configs are startable by
    hand again instead of being stuck queued for a release that will never
    come.

    Args:
        manager (WorkerManager): Manager to drive
        queued (list[str]): Configs marked by mark_resume() of this startup
    """
    window_queued = await trio.to_thread.run_sync(manager.release_update_queue)
    dropped = list(queued)
    for config in window_queued:
        if config not in dropped:
            dropped.append(config)
    if dropped:
        await trio.to_thread.run_sync(manager.drop_resume, dropped)


async def run_startup(manager=GRACEFUL_RESTART.WORKER_MANAGER):
    """
    Startup orchestration of the new backend

    1. consume the resume credential (the read deletes the file), clean the
       leftovers of dead sessions and mark the recorded configs as queued
       (they wait, they are not started yet);
    2. run the actions carried by the resume file;
    3. release: the marked configs and the starts accepted by the startup
       gate are resolved against the config scan; the release waits behind
       the update startup events of the mods (the manager initialization and
       their first update checks) and starts them one by one with
       WORKER_START_INTERVAL between two starts.

    Without a resume credential (no supervisor, cold start, killed session)
    nothing is read: the release then starts only the configs the frontend
    asked for while the startup gate was shut (accepted as queued resumes).

    The queue lives in the manager, so a new graceful restart can take it over
    (the latest user command wins, F6): restart_begin() re-collects its marks
    into the new resume list, and this task ends early as soon as it finds that
    the manager is restarting (mark_resume / worker_resume refuse then) -- it
    starts nothing under the gate and pushes no terminal phase over the phases
    of the new restart. The release of the queue is best effort (the starts
    left queued are visible in the Worker topic and a manual retry is
    possible).

    Every call into the blocking layer (the resume file read, the actions, the
    manager queue, the worker starts, the topic pushes) goes through the trio
    thread pool. The stale resume file cleanup is part of this task and runs
    after the read (never as an independent task: it must not race the file
    this backend consumes).

    Args:
        manager (WorkerManager): Manager to drive, defaults to
            GRACEFUL_RESTART.WORKER_MANAGER (the process singleton, injectable
            for tests)
    """
    queued = []
    with trio.CancelScope() as scope:
        GRACEFUL_RESTART.resume_scope = scope
        try:
            # 1) the preparation is one critical section of the takeover lock:
            #    read the resume file (the read deletes it), clean the leftovers
            #    of dead sessions and mark the configs in the manager. A graceful
            #    restart that begins meanwhile waits for the section, so the
            #    marks of a consumed resume file always exist before it collects
            #    them (F6); a cancel waits too and drops them in its own section
            async with GRACEFUL_RESTART._takeover_lock:
                record = None
                try:
                    record = await trio.to_thread.run_sync(GRACEFUL_RESTART.read_resume)
                except Exception as e:
                    # an unexpected failure of the read must not skip the housekeeping
                    logger.error(f'[Restart] Resume file read failed: {e}')
                    logger.exception(e)
                # the leftovers of sessions killed before their transaction
                # finished. Same task, strict order: the two must never run as
                # independent tasks (the cleanup would race the file just read)
                await trio.to_thread.run_sync(GRACEFUL_RESTART.resume_cleanup)
                if record is not None:
                    logger.info(f'[Restart] Resume intent accepted: {len(record.configs)} configs, '
                                f'{len(record.actions)} actions, owner={record.owner}')
                    # mark the recorded configs right away -- the manager owns
                    # the intent from here on. They are held until the release
                    # (step 5): the startup convergence and the first update
                    # checks run first. The manager refuses the queue
                    # while it is restarting: a restart that already took the
                    # backend over owns the resume list, this task has nothing
                    # to do
                    queued = await trio.to_thread.run_sync(manager.mark_resume, record.configs)
            # 2) actions carried by the resume file (an update cleanup): they
            #    run whenever a record was accepted, also when nothing was
            #    queued (an actions-only file)
            if record is not None and record.actions:
                await trio.to_thread.run_sync(run_resume_actions, record.actions)
            if manager.restarting:
                # a restart took the backend over (its own resume list owns
                # the queue): nothing to release here
                logger.info('[Restart] Startup interrupted by a graceful restart')
                return
            # 3) the release: the recorded configs and the starts accepted by
            #    the startup gate; it waits behind the update startup events
            #    of the mods (see _release_startup_queue)
            await _release_startup_queue(manager, queued)
        except trio.Cancelled:
            # the cancel path (cancel_graceful_restart) owns the cleanup: the
            # queued marks are dropped by the manager reset, nothing here
            raise
        except Exception as e:
            logger.error(f'[Restart] Startup failed: {e}')
            logger.exception(e)
            # release the entries still waiting so they do not block a manual
            # start (an entry a restart collected is not ours to drop); the
            # resumes already started keep running, only the waiting entries
            # are dropped
            try:
                await _drop_startup_queue(manager, queued)
            except Exception as drop_error:
                logger.error(f'[Restart] Startup cleanup failed: {drop_error}')
            # the phase of the queue is cleared unless a restart owns the topic
            await _push_resume_phase(manager, '')
        finally:
            if GRACEFUL_RESTART.resume_scope is scope:
                GRACEFUL_RESTART.resume_scope = None
