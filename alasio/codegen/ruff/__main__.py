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


def expand_paths(paths):
    """Expand glob patterns, preserving order."""
    seen = set()
    for p in paths:
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


def iter_files(paths):
    """
    Expand glob patterns, and drop invalid files
    """
    for file in expand_paths(paths):
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
    args = parser.parse_args()

    if not args.files:
        parser.print_help()
        sys.exit(1)

    files = list(iter_files(args.files))
    print(f'Ruff format {len(files)} files')
    ruff = RuffFormatter()
    for fp in files:
        try:
            ruff.format_code(fp)
        except FileNotFoundError:
            print(f'File not found: {fp}')


if __name__ == '__main__':
    main()
