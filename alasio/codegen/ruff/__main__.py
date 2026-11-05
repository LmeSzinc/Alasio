"""
Ruff wrapper for alasio.

Supports wildcards and glob patterns.
"""
import argparse
import glob
import os
import sys

from alasio.backport.patch import patch_startup
from alasio.codegen.ruff.ruff_format import RuffFormatter, is_valid_module, is_valid_py
from alasio.ext.path import PathStr
from alasio.ext.path.calc import get_stem

patch_startup()


def expand_paths(paths, cwd=None):
    """
    Expand glob patterns, preserving order.

    Args:
        paths (list[str]): File paths or glob patterns
        cwd (str): Base directory of relative paths and patterns.
            Defaults to the current working directory.
    """
    seen = set()
    for p in paths:
        if cwd and not os.path.isabs(p):
            p = os.path.join(cwd, p)
        matches = glob.glob(p, recursive=False)
        if matches:
            for m in matches:
                abspath = os.path.abspath(m)
                if abspath not in seen:
                    seen.add(abspath)
                    yield abspath
        else:
            abspath = os.path.abspath(p)
            if abspath not in seen:
                seen.add(abspath)
                yield abspath


def iter_files(paths, cwd=None):
    """
    Expand glob patterns, and drop invalid files

    Args:
        paths (list[str]): File paths or glob patterns
        cwd (str): Base directory of relative paths and patterns.
            Defaults to the current working directory.
    """
    for file in expand_paths(paths, cwd=cwd):
        file = PathStr.new(file)
        if not is_valid_py(file):
            continue
        stem = get_stem(file)
        if not is_valid_module(stem):
            continue
        yield file


def main():
    parser = argparse.ArgumentParser(
        prog='python -m alasio.codegen.ruff',
        description='Ruff wrapper for alasio.',
    )
    parser.add_argument('files', nargs='*', metavar='file',
                        help='Python files to format (supports wildcards and glob patterns)')
    parser.add_argument('--cwd', default=None, metavar='dir',
                        help='Working directory. Relative paths and glob patterns are resolved '
                             'against it, and project modules are detected there. '
                             'Defaults to the project root or the current working directory.')
    args = parser.parse_args()

    if not args.files:
        parser.print_help()
        sys.exit(1)

    files = list(iter_files(args.files, cwd=args.cwd))
    print(f'Ruff format {len(files)} files')
    ruff = RuffFormatter(cwd=args.cwd)
    for fp in files:
        try:
            ruff.format_code(fp)
        except FileNotFoundError:
            print(f'File not found: {fp}')


if __name__ == '__main__':
    main()
