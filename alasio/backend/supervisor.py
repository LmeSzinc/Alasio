import os
import signal
import sys
import threading
import time


def loop_until_timeout(timeout):
    end = time.time() + timeout
    while 1:
        yield
        if time.time() >= end:
            break


def mprint(*args, start=''):
    print(f'{start}[Supervisor]', *args)


class Supervisor:
    def __init__(
            self,
            restart_delay: int = 3,
            max_restart_attempts: int = 10,
            restart_window: int = 60,
            startup_timeout: float = 5.0,
            graceful_shutdown_timeout: float = 5.0
    ):
        """
        Supervisor process for Alasio backend

        Args:
            restart_delay: Seconds to wait before restarting after crash
            max_restart_attempts: Max restarts within restart_window before giving up
            restart_window: Time window (seconds) to count restart attempts
            startup_timeout: Seconds to wait before considering startup successful.
                           If backend crashes within this time, it's a startup failure
                           and supervisor will NOT retry.
            graceful_shutdown_timeout: Seconds to wait for graceful shutdown before
                                      force killing the backend process.
        """
        # The backend process instance
        self.process: "multiprocessing.Process | None" = None

        # Communication pipe - supervisor's end only
        self.parent_conn: "multiprocessing.PipeConnection | None" = None

        # Flag to indicate a restart is requested
        self.restart_requested = False
        # main thread id
        self.main_tid = threading.get_ident()

        # Restart configuration
        self.restart_delay = restart_delay
        self.max_restart_attempts = max_restart_attempts
        self.restart_window = restart_window
        self.startup_timeout = startup_timeout
        self.graceful_shutdown_timeout = graceful_shutdown_timeout

        # Track restart attempts to prevent infinite loops
        self.restart_times = []

        # Track SIGINT count to handle multiple CTRL+C presses
        self.sigint_count = 0

    def _check_restart_limit(self) -> bool:
        """
        Check if we've hit the restart limit within the time window.

        Returns:
            True if restart is allowed, False if limit exceeded
        """
        now = time.time()

        # Clean up old restart times outside the window
        self.restart_times = [
            t for t in self.restart_times
            if now - t < self.restart_window
        ]

        # Check if we've exceeded max restarts
        if len(self.restart_times) >= self.max_restart_attempts:
            mprint(f"ERROR: Backend has crashed {self.max_restart_attempts} times in {self.restart_window} seconds")
            mprint("This indicates a persistent problem. Entering error state...")
            return False

        # Record this restart attempt
        self.restart_times.append(now)
        return True

    @staticmethod
    def backend_entry(args):
        """
        Subclasses must override this method

        Args:
            args (list[str]):
        """
        pass

    def process_entry(self, conn, args):
        """
        Entry point for the backend process.

        This function runs in the child process. It sets up the pipe connection
        as a global variable that the backend code can access, then starts the
        actual backend application.
        """
        import builtins
        builtins.__mpipe_conn__ = conn

        import signal
        import sys
        # ignore SIGINT on windows because signal is send to the entire process group
        # Supervisor should receive SIGINT and backend should ignore, then supervisor tell backend to stop
        if sys.platform == "win32":
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            signal.signal(signal.SIGBREAK, signal.SIG_IGN)

        try:
            self.backend_entry(args)
        except Exception as e:
            # Unexpected error in backend
            print(f"[Backend] Fatal error: {e}")
            import traceback
            traceback.print_exc()
        # Note that it's parent's responsibility to close pipe

    def start_backend(self, args):
        """
        Start the backend process with pipe communication.

        Returns:
            True if backend started successfully, False otherwise
        """
        mprint("Starting backend process...")

        # Cleanup any parent_conn
        if self.parent_conn:
            try:
                self.parent_conn.close()
            except Exception:
                pass
            self.parent_conn = None

        # Run subprocess in spawn mode
        import multiprocessing
        ctx = multiprocessing.get_context('spawn')

        parent_conn, child_conn = ctx.Pipe()
        self.process = ctx.Process(
            target=self.process_entry,
            args=(child_conn, args),
            name='alasio-backend',
            daemon=True,
        )

        # Start the process
        self.process.start()
        # close child_conn of the parent side immediately
        child_conn.close()
        self.parent_conn = parent_conn

        mprint(f"Backend running on PID: {self.process.pid}")

    def recv_loop(self) -> bool:
        """
        Listen for messages from backend via pipe.

        Uses a timeout on the first recv() to detect startup failures:
        - If backend crashes within startup_timeout: startup failure (return False)
        - If backend survives startup_timeout: startup success (return True)

        This blocks until the pipe is closed (backend exits) or a message arrives.

        Returns:
            True if backend successfully started, False if startup failed
        """
        if not self.parent_conn:
            return False

        startup_success = False
        try:
            # First recv with timeout to detect startup failures
            # If backend crashes within timeout, we'll get EOFError
            # If backed emits any message, backend is running successfully
            # If timeout reached, backend is running successfully
            for _ in loop_until_timeout(timeout=self.startup_timeout):
                wake = self.parent_conn.poll(timeout=0.2)
                if wake:
                    msg = self.parent_conn.recv_bytes()
                    mprint(f"Backend emits message, startup successful")
                    self.handle_backend_message(msg)
                    break
            else:
                mprint(f"Backend running for {self.startup_timeout}s, startup successful")

            startup_success = True

            # wait infinitely
            while 1:
                wake = self.parent_conn.poll(timeout=0.2)
                if wake:
                    msg = self.parent_conn.recv_bytes()
                    self.handle_backend_message(msg)

        except EOFError:
            # Pipe closed - backend exited
            if not startup_success:
                mprint("Backend closed pipe connection during startup")
            else:
                mprint("Backend closed pipe connection")
            return startup_success

        except OSError as e:
            # Pipe error
            if not startup_success:
                mprint(f"Pipe error during startup: {e}")
            else:
                mprint(f"Pipe error: {e}")
            return startup_success

    def handle_backend_message(self, msg):
        """
        Handle a message received from the backend.

        Messages are simple byte strings representing commands.

        Args:
            msg (bytes): The message received from backend (expected to be bytes)
        """
        if msg == b'restart':
            mprint("Backend requested restart")
            self.restart_requested = True
        elif msg == b'stop':
            mprint("Backend requested stop")
            self.handle_sigint(signal.SIGINT, None)
        else:
            mprint(f"WARNING: Unknown command from backend: {msg}")

    def handle_sigint(self, signum, frame):
        """
        Custom SIGINT handler to track CTRL+C presses.

        - First CTRL+C: Trigger graceful shutdown
        - Second CTRL+C: Trigger force kill
        - Third+ CTRL+C: Ignored (already shutting down)

        Args:
            signum: Signal number
            frame: Current stack frame
        """
        self.sigint_count += 1
        try:
            sig = signal.Signals(signum).name
        except ValueError:
            sig = f'Unknown-signal-{signum}'

        if self.sigint_count == 1:
            # First CTRL+C - trigger graceful shutdown
            mprint(f"Received {sig}, initiating graceful shutdown...", start='\n')
            raise KeyboardInterrupt
        elif self.sigint_count == 2:
            # Second CTRL+C - trigger force kill
            mprint(f"Received {sig}, force killing backend...", start='\n')
            raise KeyboardInterrupt
        else:
            # Third+ CTRL+C - ignore, already shutting down
            mprint(f"Already shutting down, please wait... (CTRL+C #{self.sigint_count})", start='\n')

    def wait_for_backend(self) -> int:
        """
        Wait for backend process to exit.

        Returns:
            Exit code of the backend process
        """
        if not self.process:
            return -1

        try:
            while 1:
                self.process.join(0.2)
                exitcode = self.process.exitcode
                if exitcode is not None:
                    mprint(f"Backend exited with code: {exitcode}")
                    return exitcode
        except Exception as e:
            mprint(f"Error waiting for backend: {e}")
            return -1

    def graceful_shutdown(self):
        """
        Send 'stop' to the backend process.

        Returns:
            bool: If success
        """
        if not self.process or not self.process.is_alive():
            # nothing to kill, consider success
            return True

        if self.parent_conn:
            try:
                self.parent_conn.send_bytes(b'stop')
            except Exception as e:
                mprint(f"ERROR: Failed to sending stop to backend: {e}")

        # Wait for backend to exit gracefully
        for _ in loop_until_timeout(timeout=self.graceful_shutdown_timeout):
            # interruptable join(), so KeyboardInterrupt can be injected here
            self.process.join(timeout=0.2)
            if not self.process.is_alive():
                break
        else:
            mprint(f"Backend didn't shutdown after {self.graceful_shutdown_timeout} seconds, "
                   "will force kill in cleanup")
            return False

        # cleanup on success
        self.process = None
        self._cleanup_conn()

    def force_shutdown(self):
        """
        Returns:
            bool: If success
        """
        if not self.process or not self.process.is_alive():
            # nothing to kill, consider success
            return True

        try:
            self.process.kill()
            self.process.join(timeout=2)
            mprint("Backend force killed")
        except Exception as e:
            mprint(f"ERROR: Failed to force kill backend: {e}")
            return False

        # cleanup on success
        self.process = None
        self._cleanup_conn()

    def _cleanup_conn(self):
        if self.parent_conn:
            try:
                self.parent_conn.close()
            except Exception:
                pass
            self.parent_conn = None

    def cleanup(self):
        """
        Clean up resources and ensure backend is terminated.
        """
        if self.process:
            if self.process.is_alive():
                mprint("Terminating backend process...")
                try:
                    self.process.terminate()
                    self.process.join(timeout=5)

                    if self.process.is_alive():
                        mprint("Backend didn't terminate, force killing...")
                        self.process.kill()
                        self.process.join()

                except Exception as e:
                    mprint(f"Error during cleanup: {e}")

            self.process = None

        # Clean up pipe
        self._cleanup_conn()

    def run(self):
        """
        Main supervisor loop.

        Simplified control flow using exceptions:
        - Normal flow: start backend, listen to pipe, handle restart
        - CTRL+C: KeyboardInterrupt caught, graceful shutdown
        - Errors: caught and handled appropriately
        """
        # backend entry should not be placeholder
        if self.backend_entry == Supervisor.backend_entry:
            mprint("ERROR: backend entry is still placeholder, nothing to run")
            return

        mprint(f"Running on PID: {os.getpid()}")

        args = sys.argv[1:]

        # Set up custom SIGINT handler to track CTRL+C count
        signal.signal(signal.SIGINT, self.handle_sigint)
        signal.signal(signal.SIGTERM, self.handle_sigint)
        if sys.platform == "win32":
            signal.signal(signal.SIGBREAK, self.handle_sigint)

        try:
            # Main supervision loop
            while True:
                # Start the backend
                self.cleanup()
                self.start_backend(args)

                # Listen for messages from backend
                # This blocks until backend exits (pipe closes)
                startup_success = self.recv_loop()
                self.wait_for_backend()

                # Check if this was a startup failure
                if not startup_success:
                    mprint("ERROR: Backend failed to start properly")
                    break

                # Check if restart was requested by backend
                if self.restart_requested:
                    self.restart_requested = False
                    continue

                # Check if we should restart or enter error state
                if not self._check_restart_limit():
                    mprint(f"Restart limit exceeded "
                           f"({self.restart_times} times in {self.max_restart_attempts} seconds)")
                    break

                mprint(f"Restarting in {self.restart_delay} seconds...")
                for _ in loop_until_timeout(timeout=self.restart_delay):
                    time.sleep(0.2)
                continue

        except KeyboardInterrupt:
            # First CTRL+C - initiate graceful shutdown
            try:
                if not self.graceful_shutdown():
                    self.force_shutdown()
            except KeyboardInterrupt:
                # Second CTRL+C - force kill immediately
                self.force_shutdown()

        except Exception as e:
            mprint(f"Unexpected error: {e}", start='\n')
            import traceback
            traceback.print_exc()

        finally:
            # Always clean up
            self.cleanup()
            mprint("Supervisor loop ended")
