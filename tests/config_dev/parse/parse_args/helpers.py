"""
Shared helpers of the parse_args tests.

Every test feeds a multi-line yaml document through the real yaml loader and
the real arg pipeline (populate_arg -> preprocess_arg -> ArgData), so the tests
see exactly what a config author writes in {nav}.args.yaml: shorthand scalars
(`UseLogger: true`) and block scalars (`value: |-`).

The documents are written with a backslash right after the opening triple
quote, so they keep the indentation of the file (column 0) instead of
following the indentation of the test function.

Usage:
    from tests.config_dev.parse.parse_args.helpers import parse_arg
"""

import textwrap

from alasio.config_dev.parse.parse_args import ArgData
from alasio.ext.file.yamlfile import yaml_loads


def parse_arg(text):
    """
    Parse one arg definition written as yaml text.

    Args:
        text (str): Yaml document of an arg, the opening triple quote is
            followed by a backslash so the document stays at column 0

    Returns:
        ArgData: Parsed arg of the document
    """
    data = yaml_loads(textwrap.dedent(text).encode('utf-8'))
    return ArgData.from_arg_data(data)
