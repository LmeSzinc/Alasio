import threading
import time

import pytest

from alasio.ext.concurrent.prioritythreadpool import PriorityThreadPool
from alasio.testing.timeout import AssertTimeout

pytestmark = [
    pytest.mark.parallel,
]


# 辅助函数：模拟耗时任务
def task_fn(n, duration=0.1):
    time.sleep(duration)
    return n * 2


def error_fn():
    raise ValueError("Expected error")


def test_basic_execution():
    """测试基础提交和结果获取"""
    pool = PriorityThreadPool(2)
    job = pool.enqueue(task_fn, priority=10, n=5, duration=0.1)
    assert job.get() == 10


def test_exception_handling():
    """测试任务异常捕获"""
    pool = PriorityThreadPool(1)
    job = pool.enqueue(error_fn, 0)
    with pytest.raises(ValueError, match="Expected error"):
        job.get()


def test_priority_ordering():
    """
    Test the priority order of a busy pool.

    1. Occupy the only thread with a long job.
    2. Enqueue a low priority job (P10).
    3. Enqueue a high priority job (P0).
    4. When the long job ends, the high priority job runs first.

    The jobs are waited on, never slept on: a fixed delay has to cover the
    long job plus the scheduling of two more, and a loaded runner overshoots
    it (the CI failure this pins: the assertions ran while the last job was
    still queued, so the results list was short).
    """
    pool = PriorityThreadPool(1)
    results = []
    lock = threading.Lock()
    started = threading.Event()

    def record_fn(name, duration):
        started.set()
        time.sleep(duration)
        with lock:
            results.append(name)

    # 1. Occupy the pool, and wait for the job to really run before queueing
    # the others: enqueued against a free thread, the low priority job could
    # be taken first and the order would prove nothing
    blocker = pool.enqueue(record_fn, 10, "Blocker-1", 0.5)
    assert started.wait(timeout=5), 'the blocker did not start'

    # 2. Low priority first, then high priority
    low = pool.enqueue(record_fn, 10, "Low-Priority", 0)
    high = pool.enqueue(record_fn, 0, "High-Priority", 0)

    # 3. Wait for every job to finish, then assert the whole order
    for job in (blocker, high, low):
        job.get()

    assert results == ["Blocker-1", "High-Priority", "Low-Priority"]


def test_dynamic_scaling_up():
    """测试动态扩容：连续提交任务应创建多个线程"""
    pool = PriorityThreadPool(4)

    # 连续提交 3 个耗时任务
    # 根据我们修复的“虚假空闲”逻辑，这应该触发 3 个线程创建
    pool.enqueue(task_fn, 10, n=1, duration=0.5)
    pool.enqueue(task_fn, 10, n=2, duration=0.5)
    pool.enqueue(task_fn, 10, n=3, duration=0.5)

    # Wait for the pool to reach the three workers instead of assuming the
    # threads are up after a fixed delay: a worker that finds the queue empty
    # between two enqueues registers itself idle, and the next enqueue then
    # reuses it -- the pool settles at three a moment later, or never (which
    # is what the assertion is about)
    for _ in AssertTimeout(2.0):
        with _:
            assert len(pool.all_workers) == 3
    # 检查 idle 情况，此时因为都在 sleep，应该都不在 idle 字典里
    assert len(pool.idle_workers) == 0


def test_scale_down():
    """测试自动缩容"""
    # 将缩容时间设短一点方便测试
    pool = PriorityThreadPool(4)
    pool.IDLE_TIMEOUT = 0.5

    # 运行一个任务并等待完成
    job = pool.enqueue(task_fn, 10, n=1, duration=0.1)
    job.get()

    # The result of the job is delivered before the worker loops back and
    # registers itself idle, so the pool is polled rather than read once
    for _ in AssertTimeout(2.0):
        with _:
            assert len(pool.idle_workers) == 1

    # The worker retires by itself once its timed wait expires
    for _ in AssertTimeout(3.0):
        with _:
            assert len(pool.idle_workers) == 0


def test_race_condition_stress():
    """压力测试：快速提交大量任务"""
    pool = PriorityThreadPool(10)
    count = 100
    jobs = [pool.enqueue(task_fn, i % 10, n=i, duration=0.01) for i in range(count)]

    results = [j.get() for j in jobs]
    assert len(results) == count
    assert len(pool.idle_workers) <= 10
