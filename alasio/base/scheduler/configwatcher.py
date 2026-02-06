from datetime import datetime

from alasio.config.table.base import AlasioConfigDB
from alasio.logger import logger


class ConfigWatcher:
    def __init__(self, config_name):
        """
        Args:
            config_name (str):
        """
        self.config_name = config_name
        self.mtime: "datetime | None" = None

    def _get_mtime(self):
        """
        Get last modify time of file, or None if file not exists

        Returns:
            datetime | None:
        """
        file = AlasioConfigDB.config_file(self.config_name)
        try:
            stat = file.stat()
        except FileNotFoundError:
            return None
        except PermissionError as e:
            logger.error(e)
            return None
        except OSError as e:
            logger.error(e)
            return None
        timestamp = stat.st_mtime
        mtime = datetime.fromtimestamp(timestamp)
        return mtime

    def init(self):
        """
        Remember current mtime as init state

        Returns:
            ConfigWatcher: self
        """
        self.mtime = self._get_mtime()
        return self

    def is_modified(self):
        """
        Check if config is modified, appeared or disappeared

        Returns:
            bool: True if modified
        """
        mtime = self._get_mtime()
        prev = self.mtime
        if prev is None:
            if mtime is None:
                # not exist
                return False
            else:
                logger.info(f'Config "{self.config_name}" appeared')
                self.mtime = mtime
                return True
        else:
            if mtime is None:
                logger.info(f'Config "{self.config_name}" disappeared')
                self.mtime = mtime
                return True
            else:
                if mtime > prev:
                    modify = mtime.replace(microsecond=0)
                    logger.info(f'Config "{self.config_name}" modified at {modify}')
                    self.mtime = mtime
                    return True
                else:
                    # not modified
                    return False
