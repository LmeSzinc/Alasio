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
            # The backend logged that it received command:stop, so the
            # supervisor is already inside graceful_shutdown; interrupt it
            # right away instead of waiting a fixed delay
            proc.wait_for_output("Received stop signal, ignoring for 3s", timeout=5)

            # Second interrupt - force kill
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
        """
        在启动确认前和启动确认后 两种情况下，都能优雅退出
        """
        # Case 1: interrupt before the startup confirmation. The silent
        # backend never confirms startup through the pipe, so an interrupt
        # sent right after "Backend running on PID:" lands while recv_loop
        # is still in the startup window (far before the 5s timeout). The
        # interrupt aborts the startup wait, so the timeout log can never
        # be emitted; asserting its absence after exit verifies the
        # interrupt really was processed inside the startup window.
        with create_supervisor_process("silent") as proc:
            proc.wait_for_output("Backend running on PID:", timeout=10)
            proc.send_interrupt()
            proc.wait_for_exit(timeout=15)
            assert proc.has_output("initiating graceful shutdown")
            assert not proc.has_output("Backend running for 5.0s, startup successful")

        # Case 2: interrupt after the startup confirmation
        with create_supervisor_process("normal") as proc:
            proc.wait_for_output("startup successful", timeout=10)
            proc.send_interrupt()
            proc.wait_for_exit(timeout=10)
            assert proc.has_output("initiating graceful shutdown")

    def test_startup_timeout_confirmation(self):
        """
        不发启动消息的后端（确认消息缺失的兜底路径），靠 startup_timeout 超时确认

        真实后端会在 bind 成功后发送 command:started，因此正常情况不再
        需要等满 startup_timeout；silent 后端保留该超时路径的覆盖。
        """
        with create_supervisor_process("silent") as proc:
            proc.wait_for_output("Backend running for 5.0s, startup successful", timeout=15)
            proc.send_interrupt()
            proc.wait_for_output("initiating graceful shutdown", timeout=5)
            proc.wait_for_exit(timeout=10)

    def test_backend_restart_stop(self):
        """
        后端在启动早期/启动确认后 发送restart或者 stop都能正确处理

        restart_2s/stop_2s send the request ~0.5s after boot (inside the
        startup window), so recv_loop confirms startup through the request
        message itself; restart_8s/stop_8s confirm startup first (b'ok')
        and send the request later. Both orders must be handled.
        """
        # Case 1: Restart requested early, during the startup window
        with create_supervisor_process("restart_2s") as proc:
            proc.wait_for_output("Backend requested restart", timeout=10)
            # Drop the first launch's "started" line, then the line can
            # only come from the restarted backend
            proc.output_buffer.clear()
            proc.wait_for_output("Restart 2s backend started", timeout=10)

        # Case 2: Restart after the startup confirmation
        with create_supervisor_process("restart_8s") as proc:
            proc.wait_for_output("startup successful", timeout=10)
            proc.wait_for_output("Backend requested restart", timeout=15)
            # Drop the first launch's "started" line, then the line can
            # only come from the restarted backend
            proc.output_buffer.clear()
            proc.wait_for_output("Restart 8s backend started", timeout=10)

        # Case 3: Stop requested early, during the startup window
        with create_supervisor_process("stop_2s") as proc:
            proc.wait_for_output("Backend requested stop", timeout=10)
            proc.wait_for_output("initiating graceful shutdown", timeout=5)
            proc.wait_for_exit(timeout=5)

        # Case 4: Stop after the startup confirmation
        with create_supervisor_process("stop_8s") as proc:
            proc.wait_for_output("startup successful", timeout=10)
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

            # Unknown input should be ignored. No output is produced for
            # it, so the proof is a sentinel command sent right after: the
            # backend logging the forwarded sentinel shows the supervisor
            # survived the garbage line and the stdin -> backend chain
            # still works.
            proc.process.stdin.write("garbage\n")
            proc.process.stdin.flush()
            proc.process.stdin.write("command:ping\n")
            proc.process.stdin.flush()
            proc.wait_for_output("Received message: b'command:ping'", timeout=10)

            # Real stop command still works afterwards
            proc.process.stdin.write("command:stop\n")
            proc.process.stdin.flush()
            proc.wait_for_output("Received stop signal, shutting down gracefully", timeout=5)
            proc.wait_for_exit(timeout=5)

    def test_stdin_close_triggers_shutdown(self):
        """父进程死亡（关闭 stdin）后，supervisor 自行优雅退出"""
        with create_supervisor_process("normal") as proc:
            proc.wait_for_output("startup successful", timeout=10)

            # Simulate the parent (Electron) process dying: close stdin
            proc.process.stdin.close()

            # Supervisor detects the EOF, shuts the backend down and exits
            proc.wait_for_output("Parent process exited (stdin closed), shutting down", timeout=5)
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
            # Drop the first launch's "started" line, then the line can
            # only come from the restarted backend
            proc.output_buffer.clear()
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
        Read Webapp.Lang / Webapp.Theme / Webapp.DpiScaling from deploy.yaml

        Returns:
            tuple: (lang, theme, dpi_scaling)
        """
        # atomic_read_text retries on PermissionError: the backend may be
        # replacing the file (os.replace) at the same moment on Windows.
        # A still-locked file reads as empty here; callers polling the file
        # simply retry on the next iteration.
        from alasio.ext.path.atomic import atomic_read_text

        try:
            text = atomic_read_text(DEPLOY_YAML)
        except (FileNotFoundError, UnicodeDecodeError, PermissionError):
            text = ''
        data = yaml.safe_load(text) or {}
        webapp = data.get('Webapp', {}) or {}
        return webapp.get('Lang', 'system'), webapp.get('Theme', 'system'), webapp.get('DpiScaling', True)

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
            lang, _, _ = TestStdinPrefsCommands._read_webapp()
            if lang == expected:
                return
            time.sleep(0.05)
        raise AssertionError(f'timeout waiting for Webapp.Lang == {expected}')

    @staticmethod
    def _wait_for_dpi_scaling(expected, timeout=10):
        """
        Wait until Webapp.DpiScaling equals expected

        Args:
            expected (bool):
            timeout (float): Seconds
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            _, _, dpi_scaling = TestStdinPrefsCommands._read_webapp()
            if dpi_scaling == expected:
                return
            time.sleep(0.05)
        raise AssertionError(f'timeout waiting for Webapp.DpiScaling == {expected}')

    @staticmethod
    def _wait_for_theme(expected, timeout=10):
        """
        Wait until Webapp.Theme equals expected

        Args:
            expected (str):
            timeout (float): Seconds
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            _, theme, _ = TestStdinPrefsCommands._read_webapp()
            if theme == expected:
                return
            time.sleep(0.05)
        raise AssertionError(f'timeout waiting for Webapp.Theme == {expected}')

    @staticmethod
    def _restore_prefs(lang, theme, dpi_scaling):
        """
        Restore Webapp.Lang/Theme/DpiScaling through a fresh prefs backend
        so the yaml keeps its comments (YamlConfig write path)

        Args:
            lang (str):
            theme (str):
            dpi_scaling (bool):
        """
        with create_supervisor_process("prefs") as proc:
            proc.wait_for_output("startup successful", timeout=10)
            proc.process.stdin.write(f'command:set_lang:{lang}\n')
            proc.process.stdin.flush()
            TestStdinPrefsCommands._wait_for_lang(lang)
            proc.process.stdin.write(f'command:set_theme:{theme}\n')
            proc.process.stdin.flush()
            TestStdinPrefsCommands._wait_for_theme(theme)
            proc.process.stdin.write(f'command:set_dpi_scaling:{str(dpi_scaling).lower()}\n')
            proc.process.stdin.flush()
            TestStdinPrefsCommands._wait_for_dpi_scaling(dpi_scaling)
            proc.process.stdin.write('command:stop\n')
            proc.process.stdin.flush()
            proc.wait_for_exit(timeout=5)

    def test_stdin_set_lang_persists_idempotent(self):
        """
        stdin set_lang 写入 deploy.yaml；重复写同值不写（mtime 不变）；
        非法值不写；结束恢复原值
        """
        original_lang, original_theme, original_dpi_scaling = self._read_webapp()
        try:
            with create_supervisor_process("prefs") as proc:
                proc.wait_for_output("startup successful", timeout=10)
                proc.wait_for_output("Prefs backend started", timeout=5)

                # 1. Set a value different from the current one. The value
                #    change is observed by polling the file, which is the
                #    event that the write has been processed.
                target = 'zh-CN' if original_lang != 'zh-CN' else 'en-US'
                proc.process.stdin.write(f'command:set_lang:{target}\n')
                proc.process.stdin.flush()
                self._wait_for_lang(target)
                mtime1 = os.stat(DEPLOY_YAML).st_mtime_ns

                # 2. Same value again -> no write. The invalid line sent
                #    right after is the barrier: the pipe is FIFO and
                #    mpipe_recv_loop (alasio/backend/lifespan.py) handles
                #    messages strictly serially in one thread (recv ->
                #    validate/persist -> next recv), so when the backend
                #    logged the invalid value the idempotent line before it
                #    has already been processed. Its log replaces a fixed
                #    sleep before the mtime check.
                proc.process.stdin.write(f'command:set_lang:{target}\n')
                proc.process.stdin.flush()
                proc.process.stdin.write('command:set_lang:fr-FR\n')
                proc.process.stdin.flush()
                proc.wait_for_output("Invalid set_lang value from stdin: 'fr-FR'", timeout=10)
                assert os.stat(DEPLOY_YAML).st_mtime_ns == mtime1
                assert self._read_webapp()[0] == target

                # 3. Any command:* line is forwarded verbatim; the backend
                #    logs and ignores unknown commands, no write happens.
                #    The log line is the barrier again.
                proc.process.stdin.write('command:set_font:big\n')
                proc.process.stdin.flush()
                proc.wait_for_output("Backend received unknown msg from supervisor", timeout=10)
                assert os.stat(DEPLOY_YAML).st_mtime_ns == mtime1

                # 4. Backend still alive, graceful stop works
                assert proc.is_alive()
                proc.process.stdin.write('command:stop\n')
                proc.process.stdin.flush()
                proc.wait_for_output('Received stop signal, shutting down gracefully', timeout=5)
                proc.wait_for_exit(timeout=5)
        finally:
            if self._read_webapp()[0] != original_lang:
                self._restore_prefs(original_lang, original_theme, original_dpi_scaling)

    def test_stdin_set_theme_persists(self):
        """
        stdin set_theme 写入 deploy.yaml 并幂等；结束恢复原值
        """
        original_lang, original_theme, original_dpi_scaling = self._read_webapp()
        try:
            with create_supervisor_process("prefs") as proc:
                proc.wait_for_output("startup successful", timeout=10)
                proc.wait_for_output("Prefs backend started", timeout=5)

                target = 'dark' if original_theme != 'dark' else 'light'
                proc.process.stdin.write(f'command:set_theme:{target}\n')
                proc.process.stdin.flush()
                self._wait_for_theme(target)
                mtime1 = os.stat(DEPLOY_YAML).st_mtime_ns

                # Idempotent: same value again -> no write. The invalid
                # line is the FIFO barrier (mpipe_recv_loop is a single
                # serial thread): its log proves the idempotent line before
                # it has been processed.
                proc.process.stdin.write(f'command:set_theme:{target}\n')
                proc.process.stdin.flush()
                proc.process.stdin.write('command:set_theme:blue\n')
                proc.process.stdin.flush()
                proc.wait_for_output("Invalid set_theme value from stdin: 'blue'", timeout=10)
                assert os.stat(DEPLOY_YAML).st_mtime_ns == mtime1

                proc.process.stdin.write('command:stop\n')
                proc.process.stdin.flush()
                proc.wait_for_output('Received stop signal, shutting down gracefully', timeout=5)
                proc.wait_for_exit(timeout=5)
        finally:
            if self._read_webapp()[1] != original_theme:
                self._restore_prefs(original_lang, original_theme, original_dpi_scaling)

    def test_stdin_set_dpi_scaling_persists_idempotent(self):
        """
        stdin set_dpi_scaling 写入 deploy.yaml；重复写同值不写（mtime 不变）；
        非法值不写；结束恢复原值
        """
        original_lang, original_theme, original_dpi_scaling = self._read_webapp()
        try:
            with create_supervisor_process("prefs") as proc:
                proc.wait_for_output("startup successful", timeout=10)
                proc.wait_for_output("Prefs backend started", timeout=5)

                # 1. Set a value different from the current one
                target = (not original_dpi_scaling)
                proc.process.stdin.write(f'command:set_dpi_scaling:{str(target).lower()}\n')
                proc.process.stdin.flush()
                self._wait_for_dpi_scaling(target)
                mtime1 = os.stat(DEPLOY_YAML).st_mtime_ns

                # 2. Same value again -> no write, mtime unchanged. The
                #    invalid line is the FIFO barrier (mpipe_recv_loop is a
                #    single serial thread): its log proves the idempotent
                #    line before it has been processed.
                proc.process.stdin.write(f'command:set_dpi_scaling:{str(target).lower()}\n')
                proc.process.stdin.flush()
                proc.process.stdin.write('command:set_dpi_scaling:yes\n')
                proc.process.stdin.flush()
                proc.wait_for_output("Invalid set_dpi_scaling value from stdin: 'yes'", timeout=10)
                assert os.stat(DEPLOY_YAML).st_mtime_ns == mtime1
                assert self._read_webapp()[2] == target

                # 3. Backend still alive, graceful stop works
                assert proc.is_alive()
                proc.process.stdin.write('command:stop\n')
                proc.process.stdin.flush()
                proc.wait_for_output('Received stop signal, shutting down gracefully', timeout=5)
                proc.wait_for_exit(timeout=5)
        finally:
            if self._read_webapp()[2] != original_dpi_scaling:
                self._restore_prefs(original_lang, original_theme, original_dpi_scaling)
