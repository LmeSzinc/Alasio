import multiprocessing
import threading
import time
from multiprocessing.connection import Connection
from typing import List, Literal, Optional

import msgspec
from msgspec.msgpack import encode

from alasio.backend.worker.bridge import mod_entry
from alasio.backend.worker.event import DECODER_CACHE, CommandEvent, ConfigEvent
from alasio.ext.singleton import Singleton
from alasio.logger import logger

# idle: not running
# starting: requesting to start a worker, starting worker process
# running: worker process running
# scheduler-stopping: requesting to stop scheduler loop, worker will stop after current task
# scheduler-waiting: worker waiting for next task, no task running currently
# killing: requesting to kill a worker, worker will stop and do GC asap.
#   worker_kill() waits KILL_WAIT_TIMEOUT for the worker to stop by itself,
#   then escalates to force-killing
# force-killing: requesting to kill worker process immediately
# disconnected: backend just lost connection worker,
#   worker process will be clean up and worker status will turn into idle or error very soon
# error: worker stopped with error
#   Note that scheduler will loop forever, so there is no "stopped" state
#   If user request "scheduler_stopping" or "killing", state will later be "idle"
# restarting: worker stopped because of a graceful backend restart, the backend
#   will auto-resume it after the restart. The entry stays in the state dict
#   (occupies the config, manual start rejected) and restart_wait() collects it
#   into the resume list. Manual start is rejected.
# resuming: queued for auto-resume after the backend restart (no process yet).
#   The new backend builds the queue with mark_resume() and starts each entry
#   through worker_resume() with a 1s interval.
WORKER_STATE = Literal[
    'idle', 'starting', 'running', 'disconnected', 'error',
    'scheduler-stopping', 'scheduler-waiting',
    'killing', 'force-killing',
    'restarting', 'resuming',
]
# Allow worker set its state to one of the allows
WORKER_STATE_ALLOWS = ['running', 'scheduler-waiting']
# Worker is considered running if state in the followings
WORKER_RUNNING_STATE = ['running', 'scheduler-stopping', 'scheduler-waiting']
# Worker is considered stopped if state in the followings
WORKER_STOPPED_STATE = ['idle', 'error', 'restarting', 'resuming']

# Seconds to wait for a worker to stop by itself in worker_kill(),
# before the kill is escalated to worker_force_kill()
KILL_WAIT_TIMEOUT = 1.0

# Poll interval of restart_wait() while it blocks for the workers to stop.
# The wait itself is a 10-minute scale wait; the poll only has to keep the
# abort event (restart_cancel) and worker stops reasonably responsive.
RESTART_WAIT_POLL = 0.1


class WorkerState(msgspec.Struct):
    mod: str
    config: str
    state: WORKER_STATE
    update: float = 0.
    # True while the worker is stopped for a graceful backend restart: the
    # disconnect handler then turns it into "restarting" (instead of
    # idle/error) and restart_wait() collects the config into the resume list.
    # Cleared when the user cancels the pending resume (stop / kill with the
    # default restart_resume=False) or when the worker starts again.
    pending_restart: bool = False

    process: Optional[multiprocessing.Process] = None
    conn: Optional[Connection] = None
    running_event: threading.Event = msgspec.field(default_factory=threading.Event)
    stopped_event: threading.Event = msgspec.field(default_factory=threading.Event)
    recv_thread: Optional[threading.Thread] = None

    def set_state(self, state: WORKER_STATE):
        self.state = state
        self.update = time.time()
        if state in WORKER_RUNNING_STATE:
            self.running_event.set()
            self.stopped_event.clear()
        elif state in WORKER_STOPPED_STATE:
            self.running_event.clear()
            self.stopped_event.set()
        else:
            self.running_event.clear()
            self.stopped_event.clear()

    def send_command(self, command: CommandEvent):
        data = encode(command)
        try:
            conn = self.conn
            # Equivalent to  conn.send_bytes() but bypass all by
            conn._check_closed()
            conn._check_writable()
            conn._send_bytes(data)
            return True
        except AttributeError:
            # this shouldn't happen
            logger.warning(f'[WorkerManager] Failed to send command config="{self.config}", command={command}: '
                           f'pipe connection not initialized')
            return False
        except Exception as e:
            logger.warning(f'[WorkerManager] Failed to send command config="{self.config}", command={command}: {e}')
            return False

    def send_test_continue(self):
        event = CommandEvent(c='test-continue')
        return self.send_command(event)

    def wait_running(self, timeout: "float | None" = None):
        return self.running_event.wait(timeout)

    def wait_stopped(self, timeout: "float | None" = None):
        return self.stopped_event.wait(timeout)

    def conn_close(self):
        """
        Close pipe if pipe opened
        """
        conn = self.conn
        if not conn:
            return
        try:
            conn.close()
        except Exception:
            pass

    def process_join(self, timeout):
        process = self.process
        if process and process.is_alive():
            process.join(timeout)

    def process_terminate(self):
        """
        Close process if process started
        """
        process = self.process
        if not process:
            return
        if process.is_alive():
            logger.info(f'[WorkerManager] Terminating worker process: "{self.config}"')
            try:
                process.terminate()
            except Exception as e:
                logger.error(f'[WorkerManager] Error while terminating "{self.config}": {e}')

    def process_kill(self, timeout=1):
        """
        Close process if process started
        """
        process = self.process
        if not process:
            return
        if process.is_alive():
            logger.info(f'[WorkerManager] Force killing worker process: "{self.config}"')
            try:
                process.kill()
                process.join(timeout=timeout)
                # no luck
                if process.is_alive():
                    logger.info(f'[WorkerManager] Worker still alive after force-kill: "{self.config}"')
            except Exception as e:
                logger.error(f'[WorkerManager] Error while force-killing "{self.config}": {e}')

    def process_graceful_kill(self, terminate_timeout=1, kill_timeout=1):
        """
        Close process if process started
        """
        process = self.process
        if not process:
            return
        if process.is_alive():
            logger.info(f'[WorkerManager] Graceful killing process: "{self.config}"')
            try:
                # try graceful terminate() first
                process.terminate()
                process.join(timeout=terminate_timeout)
                if process.is_alive():
                    # then try force-kill
                    logger.info(f'[WorkerManager] Worker did not terminate, force killing process: "{self.config}"')
                    process.kill()
                    process.join(timeout=kill_timeout)
                # no luck
                if process.is_alive():
                    logger.info(f'[WorkerManager] Worker still alive after force-kill: "{self.config}"')
            except Exception as e:
                logger.error(f'[WorkerManager] Error while force-killing "{self.config}": {e}')


class WorkerManager(metaclass=Singleton):
    def __init__(self):
        self._lock = threading.Lock()

        # dict of worker state
        # if config not in self.state, its status is default to "idle"
        self.state: "dict[str, WorkerState]" = {}

        self._ctx = multiprocessing.get_context('spawn')

        # Graceful restart orchestration state (restart_begin / restart_wait /
        # restart_cancel).
        # True while a graceful restart is in progress: worker_start() rejects
        # every config (any worker started now would be killed when the backend
        # exits and would never resume: "started but lost"). The gate is kept
        # until the process exits on the success path.
        self._restarting = False
        # Set by restart_cancel() to make a blocking restart_wait() return
        # early (a trio cancel cannot interrupt the waiting thread, the abort
        # event is the cooperative wake-up). Cleared by restart_begin().
        self._restart_abort = threading.Event()

    def get_state_info(self):
        """
        Returns:
            dict[str, WORKER_STATE]: key: config name, value: worker state
        """
        out = {}
        with self._lock:
            for w in self.state.values():
                out[w.config] = w.state
        return out

    def _handle_disconnect(self, state: WorkerState):
        """
        Cleanup worker on pipe broken
        """
        with self._lock:
            state_before = state.state
            self._set_state(state, 'disconnected')

        process = state.process
        if process:
            # after pipe broken, process should terminate every soon
            if process.is_alive():
                # On Windows, process needs a bit of time for handle cleanup
                # 0.5s is usually enough if child closed pipe manually
                process.join(timeout=0.5)
            # otherwise, kill it manually
            state.process_graceful_kill()

        # Close connection to unblock recv thread
        state.conn_close()

        # Join recv thread if it is not the current thread
        recv_thread = state.recv_thread
        if recv_thread and recv_thread is not threading.current_thread():
            if recv_thread.is_alive():
                recv_thread.join(timeout=1)

        exitcode = process.exitcode if process else None
        self.on_worker_info(state.config, f'[WorkerManager] Worker stopped: {state.config}, exitcode={exitcode}')

        with self._lock:
            state.conn = None
            state.process = None
            state.recv_thread = None
            if state.pending_restart:
                # stopped for the graceful backend restart: park the worker in
                # "restarting" (the entry stays in the dict, blocks a manual
                # start and is collected into the resume list by
                # restart_wait()); a crashed startup is still restarted, the
                # new backend simply retries the same way a manual start does
                self._set_state(state, 'restarting')
            elif exitcode == 0:
                self._set_state(state, 'idle')
            else:
                if state_before in ['killing', 'force-killing']:
                    # already killing, ignore exitcode because worker will exit with error
                    self._set_state(state, 'idle')
                else:
                    self._set_state(state, 'error')

    def on_config_event(self, event: ConfigEvent):
        """
        Callback when received config event from worker
        """
        print(event)

    def on_worker_info(self, config: str, msg: str):
        """
        Callback when logging worker info
        """
        logger.info(msg)
        event = logger.backend_event(msg, raw=1)
        event = ConfigEvent(t='Log', c=config, v=event)
        self.on_config_event(event)

    def _handle_config_event(self, data: bytes, worker: WorkerState):
        """
        Interval method to handle config event
        """
        event = DECODER_CACHE.CONFIG_EVENT.decode(data)

        # override config to avoid cross-mod or cross-config event pollution
        # we don't trust the "config" from worker, "config" can only be worker itself
        event.c = worker.config

        # handle "WorkerState" events
        if event.t == 'WorkerState':
            if event.v in WORKER_STATE_ALLOWS:
                with self._lock:
                    if worker.state in WORKER_STATE_ALLOWS:
                        # allow worker switching its state among allows
                        self._set_state(worker, event.v)
                        return
                    if worker.state == 'starting':
                        # allow worker switching to allows from "starting"
                        self._set_state(worker, event.v)
                        return
            return

        # broadcast
        self.on_config_event(event)

    def on_worker_state(self, config: str, state: WORKER_STATE):
        """
        Callback when worker state changed
        """
        print(f'Worker state "{config}": {state}')

    def _set_state(self, worker: WorkerState, state: WORKER_STATE):
        """
        Internal method to set worker state, lock required
        """
        worker.set_state(state)
        if state == 'idle':
            # remove worker state
            self.state.pop(worker.config, None)
        # broadcast
        self.on_worker_state(worker.config, state)

    def _worker_recv_loop(self, state: WorkerState):
        """
        Thread entry to receive message from worker

        我们给每个Worker进程单独开一个线程循环接收消息，而不是像web服务一样使用 wait(list_pipe) 同时接收所有消息
        在真实运行场景下，log是稀疏产生的，而一旦有log很可能是短时间内产生大量log
        wait(list_pipe) 虽然对多个pipe有很好的接收性能，但是对单一pipe的高频接收就远不如直接 conn.recv_bytes() 了。

        多线程recv_bytes() 的问题是同时接收多个pipe的时候会有频繁GIL切换导致性能远不如 wait(list_pipe)
        但因为log是稀疏产生的，每个worker的高频时段通常不会集中，所以在我们的运行情景下
        使用 多线程recv_bytes() 的性能就是单线程 recv_bytes()
        """
        conn = state.conn
        config = state.config
        try:
            while True:
                # check if pipe closed
                if not state.conn:
                    break
                try:
                    data = conn.recv_bytes()
                except (EOFError, OSError):
                    break

                try:
                    self._handle_config_event(data, state)
                except Exception as e:
                    logger.warning(f'[WorkerManager] Failed to handle config event '
                                   f'from "{config}": {e}')
        except Exception as e:
            logger.error(f'[WorkerManager] Recv loop error "{config}": {e}')

        # Handle disconnect
        self._handle_disconnect(state)

    def worker_start(self, mod: str, config: str, project_root='', mod_root='', path_main='') -> "tuple[bool, str]":
        """
        Request to start a worker
        Note that this method does not check if mod and config are valid

        Returns:
            whether success, reason
        """
        with self._lock:
            if self._restarting:
                # any worker started now would be killed when the backend exits
                # and would never resume ("started but lost")
                return False, (f'Backend is gracefully restarting, workers will resume '
                               f'after restart, cannot start now: "{config}"')
            # get or init config state
            state = self.state.get(config, None)
            if not state:
                state = WorkerState(mod=mod, config=config, state='idle')
                self.state[config] = state
            # check if already started
            if state.state not in ['idle', 'error']:
                if state.state == 'resuming':
                    return False, f'Worker is queued for auto-resume after restart: "{config}"'
                if state.state == 'restarting':
                    return False, f'Worker is stopped for backend restart and will auto-resume: "{config}"'
                return False, f'Worker is already running: "{config}", state="{state.state}"'
            # mark immediately
            state.mod = mod
            state.pending_restart = False
            self._set_state(state, 'starting')

        self.on_worker_info(config, f'[WorkerManager] Starting worker: {config}')
        return self._worker_start_process(state, mod, config, project_root, mod_root, path_main)

    def _worker_start_process(self, state: WorkerState, mod: str, config: str,
                              project_root='', mod_root='', path_main='') -> "tuple[bool, str]":
        """
        Spawn the worker process and its recv thread (lock released)

        The caller must have marked the worker as "starting" under the lock;
        worker_start() and worker_resume() share this tail.

        Returns:
            whether success, reason
        """
        # start process without lock
        parent_conn, child_conn = self._ctx.Pipe()
        if project_root and mod_root and path_main:
            # if project_root, mod_root, path_main all provided, consider as real mod
            args = (mod, config, child_conn, project_root, mod_root, path_main)
        else:
            # otherwise just testing
            args = (mod, config, child_conn)
        process = self._ctx.Process(
            target=mod_entry,
            args=args,
            name=f"Worker-{mod}-{config}",
            daemon=True
        )
        process.start()
        # close child_conn of the parent side immediately
        child_conn.close()

        with self._lock:
            state.process = process
            state.conn = parent_conn
            # status will become "running" when worker process initialize BackendBridge

            # start recv thread
            thread = threading.Thread(
                target=self._worker_recv_loop,
                args=(state,),
                name=f"WorkerRecv-{config}",
                daemon=True
            )
            thread.start()
            state.recv_thread = thread

        return True, 'Success'

    def worker_wait_running(self, config: str, timeout: "float | None" = None) -> bool:
        """
        Wait until worker running

        Returns:
            If waited
        """
        # dict access is thread safe, so no lock needed
        try:
            state = self.state[config]
        except KeyError:
            raise KeyError(f'No such worker: {config}') from None
        return state.wait_running(timeout)

    def worker_wait_stopped(self, config: str, timeout: "float | None" = None) -> bool:
        """
        Wait until worker stopped

        Returns:
            If waited
        """
        try:
            state = self.state[config]
        except KeyError:
            # No such worker means not yet running or stopped
            return True
        return state.wait_stopped(timeout)

    def worker_scheduler_stop(self, config: str, restart_resume=False) -> "tuple[bool, str]":
        """
        Send "scheduler-stopping" to worker

        Args:
            config (str): Config name
            restart_resume (bool): Only meaningful for a worker already stopped
                for the restart ("restarting", no process): True keeps the
                pending resume, False (default) cancels it

        Returns:
            whether success, reason
        """
        with self._lock:
            # get config state
            state = self.state.get(config, None)
            if not state:
                return False, f'No such worker to stop: {config}'
            # process-less restart states: a stop cancels the auto-resume
            result = self._cancel_or_keep_resume(state, restart_resume)
            if result is not None:
                return result
            # check if worker is running
            if state.state in ['idle', 'error', 'disconnected']:
                return False, f'Worker not running: "{config}", state="{state.state}"'
            if state.state in ['scheduler-stopping']:
                return False, f'Worker is already stopping: "{config}", state="{state.state}"'
            if state.state in ['killing', 'force-killing']:
                return False, f'Worker is already killing: "{config}", state="{state.state}"'
            # mark immediately
            self._set_state(state, 'scheduler-stopping')

        self.on_worker_info(config, f'[WorkerManager] Requesting scheduler stop: {config}')
        # send command without lock
        command = CommandEvent(c='scheduler-stopping')
        state.send_command(command)

        return True, 'Success'

    def worker_scheduler_continue(self, config: str) -> "tuple[bool, str]":
        """
        Send "scheduler-continue" to worker, to cancel previous "scheduler-stopping"

        Returns:
            whether success, reason
        """
        with self._lock:
            # get config state
            state = self.state.get(config, None)
            if not state:
                return False, f'No such worker to stop: {config}'
            if state.pending_restart:
                # the worker is already part of the graceful restart, the stop
                # cannot be revoked
                return False, f'Worker is stopping for backend restart, cannot continue: "{config}"'
            # check if worker is running
            if state.state in ['idle', 'error', 'disconnected']:
                return False, f'Worker not running: "{config}", state="{state.state}"'
            if state.state in ['killing', 'force-killing']:
                return False, f'Worker is already killing: "{config}", state="{state.state}"'
            if state.state not in ['scheduler-stopping', ]:
                return False, f'Worker is not in scheduler-stopping: "{config}", state="{state.state}"'
            # mark immediately
            self._set_state(state, 'running')

        self.on_worker_info(config, f'[WorkerManager] Requesting scheduler continue: {config}')
        # send command without lock
        command = CommandEvent(c='scheduler-continue')
        state.send_command(command)

        return True, 'Success'

    def worker_kill(self, config: str, restart_resume=False) -> "tuple[bool, str]":
        """
        Send "killing" to worker, wait for the worker to stop by itself

        The call blocks until the worker stops. If the worker does not stop
        within KILL_WAIT_TIMEOUT seconds, escalate to worker_force_kill().

        Args:
            config (str): Config name
            restart_resume (bool): Only meaningful while a graceful restart is
                waiting: True stops the worker but keeps the pending resume
                (it still resumes after the backend restart), False (default)
                stops the worker for good (the config leaves the resume list)

        Returns:
            whether success, reason
        """
        with self._lock:
            # get config state
            state = self.state.get(config, None)
            if not state:
                return False, f'No such worker to kill: {config}'
            # process-less restart states: a stop cancels (or keeps) the auto-resume
            result = self._cancel_or_keep_resume(state, restart_resume)
            if result is not None:
                return result
            # check if worker is running
            if state.state in ['idle', 'error', 'disconnected']:
                return False, f'Worker not running: "{config}", state="{state.state}"'
            if state.state in ['killing', 'force-killing']:
                return False, f'Worker is already killing: "{config}", state="{state.state}"'
            if not restart_resume:
                # default stop cancels the pending resume: the worker stops and
                # does not come back after the backend restart
                state.pending_restart = False
            # mark immediately
            self._set_state(state, 'killing')

        self.on_worker_info(config, f'[WorkerManager] Requesting worker kill: {config}')
        # send command without lock
        command = CommandEvent(c='killing')
        state.send_command(command)

        # Wait for worker to stop by itself
        if state.wait_stopped(timeout=KILL_WAIT_TIMEOUT):
            return True, 'Success'

        # Worker did not stop by itself, escalate to force kill
        self.on_worker_info(
            config,
            f'[WorkerManager] Worker did not stop by itself within {KILL_WAIT_TIMEOUT}s, force killing: {config}')
        success, msg = self.worker_force_kill(config, restart_resume=restart_resume)
        if not success:
            # Worker may have just stopped by itself, or is being stopped by another request,
            # the worker is no longer running, so the kill is considered successful
            self.on_worker_info(config, f'[WorkerManager] Worker already stopped, force kill skipped: {msg}')

        return True, 'Success'

    def worker_force_kill(self, config: str, restart_resume=False) -> "tuple[bool, str]":
        """
        Request to force kill a worker

        Args:
            config (str): Config name
            restart_resume (bool): Only meaningful while a graceful restart is
                waiting: True keeps the pending resume (the worker is killed
                now and still resumes after the backend restart), False
                (default) stops the worker for good

        Returns:
            whether success, reason
        """
        with self._lock:
            # get or init config state
            state = self.state.get(config, None)
            if not state:
                return False, f'No such worker to force-kill: {config}'
            # process-less restart states: a stop cancels (or keeps) the auto-resume
            result = self._cancel_or_keep_resume(state, restart_resume)
            if result is not None:
                return result
            # check if already killed
            if state.state in ['idle', 'error', 'disconnected']:
                return False, f'Worker not running: "{config}", state="{state.state}"'
            if state.state in ['force-killing']:
                return False, f'Worker is already force-killing: "{config}", state="{state.state}"'
            if not restart_resume:
                # default stop cancels the pending resume (see worker_kill)
                state.pending_restart = False
            # mark immediately
            self._set_state(state, 'force-killing')

        # cleanup
        state.process_graceful_kill()
        state.conn_close()
        if state.recv_thread and state.recv_thread.is_alive():
            state.recv_thread.join(timeout=1)

        with self._lock:
            state.process = None
            state.conn = None
            state.recv_thread = None
            if state.pending_restart:
                # stopped for the graceful restart: park in "restarting" so
                # restart_wait() collects the config into the resume list
                self._set_state(state, 'restarting')
            else:
                self._set_state(state, 'idle')

        return True, 'Success'

    def _cancel_or_keep_resume(self, state: WorkerState, restart_resume: bool) -> "Optional[tuple[bool, str]]":
        """
        Routing of the stop functions for the process-less restart states (lock required)

        A stop request on these states means "cancel the auto-resume": the
        entry returns to idle and leaves the resume list / queue. The single
        exception is "restarting" with restart_resume=True: the worker is
        already stopped, keep waiting (no-op success). "resuming" is always
        cancelled (no process exists, any stop means cancel).

        Args:
            state (WorkerState): Worker state, looked up under the lock
            restart_resume (bool): True keeps the pending resume of
                "restarting"; ignored for "resuming"

        Returns:
            Optional[tuple[bool, str]]: The call result when the state is
                "restarting" / "resuming", None for any other state (the
                caller continues with its normal routing)
        """
        if state.state == 'restarting':
            if restart_resume:
                return True, 'Success'
        elif state.state != 'resuming':
            return None
        # cancel the auto-resume
        state.pending_restart = False
        self._set_state(state, 'idle')
        self.on_worker_info(state.config, f'[WorkerManager] Auto resume cancelled: {state.config}')
        return True, 'Success'

    # ---------------- graceful restart orchestration ----------------

    def restart_begin(self) -> None:
        """
        Begin a graceful backend restart

        Runs under one lock: sets the restarting gate, snapshots the running
        workers ({starting, running, scheduler-waiting}) and marks them
        "pending_restart", and converts the entries queued by a previous
        auto-resume ("resuming") into "restarting" so this restart collects
        them too. The stop requests are sent outside the lock. The call
        returns immediately, the wait belongs to restart_wait().

        Raises:
            RuntimeError: When a graceful restart is already in progress
        """
        with self._lock:
            if self._restarting:
                raise RuntimeError('Restart already in progress')
            # gate and snapshot under the same lock as worker_start's check:
            # no worker can slip between the gate and the snapshot
            self._restarting = True
            self._restart_abort.clear()
            running = [
                state for state in self.state.values()
                if state.state in ['starting', 'running', 'scheduler-waiting']
            ]
            for state in running:
                state.pending_restart = True
            # a resume queue of a previous restart that has not been drained
            # yet (the backend is restarted again right after): the entries
            # have no process and are already "stopped for restart", turn
            # them into "restarting" so restart_wait() collects them into
            # this restart's resume list instead of leaving them to be
            # started under the gate
            for state in list(self.state.values()):
                if state.state == 'resuming':
                    self._set_state(state, 'restarting')

        # send the graceful stop requests outside the lock (worker_scheduler_stop
        # takes the lock itself); a "starting" worker gets the command queued
        # in the pipe and exits from its earliest checkpoint
        for state in running:
            self.worker_scheduler_stop(state.config)

    def restart_wait(self, timeout=None) -> "List[str]":
        """
        Block until every worker stopped for the graceful restart

        Must run in its own thread (the async orchestrator calls it through
        trio.to_thread.run_sync): the wait can last up to the timeout. When the
        wait exceeds the timeout, every remaining worker is escalated to
        worker_kill(restart_resume=True) (which itself escalates to a force
        kill) and the wait continues until all of them stopped. restart_cancel()
        (abort) makes the wait return early.

        Args:
            timeout (float): Seconds to wait for the graceful stop before
                escalating to kill. None waits forever

        Returns:
            List[str]: Configs to resume after the restart = the workers whose
                state is "restarting" when the wait ends (sorted). A worker the
                user stopped (default kill, no restart_resume) is not in it
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        escalated = False
        while True:
            remaining = self._restart_remaining()
            if not remaining:
                break
            if self._restart_abort.is_set():
                # cancelled: return early, the cancel path owns the state cleanup
                break
            if not escalated and deadline is not None and time.monotonic() >= deadline:
                logger.info(f'[WorkerManager] Graceful stop timeout ({timeout}s), '
                            f'killing workers: {", ".join(remaining)}')
                escalated = True
                for config in remaining:
                    self.worker_kill(config, restart_resume=True)
                continue
            # wait on the abort event: a cancel wakes the loop immediately,
            # otherwise the poll does not outlive the poll interval
            self._restart_abort.wait(RESTART_WAIT_POLL)
        return self._restart_resume_list()

    def restart_cancel(self) -> None:
        """
        Cancel the graceful restart in progress (idempotent)

        Sets the abort event (a blocking restart_wait() returns early), clears
        the restarting gate, drops every pending_restart mark and returns the
        process-less "restarting" / "resuming" entries to idle (removed from
        the state dict). Workers still stopping (scheduler-stopping / killing)
        only lose their marks: they stop through their normal path to idle and
        are not resumed.
        """
        self._restart_abort.set()
        with self._lock:
            self._restarting = False
            for state in list(self.state.values()):
                if state.pending_restart:
                    state.pending_restart = False
                if state.state in ['restarting', 'resuming']:
                    self._set_state(state, 'idle')

    def restart_aborted(self) -> bool:
        """
        Whether the graceful restart in progress was cancelled

        Returns:
            bool: True after restart_cancel() and before the next
                restart_begin()
        """
        return self._restart_abort.is_set()

    def _restart_remaining(self) -> "List[str]":
        """
        Configs still waiting to stop for the graceful restart

        A worker counts as stopped when its entry is gone (idle) or its state
        is idle / error / restarting; "disconnected" is transient (the
        disconnect handler is finishing its cleanup) and keeps the wait going.

        Returns:
            List[str]: Sorted config names
        """
        with self._lock:
            return sorted(
                config for config, state in self.state.items()
                if state.state not in ['idle', 'error', 'restarting']
            )

    def _restart_resume_list(self) -> "List[str]":
        """
        Configs to resume after the restart (state == "restarting")

        Returns:
            List[str]: Sorted config names
        """
        with self._lock:
            return sorted(
                config for config, state in self.state.items()
                if state.state == 'restarting'
            )

    # ---------------- auto-resume queue (new backend) ----------------

    def mark_resume(self, configs) -> "List[str]":
        """
        Queue configs for auto-resume after the backend restart (new backend)

        Creates one "resuming" entry per still-idle config: no process, but the
        entry stays in the state dict, so the config is visibly queued and
        cannot be started twice. Configs already started (the user started them
        before the queue reached them) are skipped.

        Args:
            configs (list[str]): Config names recorded in the resume file

        Returns:
            List[str]: Actually queued configs, in input order
        """
        queued = []
        with self._lock:
            for config in configs:
                state = self.state.get(config, None)
                if state and state.state not in ['idle', 'error']:
                    # already started / stopped by the user: skip
                    continue
                if not state:
                    state = WorkerState(mod='', config=config, state='idle')
                    self.state[config] = state
                self._set_state(state, 'resuming')
                queued.append(config)
        return queued

    def worker_resume(self, mod: str, config: str, project_root='', mod_root='', path_main='') -> "tuple[bool, str]":
        """
        Start a worker queued by mark_resume(), like a manual start

        Args:
            mod (str): Mod name, same shape as worker_start()
            config (str): Config name
            project_root (str): Project root, same as worker_start()
            mod_root (str): Mod root, same as worker_start()
            path_main (str): Mod main entry, same as worker_start()

        Returns:
            whether success, reason
        """
        with self._lock:
            state = self.state.get(config, None)
            if not state or state.state != 'resuming':
                # cancelled by the user, started by someone else, or converted
                # to "restarting" by a new graceful restart
                return False, f'Worker is not awaiting auto-resume: "{config}"'
            if self._restarting:
                # a new graceful restart began while the queue was draining:
                # starting now would only be killed at backend exit, the entry
                # is converted to "restarting" (collected by restart_wait)
                return False, f'Backend is gracefully restarting, cannot resume now: "{config}"'
            # mark immediately
            state.mod = mod
            self._set_state(state, 'starting')

        self.on_worker_info(config, f'[WorkerManager] Resuming worker: {config}')
        return self._worker_start_process(state, mod, config, project_root, mod_root, path_main)

    def close(self):
        """
        Terminate all workers and release resources
        """
        # Remove self from singleton cache, so the next access will have a new manager
        self.__class__.singleton_clear()

        while 1:
            with self._lock:
                states = list(self.state.values())
                if not states:
                    break
                self.state.clear()
                logger.info(f'[WorkerManager] Closing manager, remaining {len(states)} workers')
                for state in states:
                    self._set_state(state, 'killing')

            # Terminate processes
            for state in states:
                state.process_terminate()

            # Wait for processes
            for state in states:
                state.process_join(timeout=1)
                state.process_kill(timeout=1)

            # Close connections
            for state in states:
                state.conn_close()

            # Wait for threads
            for state in states:
                if state.recv_thread is not None and state.recv_thread.is_alive():
                    state.recv_thread.join(timeout=1)

            with self._lock:
                for state in states:
                    state.process = None
                    state.recv_thread = None
                    self._set_state(state, 'idle')
            # maybe new worker started while we are killing existing workers

        logger.info('[WorkerManager] All closed')


if __name__ == '__main__':
    self = WorkerManager()
    self.worker_start('WorkerTestScheduler', 'alas')

    for _ in range(1):
        print(self.state)
        time.sleep(1)
        continue
    # self.worker_kill('alas')
    # self.close()
    # self.state['alas'].conn.close()

    for _ in range(10):
        print(self.state)
        time.sleep(1)
        continue
