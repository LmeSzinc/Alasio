"""Tests for ExecShell tool."""

import sys

import pytest

from alasio.mcp.tool.base import RequestModel
from alasio.mcp.tool.exec_shell import ExecShell, ShellParams, ShellResult, split_command

_PY = sys.executable


class TestExecShell:
    """Tests for shell command execution tool."""

    @pytest.fixture
    def tool(self):
        return ExecShell()

    @pytest.fixture
    def req(self):
        """Build a RequestModel for exec_shell with given params and timeout."""
        return lambda params, timeout=10: RequestModel(
            method="exec_shell", params=params, timeout=timeout
        )

    @pytest.mark.parametrize("code, expected", [
        ("print('hello')", "hello"),
        ("print(42)", "42"),
    ])
    def test_shell_success(self, tool, req, code, expected):
        """Basic shell commands should execute successfully."""
        result = tool.run(req({"command": f'{_PY} -c "{code}"'}))
        assert isinstance(result, ShellResult)
        assert result.stdout.strip() == expected, repr(result)
        assert result.exit_code == 0, repr(result)

    def test_shell_nonzero_exit(self, tool, req):
        """Commands that fail should return a non-zero exit code."""
        result = tool.run(req({"command": f'{_PY} -c "import sys; sys.exit(1)"'}))
        assert result.exit_code != 0, repr(result)

    def test_shell_missing_command(self, tool, req):
        """Missing required 'command' param should raise ValidationError."""
        with pytest.raises(Exception):
            tool.run(req({}))

    def test_shell_runtime_error(self, tool, req):
        """A command that raises a runtime error should return a non-zero exit code."""
        result = tool.run(req({"command": f'{_PY} -c "raise RuntimeError(\'x\')"'}))
        assert result.exit_code != 0, repr(result)

    def test_shell_default_timeout(self, tool):
        """Default timeout (20 from RequestModel) is used when not specified."""
        result = tool.run(RequestModel(method="exec_shell", params={"command": f'{_PY} -c "print(1)"'}))
        assert result.exit_code == 0, repr(result)

    def test_shell_respects_request_timeout(self, tool, req):
        """The request timeout is passed through to subprocess."""
        result = tool.run(req({"command": f'{_PY} -c "import time; time.sleep(99)"'}, timeout=1))
        assert result.exit_code == -1, repr(result)
        assert "timed out" in result.stderr, repr(result)

    def test_params_model(self, tool):
        """ShellParams should be the params_model."""
        assert tool.params_model is ShellParams

    def test_result_model(self, tool):
        """ShellResult should be the result_model."""
        assert tool.result_model is ShellResult


class TestSplitCommand:
    """Tests for :func:`split_command`."""

    @pytest.mark.parametrize("command, expected", [
        # Simple commands
        ("echo hello", ["echo", "hello"]),
        ("cmd arg1 arg2", ["cmd", "arg1", "arg2"]),
        # Quoted arguments — surrounding double-quotes are stripped
        ('python -c "print(1)"', ["python", "-c", "print(1)"]),
        # Single quotes are not stripped by Windows shlex
        ("python -c 'print(1)'", ["python", "-c", "'print(1)'"]),
        # Multiple spaces collapsed
        ("cmd   arg", ["cmd", "arg"]),
        # Leading / trailing whitespace
        ("  echo hello  ", ["echo", "hello"]),
        # Path with spaces — quoted
        ('"C:\\Program Files\\app.exe" --help', ["C:\\Program Files\\app.exe", "--help"]),
        # Empty command
        ("", []),
        # Single word
        ("cmd", ["cmd"]),
    ])
    def test_split_command(self, command, expected):
        """split_command should correctly parse the command string."""
        assert split_command(command) == expected
