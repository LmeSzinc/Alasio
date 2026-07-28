from io import StringIO

import msgspec
import pytest

from alasio.codegen.markdown.table import MarkdownTable


class Person(msgspec.Struct):
    """Simple model for table rows."""
    name: str
    age: int


class TestMarkdownTableEdgeCases:

    def test_read_chain(self):
        """read() returns self for chaining."""
        content = """\
| name | age |
|------|-----|
| Alice | 30 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Person).read()
        assert len(table.rows) == 1
        assert table.rows[0].name == "Alice"

    def test_read_with_extra_column(self):
        """An unrecognised header raises TypeError."""

        class Extra(msgspec.Struct):
            name: str

        content = """\
| name | age |
|------|-----|
| Alice | 30 |
"""
        f = StringIO(content)
        with pytest.raises(TypeError, match="age"):
            MarkdownTable(f, "", Extra).read()

    def test_read_header_mismatch_field_name(self):
        """Header matching is by encode_name, case-insensitive."""

        class Item(msgspec.Struct):
            label: str
            quantity: int

        content = """\
| Label | Quantity |
|-------|----------|
| apple | 3 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Item).read()
        assert table.rows[0].label == "apple"
        assert table.rows[0].quantity == 3

    def test_read_carriage_return_lines_handled(self):
        """CR+LF line endings — split produces trailing CR, stripping removes it."""

        class CR(msgspec.Struct):
            name: str
            val: str

        content = "| name | val |\r\n|------|-----|\r\n| a | 1 |\r\n"
        f = StringIO(content)
        table = MarkdownTable(f, "", CR).read()
        assert table.rows[0].name == "a"
        assert table.rows[0].val == "1"

    def test_read_alignment_separator_handled(self):
        """Separator rows with alignment markers (:---, :---:, ---:)."""

        class Align(msgspec.Struct):
            left: str
            center: str
            right: str

        content = """\
| left | center | right |
|:-----|:------:|------:|
| a | b | c |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Align).read()
        assert table.rows[0].left == "a"
        assert table.rows[0].center == "b"
        assert table.rows[0].right == "c"

    def test_read_malformed_missing_separator(self):
        """A table-like block without a separator is not a table."""
        content = """\
| name | age |
| Alice | 30 |
"""
        f = StringIO(content)
        with pytest.raises(ValueError, match="No table found"):
            MarkdownTable(f, "", Person).read()

    def test_read_inconsistent_columns_truncated(self):
        """Data rows with more columns than header — extra cells are ignored."""

        class Two(msgspec.Struct):
            a: str
            b: str

        content = """\
| a | b |
|---|---|
| 1 | 2 | 3 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Two).read()
        assert table.rows[0].a == "1"
        assert table.rows[0].b == "2"

    def test_read_inconsistent_columns_fewer(self):
        """Data rows with fewer columns than header — missing cells get defaults."""

        class Three(msgspec.Struct):
            a: str = ''
            b: str = ''
            c: str = 'default'

        content = """\
| a | b | c |
|---|---|---|
| 1 | 2 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Three).read()
        assert table.rows[0].a == "1"
        assert table.rows[0].b == "2"
        assert table.rows[0].c == "default"

    def test_read_blank_lines_between_rows_terminate(self):
        """A blank line inside the table body terminates parsing."""
        content = """\
| name | age |
|------|-----|
| Alice | 30 |

| Bob | 25 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Person).read()
        assert len(table.rows) == 1
        assert table.rows[0].name == "Alice"

    def test_read_table_only_header(self):
        """A table with only a header and separator row — no data rows."""
        content = """\
| name | age |
|------|-----|
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Person).read()
        assert table.rows == []

    def test_read_table_immediately_at_file_end(self):
        """Table ends exactly at file end without trailing newline."""

        class XY(msgspec.Struct):
            x: str
            y: str

        content = "| x | y |\n|---|---|\n| 1 | 2 |"
        f = StringIO(content)
        table = MarkdownTable(f, "", XY).read()
        assert table.rows[0].x == "1"
        assert table.rows[0].y == "2"

    def test_read_single_row_table(self):
        """A table with exactly one data row."""
        content = """\
| name | age |
|------|-----|
| solo | 99 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Person).read()
        assert len(table.rows) == 1
        assert table.rows[0].name == "solo"
        assert table.rows[0].age == 99

    def test_read_headers_with_spaces(self):
        """Header with extra whitespace is stripped."""

        class Spaced(msgspec.Struct):
            name: str
            age: int

        content = """\
|  name  |  age  |
|--------|-------|
| Alice | 30 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Spaced).read()
        assert table.rows[0].name == "Alice"
        assert table.rows[0].age == 30


class TestMarkdownTableFieldAlias:
    """Tests for struct field aliasing via ``msgspec.field(name=...)``."""

    def test_read_encode_name_mismatches_python_name(self):
        """Fields with explicit ``name=`` — headers use encode_name."""

        class Aliased(msgspec.Struct):
            user_name: str = msgspec.field(name="userName")
            user_age: int = msgspec.field(name="userAge")

        content = """\
| userName | userAge |
|----------|---------|
| Alice | 30 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Aliased).read()
        assert table.rows[0].user_name == "Alice"
        assert table.rows[0].user_age == 30

    def test_read_requires_encode_name_not_python_name(self):
        """Using Python name for a ``name=`` field raises TypeError."""

        class Aliased(msgspec.Struct):
            user_name: str = msgspec.field(name="userName")
            user_age: int = msgspec.field(name="userAge")

        content = """\
| user_name | user_age |
|-----------|----------|
| Alice | 30 |
"""
        f = StringIO(content)
        with pytest.raises(TypeError, match="user_name"):
            MarkdownTable(f, "", Aliased).read()

    def test_read_single_field_alias(self):
        """Only one field has a ``name=``; the others use Python name."""

        class Aliased(msgspec.Struct):
            user_name: str = msgspec.field(name="username")
            age: int

        content = """\
| username | age |
|----------|-----|
| Alice | 30 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Aliased).read()
        assert table.rows[0].user_name == "Alice"
        assert table.rows[0].age == 30

    def test_read_single_field_alias_wrong_header_raises(self):
        """Using Python name for the aliased field raises TypeError."""

        class Aliased(msgspec.Struct):
            user_name: str = msgspec.field(name="username")
            age: int

        content = """\
| user_name | age |
|-----------|-----|
| Alice | 30 |
"""
        f = StringIO(content)
        with pytest.raises(TypeError, match="user_name"):
            MarkdownTable(f, "", Aliased).read()

    def test_read_mixed_aliased_and_plain(self):
        """Some fields aliased, some default — each matched by encode_name."""

        class Mixed(msgspec.Struct):
            given_name: str = msgspec.field(name="givenName")
            family_name: str

        content = """\
| givenName | family_name |
|-----------|-------------|
| Alice | Smith |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Mixed).read()
        assert table.rows[0].given_name == "Alice"
        assert table.rows[0].family_name == "Smith"
