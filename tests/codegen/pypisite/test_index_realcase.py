"""
The index page of a distribution, built from the index PyPI published.

The data is the file list of https://pypi.org/simple/anyio/ on 2026-11-01,
kept in `anyio-index.txt`: every wheel and every source distribution of the
distribution, with the digest pytest verifies. The page the module writes is
kept in `anyio-index.html` and compared byte by byte, so a change of the
generator that a reader of the page would see shows up here as a diff of the
page and not as a change of an expectation.

The golden page does not change on its own: it is the page of that day, and a
test that updates it would only prove the generator agrees with itself. When a
change of the page is intended, delete the file, run the test to write it
again, read the diff and commit it.
"""
import pytest

from alasio.codegen.pypisite import simple_page
from alasio.testing.filesystem import fs  # noqa: F401
from tests.codegen.pypisite import fixture

# the files of the index of anyio, one record a line
DATA = fixture.HERE.joinpath('anyio-index.txt')

# the page of the distribution, as the generator writes it
GOLDEN = fixture.HERE.joinpath('anyio-index.html')

# the number of files the index served: every wheel and every sdist of a version
FILES = 144

# the number of versions, one wheel and one sdist each
VERSIONS = 72


def read_entries():
    """
    Read the fixture of the index.

    Returns:
        list[fixture.Entry]: The versions of the distribution, in page order
    """
    return fixture.read_entries(DATA)


def build_page():
    """
    Build the page of the distribution from the files of the index.

    Returns:
        str: The page, the shape pip reads as an index

    Raises:
        ValueError: If a link of the index is not a link of a page
    """
    links = [link for entry in read_entries() for link in entry.links]
    return simple_page('anyio', links)


def read_golden():
    """
    Read the page committed with the test.

    Returns:
        str: The page, LF line endings
    """
    with open(GOLDEN, encoding='utf-8', newline='') as f:
        return f.read()


class TestFixture:
    def test_versions(self):
        assert len(read_entries()) == VERSIONS

    def test_files(self):
        assert sum(len(entry.files) for entry in read_entries()) == FILES

    def test_every_version_has_a_wheel_and_an_sdist(self):
        """The index of a distribution lists both the wheel and the source of a release."""
        for entry in read_entries():
            assert len(entry.files) == 2
            assert entry.files[0][0].endswith('.whl')
            assert entry.files[1][0].endswith('.tar.gz')

    def test_digests_are_sha256(self):
        for entry in read_entries():
            for _filename, sha256 in entry.files:
                assert len(sha256) == 64
                assert all(char in '0123456789abcdef' for char in sha256)


class TestPage:
    def test_page_is_the_golden(self):
        """The page of the real index, byte by byte."""
        fixture.assert_page(build_page(), read_golden())

    def test_golden_holds_lf(self):
        """The page is compared byte by byte, so the committed file stays LF."""
        assert '\r' not in read_golden()

    def test_page_of_a_distribution(self):
        page = build_page()
        assert page.startswith("""\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="pypi:repository-version" content="1.0">
    <title>Links for anyio</title>
  </head>
  <body>
    <h1>Links for anyio</h1>
""")

    def test_every_file_is_linked(self):
        page = build_page()
        assert page.count('<a href=') == FILES

    def test_every_link_carries_the_digest(self):
        """A file of the index comes with the sha256 it was published with."""
        page = build_page()
        assert page.count('#sha256=') == FILES

    def test_link_shape(self):
        """The page sits in the folder of the distribution, so a link starts at the version."""
        page = build_page()
        assert '<a href="4.15.1/anyio-4.15.1-py3-none-any.whl#sha256=' in page
        assert '<a href="4.15.1/anyio-4.15.1.tar.gz#sha256=' in page

    def test_versions_sorted_by_pep440(self):
        """The order of the page is the one pip resolves a candidate in."""
        page = build_page()
        for small, big in (
            ('1.0.0a1', '1.0.0a2'),
            ('1.2.1', '1.3.0'),
            ('2.0.0rc1', '2.0.0'),
            ('4.15.0', '4.15.1'),
        ):
            assert page.index(f'href="{small}/') < page.index(f'href="{big}/')

    def test_sdist_after_the_wheel_of_the_version(self):
        page = build_page()
        assert page.index('href="4.15.1/anyio-4.15.1-py3-none-any.whl') < page.index(
            'href="4.15.1/anyio-4.15.1.tar.gz')

    def test_pre_release_before_its_release(self):
        """'2.0.0rc1' is a candidate pip reads before '2.0.0', the page says the same."""
        page = build_page()
        assert page.index('href="2.0.0rc1/') < page.index('href="2.0.0/')

    def test_page_is_stable(self):
        """Two runs of the same index write the same bytes."""
        assert build_page() == build_page()


class TestAssertPage:
    def test_same(self):
        fixture.assert_page('a\nb\n', 'a\nb\n')

    def test_first_differing_line(self):
        with pytest.raises(AssertionError) as e:
            fixture.assert_page('a\nb\nc\n', 'a\nX\nc\n')
        assert str(e.value) == (
            'The page differs from the golden page on line 2:\n'
            "  golden: 'X'\n"
            "  page:   'b'")

    def test_length(self):
        with pytest.raises(AssertionError, match='lines, the golden page has'):
            fixture.assert_page('a\nb\n', 'a\nb')


class TestReadEntries:
    def test_records(self, fs):
        fs.create_file('/index.txt', contents=f"""\
# The files of a page.
#
# project httpx2
# version 2.13.1 20261019-PyAV==18.1.0 https://example.com/tag
httpx2-2.13.1-py3-none-any.whl {'a' * 64}
httpx2-2.13.1-cp38-cp38-win_amd64.whl {'b' * 64}
# version 2.9.0
httpx2-2.9.0-py3-none-any.whl {'c' * 64}
""")
        entries = fixture.read_entries('/index.txt')
        assert [(entry.project, entry.version) for entry in entries] == [
            ('httpx2', '2.13.1'), ('httpx2', '2.9.0')
        ]
        assert entries[0].origin == ('20261019-PyAV==18.1.0', 'https://example.com/tag')
        assert entries[1].origin is None
        assert [filename for filename, _sha256 in entries[0].files] == [
            'httpx2-2.13.1-py3-none-any.whl', 'httpx2-2.13.1-cp38-cp38-win_amd64.whl'
        ]
        assert entries[0].links[0].href() == (
            f'2.13.1/httpx2-2.13.1-py3-none-any.whl#sha256={"a" * 64}')

    def test_pure(self, fs):
        fs.create_file('/index.txt', contents=f"""\
# project anyio
# version 4.15.1
anyio-4.15.1-py3-none-any.whl {'a' * 64}
""")
        assert fixture.read_entries('/index.txt')[0].pure is True

    def test_not_pure(self, fs):
        """A version that ships a file a matrix cannot place is not a version of pure python."""
        fs.create_file('/index.txt', contents=f"""\
# project anyio
# version 4.15.1
anyio-4.15.1-py3-none-any.whl {'a' * 64}
anyio-4.15.1.tar.gz {'b' * 64}
""")
        assert fixture.read_entries('/index.txt')[0].pure is False

    def test_file_before_version(self, fs):
        fs.create_file('/index.txt', contents=(
            f'# project anyio\nanyio-4.15.1-py3-none-any.whl {"a" * 64}\n'))
        with pytest.raises(ValueError, match='comes before its version'):
            fixture.read_entries('/index.txt')

    def test_version_before_project(self, fs):
        fs.create_file('/index.txt', contents='# version 4.15.1\n')
        with pytest.raises(ValueError, match='comes before its project'):
            fixture.read_entries('/index.txt')

    def test_file_without_digest(self, fs):
        fs.create_file('/index.txt', contents=(
            '# project anyio\n# version 4.15.1\nanyio-4.15.1-py3-none-any.whl\n'))
        with pytest.raises(ValueError, match='holds no digest'):
            fixture.read_entries('/index.txt')

    def test_bad_version_record(self, fs):
        fs.create_file('/index.txt', contents='# project anyio\n# version 4.15.1 a b c\n')
        with pytest.raises(ValueError, match='is not shaped as'):
            fixture.read_entries('/index.txt')
