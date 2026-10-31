"""
The tables of a flat page: one version as a matrix, a project as a list of files.

A version of a compiled extension is a matrix: the platforms are its rows and
the python/abi pairs of its wheels are its columns. The wheels of pure python
have no platform, so they are the last row (`any`) of the same matrix, merged
across the columns: they install where a wheel of a platform does not exist.

A project of pure python wheels has nothing to spread: every version is one
line of two cells, the version and the file. Such a table covers every version
of a project, where the matrix covers one version of it.

The two builders read the links and answer what the page shows; they do not
read the page and they do not register anything, so a caller can look at the
numbers of a table before a section of the page exists.

Usage:
    from alasio.codegen.pypisite.flat import build_version

    table = build_version(links)
    table.columns   # ['cp38', 'cp39']
    table.rows      # [(('win_amd64',), 'win_amd64')]
"""

from msgspec import Struct

from ..wheel import WheelLink, split_by_version, unique_links
from .tags import column_key, column_sort_key, is_pure, parse_tags, row_display, row_key, row_sort_key, tag_label

__all__ = [
    'FlatCell',
    'VersionTable',
    'build_pure_table',
    'build_version',
]


class FlatCell(Struct, frozen=True):
    """
    One wheel of a table cell.

    Attributes:
        label (str): Text the cell shows: the column of a matrix, the tags of a
            wheel of pure python
        link (WheelLink): The wheel the cell links
        abi3 (bool): True for a stable abi wheel, which installs on the labelled
            python version and every newer one
    """

    label: str
    link: WheelLink
    abi3: bool = False


class VersionTable(Struct, frozen=True):
    """
    The matrix of one version.

    Attributes:
        columns (list): Column keys, in the order the page shows them
        rows (list): (row_key, display) of the platforms, in page order
        cells (dict): {(row_key, column): [FlatCell]}, the files of a cell in
            file name order
        pure (list): [FlatCell] of the `any` row, empty when the version ships
            no wheel of pure python
        wheels (list): The wheels of the version, unique and in file name order
        has_abi3 (bool): True when a cell holds a stable abi wheel
    """

    columns: list
    rows: list
    cells: dict
    pure: list
    wheels: list
    has_abi3: bool = False


def _check_one_project(wheels):
    """
    Check that the wheels belong to one distribution.

    Args:
        wheels (list[WheelLink]): The wheels of a table

    Raises:
        ValueError: If the wheels name two distributions
    """
    projects = {link.key for link in wheels}
    if len(projects) > 1:
        raise ValueError(
            f'The table of a flat page holds the wheels of two distributions: '
            f'{", ".join(sorted(projects))}')


def build_version(links):
    """
    Split the wheels of one version into the matrix and the row of pure python.

    The input is every wheel of the version, the wheels of pure python included:
    a compiled version can ship a fallback wheel that runs anywhere, and that
    wheel belongs to the table (as the `any` row) rather than to a second table.

    Args:
        links (Iterable[WheelLink]): The wheels of one version of a distribution

    Returns:
        VersionTable: The keys of the matrix, the cells, the wheels of pure
            python and the facts the meta line of the page counts

    Raises:
        TypeError: If a link is not a WheelLink
        ValueError: If there is no wheel, if the wheels belong to two
            distributions or two versions, if two of them carry one file name
            with two digests, if a file name is not a wheel, or if no wheel of
            the version belongs to a platform
    """
    wheels = unique_links(links)
    if not wheels:
        raise ValueError('The table of a version has no wheel')
    _check_one_project(wheels)
    versions = {link.version for link in wheels}
    if len(versions) > 1:
        raise ValueError(
            f'The table of a version holds the wheels of two versions: {", ".join(sorted(versions))}')

    columns = set()
    rows = {}
    cells = {}
    pure = []
    has_abi3 = False
    for link in wheels:
        tags = parse_tags(link.filename)
        if is_pure(tags):
            pure.append(FlatCell(tag_label(tags), link))
            continue
        key = row_key(tags)
        rows[key] = row_display(key)
        python_tags, abi_tags, _platform_tags = tags
        for interpreter in python_tags:
            for abi in abi_tags:
                column = column_key(interpreter, abi)
                columns.add(column)
                abi3 = abi == 'abi3'
                has_abi3 = has_abi3 or abi3
                cells.setdefault((key, column), []).append(FlatCell(column, link, abi3))

    if not cells:
        raise ValueError(
            'The compiled table of a version holds no wheel of a platform: the version of pure '
            'python wheels belongs to WheelTablePurePython')

    for entries in cells.values():
        entries.sort(key=lambda cell: cell.link.filename)
    wheels.sort(key=lambda link: link.filename)
    return VersionTable(
        columns=sorted(columns, key=column_sort_key),
        rows=[(key, rows[key]) for key in sorted(rows, key=row_sort_key)],
        cells=cells,
        pure=pure,
        wheels=wheels,
        has_abi3=has_abi3,
    )


def build_pure_table(links):
    """
    Group the wheels of a project of pure python by version, for the two cell table.

    The rows keep the order the caller gave the links in, so a caller that hands
    the versions over from the newest to the oldest gets the table of the page;
    the files of one version are in file name order, so two runs of the same
    links render the same bytes whichever order they arrived in.

    Args:
        links (Iterable[WheelLink]): The wheels of one distribution, any number
            of versions of it

    Returns:
        list: (version, [WheelLink]) per version, in the order of the input

    Raises:
        TypeError: If a link is not a WheelLink
        ValueError: If the wheels belong to two distributions, if two of them
            carry one file name with two digests, if a file name is not a wheel,
            or if a wheel of a platform is in the list
    """
    wheels = unique_links(links)
    _check_one_project(wheels)
    rows = []
    for version, group in split_by_version(wheels).items():
        for link in group:
            tags = parse_tags(link.filename)
            if not is_pure(tags):
                raise ValueError(
                    f'The pure table of "{version}" holds a wheel of a platform: '
                    f'"{link.filename}" belongs to the matrix of its version')
        rows.append((version, sorted(group, key=lambda link: link.filename)))
    return rows
