import re

import pytest

from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.logger.logger import LogWriter, logger


class BaseLoggerTest:
    """
    Base class for logger tests with setup and teardown
    """

    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        """
        Setup and cleanup for each test
        """
        # Store original env values
        original_root = env.PROJECT_ROOT
        original_electron = env.ELECTRON_SECRET

        # Create temp directory for test logs
        self.test_dir = PathStr.new(__file__).uppath(1)
        env.PROJECT_ROOT = self.test_dir
        env.ELECTRON_SECRET = None

        # init fd
        writer = LogWriter()
        _ = writer.fd

        yield

        # Cleanup: close file descriptor if exists
        writer = LogWriter()
        writer.close()

        # Restore original env values
        env.PROJECT_ROOT = original_root
        env.ELECTRON_SECRET = original_electron

        # Clean up log directory
        log_dir = self.test_dir / 'log'
        log_dir.folder_rmtree()

    def _read_log_file(self):
        """
        Read content from log file

        Returns:
            str: Log file content
        """
        log_file = LogWriter().file

        if not log_file.exists():
            return ''

        return log_file.atomic_read_text()


class TestLogger(BaseLoggerTest):
    """
    Test basic logging functionality
    """

    def test_basic_logging_info(self):
        """
        Test basic info level logging
        """
        # Log a simple message
        logger.info('Test info message')

        # Read log file
        content = self._read_log_file()

        # Verify log content contains expected message
        assert 'Test info message' in content
        assert 'INFO' in content

        # Verify log format: YYYY-MM-DD HH:MM:SS.mmm | LEVEL | message
        pattern = r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \| INFO \| Test info message'
        assert re.search(pattern, content) is not None

    def test_basic_logging_levels(self):
        """
        Test different logging levels: debug, info, warning, error, critical
        """
        logger.debug('Debug message')
        logger.info('Info message')
        logger.warning('Warning message')
        logger.error('Error message')
        logger.critical('Critical message')

        content = self._read_log_file()

        # Verify all log levels are present
        assert 'DEBUG' in content and 'Debug message' in content
        assert 'INFO' in content and 'Info message' in content
        assert 'WARNING' in content and 'Warning message' in content
        assert 'ERROR' in content and 'Error message' in content
        assert 'CRITICAL' in content and 'Critical message' in content

    def test_logging_with_format(self):
        """
        Test logging with string formatting
        """
        logger.info('User {name} logged in', name='John')

        content = self._read_log_file()

        # Verify formatted message
        assert 'User John logged in' in content

    def test_raw_logging(self):
        """
        Test raw logging without timestamp and level
        """
        logger.raw('Raw log message')

        content = self._read_log_file()

        # Verify raw message exists
        assert 'Raw log message' in content
        # Raw message shouldn't have timestamp and level format
        lines = content.strip().split('\n')
        raw_line = None
        for line in lines:
            if 'Raw log message' in line:
                raw_line = line
                break

        assert raw_line == 'Raw log message'

    def test_hr_logging(self):
        """
        Test horizontal rule logging with different levels
        """
        logger.hr('Title', level=0)
        logger.hr('Title', level=1)
        logger.hr('Title', level=2)
        logger.hr('Title', level=3)

        content = self._read_log_file()

        # Verify hr messages contain TITLE (uppercase)
        assert content.count('TITLE') >= 4
        # Verify hr0 contains box drawing
        assert '+=' in content or '|' in content
        # Verify hr1 contains equal signs
        assert '=====' in content
        # Verify hr2 contains dashes
        assert '-----' in content
        # Verify hr3 contains dots
        assert '.....' in content


class TestLoggerBind(BaseLoggerTest):
    """
    Test that logger.bind() does not create new file writes
    """

    def _get_log_dir(self):
        """
        Get log directory path

        Returns:
            PathStr: Log directory path
        """
        return self.test_dir / 'log'

    def _count_log_files(self):
        """
        Count number of log files in log directory

        Returns:
            int: Number of log files
        """
        log_dir = self._get_log_dir()
        if not log_dir.exists():
            return 0

        return len(list(log_dir.iter_files(ext='.txt')))

    def test_bind_does_not_create_new_file(self):
        """
        Test that logger.bind() uses the same file descriptor and doesn't create new files
        """
        # Log with original logger
        logger.info('Original logger message')

        # Check there's only one log file
        assert self._count_log_files() == 1

        # Bind logger with context
        bound_logger1 = logger.bind(user='Alice')
        bound_logger1.info('Bound logger 1 message')

        # Check still only one log file
        assert self._count_log_files() == 1

        # Bind another logger with different context
        bound_logger2 = logger.bind(user='Bob', session='session123')
        bound_logger2.info('Bound logger 2 message')

        # Check still only one log file
        assert self._count_log_files() == 1

        # All messages should be in the same file
        log_files = list(self._get_log_dir().iter_files(ext='.txt'))
        assert len(log_files) == 1

        with open(log_files[0], 'r', encoding='utf-8') as f:
            content = f.read()

        assert 'Original logger message' in content
        assert 'Bound logger 1 message' in content
        assert 'Bound logger 2 message' in content

    def test_bind_shares_singleton_writer(self):
        """
        Test that all bound loggers share the same LogWriter singleton
        """
        # Get LogWriter instance
        writer1 = LogWriter()

        # Create bound logger
        bound_logger = logger.bind(context='test')

        # Log something to ensure writer is initialized
        bound_logger.info('Test message')

        # Get LogWriter instance again
        writer2 = LogWriter()

        # Verify they are the same instance (singleton)
        assert writer1 is writer2

        # Verify file descriptors are the same
        fd1 = writer1.fd
        fd2 = writer2.fd
        assert fd1 is fd2

    def test_multiple_binds_same_output(self):
        """
        Test multiple bind operations write to the same file
        """
        # Create chain of bound loggers
        logger1 = logger.bind(module='auth')
        logger2 = logger1.bind(function='login')
        logger3 = logger2.bind(user='test_user')

        # Log with each
        logger.info('Base logger')
        logger1.info('Module logger')
        logger2.info('Function logger')
        logger3.info('User logger')

        # Verify only one log file exists
        assert self._count_log_files() == 1

        # Verify all messages are in the same file
        log_files = list(self._get_log_dir().iter_files(ext='.txt'))
        with open(log_files[0], 'r', encoding='utf-8') as f:
            content = f.read()

        assert 'Base logger' in content
        assert 'Module logger' in content
        assert 'Function logger' in content
        assert 'User logger' in content


class TestLoggerFormatting(BaseLoggerTest):
    """
    Test logger formatting behavior to prevent double-formatting bugs
    """

    def test_fstring_with_set_no_double_format(self):
        """
        Test that f-string with set doesn't get double-formatted
        Bug: logger.info(f'modules={modules}') where modules={'a', 'b'}
        should output: modules={'a', 'b'}
        not: modules=<key 'a' missing>
        """
        modules = {'combat_ui', 'combat_support'}
        logger.info(f'Assets generate, modules={modules}')

        content = self._read_log_file()

        # Should contain the set representation
        assert 'Assets generate, modules=' in content
        assert 'combat_ui' in content
        assert 'combat_support' in content
        # Should NOT contain error message
        assert '<key' not in content
        assert 'missing>' not in content

    def test_fstring_with_dict_no_double_format(self):
        """
        Test that f-string with dict doesn't get double-formatted
        """
        config = {'timeout': 30, 'retries': 3}
        logger.info(f'Config: {config}')

        content = self._read_log_file()

        # Should contain the dict representation
        assert 'Config:' in content
        assert 'timeout' in content
        assert '30' in content
        # Should NOT contain error message
        assert '<key' not in content
        assert 'missing>' not in content

    def test_fstring_with_list_of_dicts(self):
        """
        Test that f-string with complex nested structures works
        """
        items = [{'name': 'item1'}, {'name': 'item2'}]
        logger.info(f'Processing items: {items}')

        content = self._read_log_file()

        # Should contain the list representation
        assert 'Processing items:' in content
        assert 'item1' in content
        assert 'item2' in content
        # Should NOT contain error message
        assert '<key' not in content
        assert 'missing>' not in content

    def test_parametrized_format_still_works(self):
        """
        Test that parametrized logging (the elegant way) still works correctly
        """
        logger.info('User {name} logged in from {location}', name='Alice', location='Singapore')

        content = self._read_log_file()

        # Should contain formatted message
        assert 'User Alice logged in from Singapore' in content

    def test_parametrized_with_set_value(self):
        """
        Test that parametrized logging works with set values
        """
        modules = {'combat', 'ui'}
        logger.info('Assets generate, modules={modules}', modules=modules)

        content = self._read_log_file()

        # Should contain the set representation
        assert 'Assets generate, modules=' in content
        assert 'combat' in content
        assert 'ui' in content

    def test_mixed_braces_no_params(self):
        """
        Test that messages with braces but no parameters don't get formatted
        """
        # JSON-like string
        logger.info('Response: {"status": "ok", "count": 5}')

        content = self._read_log_file()

        # Should contain the exact JSON
        assert 'Response: {"status": "ok", "count": 5}' in content
        # Should NOT try to format
        assert '<key' not in content

    def test_unpaired_braces_no_crash(self):
        """
        Test that unpaired braces don't cause crashes
        """
        logger.info('Invalid format: {incomplete')
        logger.info('Another one: just}closing')
        logger.info('Multiple {{ and }}')

        content = self._read_log_file()

        # Should contain the messages (even if malformed)
        assert 'Invalid format: {incomplete' in content
        assert 'Another one: just}closing' in content
        assert 'Multiple' in content

    def test_empty_braces_no_params(self):
        """
        Test that empty braces without parameters don't cause issues
        """
        logger.info('Empty braces: {}')

        content = self._read_log_file()

        # Should contain the message
        assert 'Empty braces: {}' in content
        # Should NOT try to format or error
        assert '<key' not in content

    def test_format_with_missing_key_uses_safe_dict(self):
        """
        Test that when parametrized format has missing keys, SafeDict provides placeholder
        """
        logger.info('User {name} from {location}', name='Bob')

        content = self._read_log_file()

        # Should use SafeDict placeholder for missing 'location'
        assert 'User Bob from <key location missing>' in content or 'User Bob from {location}' in content


class TestLoggerError(BaseLoggerTest):
    """
    Test logger.error() specific functionality
    """

    def test_error_with_exception(self):
        """
        Test logger.error(e) prints exception name and message
        """
        try:
            raise ValueError("Invalid value")
        except ValueError as e:
            logger.error(e)

        content = self._read_log_file()

        # Should contain exception type and message
        assert "ValueError: Invalid value" in content
        # Should be ERROR level
        assert "ERROR" in content

    def test_error_with_exception_group(self):
        """
        Test logger.error(e) with ExceptionGroup prints tree structure
        """
        import sys
        if sys.version_info >= (3, 11):
            ExceptionGroupType = ExceptionGroup
        else:
            try:
                from exceptiongroup import ExceptionGroup as ExceptionGroupType
            except ImportError:
                pytest.skip("exceptiongroup module not installed")

        # Create a complex exception group
        # Top
        #  - ValueError: val_err
        #  - NestedGroup: nested
        #    - TypeError: type_err
        #    - IndexError: index_err

        exc = ExceptionGroupType(
            "Top error",
            [
                ValueError("val_err"),
                ExceptionGroupType(
                    "Nested group",
                    [
                        TypeError("type_err"),
                        IndexError("index_err")
                    ]
                )
            ]
        )

        logger.error(exc)

        content = self._read_log_file()

        # Verify structure by checking lines order or indentation
        lines = content.strip().split('\n')

        # Find the start of the error message
        error_lines = []
        capture = False
        for line in lines:
            if "ExceptionGroup: Top error" in line:
                capture = True
            if capture:
                error_lines.append(line)

        # We expect something like:
        # ... | ERROR | ExceptionGroup: Top error
        # - ExceptionGroup: Top error
        #   - ValueError: val_err
        #   - ExceptionGroup: Nested group
        #     - TypeError: type_err
        #     - IndexError: index_err

        # Join relevant lines to check for substrings with indentation
        full_text = '\n'.join(error_lines)

        assert "- ExceptionGroup: Top error" in full_text
        assert "  - ValueError: val_err" in full_text
        assert "  - ExceptionGroup: Nested group" in full_text
        assert "    - TypeError: type_err" in full_text
        assert "    - IndexError: index_err" in full_text

        # Verify order: val_err comes before Nested group
        pos_val = full_text.find("ValueError: val_err")
        pos_nested = full_text.find("ExceptionGroup: Nested group")
        assert pos_val < pos_nested

        # Verify order within nested group
        pos_type = full_text.find("TypeError: type_err")
        pos_index = full_text.find("IndexError: index_err")
        assert pos_type < pos_index
        assert pos_type < pos_index
        assert pos_nested < pos_type


class TestLoggerException(BaseLoggerTest):
    """
    Test logger.exception() functionality
    """

    def test_exception_logging(self):
        """
        Test exception logging
        """
        try:
            raise ValueError('Test exception')
        except ValueError:
            logger.exception('An error occurred')

        content = self._read_log_file()

        # Verify exception message and traceback
        assert 'An error occurred' in content
        assert 'ValueError' in content
        assert 'Test exception' in content
        assert 'Traceback' in content or 'ERROR' in content

    def test_exception_group_logging(self):
        """
        Test logging of ExceptionGroup (Python 3.11+ or via exceptiongroup backport)
        """
        import sys
        if sys.version_info >= (3, 11):
            # Built-in in Python 3.11+
            ExceptionGroupType = ExceptionGroup
        else:
            try:
                from exceptiongroup import ExceptionGroup as ExceptionGroupType
            except ImportError:
                pytest.skip("exceptiongroup module not installed on Python < 3.11")

        try:
            raise ExceptionGroupType(
                "multiple errors",
                [
                    ValueError("error 1"),
                    TypeError("error 2"),
                    ExceptionGroupType(
                        "nested errors",
                        [
                            ValueError("nested error 1")
                        ]
                    )
                ]
            )
        except ExceptionGroupType as e:
            logger.exception(e)

        content = self._read_log_file()

        # Verify main group message
        assert "ExceptionGroup: multiple errors" in content

        # Verify nested exceptions
        assert "ValueError: error 1" in content
        assert "TypeError: error 2" in content

        # Verify nested group
        assert "nested errors" in content
        assert "nested error 1" in content
