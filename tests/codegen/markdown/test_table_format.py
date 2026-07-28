from io import StringIO

import msgspec
from msgspec import Meta
from typing_extensions import Annotated

from alasio.codegen.markdown.table import MarkdownTable


class TestMarkdownTableFormat:

    def test_default_format_left_auto(self):
        """Default formatting: left-aligned, auto width."""

        class Model(msgspec.Struct):
            name: str
            age: int

        content = """\
| name | age |
|------|-----|
| Alice | 30 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Model).read()
        table.write()

        expected = """\
| name  | age |
|-------|-----|
| Alice | 30  |
"""
        assert f.getvalue() == expected

    def test_fixed_width(self):
        """Fixed width makes column wider than auto."""

        class Model(msgspec.Struct):
            name: Annotated[str, Meta(extra={"width": 20})]
            age: int

        content = """\
| name | age |
|------|-----|
| Alice | 30 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Model).read()
        table.write()

        expected = """\
| name                 | age |
|----------------------|-----|
| Alice                | 30  |
"""
        assert f.getvalue() == expected

    def test_fixed_width_content_longer_extends(self):
        """Content wider than fixed width overflows, creating misalignment."""

        class Model(msgspec.Struct):
            name: Annotated[str, Meta(extra={"width": 3})]
            age: int

        content = """\
| name | age |
|------|-----|
| LongLongLongName | 30 |
| A | 25 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Model).read()
        table.write()

        expected = """\
| name | age |
|-----|-----|
| LongLongLongName | 30  |
| A   | 25  |
"""
        assert f.getvalue() == expected

    def test_center_alignment(self):
        """Center-aligned column."""

        class Model(msgspec.Struct):
            name: Annotated[str, Meta(extra={"align": "center"})]
            age: int

        content = """\
| name | age |
|------|-----|
| Alice | 30 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Model).read()
        table.write()

        expected = """\
| name  | age |
|:-----:|-----|
| Alice | 30  |
"""
        assert f.getvalue() == expected

    def test_right_alignment(self):
        """Right-aligned column."""

        class Model(msgspec.Struct):
            name: Annotated[str, Meta(extra={"align": "right"})]
            age: int

        content = """\
| name | age |
|------|-----|
| Alice | 30 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Model).read()
        table.write()

        expected = """\
|  name | age |
|------:|-----|
| Alice | 30  |
"""
        assert f.getvalue() == expected

    def test_mixed_alignment(self):
        """Different alignments per column."""

        class Model(msgspec.Struct):
            left: Annotated[str, Meta(extra={"align": "left"})]
            center: Annotated[str, Meta(extra={"align": "center"})]
            right: Annotated[str, Meta(extra={"align": "right"})]

        content = """\
| left | center | right |
|------|--------|-------|
| a | b | c |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Model).read()
        table.write()

        expected = """\
| left | center | right |
|------|:------:|------:|
| a    |   b    |     c |
"""
        assert f.getvalue() == expected

    def test_fixed_width_with_center_alignment(self):
        """Fixed width combined with center alignment."""

        class Model(msgspec.Struct):
            name: Annotated[str, Meta(extra={"width": 16, "align": "center"})]
            age: int

        content = """\
| name | age |
|------|-----|
| Alice | 30 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Model).read()
        table.write()

        expected = """\
|       name       | age |
|:----------------:|-----|
|      Alice       | 30  |
"""
        assert f.getvalue() == expected


class TestMarkdownTableMinWidth:

    def test_fixed_width_below_minimum_raises_to_3(self):
        """A fixed width below 3 is clamped to 3 for valid separator."""

        class Model(msgspec.Struct):
            x: Annotated[str, Meta(extra={"width": 1})]

        content = """\
| x |
|---|
| a |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Model).read()
        table.write()
        # width=1 → clamped to 3; separator = 5 dashes
        expected = """\
| x   |
|-----|
| a   |
"""
        assert f.getvalue() == expected

    def test_auto_width_short_content_clamped_to_3(self):
        """Auto-width from short content is clamped to 3."""

        class Model(msgspec.Struct):
            x: str

        content = """\
| x |
|---|
| a |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Model).read()
        table.write()
        # auto max=1 → clamped to 3; separator = 5 dashes
        expected = """\
| x   |
|-----|
| a   |
"""
        assert f.getvalue() == expected
