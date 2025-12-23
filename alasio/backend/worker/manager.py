import multiprocessing
import threading
import time
from multiprocessing.connection import Connection, wait
from typing import Literal, Optional

import msgspec
from msgspec.msgpack import encode

from alasio.backend.worker.event import CommandEvent, ConfigEvent, DECODER_CACHE
from alasio.backend.worker.bridge import mod_entry
from alasio.ext.singleton import Singleton
from alasio.logger import logger

# idle: not running
# starting: requesting to start a worker, starting worker process
# running: worker process running
# scheduler-stopping: requesting to stop scheduler loop, worker will stop after current task
# scheduler-waiting: worker waiting for next task, no task running currently
# killing: requesting to kill a worker, worker will stop and do GC asap
# force-killing: requesting to kill worker process immediately
# error: worker stopped with error
#   Note that scheduler will loop forever, so there is no "stopped" state
#   If user request "scheduler_stopping" or "killing", state will later be "idle"
WORKER_STATUS = Literal[
    'idle', 'starting', 'running',
    'scheduler-stopping', 'scheduler-waiting',
    'killing', 'force-killing', 'error'
]


class WorkerState(msgspec.Struct):
    mod: str
    config: str
    status: WORKER_STATUS
    update: float = 0.
    process: Optional[multiprocessing.Process] = None
    conn: Optional[Connection] = None

    def set_status(self, status: WORKER_STATUS):
        self.status = status
        self.update = time.time()

    def send_command(self, command: CommandEvent):
        data = encode(command)
        try:
            conn = self.conn
            # Equivalent to  conn.send_bytes() but bypass all by
            conn._check_closed()
            conn._check_writable()
            conn._send_bytes(data)
        except AttributeError:
            # this shouldn't happen
            logger.warning(f'[WorkerManager] Failed to send command to "{self.config}": '
                           f'pipe connection not initialized')
        except Exception as e:
            logger.warning(f'[WorkerManager] Failed to send command to "{self.config}": {e}')

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
            state.set_status('force-killing')

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
                state.set_status('idle')
            else:
                if status_before in ['killing', 'force-killing']:
                    # already killing, ignore exitcode because worker will exit with error
                    state.set_status('idle')
                    # remove worker state
                    self.state.pop(state.config, None)
                else:
                    state.set_status('error')

    def handle_config_event(self, event: ConfigEvent):
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
            allows = ['running', 'scheduler-waiting']
            if event.v in allows:
                with self._lock:
                    if worker.status in allows:
                        # allow worker switching its status among allows
                        worker.set_status(event.v)
            return

        self.handle_config_event(event)

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

    def worker_start(self, mod: str, config: str) -> "tuple[bool, str]":
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
            state.set_status('starting')

        logger.info(f'[WorkerManager] Starting worker {config}')
        # start process without lock
        parent_conn, child_conn = self._ctx.Pipe()
        process = self._ctx.Process(
            target=mod_entry,
            args=(mod, config, child_conn),
            name=f"Worker-{mod}-{config}",
            daemon=True
        )
        process.start()
        # close child_conn of the parent side immediately
        child_conn.close()

        with self._lock:
            state.process = process
            state.conn = parent_conn
            state.set_status('running')
            self._notify_update()

        return True, 'Success'

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
            if state.status in ['idle', 'error']:
                return False, f'Worker not running: "{config}", state="{state.status}"'
            if state.status in ['scheduler-stopping', 'scheduler-waiting', 'killing', 'force-killing']:
                return False, f'Worker is already stopping: "{config}", state="{state.status}"'
            # mark immediately
            state.set_status('scheduler-stopping')

        logger.info(f'[WorkerManager] Requesting scheduler stop: {config}')
        # send command without lock
        command = CommandEvent(c='scheduler-stopping')
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
            if state.status in ['idle', 'error']:
                return False, f'Worker not running: "{config}", state="{state.status}"'
            if state.status in ['killing', 'force-killing']:
                return False, f'Worker is already killing: "{config}", state="{state.status}"'
            # mark immediately
            state.set_status('killing')

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
            if state.status in ['idle', 'error']:
                return False, f'Worker not running: "{config}", state="{state.status}"'
            if state.status in ['force-killing']:
                return False, f'Worker is already force-killing: "{config}", state="{state.status}"'
            # mark immediately
            state.set_status('force-killing')

        # cleanup
        state.process_graceful_kill()
        state.conn_close()

        with self._lock:
            state.process = None
            state.conn = None
            state.set_status('idle')
            # remove worker state
            self.state.pop(state.config, None)

        return True, 'Success'

    def close(self):
        """
        Terminate all workers and release resources
        """
        logger.info('[WorkerManager] Closing...')
        # Remove self from singleton cache, so the next access will have a new manager
        self.__class__.singleton_clear_all()

        while 1:
            with self._lock:
                states = list(self.state.values())
                if not states:
                    break
                self.state.clear()
                for state in states:
                    state.set_status('killing')

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
                    state.set_status('idle')
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
