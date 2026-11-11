"""
One log file per unit of work: the file a block writes to, and the file the
lines outside the blocks go back to.

A process that runs several units one after another (the pack server runs one
config after another) writes a file per unit, so the lines of one unit are read
without the thousands of lines of the others. The tests pin the name of the
file, the restore after the block, and the refusal of a name that is not a
single file name.
"""

import os
import sys
from datetime import date

import pytest

from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.logger import logger
from alasio.logger.writer import LogWriter
from tests.logger.test_logger import BaseLoggerTest

TODAY = date.today()


def log_path(name):
    """
    The path of the log file of one name, as the writer names it.

    Returns:
        PathStr: xxx/log/{date}_{name}.txt
    """
    return PathStr.new(env.PROJECT_ROOT).joinpath('log').joinpath(f'{TODAY}_{name}.txt')


def program_path(*parts):
    """
    The path a program file has in sys.argv[0] on this platform.

    The value is always a native path: a windows one on windows and a posix one
    everywhere else, which is what the interpreter puts in argv[0].

    Args:
        parts (str): Components below the root

    Returns:
        str: Absolute path of the program
    """
    return os.path.join(os.sep, *parts)


class TestLogFile(BaseLoggerTest):
    """The file a block writes to."""

    def test_the_name_names_the_file(self):
        """The lines of the block land in log/{date}_{name}.txt, and only there."""
        default = LogWriter().file
        default_before = default.atomic_read_text() if default.exists() else ''

        with logger.log_file('20260101_120000_my_config'):
            logger.info('Inside the block')

        named = log_path('20260101_120000_my_config')
        assert named.isfile()
        assert 'Inside the block' in named.atomic_read_text()
        # the default file of the process does not hold the line of the block
        default_after = default.atomic_read_text() if default.exists() else ''
        assert default_after == default_before

    def test_the_lines_after_the_block_go_back(self):
        """A line outside the block is written to the file of the process again."""
        with logger.log_file('20260101_120000_my_config'):
            logger.info('Inside the block')
        logger.info('After the block')

        assert 'After the block' in LogWriter().file.atomic_read_text()
        assert 'After the block' not in log_path('20260101_120000_my_config').atomic_read_text()

    def test_two_blocks_write_two_files(self):
        """Every block has a file of its own; the first one stops at its end."""
        with logger.log_file('first'):
            logger.info('First line')
        with logger.log_file('second'):
            logger.info('Second line')

        assert 'First line' in log_path('first').atomic_read_text()
        assert 'First line' not in log_path('second').atomic_read_text()
        assert 'Second line' in log_path('second').atomic_read_text()

    def test_a_nested_block_restores_the_outer_name(self):
        """The inner block ends inside the outer one, which keeps its file."""
        with logger.log_file('outer'):
            logger.info('Outer line')
            with logger.log_file('inner'):
                logger.info('Inner line')
            logger.info('Outer again')

        assert log_path('inner').atomic_read_text().count('Inner line') == 1
        assert 'Inner line' not in log_path('outer').atomic_read_text()
        outer = log_path('outer').atomic_read_text()
        assert 'Outer line' in outer and 'Outer again' in outer

    def test_the_file_of_a_block_is_closed_at_its_end(self):
        """The handle of the block is closed, so the file is readable on Windows."""
        with logger.log_file('closed_at_end'):
            logger.info('Closing line')
            fd = LogWriter().fd
            assert not fd.closed
        assert fd.closed

    def test_the_same_name_keeps_the_open_file(self):
        """Setting the name that is in force changes nothing, not even the fd.

        A no-op switch that dropped the cached name and set it again would
        close the handle of the file and reopen it for nothing.
        """
        writer = LogWriter()
        with logger.log_file('kept'):
            logger.info('First line')
            fd = writer.fd
            logger.set_log_name('kept')
            assert writer.fd is fd
            assert not fd.closed
            logger.info('Second line')
        text = log_path('kept').atomic_read_text()
        assert 'First line' in text and 'Second line' in text

    def test_resetting_reports_the_name_it_dropped(self):
        """set_name(None) drops the override and says which one it dropped."""
        writer = LogWriter()
        with logger.log_file('dropped'):
            assert writer.name == 'dropped'
            assert writer.set_name(None) == 'dropped'
        # the file of the process is in force again, its name computed from
        # sys.argv[0]
        assert writer.name == LogWriter().name
        assert writer.file.name.endswith(f'_{writer.name}.txt')

    @pytest.mark.parametrize('name', [
        'a/b',
        'a\\b',
        'a:b',
        '..',
        '',
    ])
    def test_a_name_that_is_not_a_file_name_is_refused(self, name):
        """A name is a file name, not a path: it cannot leave the log folder."""
        with pytest.raises(ValueError):
            with logger.log_file(name):
                pass

    def test_the_date_prefix_is_the_day(self):
        """The file of a name carries the date of the day, the default rule."""
        with logger.log_file('dated'):
            logger.info('Dated line')
        path = log_path('dated')
        assert path.name == f'{TODAY}_dated.txt'
        assert path.uppath(1).name == 'log'


class TestLogFileWhenCaptured(BaseLoggerTest):
    """A captured writer has no file to switch."""

    def test_the_block_only_nests(self):
        """A capture inside a named block captures the lines of the block."""
        with logger.mock_capture_writer() as capture:
            with logger.log_file('20260101_120000_my_config'):
                logger.info('Captured line')
        assert capture.concat() == 'INFO | Captured line\n'

    def test_the_name_does_not_leak_out_of_the_capture(self):
        """The real writer of the process keeps the file it had."""
        writer = LogWriter()
        writer.set_name(None)
        before = writer.name
        with logger.mock_capture_writer():
            with logger.log_file('captured'):
                logger.info('Captured line')
        assert writer.name == before
        assert writer.file == log_path(before)


class TestModuleName(BaseLoggerTest):
    """The name of the process, the file a line outside a block goes to."""

    @pytest.mark.parametrize('argv, expected', [
        # python -m a.b: argv[0] is the path of the module file
        ([program_path('pack', 'alasio', 'deploy_dev', 'pack_server', 'main.py')], 'main'),
        # python -m a: argv[0] is the __main__.py of the package, whose stem
        # says nothing, the folder names the program
        ([program_path('pack', 'alasio', 'deploy_dev', 'pack_server', '__main__.py')], 'pack_server'),
        # a script run by path
        ([program_path('pack', 'run_pack_server.py')], 'run_pack_server'),
        # python -c and a script from stdin have no module to name
        (['-c'], '-c'),
    ])
    def test_the_program_name_of_argv(self, monkeypatch, argv, expected):
        """The name follows what python -m and a script put in sys.argv[0]."""
        monkeypatch.setattr(sys, 'argv', argv)
        writer = LogWriter()
        # drop the name the fixture of the test computed from the argv of pytest
        writer.set_name(None)
        assert writer.name == expected

    def test_the_default_file_is_named_after_the_module(self, monkeypatch):
        """Without a block, a line goes to the file of the program."""
        monkeypatch.setattr(sys, 'argv', [program_path('pack', 'pack_server', 'main.py')])
        writer = LogWriter()
        writer.set_name(None)
        assert writer.name == 'main'
        assert writer.file.name == f'{TODAY}_main.txt'

    def test_a_block_goes_back_to_the_file_of_the_program(self, monkeypatch):
        """After the block the lines land in the file of the program again."""
        monkeypatch.setattr(sys, 'argv', [program_path('pack', 'pack_server', 'main.py')])
        writer = LogWriter()
        writer.set_name(None)
        with logger.log_file('a_config'):
            logger.info('Inside')
            assert writer.file.name == f'{TODAY}_a_config.txt'
        logger.info('Outside')
        assert writer.file.name == f'{TODAY}_main.txt'
        assert 'Outside' in writer.file.atomic_read_text()
