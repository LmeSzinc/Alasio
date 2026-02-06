import threading
import time
from datetime import datetime
from io import StringIO

from exceptiongroup import BaseExceptionGroup, ExceptionGroup

from alasio.ext.backport import patch_rich_traceback_extract
from alasio.logger.utils import (
    empty_function, event_format, figure_out_exc_info, join_event_dict, replace_unicode_table, stringify_event
)
from alasio.logger.writer import CaptureWriter, LogWriter

patch_rich_traceback_extract()


def rich_formatter(exc_info):
    # wrap structlog.dev.RichTracebackFormatter().__call__() as an input to ExceptionRenderer
    # TODO: maybe having colors, dynamic width, unicode table chars in console, while keeping clean in log file
    from rich.console import Console
    from rich.traceback import Traceback

    sio = StringIO()
    console = Console(
        file=sio, color_system=None, width=120
    )
    tb = Traceback.from_exception(
        # see structlog.dev.RichTracebackFormatter()
        *exc_info, show_locals=True, word_wrap=True, max_frames=100,
    )
    if hasattr(tb, "code_width"):
        # `code_width` requires `rich>=13.8.0`
        tb.code_width = 120
    console.print(tb)
    output = sio.getvalue()

    output = replace_unicode_table(output)
    return output


class CaptureWriterContext:
    def __init__(self, logger):
        """
        Args:
            logger (AlasioLogger): Logger instance
        """
        self.logger = logger
        self.writer = CaptureWriter()
        self.old_writer = None

    def __enter__(self):
        self.old_writer = self.logger._writer
        self.logger._writer = self.writer
        return self.writer

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.logger._writer = self.old_writer


LOG_LEVELS = {
    'DEBUG': 10,
    'INFO': 20,
    'WARNING': 30,
    'ERROR': 40,
    'CRITICAL': 50,
}

_LEVEL_METHODS = {
    'debug': 10,
    'info': 20,
    'raw': 20,
    'hr': 20,
    'hr0': 20,
    'hr1': 20,
    'hr2': 20,
    'hr3': 20,
    'attr': 20,
    'attr_align': 20,
    'warning': 30,
    'error': 40,
    'exception': 40,
    'critical': 50,
}


class AlasioLogger:
    # global logging lock
    _lock = threading.Lock()

    def __init__(self):
        self._context = {}
        self._writer = LogWriter()
        self._level = 20
        self.set_level(self._level)

    def mock_capture_writer(self):
        """
        Mock writer for testing purpose

        Examples:
            with logger.mock_capture_writer() as capture:
                logger.info("Hello Info")
                assert any("Hello Info" in log for log in capture.fd.logs)
                assert any("Hello Info" in log for log in capture.stdout.logs)
                assert any(log['l'] == 'INFO' and log['m'] == 'Hello Info' for log in capture.backend.logs)
                capture.clear()
        """
        return CaptureWriterContext(self)

    def _process_event(self, level, event, event_dict):
        """
        Internal method that emulates structlog processors chain

        Args:
            level (str): Log level name
            event (str): Log message
            event_dict (dict): Log context

        Returns:
            tuple[str, str, dict]: Processed level, event, and event_dict
        """
        # from structlog.__log_levels.add_log_level()
        # warn is just a deprecated alias in the stdlib.
        if level == "WARN":
            level = "WARNING"
        # Calling exception("") is the same as error("", exc_info=True)
        if level == "EXCEPTION":
            level = "ERROR"

        # from structlog _process_event
        if self._context:
            context = self._context.copy()
            context.update(**event_dict)
            event_dict = context

        # ExceptionRenderer(rich_formatter)
        exc_info = event_dict.pop('exc_info', None)
        if exc_info is not None:
            exc_info = figure_out_exc_info(exc_info)
            if exc_info is not None:
                event_dict['exception'] = rich_formatter(exc_info)

        return level, event, event_dict

    def _msg(self, level, event, event_dict):
        """
        Internal method to render message

        Args:
            level (str): Log level name
            event (str): Log message
            event_dict (dict): Log context
        """
        level, event, event_dict = self._process_event(level, event, event_dict)

        # build message, ignore errors
        raw = event_dict.pop('__raw__', None)
        event = event_format(event, event_dict)
        event = join_event_dict(event, event_dict)

        # inject time
        timestamp = time.time()
        now = datetime.fromtimestamp(timestamp).isoformat(sep=' ', timespec='milliseconds')

        # build log text
        if raw:
            text = f'{event}\n'
        else:
            text = f'{now} | {level} | {event}\n'

        backend_inited = self._writer.backend.inited
        if backend_inited:
            backend_event = {'t': timestamp, 'l': level, 'm': event}
            # add exception
            if 'exception' in event_dict:
                exception = event_dict['exception']
                text = f'{text}{exception}\n'
                backend_event['e'] = exception
            # add raw tag
            if raw:
                backend_event['r'] = 1
        else:
            # no backend
            backend_event = {}
            if 'exception' in event_dict:
                exception = event_dict['exception']
                text = f'{text}{exception}\n'

        # print text
        self._emit(text, backend_event)

    def _emit(self, text, backend_event):
        """
        Internal method to emit event directly
        `text` will be print to stdout and log file, `event` will be sent to backend

        Args:
            text (str): Formatted log text
            backend_event (dict): Event dictionary for backend
        """
        writer = self._writer
        backend_inited = writer.backend.inited
        is_electron = writer.is_electron
        with self._lock:
            # do 3 things parallely, print to stdout, write into file, send to backend
            if backend_inited:
                if is_electron:
                    # backend + file
                    job = writer.backend.send_log(backend_event)
                    writer.fd.write(text)
                    writer.fd.flush()
                    job.acquire()
                else:
                    # backend + stdout + file
                    job = writer.backend.send_log(backend_event)
                    writer.fd.write(text)
                    writer.stdout.write(text)
                    writer.fd.flush()
                    writer.stdout.flush()
                    job.acquire()
            else:
                if is_electron:
                    # file
                    writer.fd.write(text)
                    writer.fd.flush()
                else:
                    # stdout + file
                    writer.fd.write(text)
                    writer.stdout.write(text)
                    writer.fd.flush()
                    writer.stdout.flush()

    def backend_event(self, event, timestamp: float = None, level='INFO', raw=0):
        # create backend event directly
        if timestamp is None:
            timestamp = time.time()
        backend_event = {'t': timestamp, 'l': level, 'm': event}
        if raw:
            backend_event['r'] = 1
        return backend_event

    """
    structlog-like features
    """

    def bind(self, **value_dict):
        """
        Return a new logger with *new_values* added to the existing ones.

        Args:
            **value_dict: Key-value pairs to bind.

        Returns:
            AlasioLogger: A new logger with the context bound.
        """
        new = self.__class__()
        context = self._context.copy()
        context.update(**value_dict)
        new._context = context
        new._writer = self._writer
        new.set_level(self._level)
        return new

    def unbind(self, *keys):
        """
        Return a new logger with *keys* removed from the context.
        missing keys are ignored.

        Args:
            *keys: Keys to unbind.

        Returns:
            AlasioLogger: A new logger with the keys removed.
        """
        new = self.__class__()
        context = self._context.copy()
        for key in keys:
            context.pop(key, None)
        new._context = context
        new._writer = self._writer
        new.set_level(self._level)
        return new

    def set_level(self, level=20):
        """
        Set logging level.
        Methods below this level will be replaced with empty_function.

        Args:
            level (str | int): Level name or value. Defaults to 20 (INFO).
        """
        if isinstance(level, str):
            level = LOG_LEVELS.get(level.upper(), level)
        if isinstance(level, str):
            # level is still str, maybe invalid level name
            level = 20

        self._level = level
        for method_name, method_level in _LEVEL_METHODS.items():
            if method_level < level:
                setattr(self, method_name, empty_function)
            else:
                # Restore original method from class if it was overridden
                if method_name in self.__dict__:
                    try:
                        delattr(self, method_name)
                    except AttributeError:
                        pass

    """
    Logging levels
    """

    def debug(self, event, **kwargs):
        """
        Log at DEBUG level.

        Args:
            event: Log message or exception
            **kwargs: Log context
        """
        event = stringify_event(event)
        self._msg('DEBUG', event, kwargs)

    def info(self, event, **kwargs):
        """
        Log at INFO level.

        Args:
            event: Log message or exception
            **kwargs: Log context
        """
        event = stringify_event(event)
        self._msg('INFO', event, kwargs)

    def warning(self, event, **kwargs):
        """
        Log at WARNING level.

        Args:
            event: Log message or exception
            **kwargs: Log context
        """
        event = stringify_event(event)
        self._msg('WARNING', event, kwargs)

    def error(self, event, **kwargs):
        """
        Log at ERROR level.

        Args:
            event: Log message or exception
            **kwargs: Log context
        """
        event = stringify_event(event)
        self._msg('ERROR', event, kwargs)

    def exception(self, event, **kwargs):
        """
        Log at ERROR level with exception info.
        """
        if isinstance(event, Exception):
            if isinstance(event, (BaseExceptionGroup, ExceptionGroup)):
                kwargs["exc_info"] = (type(event), event, event.__traceback__)
            msg = str(event)
            if msg:
                event = f'{type(event).__name__}: {msg}'
            else:
                event = type(event).__name__
        # see structlog._native.exception()
        kwargs.setdefault("exc_info", True)
        self._msg('EXCEPTION', event, kwargs)

    def critical(self, event, **kwargs):
        """
        Log at CRITICAL level.

        Args:
            event: Log message or exception
            **kwargs: Log context
        """
        event = stringify_event(event)
        self._msg('CRITICAL', event, kwargs)

    """
    Custom logging methods
    """

    def raw(self, event, **kwargs):
        """
        Log raw messages

        Args:
            event (str): Log message
            **kwargs: Log context
        """
        # act like info but tag __raw__
        kwargs['__raw__'] = 1
        self._msg('INFO', event, kwargs)

    def hr(self, title, level=3, **kwargs):
        """
        Log a horizontal rule.

        Args:
            title (str): Title of the horizontal rule
            level (int): Rule level (0-3). Defaults to 3.
            **kwargs: Log context
        """
        if level == 0:
            self.hr0(title, **kwargs)
        elif level == 1:
            self.hr1(title, **kwargs)
        elif level == 2:
            self.hr2(title, **kwargs)
        else:
            self.hr3(title, **kwargs)

    def hr0(self, title, **kwargs):
        """
        +==================================================================================================+
        |                                              LOGIN                                               |
        +==================================================================================================+
        2026-01-01 13:33:48.282 | INFO | LOGIN
        """
        title = title.upper()
        edge = f'+{"=" * 98}+'
        hr = f' {title} '.center(98, ' ')
        hr = f'|{hr}|'
        self.raw(edge)
        self.raw(hr, **kwargs)
        self.raw(edge)
        self.info(title, **kwargs)

    def hr1(self, title, **kwargs):
        """
        ============================================== LOGIN ===============================================
        2026-01-01 13:33:48.282 | INFO | LOGIN
        """
        title = title.upper()
        hr = f' {title} '.center(100, '=')
        self.raw(hr, **kwargs)
        self.info(title, **kwargs)

    def hr2(self, title, **kwargs):
        """
        ---------------------------------------------- LOGIN -----------------------------------------------
        2026-01-01 13:33:48.282 | INFO | LOGIN
        """
        title = title.upper()
        hr = f' {title} '.center(100, '-')
        self.raw(hr, **kwargs)
        self.info(title, **kwargs)

    def hr3(self, title, **kwargs):
        """
        2026-01-01 13:33:48.282 | INFO | ................ LOGIN .................
        """
        title = title.upper()
        hr = f' {title} '.center(40, '.')
        self.info(hr, **kwargs)

    def attr(self, name, text):
        """
        2026-01-01 13:33:48.282 | INFO | [name] text

        Args:
            name (str): Attribute name
            text (Any): Attribute value
        """
        self.info(f'[{name}] {text}')

    def attr_align(self, name, text, front='', align=22):
        """
        2026-01-01 13:33:48.282 | INFO |           globe_center: (0, 0)
        2026-01-01 13:33:48.282 | INFO | 0.330s      similarity: 0.900

        Args:
            name (str): Attribute name
            text (Any): Attribute value
            front (str): Text to prepend to name. Defaults to ''.
            align (int): Alignment width. Defaults to 22.
        """
        name = str(name).rjust(align)
        if front:
            name = front + name[len(front):]
        self.info(f'{name}: {text}')


logger = AlasioLogger()
