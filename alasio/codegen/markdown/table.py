import os
import re
from functools import cached_property
from itertools import chain
from typing import Generic, List, Literal, TypeVar, Union

import msgspec
from msgspec.structs import asdict, fields

from alasio.ext.cache.msgspec_meta import get_field_metadata

T = TypeVar('T', bound=msgspec.Struct)


class FieldFormatExtra(msgspec.Struct):
    """
    Per-field formatting options for markdown table output.

    Usage in a struct field::

        class MyModel(msgspec.Struct):
            name: Annotated[str, Meta(extra={"width": 20, "align": "center"})]

    Args:
        align (str): Column alignment. Defaults to 'left'.
        width (str | int): 'auto' = adjust to longest content;
            int = fixed minimum width (extended if content is longer).
            Defaults to 'auto'.
    """
    align: Literal['left', 'center', 'right'] = 'left'
    width: Union[Literal['auto'], int] = 'auto'


class MarkdownTable(Generic[T]):
    """
    Read and write markdown tables.

    ``model`` is a ``msgspec.Struct`` whose field **encode names** (or Python
    names if no alias/rename is set) must match the table column headers
    case-insensitively.  ``read()`` populates ``self.rows`` with model
    instances; ``write()`` serialises them back.

    Only the table body is replaced — content before and after is preserved.

    When ``title`` is non-empty the matching heading defines a *section
    scope*: the table is searched for **after** that heading, and the search
    stops at the next heading of the same **or higher** level (e.g. if
    ``title`` matches ``## Config``, the scope ends before the next ``#`` or
    ``##`` heading; ``###`` sub-headings remain inside the scope).

    Args:
        file (str or file-like): Path to the markdown file, or a file-like
            object such as StringIO for testing.
        title (str): Heading title to locate the table.  Empty string means
            find the first table in the whole document.  Must match the
            heading text exactly (after stripping '#' markers).
        model (type[msgspec.Struct]): Model class for table rows.  Field
            encode names are matched to table headers case-insensitively.

    Raises:
        ValueError: If the title or table is not found.
        TypeError: If a table header does not match any model field.
    """

    def __init__(self, file, title, model):
        self.file = file
        self.title = title
        self.model = model
        self.headers = []
        self.rows: "List[T]" = []
        self._before = []
        self._after = []
        self._read = False

    # -- I/O helpers ----------------------------------------------------------

    def _read_content(self):
        """Read content from file path or file-like object."""
        if isinstance(self.file, (str, bytes, os.PathLike)):
            from alasio.ext.path.atomic import atomic_read_text
            return atomic_read_text(self.file)
        pos = self.file.tell()
        content = self.file.read()
        self.file.seek(pos)
        if isinstance(content, bytes):
            content = content.decode('utf-8')
        return content

    def _write_content(self, content):
        """Write content to file path or file-like object."""
        if isinstance(self.file, (str, bytes, os.PathLike)):
            from alasio.ext.path.atomic import atomic_write
            atomic_write(self.file, content)
        else:
            self.file.seek(0)
            self.file.truncate()
            self.file.write(content)

    # -- Heading helpers ------------------------------------------------------

    @staticmethod
    def _parse_header(line):
        """
        Parse a markdown heading line.

        Returns:
            tuple[int, str]: ``(level, title)`` where *level* is the heading
            level (1 for ``#``, 2 for ``##``, …) and *title* is the heading
            text.  Returns ``(0, '')`` if *line* is not a heading.
        """
        stripped = line.strip()
        if not stripped.startswith('#'):
            return 0, ''
        rest = stripped.lstrip('#')
        return len(stripped) - len(rest), rest.strip()

    # -- Field info -----------------------------------------------------------

    @cached_property
    def _field_info(self):
        """
        Map field encode name -> Python field name.

        ``FieldInfo.encode_name`` is the key used when serialising
        (JSON/msgpack), which may differ from the Python attribute name if
        ``rename`` or field-level aliases are configured.
        """
        return {f.encode_name: f.name for f in fields(self.model)}

    @cached_property
    def _field_format(self):
        """
        Map Python field name -> ``FieldFormatExtra``.

        Built from each field's ``Meta(extra=...)`` dict via
        :func:`get_field_metadata`.  Supported keys are ``width``
        (``'auto'`` or ``int``) and ``align`` (``'left'``,
        ``'center'``, ``'right'``).  Fields without metadata get
        a default ``FieldFormatExtra()``.
        """
        result = {}
        for name, meta in get_field_metadata(self.model).items():
            result[name] = msgspec.convert(
                meta.extra, FieldFormatExtra, strict=False,
            )
        return result

    # -- Table line predicates -------------------------------------------------

    @staticmethod
    def _is_table_line(stripped):
        """Return True if *stripped* looks like a markdown table row."""
        return stripped.startswith('|') and stripped.endswith('|')

    @staticmethod
    def _is_dash_line(stripped):
        """Return True if *stripped* is a table separator (``|---|``)."""
        return (
                stripped.startswith('|')
                and '---' in stripped
                and all(c in '| -:' for c in stripped)
        )

    @staticmethod
    def _parse_row(line):
        """
        Parse a markdown table row into cell strings.

        Args:
            line (str): A markdown table row line, beginning and ending
                with '|'.

        Returns:
            list[str]: Cell values, stripped of leading/trailing whitespace.
        """
        # content lines already get stripped in _find_table()
        # line = line.strip()
        if line.startswith('|'):
            if line.endswith('|'):
                line = line[1:-1]
            else:
                line = line[1:]
        else:
            if line.endswith('|'):
                line = line[:-1]
        return [cell.strip() for cell in line.split('|')]

    def _find_table(self, lines):
        """
        Locate the target table and return its raw line slices.

        Returns:
            tuple[list[str], str, list[str], list[str]]:
            ``(before_lines, header_line, body_lines, after_lines)``
            where *header_line* is the raw header row line and
            *body_lines* are the raw data row lines (excluding the
            separator).  No cell-level parsing is done here.

        Raises:
            ValueError: If the title or table is not found.
        """
        search_start = 0
        scope_end = len(lines)

        if self.title:
            for i, line in enumerate(lines):
                level, text = self._parse_header(line)
                if level and text == self.title:
                    heading_idx, heading_level = i, level
                    break
            else:
                raise ValueError(
                    f"Title '{self.title}' not found in document"
                )
            search_start = heading_idx + 1

            # Scope ends at the next heading of same or *higher* level
            for i in range(heading_idx + 1, len(lines)):
                level, _ = self._parse_header(lines[i])
                if level and level <= heading_level:
                    scope_end = i
                    break

        # Find table via state machine, tracking header position
        header_idx = -1
        header_line = ''
        sep_idx = 0

        in_header = False
        for i, line in enumerate(lines[search_start:scope_end], start=search_start):
            stripped = line.strip()
            if in_header:
                if self._is_dash_line(stripped):
                    sep_idx = i
                    break
                in_header = False
                header_idx = -1
                header_line = ''
                continue
            if self._is_table_line(stripped):
                header_line = stripped
                header_idx = i
                in_header = True
                continue

        if header_idx < 0:
            if self.title:
                raise ValueError(
                    f"No table found after title '{self.title}'"
                )
            raise ValueError("No table found in document")

        # Collect body rows, tracking table-end position.
        # Blank lines are NOT allowed within standard markdown tables
        # and terminate the body.
        body_lines = []
        table_end = sep_idx + 1
        i = sep_idx
        for i, line in enumerate(lines[sep_idx + 1:scope_end], start=sep_idx + 1):
            stripped = line.strip()
            if not stripped or not stripped.startswith('|'):
                table_end = i
                break
            body_lines.append(stripped)
        else:
            if body_lines:
                table_end = i + 1

        return lines[:header_idx], header_line, body_lines, lines[table_end:]

    # -- Header validation ----------------------------------------------------

    def _validate_headers(self, headers):
        """Raise ``TypeError`` if any header lacks a matching model field."""
        for h in headers:
            if h not in self._field_info:
                raise TypeError(
                    f"Table header '{h}' does not match any field in "
                    f"model '{self.model.__name__}'.  "
                    f"Available encode names: {sorted(self._field_info)}"
                )

    # -- Public API -----------------------------------------------------------

    def read(self):
        """
        Read the markdown table and populate ``self.rows``.

        ``self.headers`` is set to the original header row from the markdown.

        Returns:
            MarkdownTable: ``self`` for chaining.

        Raises:
            ValueError: If the title or table is not found.
            TypeError: If a table header does not match any model field.
        """
        content = self._read_content()
        lines = content.split('\n')

        self._before, header_line, body_lines, self._after = self._find_table(lines)
        self.headers = self._parse_row(header_line)
        self._validate_headers(self.headers)

        # Use headers directly as encode-name keys (validated to match)
        dict_rows = [
            {h: val for h, val in zip(self.headers, self._parse_row(row))}
            for row in body_lines
        ]

        # Batch convert all rows at once
        self.rows = (
            msgspec.convert(dict_rows, List[self.model], strict=False)
            if dict_rows else []
        )
        self._read = True
        return self

    def write(self):
        """
        Write ``self.rows`` back to the file.

        Only the table is replaced; content before and after is preserved.
        The original markdown headers are kept.

        Raises:
            RuntimeError: If ``read()`` has not been called first.
        """
        if not self._read:
            raise RuntimeError("Must call read() before write()")

        result = '\n'.join(chain(self._before, self._format_table(), self._after))
        self._write_content(result)

    # -- Table formatting -----------------------------------------------------

    _RE_WIDE = re.compile(r'[^\x00-\xff]')

    @classmethod
    def _display_width(cls, text):
        """
        Approximate display width — non-ASCII chars count as 2.

        Non-string types (int, float, …) produce ASCII-only ``str()`` so
        they skip the regex entirely.  Pure ASCII strings also skip it.
        """
        if text.isascii():
            return len(text)
        return len(text) + len(cls._RE_WIDE.findall(text))

    @classmethod
    def _pad_cell(cls, text, width, align='left'):
        """
        Pad *text* to display *width*, accounting for CJK double-width chars.
        """
        padding = width - cls._display_width(text)
        if padding <= 0:
            return text
        if align == 'left':
            return text.ljust(len(text) + padding)
        if align == 'right':
            return text.rjust(len(text) + padding)
        if align == 'center':
            return text.center(len(text) + padding)
        return text.ljust(len(text) + padding)

    def _format_table(self):
        """
        Yield aligned markdown table lines.

        Yields:
            str: Each row of the formatted table.
        """
        num_cols = len(self.headers)
        if num_cols == 0:
            return

        # Build col_fmt list aligned with headers (by source header order)
        into_header = {h: self._field_info[h] for h in self.headers}
        col_fmt = []
        for h in self.headers:
            fmt = FieldFormatExtra()
            meta = self._field_format.get(into_header[h])
            if meta is not None:
                fmt = meta
            col_fmt.append(fmt)

        # Build row-value matrix
        all_rows = [list(self.headers)]
        for obj in self.rows:
            obj_dict = asdict(obj)
            all_rows.append([
                str(obj_dict.get(into_header[h], '')) for h in self.headers
            ])

        # Resolve auto widths using display width; enforce minimum 3
        for i in range(num_cols):
            cf = col_fmt[i]
            if cf.width == 'auto':
                cf.width = max(self._display_width(r[i]) for r in all_rows)
            if cf.width < 3:
                cf.width = 3

        # Header row
        parts = [
            f' {self._pad_cell(h, cf.width, cf.align)} '
            for h, cf in zip(self.headers, col_fmt)
        ]
        yield f'|{"|".join(parts)}|'

        # Separator row (total width = cf.width + 2)
        sep_parts = []
        for cf in col_fmt:
            w: int = cf.width
            if cf.align == 'center':
                sep_parts.append(f':{"-" * w}:')
            elif cf.align == 'right':
                sep_parts.append(f'{"-" * (w + 1)}:')
            else:
                sep_parts.append('-' * (w + 2))
        yield f'|{"|".join(sep_parts)}|'

        # Data rows
        for vals in all_rows[1:]:
            parts = [
                f' {self._pad_cell(v, cf.width, cf.align)} '
                for v, cf in zip(vals, col_fmt)
            ]
            yield f'|{"|".join(parts)}|'
