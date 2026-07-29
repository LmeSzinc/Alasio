from io import StringIO

import msgspec
from msgspec import Meta
from typing_extensions import Annotated

from alasio.codegen.markdown.table import MarkdownTable
from alasio.logger import logger


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
        with logger.mock_capture_writer() as capture:
            table.write()
            assert capture.fd.any_contains("Write file")
            assert capture.stdout.any_contains("Write file")

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
        with logger.mock_capture_writer() as capture:
            table.write()
            assert capture.fd.any_contains("Write file")
            assert capture.stdout.any_contains("Write file")

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
        with logger.mock_capture_writer() as capture:
            table.write()
            assert capture.fd.any_contains("Write file")
            assert capture.stdout.any_contains("Write file")

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
        with logger.mock_capture_writer() as capture:
            table.write()
            assert capture.fd.any_contains("Write file")
            assert capture.stdout.any_contains("Write file")

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
        with logger.mock_capture_writer() as capture:
            table.write()
            assert capture.fd.any_contains("Write file")
            assert capture.stdout.any_contains("Write file")

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
        with logger.mock_capture_writer() as capture:
            table.write()
            assert capture.fd.any_contains("Write file")
            assert capture.stdout.any_contains("Write file")

        expected = """\
|       name       | age |
|:----------------:|-----|
|      Alice       | 30  |
"""
        assert f.getvalue() == expected

    def test_display_width_cjk_auto_width(self):
        """CJK chars count as 2 display units for auto-width."""

        class Model(msgspec.Struct):
            name: str
            age: int

        content = """\
| name | age |
|------|-----|
| 你好世界 | 30 |
| a | 25 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Model).read()
        with logger.mock_capture_writer() as capture:
            table.write()
            assert capture.fd.any_contains("Write file")
            assert capture.stdout.any_contains("Write file")
        # Column width = max(4, 8, 1) = 8 -> ljust pads CJK (len=4) to 8
        expected = """\
| name     | age |
|----------|-----|
| 你好世界 | 30  |
| a        | 25  |
"""
        assert f.getvalue() == expected

    def test_display_width_cjk_fixed_width(self):
        """CJK content with fixed width — padding based on display width."""

        class Model(msgspec.Struct):
            name: Annotated[str, Meta(extra={"width": 10})]
            age: int

        content = """\
| name | age |
|------|-----|
| 你好 | 30 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Model).read()
        with logger.mock_capture_writer() as capture:
            table.write()
            assert capture.fd.any_contains("Write file")
            assert capture.stdout.any_contains("Write file")
        # "你好" display width = 4, fixed width = 10 -> padding = 6
        expected = """\
| name       | age |
|------------|-----|
| 你好       | 30  |
"""
        assert f.getvalue() == expected

    def test_display_width_cjk_overflows_fixed(self):
        """CJK content wider than fixed width overflows."""

        class Model(msgspec.Struct):
            name: Annotated[str, Meta(extra={"width": 4})]
            age: int

        content = """\
| name | age |
|------|-----|
| 你好世界 | 30 |
| a | 25 |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Model).read()
        with logger.mock_capture_writer() as capture:
            table.write()
            assert capture.fd.any_contains("Write file")
            assert capture.stdout.any_contains("Write file")
        # "你好世界" display width = 8 > 4 -> overflow
        # "a" display width = 1 < 4 -> padded to 4
        # "name" display width = 4 = width -> exact fit
        expected = """\
| name | age |
|------|-----|
| 你好世界 | 30  |
| a    | 25  |
"""
        assert f.getvalue() == expected

    def test_display_width_mixed_content(self):
        """Mixed ASCII + CJK content aligns correctly."""

        class Model(msgspec.Struct):
            mixed: Annotated[str, Meta(extra={"width": 12})]

        content = """\
| mixed |
|-------|
| hello你好 |
| abc |
"""
        f = StringIO(content)
        table = MarkdownTable(f, "", Model).read()
        with logger.mock_capture_writer() as capture:
            table.write()
            assert capture.fd.any_contains("Write file")
            assert capture.stdout.any_contains("Write file")
        # "hello你好" = 5 + 4 = 9 display width, padding = 3
        # "abc" = 3 display width, padding = 9
        expected = """\
| mixed        |
|--------------|
| hello你好    |
| abc          |
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
        with logger.mock_capture_writer() as capture:
            table.write()
            assert capture.fd.any_contains("Write file")
            assert capture.stdout.any_contains("Write file")
        # width=1 -> clamped to 3; separator = 5 dashes
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
        with logger.mock_capture_writer() as capture:
            table.write()
            assert capture.fd.any_contains("Write file")
            assert capture.stdout.any_contains("Write file")
        # auto max=1 -> clamped to 3; separator = 5 dashes
        expected = """\
| x   |
|-----|
| a   |
"""
        assert f.getvalue() == expected
