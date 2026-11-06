"""
Tests for ConfigWatcher, the watcher of the config file of an idle scheduler.

The scheduler releases the config before waiting for its next task, so a write
of the file during the wait can only come from outside, and any change of it
ends the wait. The change detection is a PathWatcher subscription, this file
tests the mapping of its events to the config states.
"""
import os
import time
from datetime import datetime

import pytest

import alasio.ext.file.watcher as watcher_module
from alasio.base.scheduler.configwatcher import ConfigWatcher
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401

CONFIG_NAME = 'test'


@pytest.fixture
def config_file(fs, monkeypatch):
    """Point the project root to the fake filesystem, return the config file"""
    root = PathStr.new(fs.root_dir.path)
    monkeypatch.setattr(env, 'PROJECT_ROOT', root)
    return root.joinpath(f'config/{CONFIG_NAME}.db')


@pytest.fixture
def later_mtime():
    """A modification time some seconds after now, with a stable fraction"""
    return (int(time.time()) + 10) * 1000000000 + 500000000


class TestConfigWatcher:
    def test_not_modified(self, fs, config_file):
        """A file that is not touched is not a modification"""
        fs.create_file(config_file, contents=b'')
        watcher = ConfigWatcher(CONFIG_NAME).init()

        with logger.mock_capture_writer() as capture:
            assert watcher.is_modified() is False
        assert capture.concat() == ''

    def test_appeared(self, fs, config_file):
        """A config created during the wait ends the wait"""
        watcher = ConfigWatcher(CONFIG_NAME).init()
        # The file does not exist yet, which is the initial state
        assert watcher.is_modified() is False

        fs.create_file(config_file, contents=b'')
        with logger.mock_capture_writer() as capture:
            assert watcher.is_modified() is True
        assert capture.concat() == f'INFO | Config "{CONFIG_NAME}" appeared\n'

    def test_modified(self, fs, config_file, later_mtime):
        """A config written during the wait ends the wait, once"""
        fs.create_file(config_file, contents=b'')
        watcher = ConfigWatcher(CONFIG_NAME).init()

        fs.utime(config_file, ns=(0, later_mtime))
        modify = datetime.fromtimestamp(later_mtime / 1000000000).replace(microsecond=0)

        with logger.mock_capture_writer() as capture:
            assert watcher.is_modified() is True
        assert capture.concat() == f'INFO | Config "{CONFIG_NAME}" modified at {modify}\n'
        # The modification is reported once, the next check starts from it
        assert watcher.is_modified() is False

    def test_modified_with_the_same_mtime(self, fs, config_file):
        """A size only change is a change, the mtime may stay the same"""
        fs.create_file(config_file, contents=b'')
        st = os.stat(config_file)
        watcher = ConfigWatcher(CONFIG_NAME).init()

        with open(config_file, 'wb') as f:
            f.write(b'written')
        fs.utime(config_file, ns=(int(st.st_atime * 1000000000), int(st.st_mtime * 1000000000)))

        assert watcher.is_modified() is True

    def test_disappeared(self, fs, config_file):
        """A config removed during the wait ends the wait"""
        fs.create_file(config_file, contents=b'')
        watcher = ConfigWatcher(CONFIG_NAME).init()

        fs.remove(config_file)
        with logger.mock_capture_writer() as capture:
            assert watcher.is_modified() is True
        assert capture.concat() == f'INFO | Config "{CONFIG_NAME}" disappeared\n'

    def test_replaced(self, fs, config_file):
        """A config deleted and written again is a change"""
        fs.create_file(config_file, contents=b'')
        watcher = ConfigWatcher(CONFIG_NAME).init()

        fs.remove(config_file)
        fs.create_file(config_file, contents=b'')

        assert watcher.is_modified() is True

    def test_unreadable_config(self, fs, config_file, monkeypatch):
        """A path that cannot be read is not a change, the watcher logs it"""
        fs.create_file(config_file, contents=b'')
        watcher = ConfigWatcher(CONFIG_NAME).init()
        # A path that cannot be stat'ed keeps its previous state
        monkeypatch.setattr(watcher_module, '_lstat', lambda path: None)

        assert watcher.is_modified() is False


class TestConfigWithoutAFile:
    """A config that is not a file on disk has nothing to watch"""

    def test_memory_config(self):
        """An in memory config has no file, nothing can write it from outside"""
        watcher = ConfigWatcher(':memory:')

        assert watcher.watcher is None
        assert watcher.init().is_modified() is False

    def test_config_of_a_relative_project_root(self, fs, monkeypatch):
        """
        Before the project root is set the path of the config is relative, and
        the database opens it in the working folder: that is the watched file
        """
        monkeypatch.setattr(env, 'PROJECT_ROOT', PathStr.new(''))
        watcher = ConfigWatcher(CONFIG_NAME).init()
        assert watcher.watcher is not None

        # The file the database opens, the one of the working folder
        file = PathStr.new(os.getcwd()).joinpath(f'config/{CONFIG_NAME}.db')
        fs.create_file(file, contents=b'')

        assert watcher.is_modified() is True
