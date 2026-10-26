"""
Shared helpers of the parse_groups tests.

A group fixture is a yaml document written at column 0 (the opening triple
quote is followed by a backslash), so it keeps the layout of a real
{nav}.args.yaml.

The dashboard fixtures build GroupData directly. `dashboard` is an internal
field filled by the MRO build, GroupData.from_group_data() pops it from the
yaml, so the dashboard branches of __post_init__ are only reachable by direct
construction.

Usage:
    from tests.config_dev.parse.parse_groups.helpers import (
        make_dashboard_group, make_group, make_parser, yaml_data
    )
"""

import textwrap

from alasio.config_dev.parse.parse_args import ArgData
from alasio.config_dev.parse.parse_groups import GroupData, ParseGroups
from alasio.ext.file.yamlfile import yaml_loads
from alasio.ext.path import PathStr
from alasio.ext.path.calc import joinpath


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


def make_dashboard_group(dashboard, args=None, name='Group', **kwargs):
    """
    Build a GroupData with an explicit dashboard datatype.

    Args:
        dashboard (str): Dashboard datatype, e.g. "value"
        args (dict): Arg definitions, {arg_name: arg_definition}
        name (str): Group name
        **kwargs: Other GroupData fields, e.g. parent

    Returns:
        GroupData: Parsed group
    """
    args = {arg_name: ArgData.from_arg_data(value) for arg_name, value in (args or {}).items()}
    return GroupData(name=name, dashboard=dashboard, args=args, **kwargs)


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
