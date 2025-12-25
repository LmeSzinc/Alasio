import os
import re
import time

import psutil

from alasio.testing.managed_process import ManagedProcess


def create_supervisor_process(backend_type: str) -> ManagedProcess:
    """
    便捷函数：创建supervisor进程

    Args:
        backend_type: 后端类型 (normal, immediate_error, etc.)

    Returns:
        ManagedProcess实例
    """
    script_path = os.path.join(os.path.dirname(__file__), "backends.py")
    return ManagedProcess(script_path, backend_type)


class TestSupervisor:

    def test_normal_startup_and_graceful_exit(self):
        """测试正常启动并且优雅退出"""
        with create_supervisor_process("normal") as proc:
            # Wait for startup
            proc.wait_for_output("startup successful", timeout=10)

            # Send interrupt
            proc.send_interrupt()

            # Wait for graceful shutdown
            proc.wait_for_output("initiating graceful shutdown", timeout=5)
            proc.wait_for_output("Received stop signal, shutting down gracefully", timeout=5)

            # Wait for exit
            proc.wait_for_exit(timeout=5)

    def test_slow_shutdown_force_kill(self):
        """如果优雅退出很慢，再次发送CTRL+C可以直接退出"""
        with create_supervisor_process("slow_shutdown") as proc:
            proc.wait_for_output("startup successful", timeout=10)

            # First interrupt - graceful shutdown
            proc.send_interrupt()
            proc.wait_for_output("initiating graceful shutdown", timeout=5)
            proc.wait_for_output("Received stop signal, ignoring for 10s", timeout=5)

            # Second interrupt - force kill
            time.sleep(1)
            proc.send_interrupt()
            proc.wait_for_output("force killing backend", timeout=5)

            # Should exit quickly
            proc.wait_for_exit(timeout=5)

    def test_immediate_failure(self):
        """运行立即失败的后端会立即退出"""
        with create_supervisor_process("immediate_error") as proc:
            # Should exit quickly
            proc.wait_for_exit(timeout=5)

            assert proc.has_output("Immediate error")
            assert proc.has_output("Backend failed to start properly")

    def test_graceful_exit_timings(self):
        """在5s内和5s后 两种情况下，都能优雅退出"""
        # Case 1: Early exit (send interrupt within 5s)
        with create_supervisor_process("normal") as proc:
            proc.wait_for_output("startup successful", timeout=10)
            time.sleep(2)
            proc.send_interrupt()
            proc.wait_for_exit(timeout=5)
            assert proc.has_output("initiating graceful shutdown")

        # Case 2: Late exit (send interrupt after 5s)
        with create_supervisor_process("normal") as proc:
            proc.wait_for_output("startup successful", timeout=10)
            time.sleep(6)  # Wait > 5s
            proc.send_interrupt()
            proc.wait_for_exit(timeout=5)
            assert proc.has_output("initiating graceful shutdown")

    def test_backend_restart_stop(self):
        """在5s内和5s后 两种情况下，后端发送restart或者 stop都能正确处理"""
        # Case 1: Restart 2s
        with create_supervisor_process("restart_2s") as proc:
            proc.wait_for_output("Backend requested restart", timeout=10)
            # It should restart
            proc.wait_for_output("Restart 2s backend started", timeout=10)

        # Case 2: Restart 8s
        with create_supervisor_process("restart_8s") as proc:
            proc.wait_for_output("Backend requested restart", timeout=15)
            # It should restart
            proc.wait_for_output("Restart 8s backend started", timeout=10)

        # Case 3: Stop 2s
        with create_supervisor_process("stop_2s") as proc:
            proc.wait_for_output("Backend requested stop", timeout=10)
            proc.wait_for_output("initiating graceful shutdown", timeout=5)
            proc.wait_for_exit(timeout=5)

        # Case 4: Stop 8s
        with create_supervisor_process("stop_8s") as proc:
            proc.wait_for_output("Backend requested stop", timeout=15)
            proc.wait_for_output("initiating graceful shutdown", timeout=5)
            proc.wait_for_exit(timeout=5)

    def test_kill_backend_restart(self):
        """在正常启动之后，直接杀死后端进程，supervisor能够重新拉起后端"""
        with create_supervisor_process("normal") as proc:
            proc.wait_for_output("Backend running on PID:", timeout=10)
            proc.wait_for_output("startup successful", timeout=10)

            # Extract PID
            output = proc.get_output()
            match = re.search(r"Backend running on PID: (\d+)", output)
            assert match, "Could not find backend PID"
            pid = int(match.group(1))

            # Kill the backend process
            psutil.Process(pid).kill()

            # Supervisor should detect exit and restart
            proc.wait_for_output("Backend exited with code", timeout=5)
            proc.wait_for_output("Restarting in", timeout=5)

            # Should start again (new PID)
            proc.output_buffer.clear()
            proc.wait_for_output("Backend running on PID:", timeout=10)

            # Extract PID
            output = proc.get_output()
            match = re.search(r"Backend running on PID: (\d+)", output)
            assert match, "Could not find backend PID"
            new_pid = int(match.group(1))

            # Verify new PID is different
            assert pid != new_pid

    def test_close_pipe_restart(self):
        """在正常启动之后，直接关闭pipe，supervisor能够重新拉起后端"""
        # Use late_exit backend which closes pipe (exits) after 8s
        with create_supervisor_process("late_exit") as proc:
            proc.wait_for_output("startup successful", timeout=10)

            # Wait for it to exit/close pipe
            proc.wait_for_output("Backend closed pipe connection", timeout=15)

            # Should restart
            proc.wait_for_output("Restarting in", timeout=5)
            proc.wait_for_output("Late exit backend started", timeout=10)

    def test_restart_limit(self):
        """短时间内多次杀死后端，supervisor会停止拉起后端"""
        with create_supervisor_process("crash_after_success") as proc:
            # It should crash and restart multiple times
            # Max restarts is 3.
            # We expect to see "Restart limit exceeded"

            proc.wait_for_output("Restart limit exceeded", timeout=30)
            proc.wait_for_exit(timeout=5)
