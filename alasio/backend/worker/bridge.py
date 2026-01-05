import os
import sys
from threading import Event, Lock, Thread, get_ident
from typing import Literal

from msgspec.msgpack import Encoder, decode

from alasio.backend.worker.event import CommandEvent, ConfigEvent
from alasio.ext.cache import cached_property
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


def mod_entry(mod_name, config_name, child_conn, project_root='', mod_root='', path_main=''):
    """
    Run mod scheduler infinitely

    Args:
        mod_name:
        config_name:
        child_conn:
        project_root:
        mod_root:
        path_main:
    """
    BackendBridge().init(mod_name, config_name, child_conn)

    if mod_name == 'WorkerTestInfinite':
        worker_test_infinite()
        return
    if mod_name == 'WorkerTestRun3':
        worker_test_run3()
        return
    if mod_name == 'WorkerTestError':
        worker_test_error()
        return
    if mod_name == 'WorkerTestScheduler':
        worker_test_scheduler()
        return
    if mod_name == 'WorkerTestSendEvents':
        worker_test_send_events()
        return

    # if project_root, mod_root, path_main all provided, consider as real mod
    if project_root and mod_root and path_main:
        try:
            # set mod root path
            os.chdir(mod_root)
            sys.path[0] = mod_root

            # set project root path
            from alasio.ext import env
            env.set_project_root(project_root)

            # import Scheduler
            import importlib
            from alasio.ext.path.calc import to_python_import
            entry = to_python_import(path_main)
            module = importlib.import_module(entry)
            try:
                cls = module.Scheduler
            except AttributeError:
                raise AttributeError('Module entry file did not define class Scheduler')

            # run mod scheduler
            scheduler = cls(config_name)
            scheduler.run()

        except KeyboardInterrupt:
            pass

    else:
        raise KeyError(f'No such mod to run {mod_name}')


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
        self.inited = False
        self.mod_name = ''
        self.config_name = ''
        self.conn = None

        self.main_tid = 0
        self._recv_thread: "Thread | None" = None
        self._send_thread: "Thread | None" = None
        self.scheduler_stopping = Event()
        # For test control
        self.test_wait = Event()

        # send thread
        self.running = True
        # 初始为空，稍后存放 (bytes, threading.Lock)
        self._task_slot: "tuple[bytes, Lock] | None" = None
        # 1. 互斥锁：保护 send 入口
        self._mutex = Lock()
        # 2. Worker 空闲信号：
        # Locked = Worker 忙; Unlocked = Worker 空闲
        self._worker_idle = Lock()
        # 3. 任务就绪信号：
        # Locked = 无任务; Unlocked = 有任务
        self._work_ready = Lock()
        self._work_ready.acquire()  # 初始锁定，让 Worker 待命

    def init(self, mod_name, config_name, child_conn):
        """
        initialize BackendBridge in main thread
        """
        self.mod_name = mod_name
        self.config_name = config_name
        self.conn = child_conn
        self.main_tid = get_ident()

        self._send_thread = Thread(target=self._send_loop, daemon=True, name='BackendBridgeSender')
        self._send_thread.start()
        self._recv_thread = Thread(target=self._recv_loop, daemon=True, name='BackendBridgeReceiver')
        self._recv_thread.start()

        self.send_worker_state('running')
        self.inited = True

    @cached_property
    def _encoder(self):
        return Encoder()

    def send(self, event: ConfigEvent) -> Lock:
        """
        高性能事件发送

        1. 崩溃安全性 (Crash Safety) - "No-Queue Strategy":
           - 核心痛点：在高频日志场景下，使用 Queue 会导致内存中堆积大量未发送数据。一旦主进程崩溃（Crash），队列中所有数据瞬间丢失。
           - 解决方案：放弃队列，采用“单槽位（Single Slot）”设计。内存中永远最多只有 1 条正在处理的消息。
           - 收益：最大程度减少进程崩溃时的数据丢失窗口，仅限于当前正在 socket/pipe 中传输的那一条。

        2. 流量控制 (Back-Pressure):
           - 机制：当 Worker 线程正在发送上一条消息时，新的 send() 调用会在 _worker_idle 锁上发生物理阻塞。
           - 收益：自动平衡生产速度与消费（IO）速度。防止因日志产生过快导致内存暴涨（OOM）。
           - 行为：并发调用 send() 时，线程会排队等待槽位释放，严格保证串行化入管。

        3. 极致性能 (High Performance):
           - Zero-Copy: 数据仅通过引用传递，不进行 bytes 对象的内存复制（避免 append/pop 开销）。
           - Raw Bytes IO: 使用 pipe.send_bytes() 而非 pipe.send()，避开 pickle 序列化开销，直接传输二进制流。
           - Lightweight Locking: 仅使用 threading.Lock（底层 futex/semaphore），避免 Condition/Event 的额外开销。
           - Fast Path: 减少属性查找，Worker 内部使用局部变量缓存方法引用。

        4. 线程安全 (Thread Safety):
           - Lock Handoff: 使用锁传递机制（主线程 acquire，子线程 release）实现精确的同步接力。
           - Snapshotting: 将 (data, lock) 打包为元组，Worker 取出后即形成“本地快照”。
             即使实例属性在下一毫秒被新任务覆盖，Worker 手中的锁依然能正确通知对应的旧任务调用者。
        """
        conn = self.conn
        if not conn:
            # allow worker running without backend
            return Lock()

        data = self._encoder.encode(event)

        # 创建属于本次任务的专属锁
        # 调用者可以通过这个 lock.acquire() 等待消息真正发送完毕
        task_lock = Lock()
        # 预先锁住，用户只有在 Worker 释放后才能 acquire 成功
        task_lock.acquire()

        # 1. 竞争入口，防止多个线程同时修改共享数据
        self._mutex.acquire()
        try:
            # 2. 等待 Worker 空闲 (Back-pressure 核心)
            # 如果 Worker 正在发上一条，这里会阻塞，直到 Worker 释放锁
            self._worker_idle.acquire()

            # 3. 数据交接 (Zero Copy，只是引用赋值)
            # 将数据和锁打包在一起,这是一个原子赋值操作，不可分割。
            self._task_slot = (data, task_lock)

            # 4. 唤醒 Worker
            # 释放触发锁，允许 Worker 通过阻塞点
            self._work_ready.release()

            return task_lock

        finally:
            # 5. 释放入口锁
            # 注意：此时 _worker_idle 依然被持有（被当前线程获取，将在 Worker 线程释放）
            # 下一个调用者进来后，会在步骤 2 被阻塞，直到 Worker 完成本次任务。
            self._mutex.release()

    def _send_loop(self):
        """
        后台 Worker 线程逻辑
        """
        # 本地缓存方法查找，微小的性能优化
        wait_for_work = self._work_ready.acquire
        signal_idle = self._worker_idle.release
        conn = self.conn

        while True:
            # 1. 等待任务 (阻塞)
            # 只有当 send() 调用 release() 时，这里才会通过
            wait_for_work()

            if not self.running:
                break

            try:
                data, task_lock = self._task_slot
            except (TypeError, ValueError):
                # 防御性编程，处理可能的 None 或格式错误（虽然逻辑上不应发生）
                try:
                    signal_idle()
                except RuntimeError:
                    pass
                continue

            try:
                # 2. 发送数据
                # Equivalent to conn.send_bytes() but bypass all memorybuffer pre-checks
                conn._check_closed()
                conn._check_writable()
                conn._send_bytes(data)
            except AttributeError:
                # this shouldn't happen
                from alasio.logger import logger
                logger.error(f'[BackendBridge] Failed to send command: pipe connection not initialized')
                return False
            except (EOFError, OSError):
                if self.running:
                    from alasio.logger import logger
                    logger.error(f'[BackendBridge] Failed to send command: pipe broken')
                # if pipe is already closed, failed silently
                return False
            except Exception as e:
                from alasio.logger import logger
                logger.error(f'[BackendBridge] Failed to send command: {e}')
            finally:
                # 3. 通知用户（如果用户在关心结果）
                # release 会让等待这个锁的用户线程继续执行
                try:
                    task_lock.release()
                except RuntimeError:
                    pass
                # 4. 标记 Worker 空闲
                # 释放锁，允许下一个 send() 调用通过步骤 2
                try:
                    signal_idle()
                except RuntimeError:
                    pass

    def _handle_backend_command(self, data: bytes):
        event = decode(data, type=CommandEvent)
        command = event.c
        if command == 'scheduler-stopping':
            self.scheduler_stopping.set()
            return
        if command == 'scheduler-continue':
            self.scheduler_stopping.clear()
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

    def _recv_loop(self):
        conn = self.conn
        if not conn:
            # this shouldn't happen
            from alasio.logger import logger
            logger.error(f'[BackendBridge] Failed to recv command: pipe connection not initialized')
            return False

        while self.running:
            try:
                data = conn.recv_bytes()
            except (EOFError, OSError):
                if self.running:
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

    def close(self):
        """
        Gracefully close the BackendBridge

        This method will:
        1. Set the closing flag to stop background threads
        2. Close the pipe connection
        3. Wait for threads to finish (with timeout)
        """
        if not self.inited:
            return

        # Set closing flag to stop threads from logging errors
        self.running = False

        # Close connection to unblock recv_bytes() call
        if self.conn:
            try:
                self.conn.close()
            except:
                pass

        # Wake up send thread if it's waiting
        try:
            self._work_ready.release()
        except RuntimeError:
            pass

        # Wait for threads to finish with timeout
        if self._send_thread and self._send_thread.is_alive():
            self._send_thread.join(timeout=0.5)

        if self._recv_thread and self._recv_thread.is_alive():
            self._recv_thread.join(timeout=0.5)

        self.inited = False

    def send_log(self, value):
        return self.send(ConfigEvent(t='Log', v=value))

    def send_worker_state(self, value: Literal['running', 'scheduler-waiting']):
        return self.send(ConfigEvent(t='WorkerState', v=value))
