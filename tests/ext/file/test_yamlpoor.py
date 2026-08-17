import msgspec
import pytest
from msgspec import Meta, Struct
from typing_extensions import Annotated

from alasio.ext.file.yamlpoor import (
    PoorYaml, build_help_map, insert_comments, insert_comments_iter, iter_yaml_rows
)
from alasio.testing.filesystem import fs  # noqa: F401


class Config(Struct):
    """Flat model used in tests."""

    port: int = 8080
    name: str = "server"
    debug: bool = False


class CommentedConfig(Struct):
    """Model with help comments used in tests."""

    port: Annotated[int, Meta(extra={"help": "line 1\nline 2"})] = 8080
    name: Annotated[str, Meta(extra={"help": "server name"})] = "server"


class InnerConfig(Struct):
    """Inner model used in tests."""

    port: Annotated[int, Meta(extra={"help": "inner port"})] = 8080


class OuterConfig(Struct):
    """Outer model used in tests."""

    inner: InnerConfig = msgspec.field(default_factory=InnerConfig)
    name: str = "server"


class MultiLineConfig(Struct):
    """Model with multiline string used in tests."""

    desc: str = "line1\nline2"


class TestBuildHelpMap:
    def test_build_help_map(self):
        help_map = build_help_map(CommentedConfig)
        # Multiline help is parsed by format_i18n into a list of lines
        assert help_map == {("port",): ["line 1", "line 2"], ("name",): "server name"}

    def test_build_help_map_nested(self):
        help_map = build_help_map(OuterConfig)
        assert help_map == {("inner", "port"): "inner port"}

    def test_build_help_map_same_key_different_levels(self):
        class Inner(Struct):
            port: Annotated[int, Meta(extra={"help": "inner port help"})] = 1

        class Outer(Struct):
            port: Annotated[int, Meta(extra={"help": "outer port help"})] = 2
            inner: Inner = msgspec.field(default_factory=Inner)

        help_map = build_help_map(Outer)
        assert help_map == {
            ("port",): "outer port help",
            ("inner", "port"): "inner port help",
        }

    def test_build_help_map_help_list(self):
        class Model(Struct):
            port: Annotated[int, Meta(extra={"help": ["line 1", "line 2"]})] = 8080

        assert build_help_map(Model) == {("port",): ["line 1", "line 2"]}

    def test_no_help_fields(self):
        assert build_help_map(Config) == {}


class TestIterYamlRows:
    def test_basic(self):
        rows = list(iter_yaml_rows("""\
port: 8080
name: server
"""))
        assert rows == [
            (("port",), "port: 8080"),
            (("name",), "name: server"),
        ]

    def test_nested(self):
        rows = list(iter_yaml_rows("""\
outer:
  port: 8080
"""))
        assert rows == [
            (("outer",), "outer:"),
            (("outer", "port"), "  port: 8080"),
        ]

    def test_same_key_different_levels(self):
        rows = list(iter_yaml_rows("""\
port: 1
inner:
  port: 2
"""))
        assert rows == [
            (("port",), "port: 1"),
            (("inner",), "inner:"),
            (("inner", "port"), "  port: 2"),
        ]

    def test_block_scalar_content_none(self):
        rows = list(iter_yaml_rows("""\
desc: |-
  line1
  line2
port: 8080
"""))
        assert rows == [
            (("desc",), "desc: |-"),
            (None, "  line1"),
            (None, "  line2"),
            (("port",), "port: 8080"),
        ]

    def test_comment_and_empty_none(self):
        rows = list(iter_yaml_rows("""\
# comment

port: 8080
"""))
        assert rows == [
            (None, "# comment"),
            (None, ""),
            (("port",), "port: 8080"),
        ]


class TestInsertCommentsIter:
    def test_comment_before_key(self):
        rows = iter_yaml_rows("""\
port: 8080
""")
        out = list(insert_comments_iter(rows, {("port",): "port help"}))
        assert out == ["# port help", "port: 8080"]

    def test_multiline_help(self):
        rows = iter_yaml_rows("""\
port: 8080
""")
        out = list(insert_comments_iter(rows, {("port",): ["line 1", "line 2"]}))
        assert out == ["# line 1", "# line 2", "port: 8080"]

    def test_no_help_key(self):
        rows = iter_yaml_rows("""\
port: 8080
""")
        out = list(insert_comments_iter(rows, {}))
        assert out == ["port: 8080"]

    def test_indent_preserved(self):
        rows = iter_yaml_rows("""\
outer:
  port: 8080
""")
        out = list(insert_comments_iter(rows, {("outer", "port"): "port help"}))
        assert out == ["outer:", "  # port help", "  port: 8080"]

    def test_unknown_key_untouched(self):
        rows = iter_yaml_rows("""\
name: server
""")
        out = list(insert_comments_iter(rows, {("port",): "port help"}))
        assert out == ["name: server"]


class TestInsertComments:
    def test_insert_above_key(self):
        text = """\
port: 8080
name: server
"""
        help_map = {("port",): ["line 1", "line 2"]}
        assert insert_comments(text, help_map) == """\
# line 1
# line 2
port: 8080
name: server
"""

    def test_no_help_map(self):
        text = """\
port: 8080
"""
        assert insert_comments(text, {}) == text

    def test_indent_preserved(self):
        text = """\
outer:
  port: 8080
"""
        help_map = {("outer", "port"): "port help"}
        assert insert_comments(text, help_map) == """\
outer:
  # port help
  port: 8080
"""

    def test_existing_comment_kept(self):
        text = """\
# existing
port: 8080
"""
        help_map = {("port",): "port help"}
        assert insert_comments(text, help_map) == """\
# existing
# port help
port: 8080
"""

    def test_empty_lines_kept(self):
        text = """\
port: 8080

name: server
"""
        help_map = {("name",): "name help"}
        assert insert_comments(text, help_map) == """\
port: 8080

# name help
name: server
"""

    def test_key_prefix_no_collision(self):
        text = """\
a: 1
a1: 2
"""
        help_map = {("a",): "a help", ("a1",): "a1 help"}
        assert insert_comments(text, help_map) == """\
# a help
a: 1
# a1 help
a1: 2
"""

    def test_comment_not_inserted_inside_multiline_string(self):
        text = """\
desc: |-
  line1
  line2
"""
        help_map = {("desc",): "desc help"}
        assert insert_comments(text, help_map) == """\
# desc help
desc: |-
  line1
  line2
"""

    def test_same_key_different_levels(self):
        text = """\
port: 1
inner:
  port: 2
"""
        help_map = {("port",): "top port", ("inner", "port"): "inner port"}
        assert insert_comments(text, help_map) == """\
# top port
port: 1
inner:
  # inner port
  port: 2
"""

    def test_block_scalar_content_not_tracked_as_key(self):
        text = """\
desc: |-
  key: not a real key
port: 8080
"""
        help_map = {("port",): "port help"}
        assert insert_comments(text, help_map) == """\
desc: |-
  key: not a real key
# port help
port: 8080
"""

    def test_block_scalar_with_comment(self):
        text = """\
desc: | # comment
  key: not a real key
port: 8080
"""
        help_map = {("port",): "port help"}
        assert insert_comments(text, help_map) == """\
desc: | # comment
  key: not a real key
# port help
port: 8080
"""


class TestPoorYamlInit:
    def test_model_not_struct(self):
        with pytest.raises(TypeError, match="msgspec.Struct"):
            PoorYaml("config.yaml", dict)

    def test_model_not_default_constructible(self, fs):
        class NoDefault(Struct):
            port: int

        with pytest.raises(ValueError, match="default constructible"):
            PoorYaml('/config.yaml', NoDefault)


class TestPoorYamlHelpMap:
    def test_help_map(self, fs):
        config = PoorYaml('/config.yaml', CommentedConfig)
        assert config.help_map == {("port",): ["line 1", "line 2"], ("name",): "server name"}

    def test_help_map_cached(self, fs):
        config = PoorYaml('/config.yaml', CommentedConfig)
        assert config.help_map is config.help_map

    def test_help_map_no_help(self, fs):
        config = PoorYaml('/config.yaml', Config)
        assert config.help_map == {}

    def test_help_map_same_key_different_levels(self, fs):
        class Inner(Struct):
            port: Annotated[int, Meta(extra={"help": "inner port help"})] = 1

        class Outer(Struct):
            port: Annotated[int, Meta(extra={"help": "outer port help"})] = 2
            inner: Inner = msgspec.field(default_factory=Inner)

        config = PoorYaml('/config.yaml', Outer)
        assert config.help_map == {
            ("port",): "outer port help",
            ("inner", "port"): "inner port help",
        }


class TestPoorYamlRead:
    def test_missing_file_returns_defaults(self, fs):
        config = PoorYaml('/config.yaml', Config)
        assert config.data == Config()
        assert config.errors == []

    def test_read_values(self, fs):
        fs.create_file('/config.yaml', contents="""\
# comment
port: 9090
name: custom
""")
        config = PoorYaml('/config.yaml', Config)
        assert config.data.port == 9090
        assert config.data.name == "custom"
        assert config.errors == []

    def test_read_attribute_access(self, fs):
        fs.create_file('/config.yaml', contents="""\
port: 9090
""")
        config = PoorYaml('/config.yaml', Config)
        assert config.data.port == 9090

    def test_read_invalid_value_falls_back_to_default(self, fs):
        fs.create_file('/config.yaml', contents="""\
port: not-a-number
name: custom
""")
        config = PoorYaml('/config.yaml', Config)
        assert config.data.port == 8080
        assert config.data.name == "custom"
        assert config.errors

    def test_read_unknown_key_ignored(self, fs):
        fs.create_file('/config.yaml', contents="""\
unknown: 1
port: 9090
""")
        config = PoorYaml('/config.yaml', Config)
        assert config.data.port == 9090

    def test_read_invalid_yaml(self, fs):
        fs.create_file('/config.yaml', contents="""\
port: [unclosed
""")
        config = PoorYaml('/config.yaml', Config)
        assert config.data == Config()

    def test_read_non_utf8(self, fs):
        # Invalid utf-8 bytes can't be expressed with multiline string
        fs.create_file('/config.yaml', contents=b"port: \xff\xfe\n")
        config = PoorYaml('/config.yaml', Config)
        assert config.data == Config()

    def test_read_bom(self, fs):
        # BOM bytes can't be expressed with multiline string
        fs.create_file('/config.yaml', contents=b"\xef\xbb\xbfport: 9090\n")
        config = PoorYaml('/config.yaml', Config)
        assert config.data.port == 9090

    def test_read_numeric_string_keeps_type(self, fs):
        fs.create_file('/config.yaml', contents="""\
name: '8080'
""")
        config = PoorYaml('/config.yaml', Config)
        assert config.data.name == "8080"

    def test_read_list_value(self, fs):
        class ListConfig(Struct):
            ports: list = msgspec.field(default_factory=list)

        fs.create_file('/config.yaml', contents="""\
ports: [4, 5, 6]
""")
        config = PoorYaml('/config.yaml', ListConfig)
        assert config.data.ports == [4, 5, 6]

    def test_read_nested_struct(self, fs):
        fs.create_file('/config.yaml', contents="""\
inner:
  port: 9090
name: custom
""")
        config = PoorYaml('/config.yaml', OuterConfig)
        assert config.data == OuterConfig(inner=InnerConfig(port=9090), name="custom")

    def test_read_multiline_string(self, fs):
        fs.create_file('/config.yaml', contents="""\
desc: |-
  hello
  world
""")
        config = PoorYaml('/config.yaml', MultiLineConfig)
        assert config.data.desc == "hello\nworld"


class TestPoorYamlWrite:
    def test_write_comments(self, fs):
        config = PoorYaml('/config.yaml', CommentedConfig)
        config.write()
        text = open('/config.yaml', encoding="utf-8").read()
        assert text == """\
# line 1
# line 2
port: 8080
# server name
name: server
"""

    def test_write_creates_file(self, fs):
        config = PoorYaml('/config.yaml', Config)
        assert config.write() is True
        assert fs.exists('/config.yaml')

    def test_write_round_trip(self, fs):
        config = PoorYaml('/config.yaml', Config)
        config.data.port = 9090
        config.data.name = "custom"
        config.data.debug = True
        config.write()

        config2 = PoorYaml('/config.yaml', Config)
        assert config2.data == Config(port=9090, name="custom", debug=True)

    def test_write_round_trip_numeric_string(self, fs):
        class StringConfig(Struct):
            port: str = "8080"

        config = PoorYaml('/config.yaml', StringConfig)
        config.write()

        config2 = PoorYaml('/config.yaml', StringConfig)
        assert config2.data == StringConfig(port="8080")

    def test_write_nested_round_trip(self, fs):
        config = PoorYaml('/config.yaml', OuterConfig)
        config.data.inner.port = 9090
        config.write()

        text = open('/config.yaml', encoding="utf-8").read()
        assert text == """\
inner:
  # inner port
  port: 9090
name: server
"""

        config2 = PoorYaml('/config.yaml', OuterConfig)
        assert config2.data == OuterConfig(inner=InnerConfig(port=9090))

    def test_write_multiline_round_trip(self, fs):
        config = PoorYaml('/config.yaml', MultiLineConfig)
        config.data.desc = "hello\nworld"
        config.write()

        config2 = PoorYaml('/config.yaml', MultiLineConfig)
        assert config2.data == MultiLineConfig(desc="hello\nworld")

    def test_write_same_key_different_levels(self, fs):
        class Inner(Struct):
            port: Annotated[int, Meta(extra={"help": "inner port help"})] = 1

        class Outer(Struct):
            port: Annotated[int, Meta(extra={"help": "outer port help"})] = 2
            inner: Inner = msgspec.field(default_factory=Inner)

        config = PoorYaml('/config.yaml', Outer)
        config.write()

        text = open('/config.yaml', encoding="utf-8").read()
        assert text == """\
# outer port help
port: 2
inner:
  # inner port help
  port: 1
"""

        config2 = PoorYaml('/config.yaml', Outer)
        assert config2.data == Outer(port=2, inner=Inner(port=1))

    def test_write_skip_same(self, fs):
        config = PoorYaml('/config.yaml', Config)
        assert config.write() is True
        assert config.write(skip_same=True) is False

    def test_write_skip_same_after_change(self, fs):
        config = PoorYaml('/config.yaml', Config)
        config.write()
        config.data.port = 9090
        assert config.write(skip_same=True) is True

    def test_write_comments_preserved_after_rewrite(self, fs):
        config = PoorYaml('/config.yaml', CommentedConfig)
        config.write()
        config.data.port = 9090
        config.write()
        text = open('/config.yaml', encoding="utf-8").read()
        assert text == """\
# line 1
# line 2
port: 9090
# server name
name: server
"""
