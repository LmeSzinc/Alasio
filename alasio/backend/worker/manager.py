import multiprocessing
import threading
import time
from multiprocessing.connection import Connection, wait
from typing import Literal, Optional

import msgspec
from msgspec.msgpack import encode

from alasio.backend.worker.bridge import mod_entry
from alasio.backend.worker.event import CommandEvent, ConfigEvent, DECODER_CACHE
from alasio.ext.singleton import Singleton
from alasio.logger import logger

# idle: not running
# starting: requesting to start a worker, starting worker process
# running: worker process running
# scheduler-stopping: requesting to stop scheduler loop, worker will stop after current task
# scheduler-waiting: worker waiting for next task, no task running currently
# killing: requesting to kill a worker, worker will stop and do GC asap
# force-killing: requesting to kill worker process immediately
# disconnected: backend just lost connection worker,
#   worker process will be clean up and worker status will turn into idle or error very soon
# error: worker stopped with error
#   Note that scheduler will loop forever, so there is no "stopped" state
#   If user request "scheduler_stopping" or "killing", state will later be "idle"
WORKER_STATUS = Literal[
    'idle', 'starting', 'running', 'disconnected', 'error',
    'scheduler-stopping', 'scheduler-waiting',
    'killing', 'force-killing',
]
# Allow worker set its status to one of the allows
WORKER_STATUS_ALLOWS = ['running', 'scheduler-waiting']
# Worker is considered running if status in the followings
WORKER_RUNNING_STATUS = ['running', 'scheduler-stopping', 'scheduler-waiting']
# Worker is considered stopped if status in the followings
WORKER_STOPPED_STATUS = ['idle', 'error']


class WorkerStateInfo(msgspec.Struct):
    mod: str
    config: str
    status: WORKER_STATUS
    update: float = 0.


class WorkerState(WorkerStateInfo):
    process: Optional[multiprocessing.Process] = None
    conn: Optional[Connection] = None
    running_event: threading.Event = msgspec.field(default_factory=threading.Event)
    stopped_event: threading.Event = msgspec.field(default_factory=threading.Event)

    def set_status(self, status: WORKER_STATUS):
        self.status = status
        self.update = time.time()
        if status in WORKER_RUNNING_STATUS:
            self.running_event.set()
            self.stopped_event.clear()
        elif status in WORKER_STOPPED_STATUS:
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

        # pipe to awake io_loop when new process added
        self._ctx = multiprocessing.get_context('spawn')
        self._notify_r, self._notify_w = self._ctx.Pipe(duplex=False)
        self._io_thread = threading.Thread(target=self._io_loop_wrapper, daemon=True)
        self._io_thread.start()

    def get_state_info(self) -> "dict[str, WorkerStateInfo]":
        """
        Returns:
            key: config name,
            value: worker state
        """
        out = {}
        with self._lock:
            for w in self.state.values():
                info = WorkerStateInfo(mod=w.mod, config=w.config, status=w.status, update=w.update)
                out[w.config] = info
        return out

    def _rebuild_notify_pipe(self):
        with self._lock:
            try:
                self._notify_r.close()
            except Exception:
                pass
            try:
                self._notify_r.close()
            except Exception:
                pass
            self._notify_r, self._notify_w = self._ctx.Pipe(duplex=False)
            logger.info('[WorkerManager] Notify pipe re-initialized')

    def _notify_update(self, msg: "Literal[b'x', b'close']" = b'x'):
        """
        Notify io_loop that pipe list changed
        """
        try:
            self._notify_w.send_bytes(msg)
            return True
        except OSError as e:
            logger.warning(f'[WorkerManager] Failed to notify io_loop: {e}')
            pass
        # retry
        self._rebuild_notify_pipe()
        try:
            self._notify_w.send_bytes(msg)
            return True
        except OSError as e:
            logger.warning(f'[WorkerManager] Failed to notify io_loop twice: {e}')
            pass
        # no luck
        return False

    def _cleanup_invalid_pipes(self):
        with self._lock:
            to_remove = []
            for state in self.state.values():
                if not state.conn:
                    continue
                try:
                    # test if pipe valid
                    state.conn.fileno()
                except (ValueError, OSError):
                    to_remove.append(state)

            # check notify pipe
            try:
                self._notify_r.fileno()
            except (ValueError, OSError):
                logger.warning('[WorkerManager] notify_r invalid, re-initializing...')
                self._notify_r, self._notify_w = self._ctx.Pipe(duplex=False)

        for state in to_remove:
            logger.warning(f'[WorkerManager] Cleaning up invalid pipe from "{state.config}"')
            self._handle_disconnect(state)

    def _dict_readers(self) -> "dict[Connection, WorkerState]":
        with self._lock:
            # build all pipes
            # wait() internally iterates object_list input, so it's safe to input a dict
            # by using dict, we can find config from pipe
            dict_readers = {}
            for state in self.state.values():
                if state.process and state.conn:
                    dict_readers[state.conn] = state
            # also add notify pipe, so readers can be rebuilt when configs changed
            dict_readers[self._notify_r] = None

        return dict_readers

    def _handle_disconnect(self, state: WorkerState):
        """
        Cleanup worker on pipe broken
        """
        with self._lock:
            status_before = state.status
            self._set_status(state, 'disconnected')

        process = state.process
        if process:
            # after pipe broken, process should terminate every soon
            if process.is_alive():
                process.join(timeout=0.2)
            # otherwise, kill it manually
            state.process_graceful_kill()

        exitcode = process.exitcode

        with self._lock:
            state.conn = None
            state.process = None
            if exitcode == 0:
                self._set_status(state, 'idle')
            else:
                if status_before in ['killing', 'force-killing']:
                    # already killing, ignore exitcode because worker will exit with error
                    self._set_status(state, 'idle')
                else:
                    self._set_status(state, 'error')

    def on_config_event(self, event: ConfigEvent):
        """
        Callback when received config event from worker
        """
        print(event)

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
            if event.v in WORKER_STATUS_ALLOWS:
                with self._lock:
                    if worker.status in WORKER_STATUS_ALLOWS:
                        # allow worker switching its status among allows
                        self._set_status(worker, event.v)
                        return
                    if worker.status == 'starting':
                        # allow worker switching to allows from "starting"
                        self._set_status(worker, event.v)
                        return
            return

        # broadcast
        self.on_config_event(event)

    def on_worker_status(self, config: str, status: WORKER_STATUS):
        """
        Callback when worker state changed
        """
        print(f'Worker status "{config}": {status}')

    def _set_status(self, worker: WorkerState, status: WORKER_STATUS):
        """
        Internal method to set worker status, lock required
        """
        worker.set_status(status)
        if status == 'idle':
            # remove worker state
            self.state.pop(worker.config, None)
        # broadcast
        self.on_worker_status(worker.config, status)

    def _io_loop(self):
        """
        IO thread entry function to handle message from workers
        """
        dict_readers = self._dict_readers()
        should_rebuild = False
        while 1:
            # rebuild
            if should_rebuild:
                dict_readers = self._dict_readers()
            # wait
            try:
                ready_pipes = wait(dict_readers)
            except OSError:
                # Race condition in manager process, which shouldn't happen
                # OSError: [Errno 9] Bad file descriptor
                self._cleanup_invalid_pipes()
                should_rebuild = True
                time.sleep(0.1)  # avoid infinite loop in bad circumstances
                continue

            should_close = False
            for pipe in ready_pipes:
                if pipe == self._notify_r:
                    try:
                        while True:
                            msg = pipe.recv_bytes()
                            if msg == b'close':
                                should_close = True
                            if not pipe.poll():
                                break
                    except (EOFError, OSError):
                        logger.warning('[WorkerManager] notify_r broke during recv, re-initializing...')
                        self._rebuild_notify_pipe()
                    except Exception:
                        pass
                    should_rebuild = True
                    continue
                # handle config event
                try:
                    data = pipe.recv_bytes()
                except (EOFError, OSError):
                    try:
                        worker = dict_readers[pipe]
                    except KeyError:
                        # this shouldn't happen
                        continue
                    self._handle_disconnect(worker)
                    should_rebuild = True
                    continue
                try:
                    worker = dict_readers[pipe]
                except KeyError:
                    # this shouldn't happen
                    continue
                try:
                    self._handle_config_event(data, worker)
                except Exception:
                    logger.warning(f'[WorkerManager] Failed to handle config event '
                                   f'from "{worker.config}": {repr(data)}')
                    continue

            if should_close:
                break

    def _io_loop_wrapper(self):
        """
        A wrapper of _io_loop to log if _io_loop died
        """
        try:
            self._io_loop()
            return
        except Exception:
            logger.error('[WorkerManager] _io_loop died')
            raise

    def worker_start(self, mod: str, config: str, project_root='', mod_root='', path_main='') -> "tuple[bool, str]":
        """
        Request to start a worker
        Note that this method does not check if mod and config are valid

        Returns:
            whether success, reason
        """
        with self._lock:
            # get or init config state
            state = self.state.get(config, None)
            if not state:
                state = WorkerState(mod=mod, config=config, status='idle')
                self.state[config] = state
            # check if already started
            if state.status not in ['idle', 'error']:
                return False, f'Worker is already running: "{config}", state="{state.status}"'
            # mark immediately
            self._set_status(state, 'starting')

        logger.info(f'[WorkerManager] Starting worker {config}')
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
            self._notify_update()
            # status will become "running" when worker process initialize BackendBridge

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

    def worker_scheduler_stop(self, config: str) -> "tuple[bool, str]":
        """
        Send "scheduler-stopping" to worker

        Returns:
            whether success, reason
        """
        with self._lock:
            # get config state
            state = self.state.get(config, None)
            if not state:
                return False, f'No such worker to stop: {config}'
            # check if worker is running
            if state.status in ['idle', 'error', 'disconnected']:
                return False, f'Worker not running: "{config}", state="{state.status}"'
            if state.status in ['scheduler-stopping', 'killing', 'force-killing']:
                return False, f'Worker is already stopping: "{config}", state="{state.status}"'
            # mark immediately
            self._set_status(state, 'scheduler-stopping')

        logger.info(f'[WorkerManager] Requesting scheduler stop: {config}')
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
            # check if worker is running
            if state.status in ['idle', 'error', 'disconnected']:
                return False, f'Worker not running: "{config}", state="{state.status}"'
            if state.status in ['scheduler-stopping', 'killing', 'force-killing']:
                return False, f'Worker is already stopping: "{config}", state="{state.status}"'
            if state.status not in ['scheduler-stopping', ]:
                return False, f'Worker is not in scheduler-stopping: "{config}", state="{state.status}"'
            # mark immediately
            self._set_status(state, 'running')

        logger.info(f'[WorkerManager] Requesting scheduler continue: {config}')
        # send command without lock
        command = CommandEvent(c='scheduler-continue')
        state.send_command(command)

        return True, 'Success'

    def worker_kill(self, config: str) -> "tuple[bool, str]":
        """
        Send "killing" to worker

        Returns:
            whether success, reason
        """
        with self._lock:
            # get config state
            state = self.state.get(config, None)
            if not state:
                return False, f'No such worker to kill: {config}'
            # check if worker is running
            if state.status in ['idle', 'error', 'disconnected']:
                return False, f'Worker not running: "{config}", state="{state.status}"'
            if state.status in ['killing', 'force-killing']:
                return False, f'Worker is already killing: "{config}", state="{state.status}"'
            # mark immediately
            self._set_status(state, 'killing')

        logger.info(f'[WorkerManager] Requesting worker kill: {config}')
        # send command without lock
        command = CommandEvent(c='killing')
        state.send_command(command)

        return True, 'Success'

    def worker_force_kill(self, config: str) -> "tuple[bool, str]":
        """
        Request to force kill a worker

        Returns:
            whether success, reason
        """
        with self._lock:
            # get or init config state
            state = self.state.get(config, None)
            if not state:
                return False, f'No such worker to force-kill: {config}'
            # check if already killed
            if state.status in ['idle', 'error', 'disconnected']:
                return False, f'Worker not running: "{config}", state="{state.status}"'
            if state.status in ['force-killing']:
                return False, f'Worker is already force-killing: "{config}", state="{state.status}"'
            # mark immediately
            self._set_status(state, 'force-killing')

        # cleanup
        state.process_graceful_kill()
        state.conn_close()

        with self._lock:
            state.process = None
            state.conn = None
            self._set_status(state, 'idle')

        return True, 'Success'

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
                    self._set_status(state, 'killing')

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

            with self._lock:
                for state in states:
                    state.process = None
                    self._set_status(state, 'idle')
            # maybe new worker started while we are killing existing workers

        # Finally close _io_loop
        self._notify_update(b'close')
        self._io_thread.join(timeout=1)
        if self._io_thread.is_alive():
            logger.warning(f'[WorkerManager] _io_thread did not close')
        try:
            self._notify_r.close()
        except Exception:
            pass
        try:
            self._notify_w.close()
        except Exception:
            pass

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
    self._notify_r.close()

    for _ in range(10):
        print(self.state)
        time.sleep(1)
        continue
