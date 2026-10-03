"""
Tests of {nav}_model.py generation (ConfigGenerator.model_gen).

A literal arg ("static" / "select" / "radio" / "secondary-select" /
"filter-order") is not inlined on the field anymore: each literal is a module
level variable emitted before the class that first uses it, and identical
literals of one file share one variable. Different files are independent.
"""

import msgspec
import pytest

from alasio.config.entry.const import ModEntryInfo
from alasio.config_dev.gen.gen_config import ConfigGenerator
from alasio.config_dev.parse import parse_groups
from alasio.config_dev.parse.base import DefinitionError
from alasio.ext.path import PathStr
from alasio.testing.filesystem import fs  # noqa: F401

YAML_MAIN = """\
TestGroup:
  args:
    Mode:
      value: normal
      option: [normal, hard]
    Same:
      value: hard
      option: [normal, hard]
    Order:
      dt: filter-order
      value: A > B
      option: [A, B, C]
    Const:
      dt: static
      value: campaign_main
    Free:
      dt: filter
      value: A > B

OtherGroup:
  args:
    Mode:
      value: 2-1
      option: [2-1, 2-2]
    Table:
      dt: secondary-select
      value: 1-1
      option_dict:
        chapter1: [1-1, 1-2]
        chapter2: [2-1, 2-2]
"""


def make_parser(fs, nav, yaml):
    """
    Create a ConfigGenerator of a nav on the fake filesystem

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture
        nav (str): Nav name
        yaml (str): Content of {nav}.args.yaml

    Returns:
        ConfigGenerator:
    """
    root = fs.root_dir.path.rstrip('/\\')
    entry = ModEntryInfo(name='test', root=root, path_config='module/config')
    file = f'{root}/module/config/{nav}/{nav}.args.yaml'
    fs.create_file(file, contents=yaml)
    return ConfigGenerator(entry, PathStr.new(file))


class TestModelGenLiteral:
    def test_literal_variables(self, fs):
        """Literal args reference module level variables of the file."""
        parser = make_parser(fs, 'testnav', YAML_MAIN)
        code = parser.model_gen.generate_str()
        expected = """\
import typing as t

import alasio.config.alasio.group_export as a
import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

LITERAL_TestGroup_Mode = t.Literal['normal', 'hard']
LITERAL_TestGroup_Order = t.Literal['A', 'B', 'C']
LITERAL_TestGroup_Const = t.Literal['campaign_main']


class TestGroup(a.GroupBase):
    Mode: LITERAL_TestGroup_Mode = 'normal'
    Same: LITERAL_TestGroup_Mode = 'hard'
    Order: t.Tuple[LITERAL_TestGroup_Order, ...] = ('A', 'B')
    Const: LITERAL_TestGroup_Const = 'campaign_main'
    Free: a.T_TUPLE_STR = ('A', 'B')


LITERAL_OtherGroup_Mode = t.Literal['2-1', '2-2']
LITERAL_OtherGroup_Table = t.Literal['1-1', '1-2', '2-1', '2-2']


class OtherGroup(a.GroupBase):
    Mode: LITERAL_OtherGroup_Mode = '2-1'
    Table: LITERAL_OtherGroup_Table = '1-1'
"""
        assert code == expected

    def test_option_dict_rows(self, fs):
        """A long option_dict is expanded with one row per option group."""
        parser = make_parser(fs, 'testnav', """\
Group:
  args:
    Table:
      dt: secondary-select
      value: chapter1-1
      option_dict:
        chapter1: [chapter1-1, chapter1-2, chapter1-3, chapter1-4]
        chapter2: [chapter2-1, chapter2-2, chapter2-3, chapter2-4]
""")
        code = parser.model_gen.generate_str()
        expected = """\
import typing as t

import alasio.config.alasio.group_export as a
import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

LITERAL_Group_Table = t.Literal[
    'chapter1-1', 'chapter1-2', 'chapter1-3', 'chapter1-4',
    'chapter2-1', 'chapter2-2', 'chapter2-3', 'chapter2-4',
]


class Group(a.GroupBase):
    Table: LITERAL_Group_Table = 'chapter1-1'
"""
        assert code == expected

    def test_cross_file_independence(self, fs):
        """The same literal of two navs is defined in each model file."""
        yaml = """\
Group:
  args:
    Mode:
      value: normal
      option: [normal, hard]
"""
        code_a = make_parser(fs, 'nava', yaml).model_gen.generate_str()
        code_b = make_parser(fs, 'navb', yaml).model_gen.generate_str()
        assert "LITERAL_Group_Mode = t.Literal['normal', 'hard']" in code_a
        assert code_a == code_b

    def test_empty_group_is_skipped(self, fs):
        """An empty group has no class and reserves no name."""
        parser = make_parser(fs, 'testnav', """\
EmptyGroup:

FullGroup:
  args:
    Mode:
      value: normal
      option: [normal, hard]
""")
        code = parser.model_gen.generate_str()
        expected = """\
import typing as t

import alasio.config.alasio.group_export as a
import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

LITERAL_FullGroup_Mode = t.Literal['normal', 'hard']


class FullGroup(a.GroupBase):
    Mode: LITERAL_FullGroup_Mode = 'normal'
"""
        assert code == expected

    def test_header_without_literal(self, fs):
        """The header has the same 2 blank lines when a class follows directly."""
        parser = make_parser(fs, 'testnav', """\
PlainGroup:
  args:
    Mode:
      value: normal
""")
        code = parser.model_gen.generate_str()
        expected = """\
import typing as t

import alasio.config.alasio.group_export as a
import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

class PlainGroup(a.GroupBase):
    Mode: str = 'normal'
"""
        assert code == expected

    def test_generated_model_works(self, fs):
        """The generated file executes, the model validates like before."""
        parser = make_parser(fs, 'testnav', YAML_MAIN)
        code = parser.model_gen.generate_str()
        namespace = {}
        exec(compile(code, 'testnav_model.py', 'exec'), namespace)

        group = namespace['TestGroup']
        assert group.get_option('Mode') == ('normal', 'hard')

        obj = group()
        assert obj.Mode == 'normal'
        assert obj.Same == 'hard'
        assert obj.Order == ('A', 'B')

        obj = msgspec.convert({'Mode': 'hard', 'Order': ['C', 'A']}, group)
        assert obj.Mode == 'hard'
        assert obj.Order == ('C', 'A')

        # filter-order value is limited to "option" by the literal
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert({'Order': ['A', 'D']}, group)


class TestModelGenNameConflict:
    def test_group_arg_name_conflict(self, fs, monkeypatch):
        """
        The joined name of two group.arg pairs may collide, e.g.
        "A_B"."C" and "A"."B_C" both want "LITERAL_A_B_C".

        Group and arg names can not contain "_" today, the name validator is
        patched to simulate a relaxed rule.
        """
        monkeypatch.setattr(parse_groups, 'validate_task_name', lambda name: True)
        parser = make_parser(fs, 'testnav', """\
A:
  args:
    B_C:
      value: x
      option: [x, y]

A_B:
  args:
    C:
      value: 1
      option: [1, 2]
""")
        with pytest.raises(DefinitionError) as e:
            _ = parser.model_gen
        assert 'Literal variable name conflict: "LITERAL_A_B_C"' in str(e.value)
        assert "keys=['A_B', 'C']" in str(e.value)
