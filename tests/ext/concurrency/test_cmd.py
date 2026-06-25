import sys
from unittest.mock import patch

import pytest

from alasio.ext.concurrent.cmd import CmdlineError, CmdlineResultBytes, CmdlineResultStr, run_cmd

PYTHON = sys.executable


class TestRunCmdSuccess:
    """Tests for successful command execution."""

    def test_stdout_output(self):
        """
        Test basic stdout capture.
        """
        res = run_cmd([PYTHON, "-c", "print('hello')"])
        assert res.stdout == "hello"
        assert res.returncode == 0

    def test_empty_stdout(self):
        """
        Test command that produces empty stdout.
        """
        res = run_cmd([PYTHON, "-c", "print()"])
        assert res.stdout == ""
        assert res.returncode == 0

    def test_return_type_is_cmdline_result_str(self):
        """
        Test that the default text mode returns CmdlineResultStr.
        """
        res = run_cmd([PYTHON, "-c", "print('hello')"])
        assert isinstance(res, CmdlineResultStr)

    def test_bytes_mode(self):
        """
        Test that text=False returns CmdlineResultBytes.
        """
        res = run_cmd([PYTHON, "-c", "print('hello')"], text=False)
        assert isinstance(res, CmdlineResultBytes)
        assert isinstance(res.stdout, bytes)
        assert res.stdout == b"hello"

    def test_check_returncode_passes(self):
        """
        Test that check_returncode() returns self for zero exit code.
        """
        res = run_cmd([PYTHON, "-c", "print('ok')"])
        assert res.check_returncode() is res

    def test_check_returncode_raises(self):
        """
        Test that check_returncode() raises CmdlineError for non-zero exit code.
        """
        res = run_cmd([PYTHON, "-c", "import sys; sys.exit(1)"], check=False)
        with pytest.raises(CmdlineError) as exc:
            res.check_returncode()
        assert exc.value.returncode == 1


class TestRunCmdStrip:
    """Tests for strip behavior."""

    def test_strip_default(self):
        """
        Test that output is stripped of leading and trailing whitespace by default.
        """
        res = run_cmd([PYTHON, "-c", "print('  hello  ')"])

        assert res.stdout == "hello"

    def test_strip_disabled(self):
        """
        Test that output is not stripped when strip=False.
        """
        res = run_cmd([PYTHON, "-c", "import sys; sys.stdout.write('  hello  \\n')"], strip=False)
        assert res.stdout == "  hello  \n"

    def test_strip_disabled_print_newline(self):
        """
        Test that without strip, print's trailing newline is preserved.
        """
        res = run_cmd([PYTHON, "-c", "print('hello')"], strip=False)
        assert res.stdout == "hello\n"

    def test_strip_bytes(self):
        """
        Test that bytes output is stripped when text=False.
        """
        res = run_cmd([PYTHON, "-c", "print('  hello  ')"], text=False)
        assert isinstance(res.stdout, bytes)
        assert res.stdout == b"hello"

    def test_stderr_not_stripped_independently(self):
        """
        Test that stdout and stderr are each stripped independently.
        """
        res = run_cmd(
            [PYTHON, "-c", "import sys; sys.stdout.write(' out '); sys.stderr.write(' err '); sys.exit(0)"],
            check=False,
        )
        assert res.stdout == "out"
        assert res.stderr == "err"


class TestRunCmdErrors:
    """Tests for error handling and edge cases in command execution."""

    def test_empty_command_list(self):
        """
        Test that an empty command list raises CmdlineError.
        """
        with pytest.raises(CmdlineError) as exc:
            run_cmd([])
        assert "Empty command list" in str(exc.value)

    def test_empty_executable(self):
        """
        Test that a command with an empty executable raises CmdlineError.
        """
        with pytest.raises(CmdlineError) as exc:
            run_cmd(["", "-c", "print('hello')"])
        assert "Empty executable" in str(exc.value)

    def test_command_not_found(self):
        """
        Test that executing a non-existent command raises CmdlineError.
        """
        with pytest.raises(CmdlineError) as exc:
            run_cmd(["non_existent_command_12345"])
        assert "Command not found" in str(exc.value)

    def test_failed_command(self):
        """
        Test that a command returning a non-zero exit code raises CmdlineError with check=True.
        """
        with pytest.raises(CmdlineError) as exc:
            run_cmd([PYTHON, "-c", "import sys; print('stdout_msg'); sys.stderr.write('error_msg\\n'); sys.exit(1)"])
        assert "Command failed with return code 1" in str(exc.value)
        assert exc.value.returncode == 1
        assert exc.value.stdout == "stdout_msg"
        assert exc.value.stderr == "error_msg"

    def test_failed_command_no_strip(self):
        """
        Test that a failed command raises CmdlineError with unstripped output.
        """
        with pytest.raises(CmdlineError) as exc:
            run_cmd(
                [PYTHON, "-c", "import sys; sys.stdout.write(' stdout_msg '); sys.stderr.write(' error_msg '); sys.exit(1)"],
                strip=False,
            )
        assert exc.value.stdout == " stdout_msg "
        assert exc.value.stderr == " error_msg "

    def test_failed_command_check_false(self):
        """
        Test that check=False returns a result object instead of raising.
        """
        res = run_cmd(
            [PYTHON, "-c", "import sys; print('stdout_msg'); sys.stderr.write('error_msg\\n'); sys.exit(1)"],
            check=False,
        )
        assert res.returncode == 1
        assert res.stdout == "stdout_msg"
        assert res.stderr == "error_msg"

    def test_failed_command_check_false_no_strip(self):
        """
        Test check=False with strip=False.
        """
        res = run_cmd(
            [PYTHON, "-c", "import sys; sys.stdout.write(' stdout_msg '); sys.stderr.write(' error_msg '); sys.exit(1)"],
            check=False,
            strip=False,
        )
        assert res.returncode == 1
        assert res.stdout == " stdout_msg "
        assert res.stderr == " error_msg "

    def test_timeout(self):
        """
        Test that a command exceeding the timeout raises CmdlineError.
        """
        with pytest.raises(CmdlineError) as exc:
            run_cmd([PYTHON, "-u", "-c", "import time; print('before_sleep', flush=True); time.sleep(2)"], timeout=0.5)
        assert "Command timed out" in str(exc.value)
        assert exc.value.stdout == "before_sleep"

    def test_unexpected_error(self):
        """
        Test that unexpected exceptions during execution are wrapped in CmdlineError.
        """
        with patch("subprocess.run", side_effect=RuntimeError("something went wrong")):
            with pytest.raises(CmdlineError) as exc:
                run_cmd([PYTHON, "-c", "print('hello')"])
            assert "Unexpected error during execution: something went wrong" in str(exc.value)


class TestRunCmdEncoding:
    """Tests for encoding handling."""

    def test_invalid_utf8_replaced(self):
        """
        Test that invalid UTF-8 output is handled with 'replace' strategy in text mode.
        """
        res = run_cmd([PYTHON, "-c", "import sys; sys.stdout.buffer.write(b'hello\\xffworld\\n')"])
        assert "hello" in res.stdout
        assert "world" in res.stdout
        # \xff should be replaced by \ufffd
        assert "\ufffd" in res.stdout

    def test_invalid_utf8_bytes_mode(self):
        """
        Test that bytes mode returns raw bytes unchanged.
        """
        res = run_cmd(
            [PYTHON, "-c", "import sys; sys.stdout.buffer.write(b'hello\\xffworld\\n')"],
            text=False,
        )
        assert isinstance(res.stdout, bytes)
        assert res.stdout == b"hello\xffworld"

    def test_no_stdout(self):
        """
        Test command that produces no stdout or stderr.
        """
        res = run_cmd([PYTHON, "-c", "pass"])
        # No output means empty string after strip, or None
        assert res.stdout == ""
        assert res.stderr == ""

    def test_custom_errors_ignore(self):
        """
        Test that errors='ignore' suppresses invalid bytes.
        """
        res = run_cmd(
            [PYTHON, "-c", "import sys; sys.stdout.buffer.write(b'hello\\xffworld\\n')"],
            errors='ignore',
        )
        # \xff is silently dropped
        assert res.stdout == "helloworld"

    def test_custom_encoding_latin1(self):
        """
        Test that a custom encoding (latin-1) can decode all byte values.
        """
        res = run_cmd(
            [PYTHON, "-c", "import sys; sys.stdout.buffer.write(b'hello\\xffworld\\n')"],
            encoding='latin-1',
        )
        assert res.stdout == "hello\xffworld"
