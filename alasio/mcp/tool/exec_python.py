"""
MCP tool: execute inline Python code in a subprocess.

Request params model::

    {"code": "print('hello')"}
"""

import subprocess
import sys
from typing import Optional

import msgspec

from alasio.mcp.tool.base import ToolBase

_PY = sys.executable


class PythonParams(msgspec.Struct):
    """Validated params for ``exec_python``."""

    code: str
    cwd: Optional[str] = None


class PythonResult(msgspec.Struct):
    """Result of inline Python execution."""

    stdout: str = ""
    stderr: str = ""
    error: bool = False


class ExecPython(ToolBase):
    """Execute inline Python code in a subprocess."""

    name = "exec_python"
    params_model = PythonParams
    result_model = PythonResult

    def execute(self, params, request):
        """Run the Python code in a subprocess and return typed result."""
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
            return PythonResult(
                stdout=result.stdout,
                stderr=result.stderr,
                error=result.returncode != 0,
            )
        except subprocess.TimeoutExpired:
            return PythonResult(
                stderr=f"Python execution timed out after {request.timeout}s",
                error=True,
            )
        except Exception as e:
            return PythonResult(
                stderr=str(e),
                error=True,
            )
