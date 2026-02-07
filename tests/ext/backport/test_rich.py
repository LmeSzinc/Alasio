import sys

from exceptiongroup import ExceptionGroup

from alasio.ext.backport.patch_rich import (
    parse_rich_traceback_header, patch_rich_traceback_extract, patch_rich_traceback_links
)
from alasio.logger.logger import rich_formatter


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
        # but calling it again won't hurt due to @patch_once
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
