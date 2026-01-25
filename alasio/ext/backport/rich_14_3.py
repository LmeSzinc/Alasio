import inspect
import os
import sys
from itertools import islice
from traceback import walk_tb
from types import TracebackType
from typing import Any, Iterable, List, Optional, Set, Tuple, Type

from exceptiongroup import BaseExceptionGroup, ExceptionGroup
from rich import pretty
from rich.traceback import Frame, LOCALS_MAX_LENGTH, LOCALS_MAX_STRING, Stack, Trace, Traceback, _SyntaxError


class RichTracebackBackport(Traceback):
    @classmethod
    def extract(
            cls,
            exc_type: Type[BaseException],
            exc_value: BaseException,
            traceback: Optional[TracebackType],
            *,
            show_locals: bool = False,
            locals_max_length: int = LOCALS_MAX_LENGTH,
            locals_max_string: int = LOCALS_MAX_STRING,
            locals_max_depth: Optional[int] = None,
            locals_hide_dunder: bool = True,
            locals_hide_sunder: bool = False,
            _visited_exceptions: Optional[Set[BaseException]] = None,
    ) -> Trace:
        """Extract traceback information.

        Args:
            exc_type (Type[BaseException]): Exception type.
            exc_value (BaseException): Exception value.
            traceback (TracebackType): Python Traceback object.
            show_locals (bool, optional): Enable display of local variables. Defaults to False.
            locals_max_length (int, optional): Maximum length of containers before abbreviating, or None for no abbreviation.
                Defaults to 10.
            locals_max_string (int, optional): Maximum length of string before truncating, or None to disable. Defaults to 80.
            locals_max_depth (int, optional): Maximum depths of locals before truncating, or None to disable. Defaults to None.
            locals_hide_dunder (bool, optional): Hide locals prefixed with double underscore. Defaults to True.
            locals_hide_sunder (bool, optional): Hide locals prefixed with single underscore. Defaults to False.

        Returns:
            Trace: A Trace instance which you can use to construct a `Traceback`.
        """

        stacks: List[Stack] = []
        is_cause = False

        from rich import _IMPORT_CWD

        notes: List[str] = getattr(exc_value, "__notes__", None) or []

        grouped_exceptions: Set[BaseException] = (
            set() if _visited_exceptions is None else _visited_exceptions
        )

        def safe_str(_object: Any) -> str:
            """Don't allow exceptions from __str__ to propagate."""
            try:
                return str(_object)
            except Exception:
                return "<exception str() failed>"

        while True:
            stack = Stack(
                exc_type=safe_str(exc_type.__name__),
                exc_value=safe_str(exc_value),
                is_cause=is_cause,
                notes=notes,
            )

            # --- BACKPORT START ---
            # remove `if sys.version_info >= (3, 11):`
            if isinstance(exc_value, (BaseExceptionGroup, ExceptionGroup)):
                stack.is_group = True
                for exception in exc_value.exceptions:
                    if exception in grouped_exceptions:
                        continue
                    grouped_exceptions.add(exception)
                    stack.exceptions.append(
                        # use this class instead of `Traceback`
                        cls.extract(
                            type(exception),
                            exception,
                            exception.__traceback__,
                            show_locals=show_locals,
                            locals_max_length=locals_max_length,
                            locals_hide_dunder=locals_hide_dunder,
                            locals_hide_sunder=locals_hide_sunder,
                            _visited_exceptions=grouped_exceptions,
                        )
                    )
            # --- BACKPORT END ---

            if isinstance(exc_value, SyntaxError):
                stack.syntax_error = _SyntaxError(
                    offset=exc_value.offset or 0,
                    filename=exc_value.filename or "?",
                    lineno=exc_value.lineno or 0,
                    line=exc_value.text or "",
                    msg=exc_value.msg,
                    notes=notes,
                )

            stacks.append(stack)
            append = stack.frames.append

            def get_locals(
                    iter_locals: Iterable[Tuple[str, object]],
            ) -> Iterable[Tuple[str, object]]:
                """Extract locals from an iterator of key pairs."""
                if not (locals_hide_dunder or locals_hide_sunder):
                    yield from iter_locals
                    return
                for key, value in iter_locals:
                    if locals_hide_dunder and key.startswith("__"):
                        continue
                    if locals_hide_sunder and key.startswith("_"):
                        continue
                    yield key, value

            for frame_summary, line_no in walk_tb(traceback):
                filename = frame_summary.f_code.co_filename

                last_instruction: Optional[Tuple[Tuple[int, int], Tuple[int, int]]]
                last_instruction = None
                if sys.version_info >= (3, 11):
                    instruction_index = frame_summary.f_lasti // 2
                    instruction_position = next(
                        islice(
                            frame_summary.f_code.co_positions(),
                            instruction_index,
                            instruction_index + 1,
                        )
                    )
                    (
                        start_line,
                        end_line,
                        start_column,
                        end_column,
                    ) = instruction_position
                    if (
                            start_line is not None
                            and end_line is not None
                            and start_column is not None
                            and end_column is not None
                    ):
                        last_instruction = (
                            (start_line, start_column),
                            (end_line, end_column),
                        )

                if filename and not filename.startswith("<"):
                    if not os.path.isabs(filename):
                        filename = os.path.join(_IMPORT_CWD, filename)
                if frame_summary.f_locals.get("_rich_traceback_omit", False):
                    continue

                frame = Frame(
                    filename=filename or "?",
                    lineno=line_no,
                    name=frame_summary.f_code.co_name,
                    locals=(
                        {
                            key: pretty.traverse(
                                value,
                                max_length=locals_max_length,
                                max_string=locals_max_string,
                                max_depth=locals_max_depth,
                            )
                            for key, value in get_locals(frame_summary.f_locals.items())
                            if not (inspect.isfunction(value) or inspect.isclass(value))
                        }
                        if show_locals
                        else None
                    ),
                    last_instruction=last_instruction,
                )
                append(frame)
                if frame_summary.f_locals.get("_rich_traceback_guard", False):
                    del stack.frames[:]

            if not grouped_exceptions:
                cause = getattr(exc_value, "__cause__", None)
                if cause is not None and cause is not exc_value:
                    exc_type = cause.__class__
                    exc_value = cause
                    # __traceback__ can be None, e.g. for exceptions raised by the
                    # 'multiprocessing' module
                    traceback = cause.__traceback__
                    is_cause = True
                    continue

                cause = exc_value.__context__
                if cause is not None and not getattr(
                        exc_value, "__suppress_context__", False
                ):
                    exc_type = cause.__class__
                    exc_value = cause
                    traceback = cause.__traceback__
                    is_cause = False
                    continue
            # No cover, code is reached but coverage doesn't recognize it.
            break  # pragma: no cover

        trace = Trace(stacks=stacks)

        return trace
