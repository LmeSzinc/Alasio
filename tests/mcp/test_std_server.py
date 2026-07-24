"""Tests for StdServer over stdin/stdout."""

import io
import json
import sys

import pytest

from alasio.mcp.std_server import StdServer

_PY = sys.executable


class TestStdServer:
    """Tests for the MCP stdin/stdout server."""

    def _run_line(self, line):
        """Feed one JSON line and return the parsed response."""
        sin = io.StringIO(line + "\n")
        sout = io.StringIO()
        StdServer(stdin=sin, stdout=sout).serve()
        return json.loads(sout.getvalue())

    # -- Successful requests -----------------------------------------------

    @pytest.mark.parametrize("code, expected", [
        ("print(1)", "1"),
        ("print(42)", "42"),
        ("print('hello world')", "hello world"),
    ])
    def test_exec_python_success(self, code, expected):
        """Successful exec_python should return 'result' with encoded stdout."""
        resp = self._run_line(json.dumps({"method": "exec_python", "params": {"code": code}}))
        assert "result" in resp, f"expected 'result', got {resp}"
        inner = json.loads(resp["result"])
        assert inner["stdout"].strip() == expected, repr(resp)

    def test_timeout_default(self):
        """Omitting timeout should default to 20."""
        # No AssertionError here confirms the default is accepted
        resp = self._run_line(json.dumps({"method": "exec_python", "params": {"code": "print(1)"}}))
        assert "result" in resp, f"expected 'result', got {resp}"

    def test_timeout_explicit(self):
        """Explicit timeout should be accepted."""
        resp = self._run_line(
            json.dumps({"method": "exec_python", "params": {"code": "print(1)"}, "timeout": 30})
        )
        assert "result" in resp, f"expected 'result', got {resp}"

    def test_exec_shell_success(self):
        """Successful exec_shell should return 'result' with encoded output."""
        cmd = f'{_PY} -c "print(\'shell ok\')"'
        resp = self._run_line(json.dumps({"method": "exec_shell", "params": {"command": cmd, "timeout": 10}}))
        assert "result" in resp, f"expected 'result', got {resp}"
        inner = json.loads(resp["result"])
        assert inner["stdout"].strip() == "shell ok", repr(resp)

    # -- Python runtime errors (tool-internal, returned as result) ---------

    def test_python_runtime_error_is_result_not_error(self):
        """Python runtime errors are caught inside the tool and returned as 'result'."""
        resp = self._run_line(json.dumps({"method": "exec_python", "params": {"code": "1/0"}}))
        assert "result" in resp, f"expected 'result', got {resp}"
        inner = json.loads(resp["result"])
        assert inner["error"] is True, repr(resp)

    def test_shell_nonexistent_command(self):
        """A shell command that fails completely should still return 'result'."""
        resp = self._run_line(json.dumps({"method": "exec_shell", "params": {"command": f'{_PY} -c "import sys; sys.exit(42)"', "timeout": 10}}))
        assert "result" in resp, f"expected 'result', got {resp}"
        inner = json.loads(resp["result"])
        assert inner["exit_code"] == 42, repr(resp)

    # -- Malformed / invalid input (returned as error) ----------------------

    def test_malformed_json(self):
        """Malformed JSON should return an error response."""
        sin = io.StringIO("not-valid-json\n")
        sout = io.StringIO()
        StdServer(stdin=sin, stdout=sout).serve()
        resp = json.loads(sout.getvalue())
        assert "error" in resp, f"expected 'error', got {resp}"

    def test_invalid_method_literal(self):
        """A method outside the Literal set should return an error response."""
        resp = self._run_line(json.dumps({"method": "hack", "params": {}}))
        assert "error" in resp, f"expected 'error', got {resp}"

    def test_missing_method_field(self):
        """Missing 'method' should return an error response."""
        resp = self._run_line(json.dumps({"params": {}}))
        assert "error" in resp, f"expected 'error', got {resp}"

    def test_empty_line(self):
        """Empty lines should return an error response."""
        sin = io.StringIO("\n")
        sout = io.StringIO()
        StdServer(stdin=sin, stdout=sout).serve()
        resp = json.loads(sout.getvalue())
        assert "error" in resp, f"expected 'error', got {resp}"

    def test_whitespace_line(self):
        """Whitespace-only lines should return an error response."""
        sin = io.StringIO("  \n")
        sout = io.StringIO()
        StdServer(stdin=sin, stdout=sout).serve()
        resp = json.loads(sout.getvalue())
        assert "error" in resp, f"expected 'error', got {resp}"

    # -- Multiple requests -------------------------------------------------

    def test_two_requests(self):
        """Multiple lines should each produce a response."""
        sin = io.StringIO(
            json.dumps({"method": "exec_python", "params": {"code": "print(1)"}}) + "\n"
            + json.dumps({"method": "exec_python", "params": {"code": "print(2)"}}) + "\n"
        )
        sout = io.StringIO()
        StdServer(stdin=sin, stdout=sout).serve()
        lines = sout.getvalue().strip().split("\n")
        assert len(lines) == 2, f"expected 2 responses, got {len(lines)}"
        for i, line in enumerate(lines):
            resp = json.loads(line)
            assert "result" in resp, f"line {i}: got {resp}"

    def test_error_then_success(self):
        """An error should not break the server; subsequent requests still work."""
        sin = io.StringIO(
            json.dumps({"params": {}}) + "\n"  # bad — missing method → error
            + json.dumps({"method": "exec_python", "params": {"code": "print(3)"}}) + "\n"
        )
        sout = io.StringIO()
        StdServer(stdin=sin, stdout=sout).serve()
        lines = sout.getvalue().strip().split("\n")
        assert len(lines) == 2, f"expected 2 responses, got {len(lines)}"
        assert "error" in json.loads(lines[0]), "first response should be error"
        assert "result" in json.loads(lines[1]), "second response should be result"
