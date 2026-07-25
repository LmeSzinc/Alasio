"""
MCP tool: execute a shell command via subprocess.

Request params model::

    {"command": "echo hello", "cwd": "/tmp"}

Result format follows the CodeWhale ``exec_shell`` tool schema.
"""

import shlex
import subprocess
import time
from typing import Literal, Optional

import msgspec

from alasio.mcp.tool.base import ToolBase

# ── Output truncation (mirrors CodeWhale constants) ──────────────────────

MAX_OUTPUT_SIZE = 30_000
TRUNCATED_HEAD_BYTES = 22_000
TRUNCATED_TAIL_BYTES = MAX_OUTPUT_SIZE - TRUNCATED_HEAD_BYTES  # 8_000


def truncate_output(output):
    """Truncate output to ``MAX_OUTPUT_SIZE`` bytes preserving head + tail.

    Follows the same algorithm as CodeWhale's ``truncate_with_meta``.

    Args:
        output (str): Raw output text.

    Returns:
        tuple[str, int, bool]: (truncated text, omitted bytes, was truncated).
    """
    original_bytes = output.encode("utf-8")
    original_len = len(original_bytes)
    if original_len <= MAX_OUTPUT_SIZE:
        return output, 0, False

    # Head
    head_bytes = original_bytes[:TRUNCATED_HEAD_BYTES]
    head = head_bytes.decode("utf-8", errors="replace")
    # Tail
    tail_bytes = original_bytes[-TRUNCATED_TAIL_BYTES:]
    tail = tail_bytes.decode("utf-8", errors="replace")

    omitted = original_len - len(head_bytes) - len(tail_bytes)
    note = (
        f"...\n\n[Output truncated: showing first {len(head_bytes)} bytes "
        f"and last {len(tail_bytes)} bytes. {omitted} bytes omitted.]"
    )

    truncated = f"{head}{note}\n\n[Output tail]\n{tail}"
    return truncated, omitted, True


def split_command(command):
    """Parse a shell command string into a list of arguments.

    Uses ``shlex.split`` with Windows-friendly settings and strips
    surrounding double-quotes that ``shlex`` preserves under ``posix=False``.

    Args:
        command (str): Shell command string, e.g. ``'python -c "print(1)"'``.

    Returns:
        list[str]: List of arguments.
    """
    return [p.strip('"') for p in shlex.split(command, posix=False)]


class ShellParams(msgspec.Struct):
    """Validated params for ``exec_shell``."""

    command: str
    cwd: Optional[str] = None


class ShellResult(msgspec.Struct):
    """Result of a shell command execution (CodeWhale-compatible)."""

    status: Literal["Completed", "Failed", "TimedOut", "Killed"] = "Completed"
    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    stdout_len: int = 0
    stderr_len: int = 0
    stdout_omitted: int = 0
    stderr_omitted: int = 0
    stdout_truncated: bool = False
    stderr_truncated: bool = False


class ExecShell(ToolBase):
    """Execute a shell command via ``subprocess.run()``."""

    name = "exec_shell"
    params_model = ShellParams
    result_model = ShellResult

    def execute(self, params, request):
        """Run the shell command and return typed result."""
        start = time.monotonic()
        try:
            cmd_parts = split_command(params.command)
            result = subprocess.run(
                cmd_parts,
                capture_output=True,
                text=True,
                timeout=request.timeout,
                cwd=params.cwd,
            )
            elapsed = int((time.monotonic() - start) * 1000)
            raw_stdout = result.stdout
            raw_stderr = result.stderr
            stdout, stdout_omitted, stdout_truncated = truncate_output(raw_stdout)
            stderr, stderr_omitted, stderr_truncated = truncate_output(raw_stderr)
            status = "Completed" if result.returncode == 0 else "Failed"
            return ShellResult(
                status=status,
                exit_code=result.returncode,
                stdout=stdout,
                stderr=stderr,
                duration_ms=elapsed,
                stdout_len=len(raw_stdout),
                stderr_len=len(raw_stderr),
                stdout_omitted=stdout_omitted,
                stderr_omitted=stderr_omitted,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
            )
        except subprocess.TimeoutExpired:
            elapsed = int((time.monotonic() - start) * 1000)
            return ShellResult(
                status="TimedOut",
                stderr=f"Command timed out after {request.timeout}s",
                duration_ms=elapsed,
                stderr_len=len(f"Command timed out after {request.timeout}s"),
            )
        except Exception as e:
            elapsed = int((time.monotonic() - start) * 1000)
            err_msg = str(e)
            return ShellResult(
                status="Failed",
                stderr=err_msg,
                duration_ms=elapsed,
                stderr_len=len(err_msg),
            )
