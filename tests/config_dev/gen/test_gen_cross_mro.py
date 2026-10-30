"""
Tests of CrossNavGenerator.dict_group_mro() error reporting.

Invalid inheritance used to escape MRO building as a bare TypeError without
the source file or group. The generator now wraps it into DefinitionError.
"""

import pytest

from alasio.config_dev.gen.gen_cross import CrossNavGenerator
from alasio.config_dev.parse.base import DefinitionError
from alasio.config_dev.parse.parse_groups import GroupData
from alasio.testing.filesystem import fs  # noqa: F401
from tests.config_dev.parse.parse_groups.helpers import make_parser


def make_generator(fs, args_yaml):
    parser = make_parser(fs, 'main', args_yaml=args_yaml)
    generator = object.__new__(CrossNavGenerator)
    generator.groups_data = parser.groups_data
    return generator, parser


class TestDictGroupMro:
    def test_missing_parent_is_reported_without_parser(self):
        group = GroupData.from_group_data('Group', {'parent': 'Missing'})
        generator = object.__new__(CrossNavGenerator)
        generator.groups_data = {'Group': group}
        with pytest.raises(DefinitionError) as e:
            _ = generator.dict_group_mro
        assert e.value.msg == 'Invalid group parent: "Missing", no such group'
        assert e.value.file == ''
        assert e.value.keys == ['Group', 'parent']
        assert e.value.value == 'Missing'

    def test_cycle_is_reported_with_file_and_group(self, fs):
        generator, parser = make_generator(fs, """\
A:
  parent: B
B:
  parent: A
""")
        with pytest.raises(DefinitionError) as e:
            _ = generator.dict_group_mro
        assert e.value.msg == (
            'Invalid group inheritance: '
            'Cycle detected in inheritance hierarchy: A -> B -> A'
        )
        assert e.value.file == parser.file
        assert e.value.keys == ['A', 'parent']
        assert e.value.value == ('B',)

    def test_inconsistent_hierarchy_is_reported_with_file_and_group(self, fs):
        generator, parser = make_generator(fs, """\
X:
Y:
A:
  parent: [X, Y]
B:
  parent: [Y, X]
Z:
  parent: [A, B]
""")
        with pytest.raises(DefinitionError) as e:
            _ = generator.dict_group_mro
        assert e.value.msg == (
            "Invalid group inheritance: Cannot create a consistent MRO for class 'Z'. "
            "Remaining merge lists: [['X', 'Y'], ['Y', 'X']]"
        )
        assert e.value.file == parser.file
        assert e.value.keys == ['Z', 'parent']
        assert e.value.value == ('A', 'B')
