import threading
import time

import pytest

from alasio.ext.concurrent.prioritythreadpool import PriorityThreadPool


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
    测试优先级排序：
    1. 提交两个长耗时任务占满 2 个线程。
    2. 提交一个低优先级任务 (P10) 进入队列。
    3. 提交一个高优先级任务 (P0) 进入队列。
    4. 当长耗时任务结束，高优先级任务应先被执行。
    """
    pool = PriorityThreadPool(1)
    results = []
    lock = threading.Lock()

    def record_fn(name, priority, duration):
        time.sleep(duration)
        with lock:
            results.append(name)

    # 1. 占满池
    pool.enqueue(record_fn, 10, "Blocker-1", 10, 0.5)

    # 确保任务已经开始执行
    time.sleep(0.2)

    # 2. 提交低优，再提交高优
    pool.enqueue(record_fn, 10, "Low-Priority", 10, 0)
    pool.enqueue(record_fn, 0, "High-Priority", 0, 0)

    # 等待所有任务完成
    time.sleep(0.4)

    # 排序结果应该是：Blockers -> High-Priority -> Low-Priority
    # 注意：两个 Blocker 顺序不固定，但 High 必须在 Low 之前
    assert results[1] == "High-Priority"
    assert results[2] == "Low-Priority"


def test_dynamic_scaling_up():
    """测试动态扩容：连续提交任务应创建多个线程"""
    pool = PriorityThreadPool(4)

    # 连续提交 3 个耗时任务
    # 根据我们修复的“虚假空闲”逻辑，这应该触发 3 个线程创建
    pool.enqueue(task_fn, 10, n=1, duration=0.5)
    pool.enqueue(task_fn, 10, n=2, duration=0.5)
    pool.enqueue(task_fn, 10, n=3, duration=0.5)

    # 给一点点时间让线程启动
    time.sleep(0.1)

    # 检查当前活跃线程数
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

    # 此时应有 1 个线程在 idle 中
    assert len(pool.idle_workers) == 1

    # 等待超过 IDLE_TIMEOUT
    time.sleep(1.0)

    # 线程应该已经自动退出
    assert len(pool.idle_workers) == 0


def test_race_condition_stress():
    """压力测试：快速提交大量任务"""
    pool = PriorityThreadPool(10)
    count = 100
    jobs = [pool.enqueue(task_fn, i % 10, n=i, duration=0.01) for i in range(count)]

    results = [j.get() for j in jobs]
    assert len(results) == count
    assert len(pool.idle_workers) <= 10
