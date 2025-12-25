import os
import subprocess
import sys
import time

from alasio.backend.supervisor import Supervisor


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
        elif backend_type == "immediate_error":
            # 立刻报错
            TestSupervisor._immediate_error_backend()
        elif backend_type == "early_exit":
            # 5s内退出（2s）
            TestSupervisor._early_exit_backend()
        elif backend_type == "late_exit":
            # 5s后退出（8s）
            TestSupervisor._late_exit_backend()
        elif backend_type == "slow_shutdown":
            # 收到stop信号后，延迟退出的后端
            TestSupervisor._slow_shutdown_backend()
        elif backend_type == "restart_2s":
            # 启动2s后发送restart请求
            TestSupervisor._restart_2s_backend()
        elif backend_type == "restart_8s":
            # 启动8s后发送restart请求
            TestSupervisor._restart_8s_backend()
        elif backend_type == "stop_2s":
            # 启动2s后发送stop请求
            TestSupervisor._stop_2s_backend()
        elif backend_type == "stop_8s":
            # 启动8s后发送stop请求
            TestSupervisor._stop_8s_backend()
        elif backend_type == "crash_after_success":
            # 启动成功后立即崩溃
            TestSupervisor._crash_after_success_backend()
        else:
            print(f"[Backend] Unknown backend type: {backend_type}")
            sys.exit(1)

    @staticmethod
    def _normal_backend():
        """正常启动，无限等待的后端"""
        import builtins
        print("[Backend] Normal backend started, waiting indefinitely...")

        try:
            # 持续监听pipe消息
            conn = builtins.__mpipe_conn__
            while True:
                if conn.poll(timeout=0.5):
                    msg = conn.recv_bytes()
                    if msg == b'stop':
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
        """启动后8秒退出的后端（大于startup_timeout）"""
        import builtins
        print("[Backend] Late exit backend started, will exit in 8 seconds...")

        conn = builtins.__mpipe_conn__
        start_time = time.time()

        try:
            while time.time() - start_time < 8:
                if conn.poll(timeout=0.5):
                    msg = conn.recv_bytes()
                    if msg == b'stop':
                        print("[Backend] Received stop signal, shutting down gracefully")
                        break
        except EOFError:
            print("[Backend] Pipe closed")
        except KeyboardInterrupt:
            print("[Backend] Interrupted")

        print("[Backend] Late exit backend exiting")

    @staticmethod
    def _slow_shutdown_backend():
        """收到stop信号后，延迟退出的后端"""
        import builtins
        print("[Backend] Slow shutdown backend started...")
        conn = builtins.__mpipe_conn__
        try:
            while True:
                if conn.poll(timeout=0.5):
                    msg = conn.recv_bytes()
                    if msg == b'stop':
                        print("[Backend] Received stop signal, ignoring for 10s...")
                        time.sleep(10)
                        print("[Backend] Finally exiting")
                        break
        except EOFError:
            pass

    @staticmethod
    def _restart_2s_backend():
        """启动2s后发送restart请求"""
        import builtins
        print("[Backend] Restart 2s backend started...")
        conn = builtins.__mpipe_conn__
        time.sleep(2)
        print("[Backend] Sending restart request")
        conn.send_bytes(b'restart')
        print("[Backend] Exiting after restart request")
        sys.exit(0)

    @staticmethod
    def _restart_8s_backend():
        """启动8s后发送restart请求"""
        import builtins
        print("[Backend] Restart 8s backend started...")
        conn = builtins.__mpipe_conn__
        time.sleep(8)
        print("[Backend] Sending restart request")
        conn.send_bytes(b'restart')
        print("[Backend] Exiting after restart request")
        sys.exit(0)

    @staticmethod
    def _stop_2s_backend():
        """启动2s后发送stop请求"""
        import builtins
        print("[Backend] Stop 2s backend started...")
        conn = builtins.__mpipe_conn__
        time.sleep(2)
        print("[Backend] Sending stop request")
        conn.send_bytes(b'stop')
        # Wait for supervisor to send stop back
        try:
            while True:
                if conn.poll(timeout=0.5):
                    msg = conn.recv_bytes()
                    if msg == b'stop':
                        print("[Backend] Received stop signal confirmation")
                        break
        except EOFError:
            pass
        sys.exit(0)

    @staticmethod
    def _stop_8s_backend():
        """启动8s后发送stop请求"""
        import builtins
        print("[Backend] Stop 8s backend started...")
        conn = builtins.__mpipe_conn__
        time.sleep(8)
        print("[Backend] Sending stop request")
        conn.send_bytes(b'stop')
        # Wait for supervisor to send stop back
        try:
            while True:
                if conn.poll(timeout=0.5):
                    msg = conn.recv_bytes()
                    if msg == b'stop':
                        print("[Backend] Received stop signal confirmation")
                        break
        except EOFError:
            pass
        sys.exit(0)

    @staticmethod
    def _crash_after_success_backend():
        """启动后发送消息标记启动成功，然后立即退出"""
        import builtins
        import time
        import sys
        conn = builtins.__mpipe_conn__
        # Send a message to trigger startup success
        conn.send_bytes(b'ok')
        time.sleep(0.5)
        print("[Backend] Crashing now")
        sys.exit(1)


if __name__ == "__main__":
    supervisor = TestSupervisor(
        restart_delay=3,
        max_restart_attempts=3,
        restart_window=60,
        startup_timeout=5.0,
        graceful_shutdown_timeout=5.0
    )
    supervisor.run()
