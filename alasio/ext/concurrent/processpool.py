import multiprocessing
import threading
import uuid
from collections import deque

from alasio.ext.backport import process_cpu_count
from alasio.ext.concurrent.processworker import worker_loop


# ===========================
# 辅助类定义
# ===========================

class Job:
    """
    任务对象，类似于 Future。
    持有任务状态、结果、异常以及用于同步的 Event。
    """

    def __init__(self, func_args, func_kwargs):
        self.id = uuid.uuid4()
        self.args = func_args
        self.kwargs = func_kwargs
        self.retries = 0
        self._last_exception = None
        self._status = 'PENDING'
        self._result = None
        self._event = threading.Event()

    def get(self):
        """阻塞直到任务完成，返回结果或抛出最后一次捕获的异常"""
        self._event.wait()
        if self._last_exception:
            raise self._last_exception
        return self._result

    def wait(self, timeout=None):
        """等待任务完成，不获取结果"""
        return self._event.wait(timeout)

    def _set_result(self, result):
        self._result = result
        self._status = 'FINISHED'
        self._event.set()

    def _set_error(self, exception):
        self._last_exception = exception
        self._status = 'ERROR'
        self._event.set()


class Worker:
    """
    Worker 封装类。
    将进程 ID、进程对象、通信管道和当前绑定的任务封装在一起。
    """

    def __init__(self, pid, process, conn):
        self.pid = pid
        self.process = process
        self.conn = conn  # 主进程端的 Pipe 连接
        self.current_job = None


def get_max_worker():
    """
    Get physical cpu core (non-logical) as possible
    because in python you cannot have performance boost using hyperthreading cores
    """
    count = process_cpu_count()
    try:
        import psutil
        physical_core = psutil.cpu_count(logical=False)
        return min(physical_core, count)
    except ImportError:
        return count


# ===========================
# ProcessPool 核心实现
# ===========================

class ProcessPool:
    """
    基于 multiprocessing.Pipe 的多进程并发工具，具有背压控制和故障恢复能力。

    ### 设计架构
    1.  **无内置队列 (No Internal Queue)**:
        不同于 multiprocessing.Pool，本工具不使用公共队列缓冲任务。
        任务直接由主进程通过独占的 Pipe 发送给特定的 Worker。

    2.  **背压控制 (Backpressure)**:
        使用 Semaphore(max_workers) 控制并发。当所有 Worker 繁忙时，
        `submit()` 会阻塞，防止内存中积压无限多的待处理任务。

    3.  **懒加载 (Lazy Creation)**:
        进程池初始化时不创建进程。仅在 submit 且没有闲置 Worker 时才创建新进程 (spawn)。
        闲置的 Worker 会被回收放入 LIFO 栈 (`_idle_workers`) 供下次复用。

    4.  **通信机制**:
        主进程与每个 Worker 之间维护一对 `multiprocessing.Pipe`。
        每个 Worker 对应主进程中的一个守护线程 (`Listener Thread`)，负责接收结果。

    ### 故障处理策略
    1.  **连接错误 (Connection Error)**:
        -   场景: Worker 进程崩溃、被 Kill、Pipe 断裂。
        -   处理: 丢弃该 Worker，增加任务重试计数，创建新 Worker 重试任务。

    2.  **数据错误 (Serialization Error)**:
        -   场景: `conn.send` 抛出 PickleError 或 TypeError。
        -   处理: 此时 Pipe 通道未被污染且 Worker 存活。标记任务失败，但**回收** Worker 以复用。

    3.  **重试机制**:
        -   任务具有 `retries` 计数。超出 `max_retry` 后任务标记为失败。
    """

    def __init__(self, worker_func, max_workers: int = None, max_retry=0):
        if max_workers is not None and max_workers < 1:
            raise ValueError('max_workers must >= 1')
        self.worker_func = worker_func
        self.max_workers = max_workers or get_max_worker()
        self.max_retry = max_retry

        # 信号量用于控制并发总数 (背压)
        self._capacity_sem = threading.Semaphore(self.max_workers)

        # 保护内部状态集合的锁
        self._lock = threading.Lock()
        self._shutdown = False

        # 状态存储
        self._workers = {}  # {pid: Worker} 所有存活的 Worker
        self._idle_workers = deque()  # [Worker, ...] 闲置 Worker 栈
        self._worker_count = 0

        # 条件变量：用于 wait 等待所有任务完成 (即所有 Worker 都变为空闲)
        self._state_cv = threading.Condition(self._lock)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._shutdown = True
        # 等待所有任务完成
        with self._state_cv:
            while len(self._workers) > len(self._idle_workers):
                self._state_cv.wait()
        # 清理资源
        self._terminate_all()

    def _notify_if_all_idle(self):
        """
        [Helper] 检查是否所有存活的 Worker 都处于闲置状态。
        如果是，通知等待在 _state_cv 上的线程 (如 __exit__)。
        注意：调用此方法前必须持有 self._lock。
        """
        if len(self._workers) == len(self._idle_workers):
            self._state_cv.notify_all()

    def submit(self, *args, **kwargs) -> Job:
        """
        提交任务。
        如果 Worker 已满，会阻塞直到有空闲资源。
        处理了 args/kwargs 序列化失败的场景，避免浪费 Worker 资源。
        """
        if self._shutdown:
            raise RuntimeError("ProcessPool is shutting down")

        # 1. 申请执行名额 (背压阻塞点)
        self._capacity_sem.acquire()
        job = Job(args, kwargs)

        while True:
            # 2. 获取 Worker (闲置或新建)
            worker = None
            with self._lock:
                if self._idle_workers:
                    worker = self._idle_workers.pop()

            if worker is None:
                # 假定 _spawn_worker 内部实现健壮，不会报错
                worker = self._spawn_worker()

            with self._lock:
                # 绑定任务状态
                worker.current_job = job

            # 3. 尝试发送任务 (IO 操作)
            try:
                worker.conn.send((args, kwargs))
                return job

            except (EOFError, OSError, BrokenPipeError) as e:
                # === 情况 A: 连接故障 (基础设施坏了) ===
                # 策略: 丢弃当前 Worker，循环重试获取下一个 Worker
                job.retries += 1
                job._last_exception = e

                # 解绑，防止 Listener 收到 EOF 后触发 Crash 恢复逻辑 (造成双重重试)
                with self._lock:
                    worker.current_job = None
                    # 注意: 不回收 worker，Listener 会处理清理工作

                if job.retries > self.max_retry:
                    self._finalize_job(job, e)
                    return job

                # 重新进入循环
                continue

            except Exception as e:
                # === 情况 B: 数据故障 (序列化失败) ===
                # 策略: 任务失败，但 Worker 是好的，回收它
                job._set_error(e)

                with self._lock:
                    worker.current_job = None
                    self._idle_workers.append(worker)  # 回收
                    self._notify_if_all_idle()  # 检查状态

                # 释放占用的名额
                self._capacity_sem.release()
                return job

    def _spawn_worker(self) -> Worker:
        """创建一个新的 Worker 进程和对应的监听线程"""
        parent_conn, child_conn = multiprocessing.Pipe()

        with self._lock:
            self._worker_count += 1
            name = f'ProcessPool-{self.worker_func.__name__}-worker{self._worker_count}'

        p = multiprocessing.Process(
            target=worker_loop,
            args=(child_conn, self.worker_func),
            name=name,
            daemon=True
        )
        p.start()

        worker = Worker(p.pid, p, parent_conn)
        self._workers[p.pid] = worker

        # 启动后台监听线程
        t = threading.Thread(
            target=self._listener_thread,
            args=(worker,),
            name=name,
            daemon=True
        )
        t.start()

        return worker

    def _listener_thread(self, worker: Worker):
        """
        后台线程，专门监听特定 Worker 的输出。
        """
        while True:
            try:
                status, payload = worker.conn.recv()
            except (EOFError, OSError, BrokenPipeError):
                # 管道断裂，进入崩溃恢复流程
                self._handle_crash(worker)
                return

            job = worker.current_job

            # 设置 Job 结果
            if job is not None:
                if status == 'OK':
                    job._set_result(payload)
                else:
                    job._set_error(payload)

            # 归还 Worker
            with self._lock:
                worker.current_job = None
                self._idle_workers.append(worker)
                self._notify_if_all_idle()

            # 释放信号量，允许新任务 submit
            self._capacity_sem.release()

    def _handle_crash(self, crashed_worker: Worker):
        """
        处理 Worker 崩溃。
        如果崩溃时手头有任务，则在重试次数内尝试复活任务。
        """
        # 1. 清理崩溃的 Worker 资源
        with self._lock:
            try:
                del self._workers[crashed_worker.pid]
            except KeyError:
                pass
            try:
                self._idle_workers.remove(crashed_worker)
            except (IndexError, ValueError):
                pass

            job = crashed_worker.current_job
            try:
                crashed_worker.conn.close()
            except:
                pass

        # 如果没有任务 (闲置时崩溃或已被解绑)，只需通知状态更新
        if job is None:
            with self._lock:
                self._notify_if_all_idle()
            return

        # 2. 准备重试任务
        if not job._last_exception:
            job._last_exception = BrokenPipeError(f"Worker {crashed_worker.pid} crashed unexpectedly.")

        while True:
            if job.retries >= self.max_retry:
                self._finalize_job(job, job._last_exception)
                return

            job.retries += 1

            # 创建新 Worker 接管任务 (假定 spawn 不报错)
            # 注意: 这里不操作信号量，因为我们继承了崩溃 Worker 的配额
            new_worker = self._spawn_worker()
            with self._lock:
                new_worker.current_job = job

            try:
                # 尝试发送重试数据
                new_worker.conn.send((job.args, job.kwargs))
                return

            except (EOFError, OSError, BrokenPipeError) as e:
                # 新 Worker 也是坏的 -> 继续重试循环
                job._last_exception = e
                with self._lock:
                    new_worker.current_job = None
                continue

            except Exception as e:
                # 重试时数据序列化失败 -> 致命错误
                # 此时 New Worker 阻塞在 recv，无法回收，必须销毁
                self._finalize_job(job, e)

                with self._lock:
                    new_worker.current_job = None
                    if new_worker.pid in self._workers:
                        del self._workers[new_worker.pid]
                    new_worker.process.terminate()
                    self._notify_if_all_idle()
                return

    def _finalize_job(self, job, exception):
        """任务彻底失败后的清理"""
        job._set_error(exception)
        self._capacity_sem.release()
        with self._lock:
            self._notify_if_all_idle()

    def _terminate_all(self):
        """强制终止所有进程"""
        with self._lock:
            for pid, worker in self._workers.items():
                if worker.process.is_alive():
                    worker.process.terminate()
                    worker.process.join()
            self._workers.clear()
            self._idle_workers.clear()
