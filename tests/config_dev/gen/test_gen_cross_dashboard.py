"""
Tests of the dashboard detection in CrossNavGenerator.build_group_mro().

The `dashboard:` yaml key is deprecated. A group becomes a dashboard item when
its MRO contains a `Dashboard{Type}` group, and the nearest Dashboard ancestor
(from the root ancestor to the group itself) defines the type. The Dashboard*
groups of alasio/config/alasio/store.args.yaml also provide the Time arg and
the base Value constraints, so build_group_mro() merges the whole chain.
"""

from msgspec import UNSET

from alasio.config.alasio.group_base import DEFAULT_TIME
from alasio.config_dev.gen.gen_cross import CrossNavGenerator
from alasio.config_dev.parse.build_mro import build_mro
from alasio.config_dev.parse.parse_groups import GroupData


def make_group(name, data=None):
    """
    Build a GroupData as ParseGroups does, including override_args.

    Args:
        name (str): Group name
        data (dict): Group body, same as the yaml body of the group

    Returns:
        GroupData: Parsed group
    """
    group = GroupData.from_group_data(name, data or {})
    group.override_args = group.args.copy()
    return group


def build_groups(*groups):
    """
    Run CrossNavGenerator.build_group_mro() over a set of groups.

    Args:
        *groups (GroupData): Groups to build

    Returns:
        dict[str, GroupData]: The same groups, keyed by name
    """
    groups = {group.name: group for group in groups}
    generator = object.__new__(CrossNavGenerator)
    generator.groups_data = groups
    generator.dict_group_mro = build_mro({name: group.parent for name, group in groups.items()})
    generator.tasks_data = {}
    generator.build_group_mro()
    return groups


class TestDashboardType:
    def test_type_comes_from_dashboard_ancestor(self):
        groups = build_groups(
            make_group('DashboardBase', {'args': {'Time': DEFAULT_TIME}}),
            make_group('DashboardAmount', {
                'parent': 'DashboardBase',
                'args': {'Value': {'value': 0, 'range': 'x>=0'}},
            }),
            make_group('DashboardTotal', {'parent': 'DashboardAmount'}),
            make_group('Oil', {
                'parent': 'DashboardTotal',
                'args': {'Value': {'value': 0, 'range': '0~25000'}},
            }),
            make_group('Gems', {'parent': 'DashboardAmount'}),
        )
        assert groups['DashboardAmount'].dashboard == 'Amount'
        assert groups['DashboardTotal'].dashboard == 'Total'
        assert groups['Oil'].dashboard == 'Total'
        assert groups['Gems'].dashboard == 'Amount'

    def test_args_are_merged_from_the_chain(self):
        groups = build_groups(
            make_group('DashboardBase', {'args': {'Time': DEFAULT_TIME}}),
            make_group('DashboardAmount', {
                'parent': 'DashboardBase',
                'args': {'Value': {'value': 0, 'range': 'x>=0'}},
            }),
            make_group('DashboardTotal', {'parent': 'DashboardAmount'}),
            make_group('Oil', {
                'parent': 'DashboardTotal',
                'args': {'Value': {'value': 0, 'range': '0~25000'}},
            }),
        )
        oil = groups['Oil']
        assert oil.mro == ('Oil', 'DashboardTotal', 'DashboardAmount', 'DashboardBase')
        assert list(oil.args) == ['Time', 'Value']
        assert oil.args['Time'].dt == 'datetime'
        assert oil.args['Time'].value == DEFAULT_TIME
        assert oil.args['Value'].value == 0
        assert oil.args['Value'].ge == 0
        assert oil.args['Value'].le == 25000

    def test_nearest_dashboard_ancestor_wins(self):
        groups = build_groups(
            make_group('DashboardBase', {'args': {'Time': DEFAULT_TIME}}),
            make_group('DashboardAmount', {'parent': 'DashboardBase'}),
            make_group('DashboardTotal', {'parent': 'DashboardAmount'}),
            make_group('Item', {'parent': ['DashboardTotal', 'DashboardAmount']}),
        )
        assert groups['Item'].mro == (
            'Item', 'DashboardTotal', 'DashboardAmount', 'DashboardBase',
        )
        assert groups['Item'].dashboard == 'Total'

    def test_dashboard_base_gets_its_own_type(self):
        """A Dashboard* group matches on its own name too, not only a parent."""
        groups = build_groups(
            make_group('DashboardBase', {'args': {'Time': DEFAULT_TIME}}),
            make_group('DashboardProgress', {
                'parent': 'DashboardBase',
                'args': {'Value': {'value': 0, 'range': 'x>=0'}},
            }),
        )
        assert groups['DashboardProgress'].dashboard == 'Progress'


class TestDeprecatedDashboardKey:
    def test_key_is_ignored_by_the_group_parser(self):
        group = make_group('Progress', {'dashboard': 'progress', 'args': {'Value': 0}})
        assert group.dashboard == ''
        assert list(group.args) == ['Value']

    def test_not_a_dashboard_without_dashboard_ancestor(self):
        groups = build_groups(
            make_group('DashboardBase', {'args': {'Time': DEFAULT_TIME}}),
            make_group('Progress', {'dashboard': 'progress', 'args': {'Value': 0}}),
        )
        progress = groups['Progress']
        assert progress.mro == ('Progress',)
        assert progress.dashboard == ''
        assert list(progress.args) == ['Value']
        assert progress.args['Value'].dt == 'input-int'
        assert progress.args['Value'].ge is UNSET
