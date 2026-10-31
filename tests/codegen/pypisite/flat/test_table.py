"""
Tests for the data of the tables of a flat page: the matrix of a version and
the list of versions of a project of pure python wheels.
"""
import pytest

from alasio.codegen.pypisite import WheelLink
from alasio.codegen.pypisite.flat import FlatCell, build_pure_table, build_version

from .data import DIGEST_A, DIGEST_B, FILE_MAC, FILE_PURE, FILE_WIN


class TestBuildVersion:
    def test_one_version(self):
        """The platforms are the rows, the python/abi pairs the columns."""
        table = build_version([
            WheelLink('httpx2', '2.13.1', FILE_WIN),
            WheelLink('httpx2', '2.13.1', FILE_MAC),
            WheelLink('httpx2', '2.13.1', FILE_PURE),
        ])
        assert table.columns == ['cp38', 'cp39']
        assert table.rows == [
            (('macosx_11_0_arm64',), 'macosx_11_0_arm64'),
            (('win_amd64',), 'win_amd64'),
        ]
        assert table.pure == [FlatCell('py3-none-any', WheelLink('httpx2', '2.13.1', FILE_PURE))]
        assert [link.filename for link in table.wheels] == [FILE_WIN, FILE_MAC, FILE_PURE]
        assert table.has_abi3 is False

    def test_cells(self):
        table = build_version([
            WheelLink('httpx2', '2.13.1', FILE_WIN),
            WheelLink('httpx2', '2.13.1', FILE_MAC),
        ])
        assert table.cells[(('win_amd64',), 'cp38')] == [
            FlatCell('cp38', WheelLink('httpx2', '2.13.1', FILE_WIN))]
        assert table.cells.get((('win_amd64',), 'cp39')) is None
        assert table.cells.get((('macosx_11_0_arm64',), 'cp38')) is None

    def test_all_compiled_has_no_pure_row(self):
        table = build_version([WheelLink('httpx2', '2.13.1', FILE_WIN)])
        assert table.pure == []

    def test_all_pure(self):
        with pytest.raises(ValueError, match='belongs to WheelTablePurePython'):
            build_version([WheelLink('httpx2', '2.13.1', FILE_PURE)])

    def test_empty(self):
        with pytest.raises(ValueError, match='has no wheel'):
            build_version([])

    def test_two_versions(self):
        with pytest.raises(ValueError, match='two versions'):
            build_version([
                WheelLink('httpx2', '2.13.1', FILE_WIN),
                WheelLink('httpx2', '2.9.0', 'httpx2-2.9.0-cp38-cp38-win_amd64.whl'),
            ])

    def test_two_distributions(self):
        with pytest.raises(ValueError, match='two distributions'):
            build_version([
                WheelLink('httpx2', '2.13.1', FILE_WIN),
                WheelLink('msgspec', '2.13.1', 'msgspec-2.13.1-cp38-cp38-win_amd64.whl'),
            ])

    def test_duplicate_dropped(self):
        link = WheelLink('httpx2', '2.13.1', FILE_WIN, DIGEST_A)
        table = build_version([link, link])
        assert table.wheels == [link]

    def test_duplicate_of_another_digest(self):
        with pytest.raises(ValueError, match='two digests'):
            build_version([
                WheelLink('httpx2', '2.13.1', FILE_WIN, DIGEST_A),
                WheelLink('httpx2', '2.13.1', FILE_WIN, DIGEST_B),
            ])

    def test_two_files_in_one_cell(self):
        """A stable abi wheel and a wheel of one interpreter share a column."""
        table = build_version([
            WheelLink('av', '18.1.0', 'av-18.1.0-cp38-cp38-win_amd64.whl', DIGEST_A),
            WheelLink('av', '18.1.0', 'av-18.1.0-cp38-abi3-win_amd64.whl', DIGEST_B),
        ])
        cell = table.cells[(('win_amd64',), 'cp38')]
        assert [entry.link.filename for entry in cell] == [
            'av-18.1.0-cp38-abi3-win_amd64.whl',
            'av-18.1.0-cp38-cp38-win_amd64.whl',
        ]
        assert [entry.abi3 for entry in cell] == [True, False]
        assert table.has_abi3 is True

    def test_free_threaded_columns(self):
        table = build_version([
            WheelLink('av', '18.1.0', 'av-18.1.0-cp314-cp314t-win_amd64.whl'),
            WheelLink('av', '18.1.0', 'av-18.1.0-cp314-cp314-win_amd64.whl'),
        ])
        assert table.columns == ['cp314', 'cp314t']

    def test_pure_label_expands(self):
        table = build_version([
            WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py2.py3-none-any.whl'),
            WheelLink('anyio', '4.15.1', 'anyio-4.15.1-cp38-cp38-win_amd64.whl'),
        ])
        assert [entry.label for entry in table.pure] == ['py2-none-any, py3-none-any']

    def test_type(self):
        with pytest.raises(TypeError, match='not a WheelLink'):
            build_version([('httpx2', '2.13.1', FILE_WIN)])


class TestBuildPureTable:
    def test_rows(self):
        """The versions keep the order of the caller, the files sort inside one."""
        new = WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl')
        old = WheelLink('anyio', '4.14.2', 'anyio-4.14.2-py3-none-any.whl')
        assert build_pure_table([new, old]) == [('4.15.1', [new]), ('4.14.2', [old])]
        assert build_pure_table([old, new]) == [('4.14.2', [old]), ('4.15.1', [new])]

    def test_files_of_one_version(self):
        other = WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py2.py3-none-any.whl')
        main = WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl')
        assert build_pure_table([other, main]) == [('4.15.1', [other, main])]

    def test_duplicates_dropped(self):
        link = WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl')
        assert build_pure_table([link, link]) == [('4.15.1', [link])]

    def test_empty(self):
        assert build_pure_table([]) == []

    def test_platform_wheel(self):
        with pytest.raises(ValueError, match='wheel of a platform'):
            build_pure_table([WheelLink('httpx2', '2.13.1', FILE_WIN)])

    def test_two_distributions(self):
        with pytest.raises(ValueError, match='two distributions'):
            build_pure_table([
                WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl'),
                WheelLink('httpx2', '2.13.1', FILE_PURE),
            ])

    def test_duplicate_of_another_digest(self):
        with pytest.raises(ValueError, match='two digests'):
            build_pure_table([
                WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl', DIGEST_A),
                WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl', DIGEST_B),
            ])
