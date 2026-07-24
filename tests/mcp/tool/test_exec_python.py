"""Tests for ExecPython tool."""

import pytest

from alasio.mcp.tool.base import RequestModel
from alasio.mcp.tool.exec_python import ExecPython, PythonParams, PythonResult


class TestExecPython:
    """Tests for inline Python execution tool."""

    @pytest.fixture
    def tool(self):
        return ExecPython()

    @pytest.fixture
    def req(self):
        """Build a RequestModel for exec_python with given params and timeout."""
        return lambda params, timeout=10: RequestModel(
            method="exec_python", params=params, timeout=timeout
        )

    @pytest.mark.parametrize("code, expected", [
        ("print('hello')", "hello"),
        ("print(1 + 1)", "2"),
        ("import math; print(math.sqrt(4))", "2.0"),
    ])
    def test_python_success(self, tool, req, code, expected):
        """Valid Python code should execute and capture stdout."""
        result = tool.run(req({"code": code}))
        assert isinstance(result, PythonResult)
        assert result.stdout.strip() == expected, repr(result)
        assert result.error is False, repr(result)

    def test_python_error(self, tool, req):
        """Python runtime error should return error=True with stderr."""
        result = tool.run(req({"code": "1/0"}))
        assert result.error is True, repr(result)
        assert "ZeroDivisionError" in result.stderr or "division by zero" in result.stderr, repr(result)

    def test_python_syntax_error(self, tool, req):
        """Syntax error should be caught and returned."""
        result = tool.run(req({"code": "print("}))
        assert result.error is True, repr(result)
        assert result.stderr, repr(result)

    def test_python_missing_code(self, tool, req):
        """Missing required 'code' param should raise ValidationError."""
        with pytest.raises(Exception):
            tool.run(req({}))

    def test_python_multiline(self, tool, req):
        """Multi-line code should work."""
        result = tool.run(req({"code": "for i in range(3):\n    print(i)"}))
        assert result.stdout.strip() == "0\n1\n2", repr(result)

    def test_python_empty_code(self, tool, req):
        """Empty code string is valid and should produce empty output."""
        result = tool.run(req({"code": ""}))
        assert result.stdout == "", repr(result)
        assert result.error is False, repr(result)

    def test_params_model(self, tool):
        """PythonParams should be the params_model."""
        assert tool.params_model is PythonParams

    def test_result_model(self, tool):
        """PythonResult should be the result_model."""
        assert tool.result_model is PythonResult
