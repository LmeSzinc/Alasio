import json
import os
import subprocess
import sys

import pytest
from exceptiongroup import ExceptionGroup

from alasio.backport.rich import parse_rich_traceback_header, patch_rich_traceback_extract, patch_rich_traceback_links
from alasio.ext.env import ALASIO_ROOT
from alasio.logger.logger import rich_formatter

# The rendering tests spawn interpreters of their own, which is what a serial
# group would wait for: the file depends on nothing but rich and its children, so
# it runs beside the group instead of holding it.
pytestmark = [
    pytest.mark.parallel,
]


class TestParseRichTracebackHeader:
    def test_normal(self):
        # Standard case
        line = "E:\\path\\to\\file.py:5 in cause_error"
        assert parse_rich_traceback_header(line) == ("E:\\path\\to\\file.py", "5", "cause_error")

    def test_path_with_spaces(self):
        # Path with spaces
        line = "C:\\Program Files\\Application\\main.py:10 in run_task"
        assert parse_rich_traceback_header(line) == ("C:\\Program Files\\Application\\main.py", "10", "run_task")

    def test_no_function(self):
        # No " in " separator
        line = "/usr/local/bin/script.py:42"
        assert parse_rich_traceback_header(line) == ("/usr/local/bin/script.py", "42", "")

    def test_no_line_number(self):
        # No colon for line number
        line = "relative/path/file.py"
        assert parse_rich_traceback_header(line) == ("relative/path/file.py", "", "")

    def test_multiple_in(self):
        # Path contains " in " (unlikely but possible)
        line = "C:\\projects\\my in project\\main.py:1 in my_func"
        assert parse_rich_traceback_header(line) == ("C:\\projects\\my in project\\main.py", "1", "my_func")

    def test_multiple_colons(self):
        # Linux path with colon (not common) or just messy input
        line = "/home/user/file:name.py:100 in <module>"
        assert parse_rich_traceback_header(line) == ("/home/user/file:name.py", "100", "<module>")

    def test_empty_string(self):
        line = ""
        assert parse_rich_traceback_header(line) == ("", "", "")

    def test_only_colons(self):
        line = ":::"
        # rpartition on ':'
        # "::" , ":" , ""
        assert parse_rich_traceback_header(line) == ("::", "", "")


class TestPatchRichTracebackExtract:
    def test_exceptiongroup_traceback(self):
        """
        Test that ExceptionGroup can be extracted and rendered by rich after patch.
        This tests both the patch and the rich compatibility.
        """
        # patch_rich_traceback_extract() is already called in alasio.logger.logger
        # but calling it again won't hurt due to @init_once
        patch_rich_traceback_extract()

        try:
            try:
                raise ValueError("error 1")
            except ValueError as e1:
                try:
                    raise TypeError("error 2")
                except TypeError as e2:
                    raise ExceptionGroup("Nested", [e1, e2])
        except ExceptionGroup as eg:
            exc_info = (type(eg), eg, eg.__traceback__)
            rich_trace, plain_trace = rich_formatter(exc_info)

            # Check if rendered content exists
            assert "ExceptionGroup: Nested" in plain_trace
            assert "ValueError: error 1" in plain_trace
            assert "TypeError: error 2" in plain_trace
            # Check if it contains some file info
            assert "test_rich.py" in plain_trace


# A rich release newer than the backports is left alone: the patch was written
# against the internals of a specific release, and reformatting a newer one
# through that copy breaks its tracebacks instead of only losing the panels the
# patch is about.
CHILD_UNLISTED_VERSION = """
import importlib.metadata
import json
import sys
from rich.traceback import Traceback
from alasio.logger.logger import rich_formatter

_reported_version = importlib.metadata.version

def version(name):
    if name == 'rich':
        return '99.0.0'
    return _reported_version(name)

importlib.metadata.version = version

def format_error():
    try:
        raise ValueError('probe')
    except ValueError:
        _, plain_trace = rich_formatter(sys.exc_info())
    return plain_trace

plain_trace = format_error()

print(json.dumps({
    'extract': Traceback.extract.__qualname__,
    'has_error': 'ValueError: probe' in plain_trace,
}))
"""


def run_child(code):
    """
    Run a snippet in a fresh interpreter and return what it printed.

    A fresh interpreter is required: the patch is applied once per process, so the
    in-process state would answer for the first traceback of the test session.

    Args:
        code (str): Python source, it must print one JSON object as its last line

    Returns:
        dict: The decoded JSON object
    """
    env = os.environ.copy()
    root = os.path.normpath(ALASIO_ROOT)
    existing = env.get('PYTHONPATH')
    env['PYTHONPATH'] = root + (os.pathsep + existing if existing else '')

    proc = subprocess.run(
        [sys.executable, '-c', code],
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr

    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    return json.loads(lines[-1])


class TestPatchRichTracebackExtractVersionList:
    def test_a_release_outside_the_list_is_not_patched(self):
        """
        The patch belongs to the releases it was copied from, a newer rich keeps
        its own extract()
        """
        result = run_child(CHILD_UNLISTED_VERSION)

        assert result['extract'] == 'Traceback.extract'
        # the fallback still renders, only without the panels of the patch
        assert result['has_error'] is True

    @pytest.mark.skipif(
        sys.version_info >= (3, 11), reason='python>=3.11 renders exception groups by itself'
    )
    def test_the_suite_applies_it_before_any_test_runs(self):
        """
        The patch is decided once per process and the decision reads the version of
        rich from the filesystem of that moment. A test may format the first
        exception of the process under the in-memory filesystem, where rich cannot
        be found at all, so the suite applies the patch at import (tests/conftest.py)
        instead of leaving it to that first exception.
        """
        from rich.traceback import Traceback

        assert Traceback.extract.__qualname__ == 'RichTracebackBackport.extract'


class TestPatchRichTracebackLinks:
    def test_clickable_links_format(self):
        """
        Test if the patch correctly formats the stack trace into standard Python format
        which IDEs recognize as clickable links.
        """
        patch_rich_traceback_links()

        def level_2():
            raise ValueError("test error")

        def level_1():
            level_2()

        try:
            level_1()
        except ValueError:
            exc_info = sys.exc_info()
            rich_trace, plain_trace = rich_formatter(exc_info)

            # Rich by default uses: path:line in func
            # After patch, it should use: File "path", line line, in func

            # Check for the patched format
            # Note: rich_formatter uses Console(width=120)
            assert '  File "' in plain_trace
            assert '", line ' in plain_trace
            assert ", in level_2" in plain_trace
            assert ", in level_1" in plain_trace

            # Verify specifically that the rich-style "path:line in func" is GONE or replaced
            # Original rich: e:\...\test_rich.py:76 in test_clickable_links_format
            # Patched:   File "e:\...\test_rich.py", line 76, in test_clickable_links_format

            # Since we check for '  File "', it pretty much confirms the patch is working
            # if rich_formatter is producing it.


class TestPlainTraceback:
    def test_plain_traceback_is_ascii_on_a_non_legacy_host(self, monkeypatch):
        """
        The plain traceback goes to log files, so it must be ascii on every host.

        rich draws its panel with box.ROUNDED and only substitutes SQUARE on a legacy
        Windows console, so a POSIX host used to leave rounded corners in the logs.
        """
        # emulate a POSIX host (or a VT capable Windows console)
        monkeypatch.setattr('rich.console.detect_legacy_windows', lambda: False)

        try:
            raise ValueError('test error')
        except ValueError:
            rich_trace, plain_trace = rich_formatter(sys.exc_info())

        # the setup took effect, otherwise the assertion below would be vacuous
        assert '\u256d' in rich_trace
        # no box drawing character is left in the plain text
        assert not any(0x2500 <= ord(char) <= 0x257F for char in plain_trace)
        lines = plain_trace.splitlines()
        assert lines[0].startswith('+---')
        assert lines[0].endswith('---+')
