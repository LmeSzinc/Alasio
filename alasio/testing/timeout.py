import time

from alasio.backport.sleep import sleep


class AssertTimeout:
    def __init__(self, timeout, interval=0.005):
        """
        Examples:
            for _ in AssertTimeout(0.5):
                with _:
                    # the following code will run repeatedly until all assertion passed or timeout
                    # if timeout, the last AssertionError will be raised
                    assert state.status == 'running'
                    assert state.process.is_alive()
        """
        self.timeout = timeout
        self.interval = interval
        self._start_time = None
        self._finished = False  # Flag to control the for loop

    def __iter__(self):
        """Control iteration logic"""
        self._start_time = time.perf_counter()
        while 1:
            yield self
            # If the with block failed (suppressed), code execution reaches here
            if self._finished:
                break
            elif self.interval > 0:
                # A precise sleep keeps the poll interval it was asked for: on
                # Windows, time.sleep() rounds the interval up to a whole
                # millisecond and the wake-up is quantized to the system tick
                sleep(self.interval)
            else:
                # A zero interval is a busy poll, time.sleep(0) yields the GIL
                # so the thread that is being polled keeps running
                time.sleep(0)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Scenario 1: Assertion passed (no exception)
        if exc_type is None:
            self._finished = True  # Mark as finished, notify __iter__ to stop loop
            return False  # Exit normally

        # Scenario 2: AssertionError occurred
        if issubclass(exc_type, AssertionError):
            elapsed = time.perf_counter() - self._start_time
            if elapsed < self.timeout:
                # Not timed out yet, return True to suppress exception and continue the loop
                return True
            else:
                # Timed out! Return False to raise the last AssertionError
                # The raised exception will preserve the stack trace of the original assert line
                return False

        # Scenario 3: Other exceptions occurred (e.g. NameError, TypeError)
        # Return False to raise immediately, no retry
        return False
