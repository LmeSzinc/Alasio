import builtins
import io
import shutil
from pathlib import Path

import pytest
import ruff

from alasio.codegen.ruff.ruff_format import RuffFormatter, is_valid_module, is_valid_py
from alasio.testing.filesystem import fs  # noqa: F401

# The in-memory fake filesystem mocks os.path.isfile (breaking the ruff
# binary lookup) and io.open() with an int fd (breaking subprocess pipes),
# so the real open and the real ruff binary path are captured at import
# time, before any fs fixture activates.
_REAL_OPEN = builtins.open
RUFF_BIN = ruff.find_ruff_bin()


@pytest.fixture(scope='module')
def real_config_dir():
    """
    Real directory bridging the ruff temp config to the ruff subprocess.

    The fake filesystem is in-memory only: the ruff subprocess reads its
    --config file from the real disk, so the same file must exist here.
    The formatter writes the config into the fake fs under this directory;
    the fixture keeps the identical content on the real disk. The directory
    lives under the repo's temp/ folder and is removed after the module.
    """
    path = Path(__file__).resolve().parents[3] / 'temp' / 'ruff_format'
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def formatter(fs, real_config_dir, monkeypatch):
    """
    Build a RuffFormatter working on the in-memory fake filesystem.

    The formatter cwd is the real temp config directory mirrored in the
    fake fs, so the temp config path is readable by the ruff subprocess.
    Two bridges are needed because the fake fs cannot serve subprocesses:

    - ruff binary lookup goes through os.path.isfile (mocked by the fake
      fs), so find_ruff_bin() is patched to the real binary path;
    - subprocess opens its pipes with io.open(int fd), so open() routes
      int fds to the real open and keeps str paths on the fake fs.
    """
    from alasio.codegen.ruff import ruff_format

    monkeypatch.setattr(ruff_format.ruff, 'find_ruff_bin', lambda: RUFF_BIN)

    # mirror the real config dir in the fake fs and make it the formatter cwd
    fs.create_dir(str(real_config_dir))
    fs.chdir(str(real_config_dir))
    formatter = RuffFormatter()

    # write the ruff config to the real disk; format_code() writes the same
    # content to the fake fs path, and the subprocess reads the real one
    with _REAL_OPEN(real_config_dir / '_temp_config.toml', 'w', encoding='utf-8', newline='') as f:
        f.write(formatter.ruff_config)

    # subprocess opens pipes with io.open(int fd): route int fds to the real
    # open, keep str paths on the fake fs
    fake_open = fs.open

    def smart_open(file, *args, **kwargs):
        if isinstance(file, int):
            return _REAL_OPEN(file, *args, **kwargs)
        return fake_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, 'open', smart_open)
    monkeypatch.setattr(io, 'open', smart_open)

    yield formatter


def _read(fs, file='test.py'):
    """Read a file from the fake filesystem as bytes."""
    return fs.get_file(file).content


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

    The input file lives on the in-memory fake filesystem (alasio.testing.
    filesystem.fs): the test creates the file, runs format_code() and reads
    the result back from the fake fs. Ruff and isort run for real so we
    verify the three checks (syntax, ruff, isort) are correctly wired
    without testing the tools' own formatting logic.
    """

    # ---- Check 1: Syntax check ---------------------------------------------

    def test_syntax_error_skips_ruff_and_isort(self, fs, formatter):
        """Invalid Python → compile raises SyntaxError → early return, file unchanged."""
        fs.create_file('test.py', contents=b"""\
invalid python @@@
""")
        formatter.format_code('test.py')
        assert _read(fs) == b"""\
invalid python @@@
"""

    # ---- Checks 2 & 3: Ruff format + Isort (real calls) --------------------

    def test_ruff_fixes_whitespace(self, fs, formatter):
        """ruff E225 adds missing whitespace around operator."""
        fs.create_file('test.py', contents=b"""\
x=1
""")
        formatter.format_code('test.py')
        assert _read(fs) == b'x = 1\n'

    def test_ruff_fixes_multiple_whitespace(self, fs, formatter):
        """ruff E225 adds missing whitespace — multiple operators."""
        fs.create_file('test.py', contents=b"""\
x=1
y=2+3
""")
        formatter.format_code('test.py')
        assert _read(fs) == b'x = 1\ny = 2 + 3\n'

    def test_isort_sorts_imports(self, fs, formatter):
        """isort reorders import blocks (unordered → alphabetically sorted)."""
        fs.create_file('test.py', contents=b"""\
import os
import json

x = os.getcwd()
y = json.dumps(x)
""")
        formatter.format_code('test.py')
        assert _read(fs) == b"""\
import json
import os

x = os.getcwd()
y = json.dumps(x)
"""

    def test_isort_multi_line_output_overrides_ruff(self, fs, formatter):
        """isort's multi_line_output=5 packs long imports into a hanging grid."""
        fs.create_file('test.py', contents=b"""\
from some_module import first_long_function_name_test, second_long_function_name_test, third_long_function_name_test, fourth_long_function_name_test, fifth_long_function_name_test

x = first_long_function_name_test()
y = second_long_function_name_test()
z = third_long_function_name_test()
w = fourth_long_function_name_test()
v = fifth_long_function_name_test()
""")
        formatter.format_code('test.py')
        assert _read(fs) == b"""\
from some_module import (
    fifth_long_function_name_test, first_long_function_name_test, fourth_long_function_name_test,
    second_long_function_name_test, third_long_function_name_test
)

x = first_long_function_name_test()
y = second_long_function_name_test()
z = third_long_function_name_test()
w = fourth_long_function_name_test()
v = fifth_long_function_name_test()
"""

    def test_line_length_120_keeps_long_lines_unwrapped(self, fs, formatter):
        """LINE_LENGTH=120 keeps 118-char import and 115-char code on single lines (vs ruff default 88)."""
        fs.create_file('test.py', contents=b"""\
from some_module import a_reasonably_long_function_name_for_testing, another_reasonably_long_function_name_for_testing

x=1
x = a_reasonably_long_function_name_for_testing()
y = another_reasonably_long_function_name_for_testing()
z = a_function_with_a_really_long_name_that_exceeds_eighty_chars_by_far(argument_one, argument_two, argument_three)
""")
        formatter.format_code('test.py')
        assert _read(fs) == b"""\
from some_module import a_reasonably_long_function_name_for_testing, another_reasonably_long_function_name_for_testing

x = 1
x = a_reasonably_long_function_name_for_testing()
y = another_reasonably_long_function_name_for_testing()
z = a_function_with_a_really_long_name_that_exceeds_eighty_chars_by_far(argument_one, argument_two, argument_three)
"""

    def test_clean_code_skips_write(self, fs, formatter):
        """Already clean code → no modification → file content unchanged."""
        fs.create_file('test.py', contents=b"""\
x = 1
y = 2
""")
        formatter.format_code('test.py')
        assert _read(fs) == b'x = 1\ny = 2\n'

    # ---- CRLF -> LF conversion ----------------------------------------------

    def test_crlf_converted_to_lf(self, fs, formatter):
        """CRLF line endings are converted to LF even when code is otherwise clean."""
        fs.create_file('test.py', contents=b'x = 1\r\ny = 2\r\n')
        formatter.format_code('test.py')
        assert _read(fs) == b'x = 1\ny = 2\n'

    def test_crlf_converted_with_ruff_fix(self, fs, formatter):
        """CRLF input that also needs a ruff fix is written with LF only."""
        fs.create_file('test.py', contents=b'x=1\r\n')
        formatter.format_code('test.py')
        assert _read(fs) == b'x = 1\n'


class TestFormatCodePrints:
    """Tests for format_code's print() output."""

    def test_print_syntax_error(self, fs, formatter, capsys):
        """Syntax error prints Formatting: then the SyntaxError message."""
        fs.create_file('test.py', contents=b'@@@ invalid\n')
        capsys.readouterr()  # discard init prints
        formatter.format_code('test.py')
        out, _ = capsys.readouterr()
        assert out.startswith('Formatting: test.py')
        assert 'SyntaxError' in out
        assert out.endswith('\n\n')

    def test_print_ruff_fix(self, fs, formatter, capsys):
        """Ruff fix triggers Formatting, Import sorting all good, Writing, and ruff stderr."""
        fs.create_file('test.py', contents=b'x=1\n')
        capsys.readouterr()
        formatter.format_code('test.py')
        out, _ = capsys.readouterr()
        assert out.startswith('Formatting: test.py')
        assert 'Import sorting all good' in out
        assert 'Writing: test.py' in out
        assert out.endswith('\n\n')

    def test_print_isort_fixed(self, fs, formatter, capsys):
        """When isort changes imports, 'Import sorting fixed' is printed."""
        fs.create_file('test.py', contents=(
            b'import os\nimport json\n\nx = os.getcwd()\ny = json.dumps(x)\n'
        ))
        capsys.readouterr()
        formatter.format_code('test.py')
        out, _ = capsys.readouterr()
        assert 'Import sorting fixed' in out
        assert 'Writing: test.py' in out

    def test_print_no_write_on_clean_code(self, fs, formatter, capsys):
        """Clean code prints Formatting but NOT Writing, and ends with blank line."""
        fs.create_file('test.py', contents=b'x = 1\ny = 2\n')
        capsys.readouterr()
        formatter.format_code('test.py')
        out, _ = capsys.readouterr()
        assert out.startswith('Formatting: test.py')
        assert 'Writing: test.py' not in out
        assert out.endswith('\n\n')
        assert _read(fs) == b'x = 1\ny = 2\n'

    def test_print_crlf_conversion(self, fs, formatter, capsys):
        """CRLF input prints the conversion message and writes the file."""
        fs.create_file('test.py', contents=b'x = 1\r\ny = 2\r\n')
        capsys.readouterr()
        formatter.format_code('test.py')
        out, _ = capsys.readouterr()
        assert 'CRLF line endings converted to LF' in out
        assert 'Writing: test.py' in out

    def test_print_ruff_stderr_is_visible(self, fs, formatter, capsys):
        """Ruff's stderr output is printed (at least non-empty)."""
        fs.create_file('test.py', contents=b'x=1\n')
        capsys.readouterr()
        formatter.format_code('test.py')
        out, _ = capsys.readouterr()
        lines = [line for line in out.splitlines() if line.strip()]
        # Last non-blank line is ruff's stderr before the trailing blank line
        assert len(lines) >= 3  # Formatting, Import, Writing, ruff stderr
