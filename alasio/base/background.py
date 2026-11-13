"""
Background tasks, one thread per name

A background task is a side-effecting function that the script does not wait
for: reading the resources on the main page with OCR while the stage already
starts, uploading a log, and so on. There is no future to await and no value to
collect, the task saves its result by itself. A caller that needs a return value
or the exception of the task should use the plain thread pool instead
(`alasio.ext.concurrent.threadpool.THREAD_POOL`).

Scheduling model:

- Every non-empty name owns at most one thread. The thread drains the pending
  queue of that name and exits when the queue is empty, so an idle name costs
  nothing and the next submission starts a new thread. The same name therefore
  never runs two tasks at the same time.
- An empty name is anonymous: the submission joins no queue at all, it runs in
  a thread of its own and cannot be dropped or replaced.
- A running task is never cancelled or replaced. Python threads cannot safely
  interrupt a native call (an OCR runs most of its time outside the
  interpreter), so new submissions only ever change the pending queue.
- There is no concurrency limit. The number of active names, plus the anonymous
  submissions, is the concurrency of the manager; the manager does not throttle.
- `shutdown()` is what the scheduler calls when the script stops: pending tasks
  are dropped, running tasks are waited for.

Examples:
    # the screenshot is taken by the caller, the background only reads it
    image = self.device.image
    BackgroundManager().submit(
        read_resources, self.config, image,
        name='resource', drop=DropPolicy.DROP_PENDING,
    )

    # a one-shot task that does not need a channel
    BackgroundManager().submit(upload_log, path)

    # inside the task
    def read_resources(config, image):
        value = Digit(OCR_OIL).ocr(image)
        if value is not None:
            config.Dashboard.Oil.set(value)
"""
import time
from collections import deque
from threading import Lock, Thread, current_thread

from alasio.ext.singleton import Singleton
from alasio.logger import logger


class DropPolicy:
    """
    What to do when the name of a submission already has pending work

    The policy of a submission is chosen by the caller, there is no default
    drop: `drop=None` keeps every submission, in submission order.

    Attributes:
        DROP_PENDING (str): Drop the pending tasks of the name and keep the new
            submission. This is the "latest wins" policy, the steady state of a
            name is one running task plus at most one pending task.
        DROP_NEW (str): Drop the new submission and keep the pending tasks.
            The method returns False for a dropped submission.
    """
    DROP_PENDING = 'drop_pending'
    DROP_NEW = 'drop_new'


class BackgroundClosed(Exception):
    """
    Raised when submitting to a manager that was shut down
    """
    pass


class BackgroundManager(metaclass=Singleton):
    """
    Run side-effect tasks on background threads, at most one thread per name

    A process-wide singleton: every module of the worker shares the manager, so
    a name is a channel of the process, not of one module. `BackgroundManager()`
    returns the same instance on every call, and the arguments of the first call
    are the ones that build it.

    See the module docstring for the scheduling model. A task must be a
    synchronous callable that only touches its arguments and long-lived state:
    it must not read `device`, `TaskState` or `config` of the running task,
    those belong to the caller's thread and may have moved on before the task
    runs.

    Attributes:
        name (str): Prefix of the thread names, for diagnostics
    """

    def __init__(self, name='background'):
        """
        Args:
            name (str): Prefix of the thread names and of the log tags
        """
        # Protects every field below. No thread ever waits on a condition:
        # without a concurrency limit a thread only checks the state once and
        # then either runs a task or exits, so a plain lock is enough.
        self._lock = Lock()
        self._closed = False
        # name -> deque of (func, args, kwargs)
        self._pending = {}
        # name -> Thread, at most one thread per name
        self._threads = {}
        # name -> monotonic start time, present only while a task runs
        self._executing = set()
        # seq -> Thread, the threads of the anonymous submissions
        self._anonymous = {}
        self._anon_seq = 0
        self._name = name

    def submit(self, func, *args, name='', drop=None, **kwargs):
        """
        Queue a task to run in the background

        Args:
            func (callable): Synchronous function to run in the background,
                its return value is discarded
            *args: Positional arguments of func, captured at submit time
            name (str): Serial channel of the submission. An empty name means
                an anonymous task, which joins no queue and runs in its own
                thread. Defaults to ''.
            drop (str): A DropPolicy value, None (the default) to never drop.
                Must be None when name is empty.
            **kwargs: Keyword arguments of func, captured at submit time

        Returns:
            bool: True if the task was queued, False if it was dropped

        Raises:
            BackgroundClosed: If the manager was shut down
        """
        if not name:
            return self._submit_anonymous(func, args, kwargs)

        dropped = []
        with self._lock:
            if self._closed:
                raise BackgroundClosed('background manager is closed')

            pending = self._pending.get(name)
            if pending is None:
                pending = deque()

            # ensure the thread before touching the queue: the new thread blocks
            # on `self._lock` until this block ends, so it cannot miss the task.
            # A queue without a live thread (only possible when a thread died on
            # a framework error) is repaired here, before a DROP_NEW may refuse
            # the submission and leave the queue stuck.
            thread = self._threads.get(name)
            if thread is None or not thread.is_alive():
                thread = Thread(
                    target=self._thread_main,
                    args=(name,),
                    name=f'{self._name}-{name}',
                    daemon=True,
                )
                self._threads[name] = thread
                try:
                    thread.start()
                except BaseException:
                    # roll back the registration, the queue was not modified yet
                    self._threads.pop(name, None)
                    raise

            # decide after the repair: dropping the submission changes no queue
            if drop == DropPolicy.DROP_NEW and pending:
                return False

            if drop == DropPolicy.DROP_PENDING and pending:
                dropped = list(pending)
                pending.clear()

            pending.append((func, args, kwargs))
            self._pending[name] = pending

        # release the dropped tasks outside the lock, their args may be large
        dropped.clear()
        return True

    def is_running(self, name):
        """
        Check whether the task of a name is executing right now

        Args:
            name (str): A non-empty name

        Returns:
            bool: True while a task of that name runs
        """
        with self._lock:
            return name in self._executing

    def pending_count(self, name):
        """
        Count the pending tasks of a name

        Args:
            name (str): A name

        Returns:
            int: Number of tasks waiting to run, 0 when the name is idle
        """
        with self._lock:
            return len(self._pending.get(name, ()))

    @property
    def running_count(self):
        """
        Count the tasks that are executing right now, named and anonymous

        This is the number of tasks `shutdown(wait=True)` actually waits for,
        derived from the thread tables instead of a counter that could drift.

        Returns:
            int:
        """
        with self._lock:
            return len(self._executing) + len(self._anonymous)

    @property
    def pending_total(self):
        """
        Count the pending tasks of every name

        Returns:
            int:
        """
        with self._lock:
            return sum(len(pending) for pending in self._pending.values())

    @property
    def thread_count(self):
        """
        Count the background threads, named and anonymous

        A named thread may exist without a running task, it is draining the
        pending queue or about to exit. Use `running_count` for the tasks.

        Returns:
            int:
        """
        with self._lock:
            return len(self._threads) + len(self._anonymous)

    def snapshot(self):
        """
        Read both counts in one critical section

        Returns:
            tuple[int, int]: (running, pending) of the same instant
        """
        with self._lock:
            running = len(self._executing) + len(self._anonymous)
            pending = sum(len(pending) for pending in self._pending.values())
        return running, pending

    def shutdown(self, wait=False, timeout=None):
        """
        Stop accepting submissions, drop the pending tasks, keep running ones

        The scheduler calls this when the script stops, so the tasks that are
        already writing their result are allowed to finish. A running task is
        never cancelled: it ends on its own, then its thread sees the closed
        manager and exits.

        Args:
            wait (bool): True to join every background thread. Defaults to False.
            timeout: Seconds to wait for the threads, None to wait forever.
                The timeout is a total budget, not a budget per thread.

        Returns:
            bool: True if no thread is alive afterwards, False if the timeout
                ended the wait first

        Raises:
            RuntimeError: If wait=True is called from a background thread of
                this manager, a thread cannot join itself
        """
        with self._lock:
            if wait:
                # one traversal serves both purposes: the snapshot to join and
                # the self-join check. A submission cannot register a thread
                # from here on, the manager is closed below
                threads = list(self._threads.values()) + list(self._anonymous.values())
                if current_thread() in threads:
                    raise RuntimeError('cannot join the background manager from its own thread')
            else:
                threads = []

            self._closed = True
            for pending in self._pending.values():
                pending.clear()
            self._pending.clear()

        if not wait:
            return True

        deadline = None if timeout is None else time.monotonic() + timeout
        for thread in threads:
            if deadline is None:
                thread.join()
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                thread.join(remaining)

        # the snapshot is complete, a thread that unregistered itself is a
        # thread that is not alive, so no second traversal is needed
        return not any(thread.is_alive() for thread in threads)

    def _submit_anonymous(self, func, args, kwargs):
        """
        Run a task that joins no queue, in a thread of its own

        Args:
            func (callable):
            args (tuple):
            kwargs (dict):

        Returns:
            bool: Always True, an anonymous task cannot be dropped
        """
        with self._lock:
            if self._closed:
                raise BackgroundClosed('background manager is closed')
            self._anon_seq += 1
            seq = self._anon_seq
            thread = Thread(
                target=self._run_anonymous,
                args=(seq, func, args, kwargs),
                name=f'{self._name}-anon-{seq}',
                daemon=True,
            )
            # registered before start(): shutdown(wait=True) joins the table and
            # a thread that was never started cannot be joined
            self._anonymous[seq] = thread
            try:
                thread.start()
            except BaseException:
                # an unstarted thread must not stay in the table, join() refuses it
                self._anonymous.pop(seq, None)
                raise
        return True

    def _thread_main(self, name):
        """
        Drain the pending queue of a name, then exit

        Args:
            name (str):
        """
        try:
            while True:
                with self._lock:
                    if self._closed:
                        return
                    pending = self._pending.get(name)
                    if not pending:
                        # nothing left: the next submit starts a new thread
                        return
                    func, args, kwargs = pending.popleft()
                    if not pending:
                        del self._pending[name]
                    # registered in the same critical section that takes the
                    # task, so running_count never misses a taken task
                    self._executing.add(name)

                try:
                    self._run_task(func, args, kwargs, name)
                finally:
                    del func, args, kwargs
                    with self._lock:
                        self._executing.discard(name)
        finally:
            with self._lock:
                # only unregister if the name still maps to this very thread
                if self._threads.get(name) is current_thread():
                    del self._threads[name]

    def _run_anonymous(self, seq, func, args, kwargs):
        """
        Run an anonymous task and unregister its thread

        Args:
            seq (int):
            func (callable):
            args (tuple):
            kwargs (dict):
        """
        try:
            self._run_task(func, args, kwargs, f'anon-{seq}')
        finally:
            with self._lock:
                self._anonymous.pop(seq, None)

    def _run_task(self, func, args, kwargs, tag):
        """
        Run one task, an exception must not kill the thread

        Args:
            func (callable):
            args (tuple):
            kwargs (dict):
            tag (str): The log tag, the name or anon-{seq}
        """
        try:
            func(*args, **kwargs)
        except Exception as exc:
            logger.exception(f'[Background] {tag} failed: {exc}')
        except BaseException as exc:
            # SystemExit and KeyboardInterrupt: a plain line, the caller did not
            # fail and the traceback of an exit signal is noise
            logger.error(f'[Background] {tag} aborted: {exc}')
