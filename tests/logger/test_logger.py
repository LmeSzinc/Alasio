import re
from datetime import date

import pytest

from alasio.ext import env
from alasio.ext.cache import threaded_cached_property
from alasio.ext.path import PathStr
from alasio.logger.logger import LogWriter, logger


class TestLogger:
    """
    Test basic logging functionality
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

        # Reset LogWriter singleton to force reinit
        if hasattr(LogWriter, '_instances'):
            LogWriter._instances = {}
        
        # Clear cached property
        writer = LogWriter()
        threaded_cached_property.pop(writer, 'fd')

        yield

        # Cleanup: close file descriptor if exists
        writer = LogWriter()
        writer.close()

        # Restore original env values
        env.PROJECT_ROOT = original_root
        env.ELECTRON_SECRET = original_electron

        # Reset singleton
        if hasattr(LogWriter, '_instances'):
            LogWriter._instances = {}

        # Clean up log directory
        log_dir = self.test_dir / 'log'
        log_dir.folder_rmtree()

    def _get_log_file_path(self):
        """
        Get the expected log file path

        Returns:
            PathStr: Path to log file
        """
        log_dir = self.test_dir / 'log'
        today = date.today()
        # Get the name from sys.argv[0]
        import sys
        from alasio.ext.path import PathStr
        name = PathStr.new(sys.argv[0]).rootstem
        return log_dir / f'{today}_{name}.txt'

    def _read_log_file(self):
        """
        Read content from log file

        Returns:
            str: Log file content
        """
        log_file = self._get_log_file_path()
        if not log_file.exists():
            return ''
        
        with open(log_file, 'r', encoding='utf-8') as f:
            return f.read()

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


class TestLoggerBind:
    """
    Test that logger.bind() does not create new file writes
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

        # Reset LogWriter singleton
        if hasattr(LogWriter, '_instances'):
            LogWriter._instances = {}
        
        # Clear cached property
        writer = LogWriter()
        threaded_cached_property.pop(writer, 'fd')

        yield

        # Cleanup
        writer = LogWriter()
        writer.close()

        env.PROJECT_ROOT = original_root
        env.ELECTRON_SECRET = original_electron

        if hasattr(LogWriter, '_instances'):
            LogWriter._instances = {}

        log_dir = self.test_dir / 'log'
        log_dir.folder_rmtree()

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
