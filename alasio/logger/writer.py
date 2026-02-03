import sys
from datetime import date
from typing import List, Optional, TYPE_CHECKING

from alasio.ext import env
from alasio.ext.cache import threaded_cached_property
from alasio.ext.path import PathStr
from alasio.ext.singleton import Singleton

if TYPE_CHECKING:
    from alasio.backend.worker.bridge import BackendBridge


class PseudoBackendBridge:
    inited = False


# It's a singleton because on each logger.bind() structlog.PrintLoggerFactory will create new `file` object
# But we don't want to open multiple files
class LogWriter(metaclass=Singleton):
    def __init__(self):
        self.create_date: Optional[date] = None
        self.is_electron = bool(env.ELECTRON_SECRET)

    @threaded_cached_property
    def backend(self) -> "BackendBridge":
        from alasio.backend.worker.bridge import BackendBridge
        backend = BackendBridge()
        if backend.inited:
            return backend
        else:
            return PseudoBackendBridge()

    @threaded_cached_property
    def file(self):
        root = env.PROJECT_ROOT.abspath()
        folder = root / 'log'
        self.create_date = date.today()

        if self.backend.inited:
            name = self.backend.config_name
            # write logs to xxx/log/2020-01-01_{config_name}.txt
            return folder / f'{self.create_date}_{name}.txt'
        else:
            # xxx/path/module.py -> module
            name = PathStr.new(sys.argv[0]).rootstem
            # write logs to xxx/log/2020-01-01_{module_name}.txt
            return folder / f'{self.create_date}_{name}.txt'

    @threaded_cached_property
    def fd(self):
        file = self.file
        try:
            return open(file, 'a', encoding='utf-8')
        except FileNotFoundError:
            file.uppath().makedirs(exist_ok=True)
        return open(file, 'a', encoding='utf-8')

    @threaded_cached_property
    def stdout(self):
        return sys.stdout

    def check_rotate(self):
        # rotate log to file with new date
        if self.create_date and self.create_date != date.today():
            self.close()

    def close(self):
        threaded_cached_property.pop(self, 'backend')
        threaded_cached_property.pop(self, 'file')
        threaded_cached_property.pop(self, 'stdout')
        fd = threaded_cached_property.pop(self, 'fd')
        if fd is not None:
            try:
                fd.close()
            except Exception:
                pass

    def __del__(self):
        self.close()


class CaptureStream:
    def __init__(self):
        self.logs: List[str] = []

    def write(self, text):
        self.logs.append(text)

    def flush(self):
        pass

    def any_contains(self, text):
        """
        Check if any log contains the given text

        Args:
            text (str): Text to search for

        Returns:
            bool: True if text is found in any log
        """
        for log in self.logs:
            if text in log:
                return True
        return False


class CaptureJob:
    def acquire(self):
        pass


class CaptureBackend:
    def __init__(self):
        self.logs: List[dict] = []
        self.inited = True
        self.config_name = "mock"

    def send_log(self, event):
        self.logs.append(event)
        return CaptureJob()

    def any_contains(self, text):
        """
        Check if any log entry (dict values) contains the given text

        Args:
            text (str): Text to search for

        Returns:
            bool: True if text is found in any log entry value
        """
        for log in self.logs:
            for value in log.values():
                if isinstance(value, str) and text in value:
                    return True
        return False


class CaptureWriter:
    def __init__(self):
        self.is_electron = False
        self.stdout = CaptureStream()
        self.fd = CaptureStream()
        self.backend = CaptureBackend()

    def clear(self):
        self.stdout.logs.clear()
        self.fd.logs.clear()
        self.backend.logs.clear()

    def check_rotate(self):
        pass

    def close(self):
        pass
