import unittest.mock as mock
from datetime import datetime

from alasio.base.scheduler.configwatcher import ConfigWatcher
from alasio.config.table.base import AlasioConfigDB
from alasio.logger import logger


def test_config_watcher():
    config_name = "test_config"
    watcher = ConfigWatcher(config_name)

    # Mock AlasioConfigDB.config_file
    mock_file = mock.MagicMock()

    with mock.patch.object(AlasioConfigDB, 'config_file', return_value=mock_file):
        with logger.mock_capture_writer() as capture:
            # 1. Initial state (file does not exist)
            mock_file.stat.side_effect = FileNotFoundError()
            watcher.init()
            assert watcher.mtime is None

            # is_modified when it still does not exist
            assert watcher.is_modified() is False
            capture.clear()

            # 2. Config appeared
            now = datetime.now()
            mock_stat = mock.MagicMock()
            mock_stat.st_mtime = now.timestamp()
            mock_file.stat.side_effect = None
            mock_file.stat.return_value = mock_stat

            assert watcher.is_modified() is True
            assert watcher.mtime == datetime.fromtimestamp(now.timestamp())
            assert capture.fd.any_contains(f'Config "{config_name}" appeared')
            capture.clear()

            # 3. No modification
            assert watcher.is_modified() is False
            capture.clear()

            # 4. Config modified
            # Add some seconds to ensure mtime > prev
            later_ts = now.timestamp() + 10
            later = datetime.fromtimestamp(later_ts)
            mock_stat.st_mtime = later_ts

            assert watcher.is_modified() is True
            assert watcher.mtime == later
            # logger.info(f'Config "{self.config_name}" modified at {modify}')
            # modify = mtime.replace(microsecond=0)
            modify_str = str(later.replace(microsecond=0))
            assert capture.fd.any_contains(f'Config "{config_name}" modified at {modify_str}')
            capture.clear()

            # 5. Config disappeared
            mock_file.stat.side_effect = FileNotFoundError()
            assert watcher.is_modified() is True
            assert watcher.mtime is None
            assert capture.fd.any_contains(f'Config "{config_name}" disappeared')
            capture.clear()


def test_config_watcher_errors():
    config_name = "test_config_error"
    watcher = ConfigWatcher(config_name)
    mock_file = mock.MagicMock()

    with mock.patch.object(AlasioConfigDB, 'config_file', return_value=mock_file):
        with logger.mock_capture_writer() as capture:
            # PermissionError
            mock_file.stat.side_effect = PermissionError("Permission denied")
            watcher.init()
            assert watcher.mtime is None
            # logger.error(e)
            assert capture.backend.any_contains("Permission denied")
            capture.clear()

            # OSError
            mock_file.stat.side_effect = OSError("OS error")
            assert watcher.is_modified() is False
            assert capture.backend.any_contains("OS error")
            capture.clear()
