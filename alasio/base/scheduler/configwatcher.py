from datetime import datetime

from alasio.config.table.base import AlasioConfigDB
from alasio.ext.file.watcher import PathWatcher
from alasio.ext.path import PathStr
from alasio.logger import logger


class ConfigWatcher:
    """
    Watch the file of a config while the scheduler waits for its next task.

    The scheduler releases the config before waiting, so the only writer of the
    file during the wait is an external one (the GUI backend) and any change of
    it means the schedule of the config has to be rebuilt: the wait ends.

    The file is watched by a PathWatcher subscription, which compares the
    modification time, the size and the inode of the file. A write inside the
    same second, a size only change and an atomic replacement (the new file
    written next to the old one and renamed over it) are reported as well.
    """

    def __init__(self, config_name):
        """
        Args:
            config_name (str): Name of the config, also the name of its file
        """
        self.config_name = config_name
        file = AlasioConfigDB.config_file(config_name)
        # An in memory database is not a file, nothing can write it from outside
        self.watcher = None
        if file != ':memory:':
            self.watcher = PathWatcher()
            # The database opens the path as it is given, so a path relative to
            # the project root, or to the working folder before the root is set,
            # is resolved to the file that is really opened
            self.watcher.subscribe(PathStr.new(file).abspath())

    def init(self):
        """
        Take the current state of the file as the initial state

        Returns:
            ConfigWatcher: self
        """
        if self.watcher is not None:
            # The baseline is dropped: the file as it is now is not a change
            self.watcher.update()
        return self

    def is_modified(self):
        """
        Check if config is modified, appeared or disappeared

        Returns:
            bool: True if modified
        """
        if self.watcher is None:
            return False
        events = self.watcher.update()
        if not events:
            return False

        for event in events:
            if event.event == 'A':
                logger.info(f'Config "{self.config_name}" appeared')
            elif event.event == 'D':
                logger.info(f'Config "{self.config_name}" disappeared')
            else:
                mtime = datetime.fromtimestamp(event.mtime_ns / 1000000000)
                logger.info(f'Config "{self.config_name}" modified at {mtime.replace(microsecond=0)}')
        return True
