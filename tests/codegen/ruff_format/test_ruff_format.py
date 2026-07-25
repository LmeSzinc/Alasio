from unittest.mock import patch

import pytest

from alasio.codegen.ruff.ruff_format import RuffFormatter, is_valid_module, is_valid_py


class TestIsValidModule:
    """Tests for is_valid_module()."""

    @pytest.mark.parametrize("name, expected", [
        ("abc", True),
        ("ABC", True),
        ("_private", True),
        ("module123", True),
        ("a", True),
        ("", False),
        ("123abc", False),
        ("__pycache__", False),
        ("has space", False),
        ("has-dash", False),
        ("a.b", False),
    ])
    def test_is_valid_module(self, name, expected):
        assert is_valid_module(name) == expected


class TestIsValidPy:
    """Tests for is_valid_py()."""

    @pytest.mark.parametrize("file, expected", [
        ("test.py", True),
        ("test.PY", True),
        ("test.pyi", True),
        ("test.pyx", True),
        ("test.txt", False),
        ("test", False),
        ("test.Py", True),
    ])
    def test_is_valid_py(self, file, expected):
        assert is_valid_py(file) == expected


class TestRulesParsing:
    """Tests for RUFF_RULES parsing — format only, not specific rules."""

    @pytest.fixture
    def formatter(self, tmp_path):
        with patch('os.getcwd', return_value=str(tmp_path)):
            yield RuffFormatter()

    def test_rules_format(self, formatter):
        """RuffFormatter.rules is comma-joined, each token is a non-empty rule code."""
        rules = formatter.rules
        assert rules, 'rules should not be empty'
        assert ' ' not in rules, 'no spaces in joined result'
        assert ',,' not in rules, 'no empty entries'
        assert not rules.startswith(',') and not rules.endswith(','), 'no leading/trailing commas'
        tokens = rules.split(',')
        assert all(tok for tok in tokens), 'each token non-empty'
        assert all(tok.isalnum() for tok in tokens), 'each token is alphanumeric rule code'


class TestFormatCode:
    """Tests for RuffFormatter.format_code.

    Only mocks atomic_read_bytes and atomic_write to control input and
    capture output.  Ruff and isort run for real so we verify the three
    checks (syntax, ruff, isort) are correctly wired without testing the
    tools' own formatting logic.
    """

    @staticmethod
    def _assert_written(mock_write, expected_bytes):
        """Assert atomic_write was called with exactly *expected_bytes*."""
        content = mock_write.call_args[0][1]
        if isinstance(content, str):
            content = content.encode()
        assert content == expected_bytes

    # ---- fixtures -----------------------------------------------------------

    @pytest.fixture
    def formatter(self, tmp_path):
        with patch('os.getcwd', return_value=str(tmp_path)):
            yield RuffFormatter()

    # ---- Check 1: Syntax check ---------------------------------------------

    @patch('alasio.codegen.ruff.ruff_format.atomic_read_bytes')
    @patch('alasio.codegen.ruff.ruff_format.atomic_write')
    def test_syntax_error_skips_ruff_and_isort(self, mock_write, mock_read, formatter):
        """Invalid Python → compile raises SyntaxError → early return."""
        mock_read.return_value = b"""\
invalid python @@@
"""
        formatter.format_code('test.py')
        mock_write.assert_not_called()

    # ---- Checks 2 & 3: Ruff format + Isort (real calls) --------------------

    @patch('alasio.codegen.ruff.ruff_format.atomic_read_bytes')
    @patch('alasio.codegen.ruff.ruff_format.atomic_write')
    def test_ruff_fixes_whitespace(self, mock_write, mock_read, formatter):
        """ruff E225 adds missing whitespace around operator."""
        mock_read.return_value = b"""\
x=1
"""
        formatter.format_code('test.py')
        mock_write.assert_called_once()
        self._assert_written(mock_write, b'x = 1\n')

    @patch('alasio.codegen.ruff.ruff_format.atomic_read_bytes')
    @patch('alasio.codegen.ruff.ruff_format.atomic_write')
    def test_ruff_fixes_multiple_whitespace(self, mock_write, mock_read, formatter):
        """ruff E225 adds missing whitespace — multiple operators."""
        mock_read.return_value = b"""\
x=1
y=2+3
"""
        formatter.format_code('test.py')
        mock_write.assert_called_once()
        self._assert_written(mock_write, b'x = 1\ny = 2 + 3\n')

    @patch('alasio.codegen.ruff.ruff_format.atomic_read_bytes')
    @patch('alasio.codegen.ruff.ruff_format.atomic_write')
    def test_isort_sorts_imports(self, mock_write, mock_read, formatter):
        """isort reorders import blocks (unordered → alphabetically sorted)."""
        mock_read.return_value = b"""\
import os
import json

x = os.getcwd()
y = json.dumps(x)
"""
        formatter.format_code('test.py')
        mock_write.assert_called_once()
        self._assert_written(mock_write, b"""\
import json
import os

x = os.getcwd()
y = json.dumps(x)
""")

    @patch('alasio.codegen.ruff.ruff_format.atomic_read_bytes')
    @patch('alasio.codegen.ruff.ruff_format.atomic_write')
    def test_isort_multi_line_output_overrides_ruff(self, mock_write, mock_read, formatter):
        """isort's multi_line_output=5 packs long imports into a hanging grid."""
        mock_read.return_value = b"""\
from some_module import first_long_function_name_test, second_long_function_name_test, third_long_function_name_test, fourth_long_function_name_test, fifth_long_function_name_test

x = first_long_function_name_test()
y = second_long_function_name_test()
z = third_long_function_name_test()
w = fourth_long_function_name_test()
v = fifth_long_function_name_test()
"""
        formatter.format_code('test.py')
        mock_write.assert_called_once()
        self._assert_written(mock_write, b"""\
from some_module import (
    fifth_long_function_name_test, first_long_function_name_test, fourth_long_function_name_test,
    second_long_function_name_test, third_long_function_name_test
)

x = first_long_function_name_test()
y = second_long_function_name_test()
z = third_long_function_name_test()
w = fourth_long_function_name_test()
v = fifth_long_function_name_test()
""")

    @patch('alasio.codegen.ruff.ruff_format.atomic_read_bytes')
    @patch('alasio.codegen.ruff.ruff_format.atomic_write')
    def test_line_length_120_keeps_long_lines_unwrapped(self, mock_write, mock_read, formatter):
        """LINE_LENGTH=120 keeps 118-char import and 115-char code on single lines (vs ruff default 88)."""
        mock_read.return_value = b"""\
from some_module import a_reasonably_long_function_name_for_testing, another_reasonably_long_function_name_for_testing

x=1
x = a_reasonably_long_function_name_for_testing()
y = another_reasonably_long_function_name_for_testing()
z = a_function_with_a_really_long_name_that_exceeds_eighty_chars_by_far(argument_one, argument_two, argument_three)
"""
        formatter.format_code('test.py')
        mock_write.assert_called_once()
        self._assert_written(mock_write, b"""\
from some_module import a_reasonably_long_function_name_for_testing, another_reasonably_long_function_name_for_testing

x = 1
x = a_reasonably_long_function_name_for_testing()
y = another_reasonably_long_function_name_for_testing()
z = a_function_with_a_really_long_name_that_exceeds_eighty_chars_by_far(argument_one, argument_two, argument_three)
""")

    @patch('alasio.codegen.ruff.ruff_format.atomic_read_bytes')
    @patch('alasio.codegen.ruff.ruff_format.atomic_write')
    def test_clean_code_skips_write(self, mock_write, mock_read, formatter):
        """Already clean code → no modification → no file write."""
        mock_read.return_value = b"""\
x = 1
y = 2
"""
        formatter.format_code('test.py')
        mock_write.assert_not_called()


class TestFormatCodePrints:
    """Tests for format_code's print() output."""

    @pytest.fixture
    def formatter(self, tmp_path):
        with patch('os.getcwd', return_value=str(tmp_path)):
            yield RuffFormatter()

    def test_print_syntax_error(self, formatter, capsys):
        """Syntax error prints Formatting: then the SyntaxError message."""
        with patch('alasio.codegen.ruff.ruff_format.atomic_read_bytes') as mock_read:
            mock_read.return_value = b'@@@ invalid\n'
            capsys.readouterr()  # discard init prints
            formatter.format_code('test.py')
            out, _ = capsys.readouterr()
            assert out.startswith('Formatting: test.py')
            assert 'SyntaxError' in out
            assert out.endswith('\n\n')

    def test_print_ruff_fix(self, formatter, capsys):
        """Ruff fix triggers Formatting, Import sorting all good, Writing, and ruff stderr."""
        with patch('alasio.codegen.ruff.ruff_format.atomic_read_bytes') as mock_read, \
             patch('alasio.codegen.ruff.ruff_format.atomic_write'):
            mock_read.return_value = b'x=1\n'
            capsys.readouterr()
            formatter.format_code('test.py')
            out, _ = capsys.readouterr()
            assert out.startswith('Formatting: test.py')
            assert 'Import sorting all good' in out
            assert 'Writing: test.py' in out
            assert out.endswith('\n\n')

    def test_print_isort_fixed(self, formatter, capsys):
        """When isort changes imports, 'Import sorting fixed' is printed."""
        with patch('alasio.codegen.ruff.ruff_format.atomic_read_bytes') as mock_read, \
             patch('alasio.codegen.ruff.ruff_format.atomic_write'):
            mock_read.return_value = (
                b'import os\nimport json\n\nx = os.getcwd()\ny = json.dumps(x)\n'
            )
            capsys.readouterr()
            formatter.format_code('test.py')
            out, _ = capsys.readouterr()
            assert 'Import sorting fixed' in out
            assert 'Writing: test.py' in out

    def test_print_no_write_on_clean_code(self, formatter, capsys):
        """Clean code prints Formatting but NOT Writing, and ends with blank line."""
        with patch('alasio.codegen.ruff.ruff_format.atomic_read_bytes') as mock_read, \
             patch('alasio.codegen.ruff.ruff_format.atomic_write') as mock_write:
            mock_read.return_value = b'x = 1\ny = 2\n'
            capsys.readouterr()
            formatter.format_code('test.py')
            out, _ = capsys.readouterr()
            assert out.startswith('Formatting: test.py')
            assert 'Writing: test.py' not in out
            assert out.endswith('\n\n')
            mock_write.assert_not_called()

    def test_print_ruff_stderr_is_visible(self, formatter, capsys):
        """Ruff's stderr output is printed (at least non-empty)."""
        with patch('alasio.codegen.ruff.ruff_format.atomic_read_bytes') as mock_read, \
             patch('alasio.codegen.ruff.ruff_format.atomic_write'):
            mock_read.return_value = b'x=1\n'
            capsys.readouterr()
            formatter.format_code('test.py')
            out, _ = capsys.readouterr()
            lines = [line for line in out.splitlines() if line.strip()]
            # Last non-blank line is ruff's stderr before the trailing blank line
            assert len(lines) >= 3  # Formatting, Import, Writing, ruff stderr
