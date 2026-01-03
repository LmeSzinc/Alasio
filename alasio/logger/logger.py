import sys
import time
from datetime import date, datetime
from io import StringIO

import structlog
from structlog import DropEvent
from structlog.processors import ExceptionRenderer

from alasio.ext import env
from alasio.ext.cache import threaded_cached_property
from alasio.ext.path import PathStr
from alasio.ext.singleton import Singleton


class PseudoBackendBridge:
    inited = False

    # Pseudo Backend that don't send logs
    def send_log(self, *args, **kwargs):
        pass


# It's a singleton because on each logger.bind() structlog.PrintLoggerFactory will create new `file` object
# But we don't want to open multiple files
class LogWriter(metaclass=Singleton):
    def __init__(self):
        self.create_date = ''
        self.is_electron = bool(env.ELECTRON_SECRET)

    @threaded_cached_property
    def backend(self):
        from alasio.backend.worker.bridge import BackendBridge
        backend = BackendBridge()
        if backend.inited:
            return backend
        else:
            return PseudoBackendBridge()

    @threaded_cached_property
    def fd(self):
        root = env.PROJECT_ROOT.abspath()
        folder = root / 'log'
        self.create_date = date.today()

        if self.backend.inited:
            name = self.backend.config_name
            # write logs to xxx/log/2020-01-01_{config_name}.txt
            file = folder / f'{self.create_date}_{name}.txt'
        else:
            # xxx/path/module.py -> module
            name = PathStr.new(sys.argv[0]).rootstem
            # write logs to xxx/log/2020-01-01_{module_name}.txt
            file = folder / f'{self.create_date}_{name}.txt'
        try:
            return open(file, 'a', encoding='utf-8')
        except FileNotFoundError:
            folder.makedirs(exist_ok=True)
        return open(file, 'a', encoding='utf-8')

    def check_rotate(self):
        # rotate log to file with new date
        if self.create_date and self.create_date != date.today():
            fd = threaded_cached_property.pop(self, 'fd')
            if fd is not None:
                try:
                    fd.close()
                except Exception:
                    pass

    def close(self):
        fd = threaded_cached_property.pop(self, 'fd')
        if fd is not None:
            try:
                fd.close()
            except Exception:
                pass

    def write(self, message):
        # suppress console logs when running in electron
        # because logs will send to backend, nobody can see the console
        if not self.is_electron:
            sys.stdout.write(message)
        self.fd.write(message)

    def flush(self):
        if not self.is_electron:
            sys.stdout.flush()
        self.fd.flush()


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
        *exc_info, show_locals=True, word_wrap=True,
    )
    if hasattr(tb, "code_width"):
        # `code_width` requires `rich>=13.8.0`
        tb.code_width = 120
    console.print(tb)
    output = sio.getvalue()

    mapping = {
        "┌": "+", "┐": "+", "└": "+", "┘": "+",
        "├": "+", "┤": "+", "┬": "+", "┴": "+",
        "┼": "+", "─": "-", "│": "|", "═": "=",
        "║": "|", "╒": "+", "╓": "+", "╔": "+",
        "╕": "+", "╖": "+", "╗": "+", "╘": "+",
        "╙": "+", "╚": "+", "╛": "+", "╜": "+",
        "╝": "+", "╞": "+", "╟": "+", "╠": "+",
        "╡": "+", "╢": "+", "╣": "+", "╤": "+",
        "╥": "+", "╦": "+", "╧": "+", "╨": "+",
        "╩": "+", "╪": "+", "╫": "+", "╬": "+",
        "…": "~",
    }
    for unicode_char, ascii_char in mapping.items():
        output = output.replace(unicode_char, ascii_char)

    return output


class SafeDict(dict):
    def __missing__(self, key):
        # Return placeholder when key is missing, for better debugging
        return f"<key {key} missing>"


def has_user_keys(event_dict):
    """
    Check if event_dict contains user-provided keys (not just built-in fields)
    Built-in fields: 'event', 'exception'
    """
    length = len(event_dict)
    # Fast path: empty or single item (must be built-in)
    if length <= 1:
        return False

    # Calculate how many built-in fields are present
    builtin_count = ('event' in event_dict) + ('exception' in event_dict)
    return length > builtin_count


class LogRenderer:
    # A renderer that mixes
    # - structlog.processors.TimeStamper(fmt="iso", utc=False)
    # - structlog.processors.add_log_level
    # - Support for logger.info("User {name}", name="John")
    # - loguru-like logging format:
    #   2026-01-01 00:00:00.000 | INFO | User John

    def __call__(self, log, level: str, event_dict: dict) -> str:
        # from structlog.__log_levels.add_log_level()
        # warn is just a deprecated alias in the stdlib.
        if level == "warn":
            level = "warning"
        # Calling exception("") is the same as error("", exc_info=True)
        if level == "exception":
            level = "error"
        level = level.upper()

        # inject time
        timestamp = time.time()
        now = datetime.fromtimestamp(timestamp).isoformat(sep=' ', timespec='milliseconds')
        # convert event
        try:
            event = event_dict['event']
        except KeyError:
            # this shouldn't happen
            event = ''
        if not isinstance(event, str):
            event = str(event)
            event_dict["event"] = event

        # build message, ignore errors
        # check event_dict also, if someone log like this:
        #   modules = set('combat_ui')
        #   logger.info(f'Assets generate, modules={modules}')
        # we won't log:
        #   Assets generate, modules=<key 'combat_ui' missing>
        if '{' in event and has_user_keys(event_dict):

            try:
                event = event.format(**event_dict)
            except KeyError:
                try:
                    event = event.format_map(SafeDict(event_dict))
                except Exception:
                    pass
            except Exception:
                # maybe {} is unpaired
                pass

        # build log text
        text = f'{now} | {level} | {event}'

        backend = log._file.backend
        if backend.inited:
            event = {'t': timestamp, 'l': level, 'm': event}
            # add exception
            if 'exception' in event_dict:
                exception = event_dict['exception']
                text = f'{text}\n{exception}'
                event['e'] = exception
            # add raw tag
            raw = event_dict.pop('__raw__', None)
            if raw:
                event['r'] = raw

            # send log to backend bridge
            log._file.backend.send_log(event)
        else:
            # no backend
            if 'exception' in event_dict:
                exception = event_dict['exception']
                text = f'{text}\n{exception}'

        return text


class AlasioLogger(structlog.BoundLoggerBase):
    def debug(self, event: str, **kwargs):
        try:
            args, kw = self._process_event('debug', event, kwargs)
            return self._logger.msg(*args, **kw)
        except DropEvent:
            return None

    def info(self, event: str, **kwargs):
        try:
            args, kw = self._process_event('info', event, kwargs)
            return self._logger.msg(*args, **kw)
        except DropEvent:
            return None

    def warning(self, event: str, **kwargs):
        try:
            args, kw = self._process_event('warning', event, kwargs)
            return self._logger.msg(*args, **kw)
        except DropEvent:
            return None

    def error(self, event: "str | Exception", **kwargs):
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
            event = f'{type(event).__name__}: {event}'
        try:
            args, kw = self._process_event('error', event, kwargs)
            return self._logger.msg(*args, **kw)
        except DropEvent:
            return None

    def exception(self, event: "str | Exception", **kwargs):
        if isinstance(event, Exception):
            event = f'{type(event).__name__}: {event}'
        # see structlog._native.exception()
        kwargs.setdefault("exc_info", True)
        return self.error(event, **kwargs)

    def critical(self, event: str, **kwargs):
        try:
            args, kw = self._process_event('critical', event, kwargs)
            return self._logger.msg(*args, **kw)
        except DropEvent:
            return None

    """
    Custom logging methods
    """

    def raw(self, event: str, **kwargs):
        """
        Log raw messages
        """
        # act like info but tag __raw__
        kwargs['__raw__'] = 1
        try:
            args, kw = self._process_event('info', event, kwargs)
            return self._logger.msg(*args, **kw)
        except DropEvent:
            return None

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


structlog.configure(
    processors=[
        ExceptionRenderer(rich_formatter),
        LogRenderer(),
    ],
    wrapper_class=AlasioLogger,
    context_class=dict,
    # ignore type error here
    # as LogWriter is a pseudo TextIO object that has write() flush()
    logger_factory=structlog.PrintLoggerFactory(
        file=LogWriter(),
    ),
    cache_logger_on_first_use=True,
)

logger: AlasioLogger = structlog.get_logger()
