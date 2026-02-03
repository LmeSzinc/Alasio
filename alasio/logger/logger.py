import threading
import time
from datetime import datetime
from io import StringIO
from typing import Tuple, Union

from exceptiongroup import BaseExceptionGroup, ExceptionGroup
from typing_extensions import Self

from alasio.ext.backport import patch_rich_traceback_extract
from alasio.logger.utils import event_format, figure_out_exc_info, gen_exception_tree, replace_unicode_table
from alasio.logger.writer import LogWriter

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


class AlasioLogger:
    # global logging lock
    _lock = threading.Lock()

    def __init__(self):
        self._context = {}
        self._writer = LogWriter()

    def _process_event(self, level: str, event: str, event_dict: dict) -> Tuple[str, str, dict]:
        """
        Internal method that emulates structlog processors chain
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

    def _msg(self, level: str, event: str, event_dict: dict):
        """
        Internal method to render message
        """
        level, event, event_dict = self._process_event(level, event, event_dict)

        # build message, ignore errors
        raw = event_dict.pop('__raw__', None)
        event = event_format(event, event_dict)

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

    def _emit(self, text: str, backend_event: dict):
        """
        Internal method to emit event directly
        `text` will be print to stdout and log file, `event` will be sent to backend
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

    """
    structlog-like features
    """

    def bind(self, **value_dict) -> Self:
        """
        Return a new logger with *new_values* added to the existing ones.
        """
        new = self.__class__()
        context = self._context.copy()
        context.update(**value_dict)
        new._context = context
        return new

    def unbind(self, *keys) -> Self:
        """
        Return a new logger with *keys* removed from the context.
        missing keys are ignored.
        """
        new = self.__class__()
        context = self._context.copy()
        for key in keys:
            context.pop(key, None)
        new._context = context
        return new

    """
    Logging levels
    """

    def debug(self, event: str, **kwargs):
        self._msg('DEBUG', event, kwargs)

    def info(self, event: str, **kwargs):
        self._msg('INFO', event, kwargs)

    def warning(self, event: str, **kwargs):
        self._msg('WARNING', event, kwargs)

    def error(self, event: Union[str, Exception], **kwargs):
        # Better exception logging
        # If someone do:
        #   try:
        #       raise ExampleError
        #   except ExampleError as e:
        #       logger.error(e)
        # We can log:
        #   ExampleError:
        # instead of just empty string ""
        if isinstance(event, Exception):
            if isinstance(event, (BaseExceptionGroup, ExceptionGroup)):
                title = f'{type(event).__name__}: {event}'
                detail = '\n'.join(gen_exception_tree(event))
                event = f'{title}\n{detail}'
            else:
                event = f'{type(event).__name__}: {event}'
        self._msg('ERROR', event, kwargs)

    def exception(self, event: Union[str, Exception], **kwargs):
        if isinstance(event, Exception):
            if isinstance(event, (BaseExceptionGroup, ExceptionGroup)):
                kwargs["exc_info"] = (type(event), event, event.__traceback__)
            event = f'{type(event).__name__}: {event}'
        # see structlog._native.exception()
        kwargs.setdefault("exc_info", True)
        self._msg('EXCEPTION', event, kwargs)

    def critical(self, event: str, **kwargs):
        self._msg('CRITICAL', event, kwargs)

    """
    Custom logging methods
    """

    def raw(self, event: str, **kwargs):
        """
        Log raw messages
        """
        # act like info but tag __raw__
        kwargs['__raw__'] = 1
        self._msg('INFO', event, kwargs)

    def hr(self, title: str, level: int = 3, **kwargs):
        if level == 0:
            self.hr0(title, **kwargs)
        elif level == 1:
            self.hr1(title, **kwargs)
        elif level == 2:
            self.hr2(title, **kwargs)
        else:
            self.hr3(title, **kwargs)

    def hr0(self, title: str, **kwargs):
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

    def hr1(self, title: str, **kwargs):
        """
        ============================================== LOGIN ===============================================
        2026-01-01 13:33:48.282 | INFO | LOGIN
        """
        title = title.upper()
        hr = f' {title} '.center(100, '=')
        self.raw(hr, **kwargs)
        self.info(title, **kwargs)

    def hr2(self, title: str, **kwargs):
        """
        ---------------------------------------------- LOGIN -----------------------------------------------
        2026-01-01 13:33:48.282 | INFO | LOGIN
        """
        title = title.upper()
        hr = f' {title} '.center(100, '-')
        self.raw(hr, **kwargs)
        self.info(title, **kwargs)

    def hr3(self, title: str, **kwargs):
        """
        2026-01-01 13:33:48.282 | INFO | ................ LOGIN .................
        """
        title = title.upper()
        hr = f' {title} '.center(40, '.')
        self.info(hr, **kwargs)

    def attr(self, name: str, text):
        """
        2026-01-01 13:33:48.282 | INFO | [name] text
        """
        logger.info(f'[{name}] {text}')

    def attr_align(self, name: str, text, front: str = '', align=22):
        """
        2026-01-01 13:33:48.282 | INFO |           globe_center: (0, 0)
        2026-01-01 13:33:48.282 | INFO | 0.330s      similarity: 0.900
        """
        name = str(name).rjust(align)
        if front:
            name = front + name[len(front):]
        logger.info(f'{name}: {text}')


logger = AlasioLogger()
