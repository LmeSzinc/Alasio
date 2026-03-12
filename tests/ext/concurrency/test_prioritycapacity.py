import threading
import time

from alasio.ext.concurrent.prioritycapacity import PriorityCapacityLimiter


def test_priority_capacity_limiter_thread_basic():
    """Test basic acquire and release functionality."""
    limiter = PriorityCapacityLimiter(total_tokens=2)

    # Take both tokens
    with limiter.use(priority=1):
        assert limiter.available_tokens == 1
        with limiter.use(priority=2):
            assert limiter.available_tokens == 0

    assert limiter.available_tokens == 2


def test_priority_capacity_limiter_thread_priority():
    """
    Test that priority is respected when threads are waiting.
    Lower priority value should be served first.
    """
    limiter = PriorityCapacityLimiter(total_tokens=1)
    results = []
    lock = threading.Lock()

    def task(name, priority, delay):
        with limiter.use(priority):
            with lock:
                results.append(f"start {name}")
            time.sleep(delay)
            with lock:
                results.append(f"end {name}")

    # Start task A with priority 10 (low) - starts immediately
    t_a = threading.Thread(target=task, args=("A", 10, 0.1), daemon=True)
    t_a.start()
    time.sleep(0.02)

    # At this point A holds the token.
    # Start task B (high) and C (medium)
    t_b = threading.Thread(target=task, args=("B", 0, 0.05), daemon=True)
    t_c = threading.Thread(target=task, args=("C", 5, 0.05), daemon=True)

    t_b.start()
    t_c.start()

    t_a.join()
    t_b.join()
    t_c.join()

    # Execution order should be A -> B -> C
    assert results == ["start A", "end A", "start B", "end B", "start C", "end C"]


def test_priority_capacity_limiter_thread_fifo():
    """
    Test that FIFO is respected for the same priority.
    """
    limiter = PriorityCapacityLimiter(total_tokens=1)
    results = []
    lock = threading.Lock()

    def task(name, priority, delay):
        with limiter.use(priority):
            with lock:
                results.append(f"start {name}")
            time.sleep(delay)
            with lock:
                results.append(f"end {name}")

    # Start task A to block the limiter
    t_a = threading.Thread(target=task, args=("A", 10, 0.1), daemon=True)
    t_a.start()
    time.sleep(0.02)

    # Submit two tasks with the same priority
    t_b = threading.Thread(target=task, args=("B", 5, 0.05), daemon=True)
    t_c = threading.Thread(target=task, args=("C", 5, 0.05), daemon=True)

    t_b.start()
    time.sleep(0.01)  # Ensure order of entry
    t_c.start()

    t_a.join()
    t_b.join()
    t_c.join()

    # Execution order should be A -> B -> C
    assert results == ["start A", "end A", "start B", "end B", "start C", "end C"]


def test_priority_capacity_limiter_thread_stress():
    """
    Concurrency stress test for PriorityCapacityLimiter.
    """
    TOTAL_TOKENS = 5
    NUM_TASKS = 100
    limiter = PriorityCapacityLimiter(total_tokens=TOTAL_TOKENS)
    results = []
    results_lock = threading.Lock()

    def worker(i, priority):
        with limiter.use(priority):
            with results_lock:
                results.append((priority, i))
            time.sleep(0.001 * (i % 5))

    threads = []
    for i in range(NUM_TASKS):
        priority = i % 10
        t = threading.Thread(target=worker, args=(i, priority), daemon=True)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    assert len(results) == NUM_TASKS
    assert limiter.available_tokens == TOTAL_TOKENS

    # Priority check
    q1 = [r[0] for r in results[:NUM_TASKS // 4]]
    q4 = [r[0] for r in results[3 * NUM_TASKS // 4:]]
    avg_q1 = sum(q1) / len(q1)
    avg_q4 = sum(q4) / len(q4)
    assert avg_q1 < avg_q4
