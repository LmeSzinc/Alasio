"""
MCP tool: execute inline Python code in a subprocess.

Request params model::

    {"code": "print('hello')"}
"""

import subprocess
import sys
import time
from typing import Optional

import msgspec

from alasio.mcp.tool.base import ToolBase
from alasio.mcp.tool.exec_shell import ShellResult, truncate_output

_PY = sys.executable


class PythonParams(msgspec.Struct):
    """Validated params for ``exec_python``."""

    code: str
    cwd: Optional[str] = None


class ExecPython(ToolBase):
    """Execute inline Python code in a subprocess."""

    name = "exec_python"
    params_model = PythonParams
    result_model = ShellResult

    def execute(self, params, request):
        """Run the Python code in a subprocess and return typed result."""
        start = time.monotonic()
        try:
            # Pipe code through stdin to avoid command-line length limits
            result = subprocess.run(
                [_PY],
                input=params.code,
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
                stderr=f"Python execution timed out after {request.timeout}s",
                duration_ms=elapsed,
                stderr_len=len(f"Python execution timed out after {request.timeout}s"),
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
