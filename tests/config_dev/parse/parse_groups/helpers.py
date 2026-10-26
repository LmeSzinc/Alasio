"""
Shared helpers of the parse_groups tests.

A group fixture is a yaml document written at column 0 (the opening triple
quote is followed by a backslash), so it keeps the layout of a real
{nav}.args.yaml.

The dashboard fixtures parse a multi-line yaml document that contains the
Dashboard* base groups and the concrete groups. A group becomes a dashboard
item when its MRO contains a "Dashboard{Type}" group; this is resolved by
CrossNavGenerator.build_group_mro(), which then calls
GroupData.populate_dashboard() explicitly.

Usage:
    from tests.config_dev.parse.parse_groups.helpers import (
        YAML_DASHBOARD_BASE, make_group, make_parser, parse_dashboard_groups, yaml_data
    )
"""

import textwrap

from alasio.config_dev.gen.gen_cross import CrossNavGenerator
from alasio.config_dev.parse.build_mro import build_mro
from alasio.config_dev.parse.parse_groups import GroupData, ParseGroups
from alasio.ext import env
from alasio.ext.file.yamlfile import yaml_loads
from alasio.ext.path import PathStr
from alasio.ext.path.calc import joinpath

# The real dashboard base groups, read from the store config so a change there
# is exposed by these tests instead of being hidden behind a duplicated copy.
DASHBOARD_STORE_FILE = env.ALASIO_ROOT.joinpath('alasio/config/alasio/store.args.yaml')
YAML_DASHBOARD_BASE = DASHBOARD_STORE_FILE.atomic_read_text().rstrip() + '\n'


def yaml_data(text):
    """
    Parse a yaml document written at column 0.

    Args:
        text (str): Yaml document, the opening triple quote is followed by
            a backslash so the document stays at column 0

    Returns:
        Any: Parsed yaml data
    """
    return yaml_loads(textwrap.dedent(text).encode('utf-8'))


def make_group(name, text=''):
    """
    Build a GroupData from the yaml body of one group.

    Args:
        name (str): Group name
        text (str): Yaml document of the group body at column 0

    Returns:
        GroupData: Parsed group
    """
    return GroupData.from_group_data(name, yaml_data(text))


def parse_dashboard_groups(text):
    """
    Parse a multi-line yaml document of dashboard groups and build their MRO.

    Args:
        text (str): Yaml document at column 0, usually YAML_DASHBOARD_BASE plus
            the concrete groups under test

    Returns:
        dict[str, GroupData]: Groups after CrossNavGenerator.build_group_mro()
    """
    data = yaml_data(text)
    groups = {name: GroupData.from_group_data(name, value) for name, value in data.items()}
    for group in groups.values():
        group.override_args = group.args.copy()

    generator = object.__new__(CrossNavGenerator)
    generator.groups_data = groups
    generator.dict_group_mro = build_mro({name: group.parent for name, group in groups.items()})
    generator.tasks_data = {}
    generator.build_group_mro()
    return groups


def make_parser(fs, nav='main', args_yaml=None, tasks_yaml=None, i18n_json=None):
    """
    Create a ParseGroups of a nav on the fake filesystem.

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture
        nav (str): Nav name
        args_yaml (str): Content of {nav}.args.yaml, None to leave it missing
        tasks_yaml (str): Content of {nav}.tasks.yaml, None to leave it missing
        i18n_json (str): Content of {nav}_i18n.json, None to leave it missing

    Returns:
        ParseGroups: Parser of the nav
    """
    root = fs.root_dir.path
    file = joinpath(root, f'module/config/{nav}/{nav}.args.yaml')
    if args_yaml is not None:
        fs.create_file(file, contents=textwrap.dedent(args_yaml))
    parser = ParseGroups(PathStr.new(file))
    if tasks_yaml is not None:
        fs.create_file(parser.tasks_file, contents=textwrap.dedent(tasks_yaml))
    if i18n_json is not None:
        fs.create_file(parser.i18n_file, contents=textwrap.dedent(i18n_json))
    return parser
