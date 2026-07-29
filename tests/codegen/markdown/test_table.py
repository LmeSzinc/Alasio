from io import StringIO

import msgspec
import pytest

from alasio.codegen.markdown.table import MarkdownTable
from alasio.logger import logger


class Person(msgspec.Struct):
    """Simple model for table rows."""
    name: str
    age: int


class OptionalFields(msgspec.Struct):
    """Model with a defaulted field."""
    name: str
    value: int = 0


# -- Read tests ---------------------------------------------------------------


class TestMarkdownTableRead:

    def test_read_first_table(self):
        """Read the first table in the document."""
        content = """\
| name | age |
|------|-----|
| Alice | 30 |
| Bob | 25 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Person)
        table.read()

        assert table.headers == ["name", "age"]
        assert len(table.rows) == 2
        assert table.rows[0].name == "Alice"
        assert table.rows[0].age == 30
        assert table.rows[1].name == "Bob"
        assert table.rows[1].age == 25

    def test_read_table_after_title(self):
        """Read the first table after a specific heading."""
        content = """\
# Section A

| name | age |
|------|-----|
| Alice | 30 |

# Section B

| name | age |
|------|-----|
| Bob | 25 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "Section B", Person)
        table.read()

        assert len(table.rows) == 1
        assert table.rows[0].name == "Bob"

    def test_read_with_heading_levels(self):
        """Title matching works with different heading levels."""
        content = """\
# Config

| name | age |
|------|-----|
| Alice | 30 |

## Other
"""
        f = StringIO(content)
        table = MarkdownTable(f, "Config", Person)
        table.read()

        assert table.rows[0].name == "Alice"

    def test_read_empty_table(self):
        """Read a table with header but no data rows."""
        content = """\
| name | age |
|------|-----|
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Person)
        table.read()

        assert table.headers == ["name", "age"]
        assert table.rows == []

    def test_read_with_unicode(self):
        """Read a table with Unicode content."""

        class Item(msgspec.Struct):
            name: str
            description: str

        content = """\
| name | description |
|------|-------------|
| café | 你好世界 |
| 中国 | 北京 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Item)
        table.read()

        assert table.rows[0].name == "café"
        assert table.rows[0].description == "你好世界"
        assert table.rows[1].name == "中国"
        assert table.rows[1].description == "北京"

    def test_read_title_not_found_raises(self):
        """Raise ValueError when title heading is not found."""
        content = """\
| name | age |
|------|-----|
| Alice | 30 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "Nonexistent", Person)
        with pytest.raises(ValueError, match="not found"):
            table.read()

    def test_read_table_not_found_raises(self):
        """Raise ValueError when no table exists in the document."""
        content = """\
Just some text

No table here
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Person)
        with pytest.raises(ValueError, match="No table found"):
            table.read()

    def test_read_table_not_found_after_title_raises(self):
        """Raise ValueError when title exists but no table follows."""
        content = """\
# Config

Just some text, no table here.
"""
        f = StringIO(content)
        table = MarkdownTable(f, "Config", Person)
        with pytest.raises(ValueError, match="No table found"):
            table.read()

    def test_read_headers_must_match_model(self):
        """Raise TypeError when a table header has no matching field."""
        content = """\
| name | age | Extra |
|------|-----|-------|
| Alice | 30 | ignore |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Person)
        with pytest.raises(TypeError, match="Extra"):
            table.read()

    def test_read_exact_header_matching(self):
        """Headers must match encode names exactly."""
        content = """\
| name | age |
|------|-----|
| Alice | 30 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Person)
        table.read()

        assert table.headers == ["name", "age"]
        assert table.rows[0].name == "Alice"
        assert table.rows[0].age == 30

    def test_read_before_after_content(self):
        """Verify _before and _after capture surrounding text."""

        class XY(msgspec.Struct):
            x: str
            y: str

        content = """\
# Header

intro

| x | y |
|---|---|
| a | b |

footer
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", XY)
        table.read()

        assert any("Header" in line for line in table._before)
        assert any("intro" in line for line in table._before)
        assert any("footer" in line for line in table._after)

    def test_read_before_after_no_trailing_newline(self):
        """When the whole file is just a table, before/after are empty."""
        content = """\
| name | age |
|------|-----|
| Alice | 30 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Person)
        table.read()

        assert table._before == []
        assert table._after == [""]


# -- Title scoping tests ------------------------------------------------------


class TestMarkdownTableScope:

    def test_title_scoped(self):
        """Scope includes sub-headings of the matched title."""
        content = """\
# Main

## Section A

### Sub Section

| name | age |
|------|-----|
| Alice | 30 |

## Section B

| name | age |
|------|-----|
| Bob | 25 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "Section A", Person)
        table.read()

        assert len(table.rows) == 1
        assert table.rows[0].name == "Alice"

    def test_title_scope_excludes_next_same_level(self):
        """Scope stops at the next heading of the same level."""
        content = """\
## Config

| name | age |
|------|-----|
| Alice | 30 |

## Other

| name | age |
|------|-----|
| Bob | 25 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "Config", Person)
        table.read()

        assert len(table.rows) == 1
        assert table.rows[0].name == "Alice"

    def test_title_scope_higher_level_ends_section(self):
        """A heading of higher level (fewer #) ends the scope."""
        content = """\
## Section

| name | age |
|------|-----|
| Alice | 30 |

# Main Title

| name | age |
|------|-----|
| Bob | 25 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "Section", Person)
        table.read()

        assert len(table.rows) == 1
        assert table.rows[0].name == "Alice"

    def test_empty_title_whole_document(self):
        """Empty title searches the whole document without scoping."""
        content = """\
# Top

| name | age |
|------|-----|
| Alice | 30 |

## Middle

| name | age |
|------|-----|
| Bob | 25 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Person)
        table.read()

        # First table in the document
        assert table.rows[0].name == "Alice"


# -- Write tests --------------------------------------------------------------


class TestMarkdownTableWrite:

    def test_write_preserves_surrounding(self):
        """Content before and after the table is preserved."""
        content = """\
# Header

Some intro text

| name | age |
|------|-----|
| Alice | 30 |
| Bob | 25 |

Some footer text
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Person)
        table.read()
        with logger.mock_capture_writer() as capture:
            table.write()
            assert capture.fd.any_contains("Write file")
            assert capture.stdout.any_contains("Write file")

        expected = """\
# Header

Some intro text

| name  | age |
|-------|-----|
| Alice | 30  |
| Bob   | 25  |

Some footer text
"""
        assert f.getvalue() == expected

    def test_write_headers_preserved(self):
        """Original markdown headers are kept on write."""
        content = """\
| name | age |
|------|-----|
| Alice | 30 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Person)
        table.read()
        with logger.mock_capture_writer() as capture:
            table.write()
            assert capture.fd.any_contains("Write file")
            assert capture.stdout.any_contains("Write file")

        expected = """\
| name  | age |
|-------|-----|
| Alice | 30  |
"""
        assert f.getvalue() == expected

    def test_write_modified_rows(self):
        """Modify self.rows, write them back."""
        content = """\
Before

| name | age |
|------|-----|
| Alice | 30 |

After
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Person)
        table.read()

        table.rows[0].name = "Alice Modified"
        table.rows[0].age = 31
        with logger.mock_capture_writer() as capture:
            table.write()
            assert capture.fd.any_contains("Write file")
            assert capture.stdout.any_contains("Write file")

        expected = """\
Before

| name           | age |
|----------------|-----|
| Alice Modified | 31  |

After
"""
        assert f.getvalue() == expected

    def test_write_added_rows(self):
        """Append rows and write."""
        content = """\
| name | age |
|------|-----|
| Alice | 30 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Person)
        table.read()

        table.rows.append(Person(name="Bob", age=25))
        table.rows.append(Person(name="Charlie", age=35))
        with logger.mock_capture_writer() as capture:
            table.write()
            assert capture.fd.any_contains("Write file")
            assert capture.stdout.any_contains("Write file")

        expected = """\
| name    | age |
|---------|-----|
| Alice   | 30  |
| Bob     | 25  |
| Charlie | 35  |
"""
        assert f.getvalue() == expected

    def test_write_removed_rows(self):
        """Remove rows and write."""
        content = """\
| name | age |
|------|-----|
| Alice | 30 |
| Bob | 25 |
| Charlie | 35 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Person)
        table.read()

        del table.rows[2]
        del table.rows[1]
        with logger.mock_capture_writer() as capture:
            table.write()
            assert capture.fd.any_contains("Write file")
            assert capture.stdout.any_contains("Write file")

        expected = """\
| name  | age |
|-------|-----|
| Alice | 30  |
"""
        assert f.getvalue() == expected

    def test_write_empty_rows(self):
        """Clear all rows and write."""
        content = """\
| name | age |
|------|-----|
| Alice | 30 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Person)
        table.read()

        table.rows.clear()
        with logger.mock_capture_writer() as capture:
            table.write()
            assert capture.fd.any_contains("Write file")
            assert capture.stdout.any_contains("Write file")

        expected = """\
| name | age |
|------|-----|
"""
        assert f.getvalue() == expected

    def test_write_with_title_preserves_surrounding(self):
        """Write after a title-scoped read preserves all surrounding content."""
        content = """\
# Config

| name | age |
|------|-----|
| Alice | 30 |

# Other

Some text
"""
        f = StringIO(content)
        table = MarkdownTable(f, "Config", Person)
        table.read()

        table.rows[0].age = 31
        with logger.mock_capture_writer() as capture:
            table.write()
            assert capture.fd.any_contains("Write file")
            assert capture.stdout.any_contains("Write file")

        expected = """\
# Config

| name  | age |
|-------|-----|
| Alice | 31  |

# Other

Some text
"""
        assert f.getvalue() == expected

    def test_write_without_read_raises(self):
        """Calling write() without prior read() raises RuntimeError."""
        content = """\
| name | age |
|------|-----|
| Alice | 30 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Person)
        with pytest.raises(RuntimeError, match=r"Must call read\(\)"):
            table.write()

    def test_read_write_roundtrip(self):
        """After read-modify-write, reading again returns modified rows."""
        content = """\
| name | age |
|------|-----|
| Alice | 30 |
| Bob | 25 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Person)
        table.read()

        table.rows[0].age = 100
        with logger.mock_capture_writer() as capture:
            table.write()
            assert capture.fd.any_contains("Write file")
            assert capture.stdout.any_contains("Write file")

        f.seek(0)
        table2 = MarkdownTable(f, "", Person)
        table2.read()

        assert len(table2.rows) == 2
        assert table2.rows[0].name == "Alice"
        assert table2.rows[0].age == 100
        assert table2.rows[1].name == "Bob"
        assert table2.rows[1].age == 25

    def test_write_idempotent(self):
        """Multiple writes without changes produce identical output."""
        content = """\
Before

| name | age |
|------|-----|
| Alice | 30 |

After
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Person)
        table.read()

        with logger.mock_capture_writer() as capture1:
            table.write()
            assert capture1.fd.any_contains("Write file")
            assert capture1.stdout.any_contains("Write file")
        f.seek(0)
        snapshot = f.getvalue()

        with logger.mock_capture_writer() as capture2:
            table.write()
            assert not capture2.fd.any_contains("Write file")
            assert not capture2.stdout.any_contains("Write file")
        assert f.getvalue() == snapshot

    def test_write_model_uses_correct_fields(self):
        """Model field values are written under the matching header."""
        content = """\
| name | age |
|------|-----|
| Alice | 30 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Person)
        table.read()

        table.rows[0].name = "Bob"
        table.rows[0].age = 25
        with logger.mock_capture_writer() as capture:
            table.write()
            assert capture.fd.any_contains("Write file")
            assert capture.stdout.any_contains("Write file")

        expected = """\
| name | age |
|------|-----|
| Bob  | 25  |
"""
        assert f.getvalue() == expected


class TestMarkdownTableWriteBehavior:
    """Tests for write() skip-on-no-change, logging, and chaining."""

    def test_write_returns_self(self):
        """write() returns self for chaining."""
        content = """\
| name | age |
|------|-----|
| Alice | 30 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Person)
        table.read()
        with logger.mock_capture_writer() as capture:
            result = table.write()
            assert capture.fd.any_contains("Write file")
            assert capture.stdout.any_contains("Write file")
        assert result is table

    def test_write_unchanged_skips_write_and_log(self):
        """Second write with no changes should not produce log output."""
        content = """\
| name | age |
|------|-----|
| Alice | 30 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Person)
        table.read()
        # First write: formats table -> content changes -> logs
        with logger.mock_capture_writer() as capture_first:
            table.write()
            assert capture_first.fd.any_contains("Write file")
            assert capture_first.stdout.any_contains("Write file")

        with logger.mock_capture_writer() as capture:
            table.write()
            # No log should be emitted since content is unchanged
            assert not capture.fd.any_contains("Write file")
            assert not capture.stdout.any_contains("Write file")

    def test_write_changed_logs(self):
        """Write after row modification should log the write."""
        content = """\
| name | age |
|------|-----|
| Alice | 30 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Person)
        table.read()
        table.rows[0].name = "Bob"

        with logger.mock_capture_writer() as capture:
            table.write()
            assert capture.fd.any_contains("Write file")
            assert capture.stdout.any_contains("Write file")
