# Supervisor file should not have any global import
# otherwise every child process will import them in spawn mode


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
        from typing import Optional
        from multiprocessing import Process
        from multiprocessing.connection import PipeConnection
        # The backend process instance
        self.process: Optional[Process] = None

        # Communication pipe - supervisor's end only
        self.parent_conn: "Optional[PipeConnection]" = None

        # Flag to indicate a restart is requested
        self.restart_requested = False

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
        import time
        now = time.time()

        # Clean up old restart times outside the window
        self.restart_times = [
            t for t in self.restart_times
            if now - t < self.restart_window
        ]

        # Check if we've exceeded max restarts
        if len(self.restart_times) >= self.max_restart_attempts:
            print(f"[Supervisor] ERROR: Backend has crashed {self.max_restart_attempts} "
                  f"times in {self.restart_window} seconds")
            print("[Supervisor] This indicates a persistent problem. Entering error state...")
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
        print("[Supervisor] Starting backend process...")

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

        print(f"[Supervisor] Backend running on PID: {self.process.pid}")

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
            while True:
                if not startup_success:
                    # First recv with timeout to detect startup failures
                    # If backend crashes within timeout, we'll get EOFError
                    # If timeout expires, backend is running successfully
                    wake = self.parent_conn.poll(timeout=self.startup_timeout)
                    if wake:
                        msg = self.parent_conn.recv_bytes()
                        startup_success = True
                        self.handle_message(msg)
                    else:
                        # Timeout on first recv - backend is alive and running
                        # This is actually good - backend started successfully
                        print(f"[Supervisor] Backend running for {self.startup_timeout}s, startup successful")
                        startup_success = True
                else:
                    # Subsequent recv() without timeout - block indefinitely
                    msg = self.parent_conn.recv_bytes()
                    self.handle_message(msg)

        except EOFError:
            # Pipe closed - backend exited
            if not startup_success:
                print("[Supervisor] Backend closed pipe connection during startup")
            else:
                print("[Supervisor] Backend closed pipe connection")
            # Clean up pipe
            try:
                self.parent_conn.close()
            except Exception:
                pass
            self.parent_conn = None
            return startup_success

        except OSError as e:
            # Pipe error
            if not startup_success:
                print(f"[Supervisor] Pipe error during startup: {e}")
            else:
                print(f"[Supervisor] Pipe error: {e}")
            # Clean up pipe
            try:
                self.parent_conn.close()
            except Exception:
                pass
            self.parent_conn = None
            return startup_success

        except Exception:
            # Clean up pipe
            # note that we don't use finally: clause, so KeyboardInterrupt can exit with parent_conn open
            try:
                self.parent_conn.close()
            except Exception:
                pass
            self.parent_conn = None
            raise

    def wait_for_backend(self) -> int:
        """
        Wait for backend process to exit.

        Returns:
            Exit code of the backend process
        """
        if not self.process:
            return -1

        try:
            self.process.join()
            exitcode = self.process.exitcode or 0
            print(f"[Supervisor] Backend exited with code: {exitcode}")
            return exitcode
        except Exception as e:
            print(f"[Supervisor] Error waiting for backend: {e}")
            return -1

    def handle_message(self, msg):
        """
        Handle a message received from the backend.

        Messages are simple byte strings representing commands.

        Args:
            msg (bytes): The message received from backend (expected to be bytes)
        """
        if msg == b'restart':
            print("[Supervisor] Backend requested restart")
            self.restart_requested = True
            if not self.graceful_shutdown():
                self.force_shutdown()
        else:
            print(f"[Supervisor] WARNING: Unknown command from backend: {msg}")

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

        if self.sigint_count == 1:
            # First CTRL+C - trigger graceful shutdown
            raise KeyboardInterrupt()
        elif self.sigint_count == 2:
            # Second CTRL+C - trigger force kill
            raise KeyboardInterrupt()
        else:
            # Third+ CTRL+C - ignore, already shutting down
            print(f"\n[Supervisor] Already shutting down, please wait... (CTRL+C #{self.sigint_count})")

    def graceful_shutdown(self):
        """
        Send SIGINT signal to the backend process.

        Returns:
            bool: If success
        """
        if self.process and self.process.is_alive():
            if self.parent_conn:
                try:
                    self.parent_conn.send_bytes(b'stop')
                except Exception as e:
                    print(f"[Supervisor] Error sending stop to backend: {e}")

            # Wait for backend to exit gracefully
            self.process.join(timeout=self.graceful_shutdown_timeout)

            if self.process.is_alive():
                print(f"[Supervisor] Backend didn't shutdown after {self.graceful_shutdown_timeout} seconds, "
                      "will force kill in cleanup")
                return False
            else:
                return True

        # nothing to kill, consider success
        return True

    def force_shutdown(self):
        """
        Returns:
            bool: If success
        """
        if self.process and self.process.is_alive():
            try:
                self.process.kill()
                self.process.join(timeout=2)
                print("[Supervisor] Backend force killed")
                return True
            except Exception as e:
                print(f"[Supervisor] Error force killing backend: {e}")
                return False

        # nothing to kill, consider success
        return True

    @staticmethod
    def _interruptible_sleep(duration):
        """
        Sleep for a duration, can be interrupted by KeyboardInterrupt.

        We break the sleep into chunks to make it more responsive,
        but KeyboardInterrupt will still work at any time.

        Args:
            duration (float | int): Time to sleep in seconds
        """
        import time
        elapsed = 0
        chunk = 0.5

        while elapsed < duration:
            sleep_time = min(chunk, duration - elapsed)
            time.sleep(sleep_time)
            elapsed += sleep_time

    def cleanup(self):
        """
        Clean up resources and ensure backend is terminated.
        """
        if self.process:
            if self.process.is_alive():
                print("[Supervisor] Terminating backend process...")
                try:
                    self.process.terminate()
                    self.process.join(timeout=5)

                    if self.process.is_alive():
                        print("[Supervisor] Backend didn't terminate, force killing...")
                        self.process.kill()
                        self.process.join()

                except Exception as e:
                    print(f"[Supervisor] Error during cleanup: {e}")

            self.process = None

        # Clean up pipe
        if self.parent_conn:
            self.parent_conn.close()
            self.parent_conn = None

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
            print("[Supervisor] ERROR: backend entry is still placeholder, nothing to run")
            return

        import os
        print(f"[Supervisor] Running on PID: {os.getpid()}")

        # Set up custom SIGINT handler to track CTRL+C count
        import signal
        signal.signal(signal.SIGINT, self.handle_sigint)

        import sys
        args = sys.argv[1:]

        try:
            # Main supervision loop
            while True:
                # Start the backend
                self.start_backend(args)

                # Listen for messages from backend
                # This blocks until backend exits (pipe closes)
                startup_success = self.recv_loop()
                self.wait_for_backend()

                # Check if this was a startup failure
                if not startup_success:
                    print("[Supervisor] ERROR: Backend failed to start properly")
                    break

                # Check if restart was requested by backend
                if self.restart_requested:
                    self.restart_requested = False
                    continue

                # Check if we should restart or enter error state
                if not self._check_restart_limit():
                    print(f"[Supervisor] Restart limit exceeded "
                          f"({self.restart_times} times in {self.max_restart_attempts} seconds)")
                    break

                print(f"[Supervisor] Restarting in {self.restart_delay} seconds...")
                self._interruptible_sleep(self.restart_delay)
                continue

        except KeyboardInterrupt:
            # First CTRL+C - initiate graceful shutdown
            try:
                print("\n[Supervisor] Received SIGINT, initiating graceful shutdown...")
                if not self.graceful_shutdown():
                    self.force_shutdown()
            except KeyboardInterrupt:
                # Second CTRL+C - force kill immediately
                print(f"\n[Supervisor] Received SIGINT, force killing backend...")
                self.force_shutdown()

        except Exception as e:
            print(f"\n[Supervisor] Unexpected error: {e}")
            import traceback
            traceback.print_exc()

        finally:
            # Always clean up
            self.cleanup()
            print("[Supervisor] Supervisor loop ended")
