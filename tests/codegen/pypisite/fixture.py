"""
The files a published index served, as the real case tests read them.

A golden test is only worth its bytes when the data it renders is the data a
site really served: the fixtures of this package are the file lists of two
pages, taken once and kept, so a change of the generator shows up as a change
of the page instead of a change of the source.

The format is the one the two files were written in, one record a line:

    # project <name>
    # version <version> <origin text> <origin url>
    <filename> <sha256>
    # plain comment

A file line belongs to the version above it, a version to the project above it.
The origin of a version is the release the wheels of it were published in, and
the files of the first fixture have none: the index of a distribution says
nothing about where the files came from.

Usage:
    from tests.codegen.pypisite import fixture

    entries = fixture.read_entries(fixture.HERE.joinpath('anyio-index.txt'))
    entry.links    # [WheelLink]
"""

from alasio.codegen.pypisite import WheelLink
from alasio.ext.path import PathStr

__all__ = [
    'HERE',
    'Entry',
    'assert_page',
    'read_entries',
]

# the folder the fixtures and the golden pages of the real cases live in
HERE = PathStr.new(__file__).uppath()

# the prefixes of the two record lines, the other '#' lines are prose
_PROJECT = '# project '
_VERSION = '# version '


class Entry:
    """
    One version of one project of a fixture.

    Attributes:
        project (str): Distribution name
        version (str): Exact version
        files (list): [(filename, sha256)] in the order of the fixture
        origin (tuple): (text, url) of the release the version came from, None
            when the fixture names none
    """

    def __init__(self, project, version, origin=None):
        self.project = project
        self.version = version
        self.files = []
        self.origin = origin

    @property
    def links(self):
        """
        The files of the version as links of a page.

        Returns:
            list[WheelLink]: One link per file, in the order of the fixture
        """
        return [
            WheelLink(self.project, self.version, filename, sha256)
            for filename, sha256 in self.files
        ]

    @property
    def pure(self):
        """
        Whether every file of the version is a wheel of pure python.

        The answer reads the file names: a wheel of pure python ends with
        `-none-any.whl`, and a version that ships a source distribution too is
        not a version a matrix of platforms can show.

        Returns:
            bool: True when the version is a version of pure python wheels only
        """
        return all(filename.endswith('-none-any.whl') for filename, _sha256 in self.files)

    def __repr__(self):
        return f'<Entry {self.project}=={self.version} {len(self.files)} file(s)>'


def assert_page(actual, expected):
    """
    Compare a page with the golden page of a test, a line at a time.

    A page of a real index is thousands of lines long, and an assertion on the
    two strings would print all of them: the answer names the first line that
    differs instead, so a change of the generator reads as the line it changed.

    Args:
        actual (str): The page the generator wrote
        expected (str): The page committed with the test

    Raises:
        AssertionError: If the pages differ
    """
    if actual == expected:
        return
    actual_lines = actual.split('\n')
    expected_lines = expected.split('\n')
    for number, (one, other) in enumerate(zip(actual_lines, expected_lines), 1):
        if one != other:
            raise AssertionError(
                f'The page differs from the golden page on line {number}:\n'
                f'  golden: {other!r}\n'
                f'  page:   {one!r}')
    raise AssertionError(
        f'The page has {len(actual_lines)} lines, the golden page has {len(expected_lines)}')


def read_entries(path):
    """
    Read a fixture of an index into the versions of it.

    Args:
        path (str): Path of the fixture

    Returns:
        list[Entry]: The versions, in the order of the fixture

    Raises:
        ValueError: If a file line comes before a version line, or a record
            line is not shaped as above
    """
    entries = []
    project = ''
    entry = None
    with open(path, encoding='utf-8') as f:
        for number, line in enumerate(f, 1):
            line = line.rstrip('\n')
            if not line.strip():
                continue
            if line.startswith(_PROJECT):
                project = line[len(_PROJECT):].strip()
                entry = None
                continue
            if line.startswith(_VERSION):
                parts = line[len(_VERSION):].split(' ')
                if len(parts) not in (1, 3):
                    raise ValueError(f'The version record on line {number} of {path} is not shaped '
                                     f'as "<version> <origin text> <origin url>": {line!r}')
                if not project:
                    raise ValueError(
                        f'The version record on line {number} of {path} comes before its project')
                origin = (parts[1], parts[2]) if len(parts) == 3 else None
                entry = Entry(project, parts[0], origin)
                entries.append(entry)
                continue
            if line.startswith('#'):
                continue
            if entry is None:
                raise ValueError(f'The file record on line {number} of {path} comes before its version')
            filename, sep, sha256 = line.partition(' ')
            if not sep:
                raise ValueError(f'The file record on line {number} of {path} holds no digest: {line!r}')
            entry.files.append((filename, sha256))
    return entries
