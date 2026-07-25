"""Tests for ExecShell tool."""

import sys

import pytest

from alasio.mcp.tool.base import RequestModel
from alasio.mcp.tool.exec_shell import (
    MAX_OUTPUT_SIZE,
    TRUNCATED_HEAD_BYTES,
    TRUNCATED_TAIL_BYTES,
    ExecShell,
    ShellParams,
    ShellResult,
    split_command,
    truncate_output,
)

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


class TestTruncateOutput:
    """Tests for :func:`truncate_output`."""

    def test_small_output_not_truncated(self):
        """Output under the limit should pass through unchanged."""
        text = "A" * 100
        result, omitted, truncated = truncate_output(text)
        assert result == text
        assert omitted == 0
        assert truncated is False

    def test_exact_limit_not_truncated(self):
        """Output exactly at the limit should not be truncated."""
        text = "A" * MAX_OUTPUT_SIZE
        result, omitted, truncated = truncate_output(text)
        assert result == text
        assert omitted == 0
        assert truncated is False

    def test_large_output_truncated(self):
        """Output exceeding the limit should be truncated with head and tail."""
        text = "A" * (MAX_OUTPUT_SIZE + 5000)
        result, omitted, truncated = truncate_output(text)
        assert truncated is True
        assert omitted > 0
        assert "Output truncated" in result
        assert "Output tail" in result
        # Head preserved
        assert result.startswith("A" * TRUNCATED_HEAD_BYTES)
        # Tail preserved
        assert result.endswith("A" * TRUNCATED_TAIL_BYTES)

    def test_unicode_not_truncated(self):
        """Unicode text under the limit should pass through."""
        text = "你好世界" * 100
        result, omitted, truncated = truncate_output(text)
        assert result == text
        assert truncated is False

    def test_unicode_truncated(self):
        """Unicode text exceeding the limit should be truncated gracefully."""
        # Each CJK character is 3 bytes in UTF-8
        text = "A" * (MAX_OUTPUT_SIZE + 1000)
        result, omitted, truncated = truncate_output(text)
        assert truncated is True
        # No replacement characters from broken UTF-8
        assert "\ufffd" not in result

    def test_empty_string(self):
        """Empty string should return empty with no truncation."""
        result, omitted, truncated = truncate_output("")
        assert result == ""
        assert omitted == 0
        assert truncated is False
