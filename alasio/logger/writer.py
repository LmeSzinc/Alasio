import sys
from datetime import date
from typing import TYPE_CHECKING

from alasio.ext import env
from alasio.ext.cache import cached_property_threadsafe
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_open
from alasio.ext.path.validate import validate_filename
from alasio.ext.singleton import Singleton

if TYPE_CHECKING:
    from alasio.backend.worker.bridge import BackendBridge


# It's a singleton because on each logger.bind() structlog.PrintLoggerFactory will create new `file` object
# But we don't want to open multiple files
class LogWriter(metaclass=Singleton):
    def __init__(self):
        self.create_date: "date | None" = None
        self.is_electron = bool(env.ELECTRON_PID)

    @cached_property_threadsafe
    def backend(self) -> "BackendBridge | PseudoBackendBridge":
        from alasio.backend.worker.bridge import BackendBridge
        backend = BackendBridge()
        if backend.inited:
            return backend
        else:
            return PseudoBackendBridge()

    @cached_property_threadsafe
    def name(self):
        """
        Name of the file the next line is written to.

        A write goes to the file of the process until a block gives the lines
        a file of their own, see set_name(). It is a cached property like the
        rest of the writer, so the name and the path and the fd of the file
        are switched under the same lock and dropped together.

        The name of the process comes from sys.argv[0]: `python -m a.b` sets
        it to the path of the module file, so its stem is the module name
        ('main' for `python -m pack_server.main`), while `python -m a` runs
        a/__main__.py, whose stem says nothing, and the folder of the module
        is the name then.

        Returns:
            str: Name of the file, without the date and the suffix
        """
        if self.backend.inited:
            return self.backend.config_name
        path = PathStr.new(sys.argv[0])
        if path.rootstem == '__main__':
            # python -m package -> .../package/__main__.py
            return PathStr.new(path.uppath(1)).name
        return path.rootstem

    @cached_property_threadsafe
    def file(self):
        # the date is read once and kept: check_rotate() compares it with the
        # date of the day, and a second read here could name a file of another
        # day than the one the writer rotated for
        self.create_date = date.today()
        return env.PROJECT_ROOT.abspath() / 'log' / f'{self.create_date}_{self.name}.txt'

    def set_name(self, name):
        """
        Write the following lines into log/{date}_{name}.txt.

        A process that runs several units of work one after another (the pack
        server runs one config at a time) gives each its own file, so the
        lines of one unit are read without the lines of the others. The file
        of the previous unit is closed here: the next line opens the new one.
        The name is a file name, not a path: a name that is not usable is
        refused instead of writing outside the log folder.

        Args:
            name (str): Name of the file, without the date and the suffix.
                None for the default name of the process

        Returns:
            str: The name that was set before this call, None when the file was
                the one of the process, so a caller can restore it

        Raises:
            ValueError: If the name is not a single usable file name
        """
        if name is not None:
            validate_filename(name)
        # read the cached name without calculating it: a writer that never
        # named a block has none in the cache, and the file of the process is
        # in force then
        previous = cached_property_threadsafe.get(self, 'name', None)
        if name == previous:
            # the file in force already has this name: nothing to close and
            # nothing to recalculate
            return previous
        if name is None:
            # back to the file of the process, computed from sys.argv[0]
            cached_property_threadsafe.pop(self, 'name', None)
        else:
            cached_property_threadsafe.set(self, 'name', name)
        # the file of the previous name is closed here: the next line opens the
        # file of the new name
        self.close_fd()
        return previous

    @cached_property_threadsafe
    def fd(self):
        return atomic_open(self.file, mode='a', encoding='utf-8')

    @cached_property_threadsafe
    def stdout(self):
        return sys.stdout

    def check_rotate(self):
        # rotate log to file with new date
        if self.create_date and self.create_date != date.today():
            self.close()

    @staticmethod
    def _close_fd_value(fd):
        """
        Close a log file fd, if any.

        The fd may be a PseudoStream while file output is muted; it has no
        close(), the exception is ignored.
        """
        if fd is not None:
            try:
                fd.close()
            except Exception:
                pass

    def _close_fd(self):
        """
        Close and drop the cached log file fd, if any.
        """
        fd = cached_property_threadsafe.pop(self, 'fd', None)
        self._close_fd_value(fd)

    def close(self):
        # the name belongs to the block that set it: a closed writer is the
        # default one of the process again
        cached_property_threadsafe.pop(self, 'name')
        cached_property_threadsafe.pop(self, 'backend')
        cached_property_threadsafe.pop(self, 'file')
        cached_property_threadsafe.pop(self, 'stdout')
        self._close_fd()

    def close_fd(self):
        """
        Close the cached log file fd, the next write reopens it.

        The log file fd is cached on the singleton. Environments that
        swap the filesystem (e.g. the in-memory fake filesystem used in
        tests) must drop the cached fd, or later real logs would keep
        writing into the swapped filesystem and get lost. Only the file
        path and fd caches are dropped, stdout and backend stay intact.
        """
        cached_property_threadsafe.pop(self, 'file', None)
        self._close_fd()

    def mute(self, stdout=False, fd=False, backend=False, all=False):
        """
        Mute logging outputs.

        Args:
            stdout (bool): Mute stdout. Defaults to False.
            fd (bool): Mute file writing. Defaults to False.
            backend (bool): Mute backend. Defaults to False.
            all (bool): Mute all outputs. Defaults to False.
        """
        if all:
            stdout = fd = backend = True
        if stdout:
            cached_property_threadsafe.set(self, 'stdout', PseudoStream())
        if fd:
            # Replace under the cache lock, then close the previous handle:
            # replacing without closing it leaks the handle, and Windows keeps
            # the log file locked (it cannot be removed or rotated).
            old_fd = cached_property_threadsafe.replace(self, 'fd', PseudoStream())
            self._close_fd_value(old_fd)
            # Drop the cached path too, so a later unmute follows a changed
            # PROJECT_ROOT instead of reopening the old path.
            cached_property_threadsafe.pop(self, 'file', None)
        if backend:
            cached_property_threadsafe.set(self, 'backend', PseudoBackendBridge())

    def mute_clear(self):
        """
        Clear all mutes.
        """
        cached_property_threadsafe.pop(self, 'stdout')
        # A real fd may be cached when mute_clear() is called without a
        # preceding mute(fd=True): close it instead of dropping the handle.
        self.close_fd()
        cached_property_threadsafe.pop(self, 'backend')

    def __del__(self):
        self.close()


class PseudoBackendBridge:
    inited = False
    config_name = "mock"

    def send_log(self, event):
        return CaptureJob()


class PseudoStream:
    def write(self, text):
        pass

    def flush(self):
        pass


class CaptureStream:
    def __init__(self, parent=None):
        """
        Args:
            parent (CaptureStream): Parent stream to also write to. Defaults to None.
        """
        self.logs: "list[str]" = []
        self.parent = parent

    def write(self, text):
        self.logs.append(text)
        if self.parent:
            self.parent.write(text)

    def flush(self):
        if self.parent:
            self.parent.flush()

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

    def any_regex(self, pattern):
        """
        Check if any log matches the given regex pattern

        Args:
            pattern (str): Regex pattern to search for

        Returns:
            bool: True if pattern matches any log
        """
        import re
        for log in self.logs:
            if re.search(pattern, log):
                return True
        return False


class CaptureJob:
    def acquire(self):
        pass


class CaptureBackend:
    def __init__(self, parent=None):
        """
        Args:
            parent (CaptureBackend): Parent backend to also send to. Defaults to None.
        """
        self.logs: "list[dict]" = []
        self.inited = True
        self.config_name = "mock"
        self.parent = parent

    def send_log(self, event):
        self.logs.append(event)
        if self.parent:
            self.parent.send_log(event)
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

    def any_regex(self, pattern):
        """
        Check if any log entry (dict values) matches the given regex pattern

        Args:
            pattern (str): Regex pattern to search for

        Returns:
            bool: True if pattern matches any log entry value
        """
        import re
        for log in self.logs:
            for value in log.values():
                if isinstance(value, str) and re.search(pattern, value):
                    return True
        return False

    def concat(self):
        """
        Concatenate captured logs into a multi-line string, for test assertions

        The text is formatted like the printed log, but the timestamp is dropped
        because it's different on every run.
            INFO | message
            raw message

        Returns:
            str: Log text, one log per line, e.g.
                INFO | message
                WARNING | message
                raw message
        """
        return ''.join(self._event_text(event) for event in self.logs)

    @staticmethod
    def _event_text(event):
        """
        Build log text from a captured log event, like the printed log but without timestamp

        Args:
            event (dict): Log event, keys are
                'l' (str): Log level
                'm' (str): Log message
                'e' (str): Exception traceback, printed after the log message. Defaults to None.
                'r' (int): Raw tag, the log message is pre-formatted. Defaults to None.

        Returns:
            str: Log text, ends with a newline
        """
        if event.get('r'):
            # raw log, the message is pre-formatted, no level prefix
            text = f"{event['m']}\n"
        else:
            text = f"{event['l']} | {event['m']}\n"
        exception = event.get('e')
        if exception:
            text = f'{text}{exception}\n'
        return text


class CaptureWriter:
    def __init__(self, parent=None):
        """
        Args:
            parent (CaptureWriter | LogWriter | None): Parent writer to chain logs to. Defaults to None.
        """
        self.is_electron = False
        if isinstance(parent, CaptureWriter):
            self.stdout = CaptureStream(parent=parent.stdout)
            self.fd = CaptureStream(parent=parent.fd)
            self.backend = CaptureBackend(parent=parent.backend)
        else:
            self.stdout = CaptureStream()
            self.fd = CaptureStream()
            self.backend = CaptureBackend()

    def concat(self):
        """
        Captured logs as a multi-line string, without timestamp, for test assertions

        The text looks like the printed log, but the timestamp is dropped because
        it's different on every run, for example
            INFO | message
            WARNING | message
            raw message

        Returns:
            str: Log text, see `CaptureBackend.concat()`
        """
        return self.backend.concat()

    def clear(self):
        self.stdout.logs.clear()
        self.fd.logs.clear()
        self.backend.logs.clear()

    def check_rotate(self):
        pass

    def set_name(self, name):
        """
        A captured run writes nowhere, so there is no file to name.

        Args:
            name (str): Ignored, the name of the block that is running
        """

    def close(self):
        pass
