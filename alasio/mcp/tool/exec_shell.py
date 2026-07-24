"""
MCP tool: execute a shell command via subprocess.

Request params model::

    {"command": "echo hello", "cwd": "/tmp"}
"""

import shlex
import subprocess
from typing import Optional

import msgspec

from alasio.mcp.tool.base import ToolBase


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
    """Result of a shell command execution."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1


class ExecShell(ToolBase):
    """Execute a shell command via ``subprocess.run()``."""

    name = "exec_shell"
    params_model = ShellParams
    result_model = ShellResult

    def execute(self, params, request):
        """Run the shell command and return typed result."""
        try:
            cmd_parts = split_command(params.command)
            result = subprocess.run(
                cmd_parts,
                capture_output=True,
                text=True,
                timeout=request.timeout,
                cwd=params.cwd,
            )
            return ShellResult(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return ShellResult(
                stderr=f"Command timed out after {request.timeout}s",
                exit_code=-1,
            )
        except Exception as e:
            return ShellResult(
                stderr=str(e),
                exit_code=-1,
            )
