import heapq
import threading


class PriorityCapacityLimiter:
    def __init__(self, total_tokens):
        """
        A thread-safe capacity limiter that respects task priority.

        Args:
            total_tokens (int): Maximum number of concurrent tasks
        """
        self.total_tokens = total_tokens
        self.available_tokens = total_tokens
        self._lock = threading.Lock()
        # Heap element format: (priority, sequence_number, threading.Event)
        self._waiters = []
        self._counter = 0

    def acquire(self, priority):
        """
        Acquire a token with a given priority. Blocks until available.

        Args:
            priority: Lower priority value takes precedence
        """
        with self._lock:
            # If there are remaining tokens, deduct and return immediately
            if self.available_tokens > 0:
                self.available_tokens -= 1
                return

            # Otherwise, enter the waiting queue
            event = threading.Event()
            self._counter += 1
            # To ensure FIFO for the same priority, we use an incrementing counter
            entry = (priority, self._counter, event)
            heapq.heappush(self._waiters, entry)

        try:
            event.wait()
        except BaseException:
            # Handle potential interruptions (e.g., KeyboardInterrupt)
            with self._lock:
                if event.is_set():
                    # Token was already acquired before the exception
                    pass
                else:
                    # Still in queue, remove from heap
                    try:
                        self._waiters.remove(entry)
                        heapq.heapify(self._waiters)
                    except ValueError:
                        # Should not happen as we held the lock when adding
                        pass
                    raise

            # If we were notified but then interrupted, we must return the token
            self.release()
            raise

    def release(self):
        """
        Release a token.
        """
        with self._lock:
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
            >>> with limiter.use(priority=1):
            >>>     # do something with high priority
            >>>     pass
            >>> with limiter.use(priority=10):
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

    def __enter__(self):
        self.limiter.acquire(self.priority)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.limiter.release()
