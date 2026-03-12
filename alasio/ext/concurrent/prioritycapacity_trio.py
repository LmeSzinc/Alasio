import heapq

import trio


class PriorityCapacityLimiter:
    def __init__(self, total_tokens):
        """
        A capacity limiter that respects task priority.

        Args:
            total_tokens (int): Maximum number of concurrent tasks
        """
        self.total_tokens = total_tokens
        self.available_tokens = total_tokens
        # Heap element format: (priority, sequence_number, trio.Event)
        self._waiters = []
        self._counter = 0

    async def acquire(self, priority):
        """
        Acquire a token with a given priority.

        Args:
            priority: Lower priority value takes precedence
        """
        # If there are remaining tokens, deduct and return immediately
        if self.available_tokens > 0:
            self.available_tokens -= 1
            return

        # Otherwise, enter the waiting queue
        event = trio.Event()
        self._counter += 1
        # To ensure FIFO for the same priority, we use an incrementing counter
        entry = (priority, self._counter, event)
        heapq.heappush(self._waiters, entry)

        try:
            await event.wait()
        except trio.Cancelled:
            # If the task is cancelled, tokens and the queue need to be handled
            if event.is_set():
                # If the token was already acquired before cancellation, release it
                self.release()
            else:
                # If still in queue, remove from heap (less efficient but ensures correctness)
                # Note: In Trio, queues are usually not extremely long, so removal is acceptable
                self._waiters.remove(entry)
                heapq.heapify(self._waiters)
            raise

    def release(self):
        """
        Release a token.
        """
        if self._waiters:
            # Wake up the task with the highest priority and earliest entry
            _, _, event = heapq.heappop(self._waiters)
            event.set()
        else:
            # If no one is waiting, return the token
            self.available_tokens += 1
            if self.available_tokens > self.total_tokens:
                self.available_tokens = self.total_tokens

    def use(self, priority):
        """
        Deduct tokens on entering and release on exiting.

        Args:
            priority: Lower priority value takes precedence

        Returns:
            PriorityCapacityLimiterUsage: Context manager to manage token lifecycle

        Examples:
            >>> limiter = PriorityCapacityLimiter(total_tokens=2)
            >>> async with limiter.use(priority=1):
            >>>     # do something with high priority
            >>>     pass
            >>> async with limiter.use(priority=10):
            >>>     # do something with low priority
            >>>     pass
        """
        return PriorityCapacityLimiterUsage(self, priority)


class PriorityCapacityLimiterUsage:
    def __init__(self, limiter, priority):
        """
        Context manager for PriorityCapacityLimiter.

        Args:
            limiter (PriorityCapacityLimiter): The limiter instance
            priority: Priority of the task
        """
        self.limiter = limiter
        self.priority = priority

    async def __aenter__(self):
        await self.limiter.acquire(self.priority)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.limiter.release()
