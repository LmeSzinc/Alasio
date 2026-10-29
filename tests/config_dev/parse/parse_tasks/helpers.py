"""
Shared helpers of the parse_tasks tests.

A task fixture is a yaml document written at column 0 (the opening triple
quote is followed by a backslash), so it keeps the layout of a real
{nav}.tasks.yaml.

Usage:
    from tests.config_dev.parse.parse_tasks.helpers import (
        make_definition, make_parser, make_task, yaml_data
    )
"""

import textwrap

import msgspec

from alasio.config_dev.parse.parse_tasks import ParseTasks, TaskData, TaskDefinition
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


def make_definition(text):
    """
    Decode the yaml body of one task with the input schema.

    Args:
        text (str): Yaml document of the task body at column 0

    Returns:
        TaskDefinition: Decoded definition
    """
    return msgspec.convert(yaml_data(text), type=TaskDefinition)


def make_task(text, task='Task'):
    """
    Build a TaskData from the yaml body of one task.

    Args:
        text (str): Yaml document of the task body at column 0
        task (str): Task name

    Returns:
        TaskData: Parsed task
    """
    return TaskData.from_task_definition(task, make_definition(text))


def make_parser(fs, nav='main', tasks_yaml=None):
    """
    Create a ParseTasks of a nav on the fake filesystem.

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture
        nav (str): Nav name
        tasks_yaml (str): Content of {nav}.tasks.yaml, None to leave it missing

    Returns:
        ParseTasks: Parser of the nav
    """
    root = fs.root_dir.path
    file = joinpath(root, f'module/config/{nav}/{nav}.args.yaml')
    fs.create_file(file, contents='')
    parser = ParseTasks(PathStr.new(file))
    if tasks_yaml is not None:
        fs.create_file(parser.tasks_file, contents=textwrap.dedent(tasks_yaml))
    return parser
