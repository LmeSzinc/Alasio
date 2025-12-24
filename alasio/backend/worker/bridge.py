import threading
from typing import Literal

from msgspec.msgpack import decode, encode

from alasio.backend.worker.event import CommandEvent, ConfigEvent
from alasio.ext.singleton import Singleton


def worker_test_infinite():
    # A worker that runs infinitely
    backend = BackendBridge()
    n = 0
    while 1:
        backend.send_log(str(n))
        n += 1
        backend.test_wait.wait(timeout=0.05)


def worker_test_run3():
    # A worker that runs only 3 times
    backend = BackendBridge()
    for n in range(3):
        backend.send_log(str(n))
        backend.test_wait.wait(timeout=0.05)


def worker_test_error():
    # A worker that will raise error
    backend = BackendBridge()
    backend.send_log('1')
    backend.test_wait.wait(timeout=0.05)
    raise Exception


def worker_test_scheduler():
    # A worker that simulates scheduler
    # - emits scheduler-waiting
    # - exits on scheduler_stopping after 0.5s
    backend = BackendBridge()
    backend.send_log('1')
    n = 0
    while 1:
        if n % 3 == 2:
            backend.send_worker_state('scheduler-waiting')
        else:
            backend.send_worker_state('running')
        n += 1
        if backend.scheduler_stopping.wait(0.05):
            backend.test_wait.wait(timeout=0.05)
            break
        else:
            backend.test_wait.wait(timeout=0.05)
            continue


def worker_test_send_events():
    # A worker that sends various config events for testing
    backend = BackendBridge()

    # Send log event
    backend.send_log('worker started')
    backend.test_wait.wait(timeout=0.05)

    # Send custom config events
    backend.send(ConfigEvent(t='CustomEvent', v='test_value_1'))
    backend.test_wait.wait(timeout=0.05)

    backend.send(ConfigEvent(t='CustomEvent', v='test_value_2'))
    backend.test_wait.wait(timeout=0.05)

    backend.send(ConfigEvent(t='DataUpdate', k=('task', 'group', 'arg'), v={'data': 123}))
    backend.test_wait.wait(timeout=0.05)

    # Send worker state
    backend.send_worker_state('scheduler-waiting')
    backend.test_wait.wait(timeout=0.05)

    backend.send_worker_state('running')
    backend.test_wait.wait(timeout=0.05)

    # Wait for stop signal
    while not backend.scheduler_stopping.wait(0.05):
        backend.send_log('still running')
        backend.test_wait.wait(timeout=0.05)


def mod_entry(mod, config, child_conn):
    """
    Run mod scheduler infinitely

    Args:
        mod:
        config:
        child_conn:
    """
    BackendBridge().init(child_conn)

    if mod == 'WorkerTestInfinite':
        worker_test_infinite()
        return
    if mod == 'WorkerTestRun3':
        worker_test_run3()
        return
    if mod == 'WorkerTestError':
        worker_test_error()
        return
    if mod == 'WorkerTestScheduler':
        worker_test_scheduler()
        return
    if mod == 'WorkerTestSendEvents':
        worker_test_send_events()
        return

    raise KeyError(f'No such mod to run {mod}')


def _async_raise(tid):
    if tid <= 0:
        from alasio.logger import logger
        logger.error(f'[BackendBridge] Failed to send KeyboardInterrupt, tid invalid: {tid}')
        return False

    import ctypes
    thread_id = ctypes.c_long(tid)
    err = ctypes.py_object(KeyboardInterrupt)
    res = ctypes.pythonapi.PyThreadState_SetAsyncExc(thread_id, err)

    if res < 1:
        from alasio.logger import logger
        logger.error(f'[BackendBridge] Failed to send KeyboardInterrupt, tid invalid: {tid}')
        return True
    elif res == 1:
        return True
    else:
        from alasio.logger import logger
        logger.error(f'[BackendBridge] Failed to send KeyboardInterrupt to thread {tid}')
        # Failed to send KeyboardInterrupt, reset it
        ctypes.pythonapi.PyThreadState_SetAsyncExc(thread_id, 0)
        return False


class BackendBridge(metaclass=Singleton):
    def __init__(self):
        self.conn = None
        self.main_tid = 0
        self._io_thread = None
        self.scheduler_stopping = threading.Event()
        # For test control
        self.test_wait = threading.Event()

    def init(self, child_conn):
        """
        initialize BackendBridge in main thread
        """
        self.conn = child_conn
        self.main_tid = threading.get_ident()
        self._io_thread = threading.Thread(target=self._io_loop, daemon=True)
        self._io_thread.start()
        self.send_worker_state('running')

    def send(self, event: ConfigEvent):
        conn = self.conn
        if not conn:
            # allow worker running without backend
            return

        data = encode(event)

        try:
            # Equivalent to  conn.send_bytes() but bypass all by
            conn._check_closed()
            conn._check_writable()
            conn._send_bytes(data)
            return True
        except AttributeError:
            # this shouldn't happen
            from alasio.logger import logger
            logger.error(f'[BackendBridge] Failed to send command: pipe connection not initialized')
            return False
        except Exception as e:
            from alasio.logger import logger
            logger.error(f'[BackendBridge] Failed to send command: {e}')
            return False

    def _handle_backend_command(self, data: bytes):
        event = decode(data, type=CommandEvent)
        command = event.c
        if command == 'scheduler-stopping':
            self.scheduler_stopping.set()
            return
        if command in ['killing', 'force-killing']:
            _async_raise(self.main_tid)
            return
        if command == 'test-continue':
            # Signal test_wait to continue immediately
            self.test_wait.set()
            self.test_wait.clear()
            return
        # ignore unknown events
        return

    def _io_loop(self):
        conn = self.conn
        if not conn:
            # this shouldn't happen
            from alasio.logger import logger
            logger.error(f'[BackendBridge] Failed to recv command: pipe connection not initialized')
            return False

        while 1:
            try:
                data = conn.recv_bytes()
            except (EOFError, OSError):
                from alasio.logger import logger
                logger.error(f'[BackendBridge] Failed to recv command: pipe broken')
                return False
            except Exception as e:
                from alasio.logger import logger
                logger.error(f'[BackendBridge] Failed to recv command: {e}')
                return False
            try:
                self._handle_backend_command(data)
            except Exception as e:
                from alasio.logger import logger
                logger.warning(f'[BackendBridge] Failed to handle command: {e}')
                continue

    def send_log(self, value):
        self.send(ConfigEvent(t='Log', v=value))

    def send_worker_state(self, value: Literal['running', 'scheduler-waiting']):
        self.send(ConfigEvent(t='WorkerState', v=value))
