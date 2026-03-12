import pytest
import trio

from alasio.ext.concurrent.prioritycapacity_trio import PriorityCapacityLimiter


@pytest.mark.trio
async def test_priority_capacity_limiter_basic():
    """Test basic acquire and release functionality."""
    limiter = PriorityCapacityLimiter(total_tokens=2)

    # Take both tokens
    async with limiter.use(priority=1):
        assert limiter.available_tokens == 1
        async with limiter.use(priority=2):
            assert limiter.available_tokens == 0

    assert limiter.available_tokens == 2


@pytest.mark.trio
async def test_priority_capacity_limiter_priority():
    """
    Test that priority is respected when tasks are waiting.
    Lower priority value should be served first.
    """
    limiter = PriorityCapacityLimiter(total_tokens=1)
    results = []

    async def task(name, priority, delay):
        async with limiter.use(priority):
            results.append(f"start {name}")
            await trio.sleep(delay)
            results.append(f"end {name}")

    async with trio.open_nursery() as nursery:
        # Start task A with priority 10 (low) - starts immediately as it gets the token first
        nursery.start_soon(task, "A", 10, 0.1)
        # Give A time to start and acquire the token
        await trio.sleep(0.02)

        # Start task B with priority 0 (high) - should wait
        nursery.start_soon(task, "B", 0, 0.05)
        # Start task C with priority 5 (medium) - should wait
        nursery.start_soon(task, "C", 5, 0.05)

        # At this point, A is running, B and C are waiting in queue [0, 5]

    # Execution order should be A -> B -> C
    assert results == ["start A", "end A", "start B", "end B", "start C", "end C"]


@pytest.mark.trio
async def test_priority_capacity_limiter_fifo():
    """
    Test that FIFO is respected for the same priority.
    """
    limiter = PriorityCapacityLimiter(total_tokens=1)
    results = []

    async def task(name, priority, delay):
        async with limiter.use(priority):
            results.append(f"start {name}")
            await trio.sleep(delay)
            results.append(f"end {name}")

    async with trio.open_nursery() as nursery:
        # Start task A to block the limiter
        nursery.start_soon(task, "A", 10, 0.1)
        await trio.sleep(0.02)

        # Submit two tasks with the same priority
        nursery.start_soon(task, "B", 5, 0.05)
        await trio.sleep(0.01)  # Ensure order of entry
        nursery.start_soon(task, "C", 5, 0.05)

    # Execution order should be A -> B -> C
    assert results == ["start A", "end A", "start B", "end B", "start C", "end C"]


@pytest.mark.trio
async def test_priority_capacity_limiter_cancel_while_waiting():
    """
    Test cancellation branch: Task is still in queue when cancelled.
    Branch: `if not event.is_set(): self._waiters.remove(entry)`
    """
    limiter = PriorityCapacityLimiter(total_tokens=1)

    # Take the only token
    await limiter.acquire(priority=0)
    assert limiter.available_tokens == 0

    async def waiting_task():
        async with limiter.use(priority=1):
            pytest.fail("Should have been cancelled")

    async with trio.open_nursery() as nursery:
        nursery.start_soon(waiting_task)
        await trio.testing.wait_all_tasks_blocked()

        # Verify task is in queue
        assert len(limiter._waiters) == 1

        # Cancel the nursery (or specifically the task)
        nursery.cancel_scope.cancel()

    # After cancellation, waiter should be removed from heap
    assert len(limiter._waiters) == 0
    # Token still held by the manual acquire at start
    assert limiter.available_tokens == 0
    limiter.release()
    assert limiter.available_tokens == 1


@pytest.mark.trio
async def test_priority_capacity_limiter_cancel_after_notified():
    """
    Test cancellation branch: Task was notified (event.set()) but cancelled before it could return.
    Branch: `if event.is_set(): self.release()`
    """
    limiter = PriorityCapacityLimiter(total_tokens=1)

    # 1. Take the token
    await limiter.acquire(priority=0)

    # 2. Start a task that will wait
    task_started = trio.Event()

    async def waiting_task():
        task_started.set()
        async with limiter.use(priority=1):
            pass  # Token acquired

    async with trio.open_nursery() as nursery:
        nursery.start_soon(waiting_task)
        await task_started.wait()
        await trio.testing.wait_all_tasks_blocked()

        # Task is waiting in queue
        assert len(limiter._waiters) == 1

        # 3. Simulate getting notified and then immediately cancelled
        # We need to call release() which sets the event, and then cancel.
        # Trio scheduler ensures that if we cancel now, the task will see both the set event and the cancellation.
        # Actually, if we cancel the scope of the task, and the event is already set,
        # the task might still raise Cancelled if it hasn't successfully returned from the wait() checkpoint.

        # Release to notify the waiter
        limiter.release()
        assert len(limiter._waiters) == 0

        # Immediately cancel the nursery
        nursery.cancel_scope.cancel()

    # If the logic is correct, the 'notified but cancelled' task should have called release() again
    # because it "stole" the token but didn't get to use it.
    # Total tokens started at 1. We called release() once manually. 
    # The task should have called release() again when it caught Cancelled.
    # Thus available_tokens should be 1.
    assert limiter.available_tokens == 1


@pytest.mark.trio
async def test_priority_capacity_limiter_stress():
    """
    Concurrency stress test for PriorityCapacityLimiter.
    """
    TOTAL_TOKENS = 5
    NUM_TASKS = 200
    limiter = PriorityCapacityLimiter(total_tokens=TOTAL_TOKENS)
    results = []

    async def worker(i, priority):
        async with limiter.use(priority):
            results.append((priority, i))
            # Varying sleep times to increase contention/interleaving
            await trio.sleep(0.001 * (i % 5))

    async with trio.open_nursery() as nursery:
        for i in range(NUM_TASKS):
            # Interleave priorities: 0 (high) to 9 (low)
            priority = i % 10
            nursery.start_soon(worker, i, priority)

    # Verification:
    # 1. All tasks completed
    assert len(results) == NUM_TASKS
    # 2. No tokens leaked
    assert limiter.available_tokens == TOTAL_TOKENS
    # 3. Priority check (rough): high priority tasks should generally finish earlier in the log
    # Divide results into 4 quarters and check average priority
    q1 = [r[0] for r in results[:NUM_TASKS // 4]]
    q4 = [r[0] for r in results[3 * NUM_TASKS // 4:]]
    # Average priority in first quarter should be significantly lower than in last quarter
    avg_q1 = sum(q1) / len(q1)
    avg_q4 = sum(q4) / len(q4)
    assert avg_q1 < avg_q4


@pytest.mark.trio
async def test_priority_capacity_limiter_token_leak():
    """Test that tokens are strictly kept within total_tokens."""
    limiter = PriorityCapacityLimiter(total_tokens=2)

    # Release without acquire
    limiter.release()
    assert limiter.available_tokens == 2

    limiter.release()
    assert limiter.available_tokens == 2
