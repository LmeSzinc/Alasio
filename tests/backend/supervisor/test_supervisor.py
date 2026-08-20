import os
import re
import time

import psutil
import yaml

from alasio.testing.managed_process import ManagedProcess

# Absolute path of the real config/deploy.yaml. The stdin contract tests
# exercise the full chain (stdin -> supervisor -> pipe -> backend ->
# deploy.yaml) against the real config file and restore the original
# values afterwards, so the file keeps its comments (the backend writes it
# through YamlConfig, which preserves comments).
DEPLOY_YAML = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'config', 'deploy.yaml'))


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

    def test_stdin_stop_command(self):
        """stdin command:stop gracefully stops the backend"""
        with create_supervisor_process("normal") as proc:
            proc.wait_for_output("startup successful", timeout=10)

            # Send stop command through stdin
            proc.process.stdin.write("command:stop\n")
            proc.process.stdin.flush()

            # Backend should receive the forwarded stop and exit gracefully
            proc.wait_for_output("Received stop signal, shutting down gracefully", timeout=5)
            proc.wait_for_exit(timeout=5)

    def test_stdin_unknown_command_ignored(self):
        """unknown stdin input is silently discarded"""
        with create_supervisor_process("normal") as proc:
            proc.wait_for_output("startup successful", timeout=10)

            # Unknown input should be ignored, backend keeps running
            proc.process.stdin.write("garbage\n")
            proc.process.stdin.flush()
            time.sleep(1)
            assert proc.is_alive()

            # Real stop command still works afterwards
            proc.process.stdin.write("command:stop\n")
            proc.process.stdin.flush()
            proc.wait_for_output("Received stop signal, shutting down gracefully", timeout=5)
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


class TestStdinPrefsCommands:
    """
    stdin 契约端到端：set_lang/set_theme 经 supervisor 转发到后端并
    幂等持久化到 config/deploy.yaml
    """

    @staticmethod
    def _read_webapp():
        """
        Read Webapp.Lang / Webapp.Theme from deploy.yaml

        Returns:
            tuple[str, str]: (lang, theme)
        """
        with open(DEPLOY_YAML, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        webapp = data.get('Webapp', {}) or {}
        return webapp.get('Lang', 'system'), webapp.get('Theme', 'system')

    @staticmethod
    def _wait_for_lang(expected, timeout=10):
        """
        Wait until Webapp.Lang equals expected

        Args:
            expected (str):
            timeout (float): Seconds
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            lang, _ = TestStdinPrefsCommands._read_webapp()
            if lang == expected:
                return
            time.sleep(0.05)
        raise AssertionError(f'timeout waiting for Webapp.Lang == {expected}')

    @staticmethod
    def _restore_prefs(lang, theme):
        """
        Restore Webapp.Lang/Theme through a fresh prefs backend so the
        yaml keeps its comments (YamlConfig write path)

        Args:
            lang (str):
            theme (str):
        """
        with create_supervisor_process("prefs") as proc:
            proc.wait_for_output("startup successful", timeout=10)
            proc.process.stdin.write(f'command:set_lang:{lang}\n')
            proc.process.stdin.flush()
            TestStdinPrefsCommands._wait_for_lang(lang)
            proc.process.stdin.write(f'command:set_theme:{theme}\n')
            proc.process.stdin.flush()
            proc.process.stdin.write('command:stop\n')
            proc.process.stdin.flush()
            proc.wait_for_exit(timeout=5)

    def test_stdin_set_lang_persists_idempotent(self):
        """
        stdin set_lang 写入 deploy.yaml；重复写同值不写（mtime 不变）；
        非法值不写；结束恢复原值
        """
        original_lang, original_theme = self._read_webapp()
        try:
            with create_supervisor_process("prefs") as proc:
                proc.wait_for_output("startup successful", timeout=10)
                proc.wait_for_output("Prefs backend started", timeout=5)

                # 1. Set a value different from the current one
                target = 'zh-CN' if original_lang != 'zh-CN' else 'en-US'
                proc.process.stdin.write(f'command:set_lang:{target}\n')
                proc.process.stdin.flush()
                self._wait_for_lang(target)
                mtime1 = os.stat(DEPLOY_YAML).st_mtime_ns

                # 2. Same value again -> no write, mtime unchanged
                time.sleep(0.1)
                proc.process.stdin.write(f'command:set_lang:{target}\n')
                proc.process.stdin.flush()
                time.sleep(1.5)
                assert os.stat(DEPLOY_YAML).st_mtime_ns == mtime1
                assert self._read_webapp()[0] == target

                # 3. Invalid value -> rejected by the backend, no write
                time.sleep(0.1)
                proc.process.stdin.write('command:set_lang:fr-FR\n')
                proc.process.stdin.flush()
                time.sleep(1.5)
                assert os.stat(DEPLOY_YAML).st_mtime_ns == mtime1
                assert self._read_webapp()[0] == target

                # 4. Any command:* line is forwarded verbatim; the backend
                #    logs and ignores unknown commands, no write happens
                time.sleep(0.1)
                proc.process.stdin.write('command:set_font:big\n')
                proc.process.stdin.flush()
                time.sleep(1.5)
                assert os.stat(DEPLOY_YAML).st_mtime_ns == mtime1

                # 5. Backend still alive, graceful stop works
                assert proc.is_alive()
                proc.process.stdin.write('command:stop\n')
                proc.process.stdin.flush()
                proc.wait_for_output('Received stop signal, shutting down gracefully', timeout=5)
                proc.wait_for_exit(timeout=5)
        finally:
            if self._read_webapp()[0] != original_lang:
                self._restore_prefs(original_lang, original_theme)

    def test_stdin_set_theme_persists(self):
        """
        stdin set_theme 写入 deploy.yaml 并幂等；结束恢复原值
        """
        original_lang, original_theme = self._read_webapp()
        try:
            with create_supervisor_process("prefs") as proc:
                proc.wait_for_output("startup successful", timeout=10)
                proc.wait_for_output("Prefs backend started", timeout=5)

                target = 'dark' if original_theme != 'dark' else 'light'
                proc.process.stdin.write(f'command:set_theme:{target}\n')
                proc.process.stdin.flush()
                deadline = time.time() + 10
                while time.time() < deadline:
                    _, theme = self._read_webapp()
                    if theme == target:
                        break
                    time.sleep(0.05)
                else:
                    raise AssertionError(f'timeout waiting for Webapp.Theme == {target}')
                mtime1 = os.stat(DEPLOY_YAML).st_mtime_ns

                # Idempotent: same value again -> no write
                time.sleep(0.1)
                proc.process.stdin.write(f'command:set_theme:{target}\n')
                proc.process.stdin.flush()
                time.sleep(1.5)
                assert os.stat(DEPLOY_YAML).st_mtime_ns == mtime1

                # Invalid value -> rejected
                time.sleep(0.1)
                proc.process.stdin.write('command:set_theme:blue\n')
                proc.process.stdin.flush()
                time.sleep(1.5)
                assert os.stat(DEPLOY_YAML).st_mtime_ns == mtime1

                proc.process.stdin.write('command:stop\n')
                proc.process.stdin.flush()
                proc.wait_for_output('Received stop signal, shutting down gracefully', timeout=5)
                proc.wait_for_exit(timeout=5)
        finally:
            if self._read_webapp()[1] != original_theme:
                self._restore_prefs(original_lang, original_theme)
