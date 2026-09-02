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
        elif backend_type == "restart_2s":
            # 启动窗口内发送restart请求
            TestSupervisor._restart_2s_backend()
        elif backend_type == "restart_8s":
            # 启动确认后发送restart请求
            TestSupervisor._restart_8s_backend()
        elif backend_type == "stop_2s":
            # 启动窗口内发送stop请求
            TestSupervisor._stop_2s_backend()
        elif backend_type == "stop_8s":
            # 启动确认后发送stop请求
            TestSupervisor._stop_8s_backend()
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
        # Simulate a realistic startup delay before confirming startup.
        # The delay keeps a window where an interrupt arrives before the
        # startup confirmation (test_graceful_exit_timings case 1).
        time.sleep(0.5)
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
        """启动后2秒内退出的后端（小于startup_timeout）"""
        print("[Backend] Early exit backend started, will exit in 2 seconds...")
        time.sleep(2)
        print("[Backend] Early exit backend exiting")
        sys.exit(0)

    @staticmethod
    def _late_exit_backend():
        """
        启动成功确认后延迟退出的后端（关闭 pipe 触发 supervisor 重启）
        """
        print("[Backend] Late exit backend started, will exit in 1.5 seconds...")

        TestSupervisor._notify_startup_success()
        # Give the supervisor time to finish the startup handshake, then
        # exit and close the pipe so it observes the unexpected exit.
        time.sleep(1.5)
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
    def _restart_2s_backend():
        """
        启动后很快发送restart请求（在 startup_timeout 窗口内到达）
        """
        import builtins
        print("[Backend] Restart 2s backend started...")
        conn = builtins.__mpipe_conn__
        # No startup confirmation: the restart request arrives while
        # recv_loop is still in the startup window, and the message itself
        # confirms startup success
        time.sleep(0.5)
        print("[Backend] Sending restart request")
        conn.send_bytes(b'command:restart')
        print("[Backend] Exiting after restart request")
        sys.exit(0)

    @staticmethod
    def _restart_8s_backend():
        """启动确认后稍后发送restart请求"""
        import builtins
        print("[Backend] Restart 8s backend started...")
        conn = builtins.__mpipe_conn__
        TestSupervisor._notify_startup_success()
        time.sleep(1.5)
        print("[Backend] Sending restart request")
        conn.send_bytes(b'command:restart')
        print("[Backend] Exiting after restart request")
        sys.exit(0)

    @staticmethod
    def _stop_2s_backend():
        """
        启动后很快发送stop请求（在 startup_timeout 窗口内到达）
        """
        import builtins
        print("[Backend] Stop 2s backend started...")
        conn = builtins.__mpipe_conn__
        # No startup confirmation: the stop request arrives while recv_loop
        # is still in the startup window, and the message itself confirms
        # startup success
        time.sleep(0.5)
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
    def _stop_8s_backend():
        """启动确认后稍后发送stop请求"""
        import builtins
        print("[Backend] Stop 8s backend started...")
        conn = builtins.__mpipe_conn__
        TestSupervisor._notify_startup_success()
        time.sleep(1.5)
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
        """启动后发送消息标记启动成功，然后立即退出"""
        import builtins
        import sys
        import time
        conn = builtins.__mpipe_conn__
        # Send a message to trigger startup success
        conn.send_bytes(b'ok')
        time.sleep(0.5)
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
    supervisor = TestSupervisor(
        restart_delay=1,
        max_restart_attempts=3,
        restart_window=60,
        startup_timeout=5.0,
        graceful_shutdown_timeout=5.0
    )
    supervisor.run()
