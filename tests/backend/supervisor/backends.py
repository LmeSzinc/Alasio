import sys
import time

from alasio.backend.supervisor import Supervisor

# Both the supervisor process (python backends.py) and the backend process
# (multiprocessing spawn re-runs this module) execute this top-level code.
# Their stdout/stderr are pipes, not a tty, so Python would block-buffer
# them: ManagedProcess would not see any log line in time. Line buffering
# makes every print (including supervisor's mprint) visible immediately,
# so the tests do not need PYTHONUNBUFFERED.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        # Not a TextIOWrapper or already closed, leave it alone
        pass


# -----------------------------------------------------------------------------
# Step protocol
#
# A test can walk a fake backend through its scenario instead of letting it act
# on a fixed timeline. The test writes a line to the supervisor stdin
# (ManagedProcess.send_command), the supervisor forwards every command:* line
# to the backend process verbatim (it only handles command:stop itself), and the
# backend resolves it in wait_for_step below.
#
# A step command looks like `command:step:<name>`. Every fake backend prints
# "waiting for step:<name>" before it blocks, so the test waits for that line
# (an event) and only then releases the step: the ordering is decided by the
# test's own observations, never by a sleep.
# -----------------------------------------------------------------------------
STEP_PREFIX = 'command:step:'


def wait_for_step(conn, step, timeout=60.0):
    """
    等待测试发来的 step 命令（由 supervisor 从 stdin 转发过来）

    Args:
        conn (multiprocessing.Connection): Pipe to the supervisor
        step (str): Step name, e.g. "restart"
        timeout (float): Seconds to wait before giving up

    Raises:
        RuntimeError: The step did not arrive (the test gave up on this
            process or forgot to release the step)
    """
    expected = f'{STEP_PREFIX}{step}'.encode()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not conn.poll(timeout=0.05):
            continue
        msg = conn.recv_bytes()
        if msg == expected:
            return
        # Other traffic (a stop forwarded by the supervisor) is not part of
        # the scenario: report it and keep waiting
        print(f"[Backend] Ignoring {msg!r} while waiting for step:{step}")
    raise RuntimeError(f'[Backend] Timed out waiting for step:{step}')


class TestSupervisor(Supervisor):
    """测试用的Supervisor子类，根据命令行参数启动不同的后端"""

    def run(self, args=None):
        args = sys.argv[1:]
        return super().run(args)

    @staticmethod
    def backend_entry(args):
        """根据命令行参数启动不同类型的后端"""
        if not args:
            print("[Backend] No backend type specified")
            sys.exit(1)

        backend_type = args[0]

        if backend_type == "normal":
            # 正常启动，无限等待
            TestSupervisor._normal_backend()
        elif backend_type == "silent":
            # 正常启动但不发送启动确认消息（靠 startup_timeout 超时确认）
            TestSupervisor._silent_backend()
        elif backend_type == "immediate_error":
            # 立刻报错
            TestSupervisor._immediate_error_backend()
        elif backend_type == "early_exit":
            # startup_timeout 内退出（启动失败路径）
            TestSupervisor._early_exit_backend()
        elif backend_type == "late_exit":
            # 启动确认后退出（触发重启）
            TestSupervisor._late_exit_backend()
        elif backend_type == "slow_shutdown":
            # 收到stop信号后，延迟退出的后端
            TestSupervisor._slow_shutdown_backend()
        elif backend_type == "restart_early":
            # 启动窗口内发送restart请求
            TestSupervisor._restart_early_backend()
        elif backend_type == "restart_late":
            # 启动确认后发送restart请求
            TestSupervisor._restart_late_backend()
        elif backend_type == "stop_early":
            # 启动窗口内发送stop请求
            TestSupervisor._stop_early_backend()
        elif backend_type == "stop_late":
            # 启动确认后发送stop请求
            TestSupervisor._stop_late_backend()
        elif backend_type == "crash_after_success":
            # 启动成功后立即崩溃
            TestSupervisor._crash_after_success_backend()
        elif backend_type == "prefs":
            # 接收 stdin 转发来的 set_lang/set_theme 命令并持久化到 deploy.yaml
            TestSupervisor._prefs_backend()
        else:
            print(f"[Backend] Unknown backend type: {backend_type}")
            sys.exit(1)

    @staticmethod
    def _announce_spawned():
        """
        Announce spawn completion like the real backend does

        entry.backend_process_entry sends command:spawned right after boot, and
        the supervisor uses it to start the stdin listener while the startup
        window is still open (it is explicitly *not* a startup confirmation).
        Without it a fake backend could not be driven by stdin before its
        startup window ends.
        """
        import builtins
        builtins.__mpipe_conn__.send_bytes(b'command:spawned')

    @staticmethod
    def _notify_startup_success():
        """
        Confirm startup to the supervisor through the pipe.

        The supervisor's recv_loop treats the first backend message as the
        startup-success signal, so the test backends confirm startup
        explicitly instead of making every test wait out startup_timeout.
        The message is not a recognized command: the supervisor logs a
        warning and keeps running, which is harmless in tests.
        """
        import builtins
        builtins.__mpipe_conn__.send_bytes(b'ok')

    @staticmethod
    def _silent_backend():
        """
        正常启动但不发送启动确认的后端

        真实后端现在会在监听端口绑定成功后发送 command:started 确认启动；
        这个后端模拟的是确认消息缺失时的兜底路径：supervisor 只能靠
        startup_timeout 超时来确认启动成功。
        """
        import builtins
        print("[Backend] Silent backend started, waiting indefinitely...")

        conn = builtins.__mpipe_conn__
        try:
            # 持续监听pipe消息
            while True:
                if conn.poll(timeout=0.5):
                    msg = conn.recv_bytes()
                    if msg == b'command:stop':
                        print("[Backend] Received stop signal, shutting down gracefully")
                        time.sleep(0.1)
                        break
                    else:
                        print(f"[Backend] Received message: {msg}")
        except EOFError:
            print("[Backend] Pipe closed")
        except KeyboardInterrupt:
            print("[Backend] Interrupted")

        print("[Backend] Silent backend exiting")

    @staticmethod
    def _normal_backend():
        """正常启动，无限等待的后端"""
        import builtins
        print("[Backend] Normal backend started, waiting indefinitely...")

        conn = builtins.__mpipe_conn__
        # Confirm startup as the first message: the supervisor's startup window
        # ends on the first backend message, so no delay is needed to give an
        # interrupt a window to land in (the window is open until the message)
        TestSupervisor._notify_startup_success()

        try:
            # 持续监听pipe消息
            while True:
                if conn.poll(timeout=0.5):
                    msg = conn.recv_bytes()
                    if msg == b'command:stop':
                        print("[Backend] Received stop signal, shutting down gracefully")
                        time.sleep(0.1)
                        break
                    else:
                        print(f"[Backend] Received message: {msg}")
        except EOFError:
            print("[Backend] Pipe closed")
        except KeyboardInterrupt:
            print("[Backend] Interrupted")

        print("[Backend] Normal backend exiting")

    @staticmethod
    def _immediate_error_backend():
        """立刻报错的后端"""
        print("[Backend] Immediate error backend starting...")
        raise RuntimeError("Immediate error in backend")

    @staticmethod
    def _early_exit_backend():
        """启动后立刻退出的后端（启动窗口内关闭 pipe：启动失败路径）"""
        print("[Backend] Early exit backend started, exiting now...")
        print("[Backend] Early exit backend exiting")
        sys.exit(0)

    @staticmethod
    def _late_exit_backend():
        """
        启动成功确认后延迟退出的后端（关闭 pipe 触发 supervisor 重启）
        """
        import builtins
        print("[Backend] Late exit backend started")
        conn = builtins.__mpipe_conn__
        TestSupervisor._announce_spawned()
        TestSupervisor._notify_startup_success()
        print("[Backend] Late exit backend waiting for step:exit")
        wait_for_step(conn, 'exit')
        print("[Backend] Late exit backend exiting")
        sys.exit(0)

    @staticmethod
    def _slow_shutdown_backend():
        """收到stop信号后，延迟退出的后端"""
        import builtins
        print("[Backend] Slow shutdown backend started...")
        conn = builtins.__mpipe_conn__
        TestSupervisor._notify_startup_success()
        try:
            while True:
                if conn.poll(timeout=0.5):
                    msg = conn.recv_bytes()
                    if msg == b'command:stop':
                        # Long enough for the test to send the second
                        # interrupt while the backend is still alive
                        print("[Backend] Received stop signal, ignoring for 3s...")
                        time.sleep(3)
                        print("[Backend] Finally exiting")
                        break
        except EOFError:
            pass

    @staticmethod
    def _restart_early_backend():
        """
        启动窗口内发送 restart 请求（由测试用 command:step:restart 放行）

        The request is the first scenario message of this backend (only the spawn
        announcement comes before it), so it arrives while recv_loop is in the
        startup window and the request itself confirms the startup. The test
        observes the "waiting for step" line and the still-empty state before it
        releases the step.
        """
        import builtins
        print("[Backend] Restart early backend started")
        conn = builtins.__mpipe_conn__
        TestSupervisor._announce_spawned()
        print("[Backend] Restart early backend waiting for step:restart")
        wait_for_step(conn, 'restart')
        print("[Backend] Sending restart request")
        conn.send_bytes(b'command:restart')
        print("[Backend] Exiting after restart request")
        sys.exit(0)

    @staticmethod
    def _restart_late_backend():
        """
        启动确认后发送 restart 请求（由测试用 command:step:restart 放行）

        The test waits for the supervisor to confirm the startup (from the
        backend's own message), checks that the request is still withheld and
        only then releases it: "after the confirmation" is decided by the test
        sequence instead of by a delay or by the pipe order.
        """
        import builtins
        print("[Backend] Restart late backend started")
        conn = builtins.__mpipe_conn__
        TestSupervisor._announce_spawned()
        TestSupervisor._notify_startup_success()
        print("[Backend] Restart late backend waiting for step:restart")
        wait_for_step(conn, 'restart')
        print("[Backend] Sending restart request")
        conn.send_bytes(b'command:restart')
        print("[Backend] Exiting after restart request")
        sys.exit(0)

    @staticmethod
    def _stop_early_backend():
        """
        启动窗口内发送 stop 请求（由测试用 command:step:stop 放行）
        """
        import builtins
        print("[Backend] Stop early backend started")
        conn = builtins.__mpipe_conn__
        TestSupervisor._announce_spawned()
        print("[Backend] Stop early backend waiting for step:stop")
        wait_for_step(conn, 'stop')
        print("[Backend] Sending stop request")
        conn.send_bytes(b'command:stop')
        # Wait for supervisor to send command:stop back
        try:
            while True:
                if conn.poll(timeout=0.5):
                    msg = conn.recv_bytes()
                    if msg == b'command:stop':
                        print("[Backend] Received stop signal confirmation")
                        break
        except EOFError:
            pass
        sys.exit(0)

    @staticmethod
    def _stop_late_backend():
        """
        启动确认后发送 stop 请求（由测试用 command:step:stop 放行）
        """
        import builtins
        print("[Backend] Stop late backend started")
        conn = builtins.__mpipe_conn__
        TestSupervisor._announce_spawned()
        TestSupervisor._notify_startup_success()
        print("[Backend] Stop late backend waiting for step:stop")
        wait_for_step(conn, 'stop')
        print("[Backend] Sending stop request")
        conn.send_bytes(b'command:stop')
        # Wait for supervisor to send command:stop back
        try:
            while True:
                if conn.poll(timeout=0.5):
                    msg = conn.recv_bytes()
                    if msg == b'command:stop':
                        print("[Backend] Received stop signal confirmation")
                        break
        except EOFError:
            pass
        sys.exit(0)

    @staticmethod
    def _crash_after_success_backend():
        """启动确认后崩溃（每次崩溃由测试用 command:step:crash 放行）"""
        import builtins
        import sys
        conn = builtins.__mpipe_conn__
        TestSupervisor._announce_spawned()
        # Send a message to trigger startup success, then wait for the test step:
        # the crash only happens after the supervisor confirmed the startup, so
        # it counts against the restart limit as a crash of a started backend
        conn.send_bytes(b'ok')
        print("[Backend] Crash after success backend waiting for step:crash")
        wait_for_step(conn, 'crash')
        print("[Backend] Crashing now")
        sys.exit(1)

    @staticmethod
    def _prefs_backend():
        """
        模拟真实后端：在 trio 环境中监听 pipe，处理 stdin 转发的
        set_lang/set_theme 命令（持久化到 config/deploy.yaml）
        """
        import builtins
        import os
        import threading

        import trio

        from alasio.backend.lifespan import SHUTDOWN_EVENT, mpipe_recv_loop
        from alasio.ext.env import set_project_root
        from alasio.ext.path import PathStr
        # this process only exists for the test: never open a log file in the
        # project log directory (the real backend modules log through it)
        from alasio.logger import logger
        logger.mute(fd=True)

        # Ensure the project root is resolved against the repository layout
        set_project_root(PathStr.new(os.path.dirname(__file__)).uppath(3))

        print("[Backend] Prefs backend started, waiting for commands...")
        TestSupervisor._notify_startup_success()

        async def main():
            trio_token = trio.lowlevel.current_trio_token()
            thread = threading.Thread(
                target=mpipe_recv_loop,
                args=(builtins.__mpipe_conn__, trio_token),
                name='mpipe_child_recv',
                daemon=True,
            )
            thread.start()
            await SHUTDOWN_EVENT.wait()
            print("[Backend] Received stop signal, shutting down gracefully")

        try:
            trio.run(main)
        except KeyboardInterrupt:
            pass

        print("[Backend] Prefs backend exiting")


if __name__ == "__main__":
    # Production defaults. The fake backends below are event driven (they act
    # on pipe events and on the supervisor's own messages instead of sleeping),
    # so the realistic timings cost the tests almost nothing.
    supervisor = TestSupervisor(
        restart_delay=1,
        max_restart_attempts=3,
        restart_window=60,
        startup_timeout=5.0,
        graceful_shutdown_timeout=5.0
    )
    supervisor.run()
