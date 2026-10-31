"""
Tests for the tags of a wheel file name: the fields, the axes of the matrix and
the order of them.
"""
import pytest

from alasio.codegen.pypisite.flat import (
    column_key, column_sort_key, is_pure, parse_tags, row_display, row_key, row_sort_key, tag_label
)

from .data import FILE_MAC, FILE_PURE, FILE_WIN


class TestParseTags:
    @pytest.mark.parametrize('filename, expected', [
        (FILE_PURE, (('py3',), ('none',), ('any',))),
        (FILE_WIN, (('cp38',), ('cp38',), ('win_amd64',))),
        ('httpx2-2.13.1-py2.py3-none-any.whl', (('py2', 'py3'), ('none',), ('any',))),
        # a compressed platform set is one field of two tags
        ('zstandard-0.25.0-cp38-cp38-manylinux2014_aarch64.manylinux_2_17_aarch64.whl',
         (('cp38',), ('cp38',), ('manylinux2014_aarch64', 'manylinux_2_17_aarch64'))),
        # a build number is a fourth field from the right, the tags do not move
        ('msgspec-0.22.0-1-cp38-cp38-win_amd64.whl', (('cp38',), ('cp38',), ('win_amd64',))),
    ])
    def test_fields(self, filename, expected):
        assert parse_tags(filename) == expected

    def test_tag_repeated_once(self):
        """A field that names one tag twice is one tag, a table has one column for it."""
        assert parse_tags('a-1.0-cp38.cp38-cp38-any.whl') == (('cp38',), ('cp38',), ('any',))

    def test_fields_are_tuples(self):
        python, abi, platform = parse_tags(FILE_WIN)
        assert (python, abi, platform) == (('cp38',), ('cp38',), ('win_amd64',))

    @pytest.mark.parametrize('filename', [
        # not a wheel at all
        'httpx2-2.13.1-py3-none-any.tar.gz',
        'httpx2-2.13.1-py3-none-any.zip',
        # fewer than three tag fields
        'httpx2-2.13.1',
        'httpx2-2.13.1.whl',
        '.whl',
        # a field with an empty tag
        'httpx2-2.13.1--none-any.whl',
        'httpx2-2.13.1-py3-.whl',
        'httpx2-2.13.1-py3-none-.whl',
    ])
    def test_invalid(self, filename):
        with pytest.raises(ValueError):
            parse_tags(filename)


class TestIsPure:
    @pytest.mark.parametrize('filename, expected', [
        (FILE_PURE, True),
        ('httpx2-2.13.1-py2.py3-none-any.whl', True),
        (FILE_WIN, False),
        (FILE_MAC, False),
        # an abi of pure python has to be paired with the platform of any
        ('httpx2-2.13.1-py3-none-win_amd64.whl', False),
        ('httpx2-2.13.1-py3-abi3-any.whl', False),
    ])
    def test_pure(self, filename, expected):
        assert is_pure(parse_tags(filename)) is expected

    def test_reads_the_fields(self):
        """A wheel of no interpreter in particular is still a wheel of a platform."""
        assert is_pure((('py2', 'py3'), ('none',), ('any',))) is True
        assert is_pure((('py3',), ('none',), ('any', 'linux'))) is False


class TestTagLabel:
    @pytest.mark.parametrize('filename, expected', [
        (FILE_PURE, 'py3-none-any'),
        (FILE_WIN, 'cp38-cp38-win_amd64'),
        # the interpreters expand, sorted
        ('httpx2-2.13.1-py3.py2-none-any.whl', 'py2-none-any, py3-none-any'),
        # the platforms expand too
        ('zstandard-0.25.0-cp38-cp38-manylinux_2_17_aarch64.manylinux2014_aarch64.whl',
         'cp38-cp38-manylinux2014_aarch64, cp38-cp38-manylinux_2_17_aarch64'),
    ])
    def test_label(self, filename, expected):
        assert tag_label(parse_tags(filename)) == expected


class TestColumnKey:
    @pytest.mark.parametrize('interpreter, abi, expected', [
        # a CPython build is the column of its interpreter
        ('cp38', 'cp38', 'cp38'),
        ('cp313', 'cp313', 'cp313'),
        # a PyPy build too
        ('pp39', 'pypy39_pp73', 'pp39'),
        # a free-threaded build shares the interpreter tag with the one of the
        # gil, so the abi is the column
        ('cp314', 'cp314t', 'cp314t'),
        # a wheel of an interpreter in general and a stable abi wheel are the
        # column of their interpreter
        ('py3', 'none', 'py3'),
        ('cp38', 'abi3', 'cp38'),
        ('cp38', 'none', 'cp38'),
    ])
    def test_column(self, interpreter, abi, expected):
        assert column_key(interpreter, abi) == expected


class TestColumnSortKey:
    @pytest.mark.parametrize('columns, expected', [
        # the versions of an interpreter compare as numbers
        (['cp310', 'cp39', 'cp38'], ['cp38', 'cp39', 'cp310']),
        (['cp313', 'cp310', 'cp39'], ['cp39', 'cp310', 'cp313']),
        # the build that holds the gil comes before the free-threaded one
        (['cp314t', 'cp314'], ['cp314', 'cp314t']),
        # CPython, then PyPy, then the rest
        (['pp39', 'py3', 'cp38'], ['cp38', 'pp39', 'py3']),
        (['py3', 'py2'], ['py2', 'py3']),
        # an older CPython before a newer PyPy
        (['pp310', 'cp27'], ['cp27', 'pp310']),
    ])
    def test_order(self, columns, expected):
        assert sorted(columns, key=column_sort_key) == expected

    def test_total(self):
        """Two builds of one version still order, whatever the input order."""
        columns = ['cp314t', 'cp314']
        assert sorted(columns, key=column_sort_key) == sorted(list(reversed(columns)), key=column_sort_key)


class TestRowKey:
    def test_sorted_tags(self):
        assert row_key(parse_tags(
            'zstandard-0.25.0-cp38-cp38-manylinux_2_28_aarch64.manylinux2014_aarch64.whl')) == (
            'manylinux2014_aarch64', 'manylinux_2_28_aarch64')

    def test_same_set_one_row(self):
        """Two files that name the same platforms in another order are one row."""
        one = row_key(parse_tags('a-1.0-cp38-cp38-manylinux2014_x86_64.manylinux_2_17_x86_64.whl'))
        other = row_key(parse_tags('a-1.0-cp38-cp38-manylinux_2_17_x86_64.manylinux2014_x86_64.whl'))
        assert one == other

    def test_display(self):
        assert row_display(('manylinux2014_aarch64', 'manylinux_2_17_aarch64')) == (
            'manylinux2014_aarch64.manylinux_2_17_aarch64')

    def test_display_one_platform(self):
        assert row_display(('win_amd64',)) == 'win_amd64'


class TestRowSortKey:
    def test_family_order(self):
        rows = [
            ('win_amd64',),
            ('macosx_11_0_arm64',),
            ('musllinux_1_2_x86_64',),
            ('manylinux_2_17_x86_64',),
        ]
        assert sorted(rows, key=row_sort_key) == [
            ('manylinux_2_17_x86_64',),
            ('musllinux_1_2_x86_64',),
            ('macosx_11_0_arm64',),
            ('win_amd64',),
        ]

    def test_numbers_not_text(self):
        """1.2 sorts before 1.10, a text order would put it after."""
        assert row_sort_key(('musllinux_1_2_x86_64',)) < row_sort_key(('musllinux_1_10_x86_64',))

    def test_macos_versions(self):
        assert row_sort_key(('macosx_10_9_x86_64',)) < row_sort_key(('macosx_10_13_x86_64',))
        assert row_sort_key(('macosx_10_13_x86_64',)) < row_sort_key(('macosx_11_0_arm64',))

    def test_other_family_last(self):
        assert row_sort_key(('manylinux_2_17_x86_64',)) < row_sort_key(('freebsd_13_2_amd64',))
        assert row_sort_key(('ios_13_0_arm64_iphoneos',)) < row_sort_key(('freebsd_13_2_amd64',))

    def test_total(self):
        """A row key of one first component still orders by its label."""
        assert row_sort_key(('manylinux_2_17_x86_64',)) < row_sort_key(
            ('manylinux_2_17_x86_64', 'manylinux2014_x86_64'))
