"""
Tests for the wheel selection of a download (select_wheels).

The selection is a pure function of the links of an index page and the target
interpreter, so the cases are index pages of the tests and a fixed target.
"""
import pytest

from alasio.deploy.pip_download._link import parse_links
from alasio.deploy.pip_download._target_python import TargetPython
from alasio.deploy.pip_download.pypi_file import WheelNotFoundError, select_wheels
from alasio.logger import logger

PAGE_URL = 'https://mirror.example/simple/numpy/'
PY38_WIN = TargetPython(py_version_info=(3, 8, 10), platforms=['win_amd64'])


def make_links(*entries):
    """
    Build the links of an index page of the tests.

    Args:
        *entries (dict): One per link, the keys: filename, and the optional
            yanked, requires_python and sha256

    Returns:
        list[Link]: The links of the page
    """
    anchors = []
    for entry in entries:
        attrs = f' href="{entry["filename"]}#sha256={entry.get("sha256", "a" * 64)}"'
        if 'yanked' in entry:
            attrs += f' data-yanked="{entry["yanked"]}"'
        if 'requires_python' in entry:
            attrs += f' data-requires-python="{entry["requires_python"]}"'
        anchors.append(f'<a{attrs}>{entry["filename"]}</a>')
    page = f'<html><body>{"".join(anchors)}</body></html>'
    return parse_links(page.encode('utf-8'), PAGE_URL)


def filenames(candidates):
    """
    Returns:
        list[str]: The filenames of the selected candidates, in order
    """
    return [candidate.filename for candidate in candidates]


# the page of the selection tests: the wheels of two versions and two projects
PAGE = (
    {'filename': 'numpy-2.1.0-cp312-cp312-win_amd64.whl', 'requires_python': '>=3.12'},
    {'filename': 'numpy-2.1.0-cp38-cp38-win_amd64.whl'},
    {'filename': 'numpy-2.1.0-cp38-cp38-manylinux_2_17_x86_64.whl'},
    {'filename': 'numpy-2.1.0-py3-none-any.whl'},
    {'filename': 'numpy-2.1.0-py3-none-any.whl.metadata'},
    {'filename': 'numpy-2.0.0-cp38-cp38-win_amd64.whl'},
    {'filename': 'other-2.1.0-cp38-cp38-win_amd64.whl'},
)


class TestSelectModes:
    """The mode decides the candidate set."""

    @pytest.fixture
    def links(self):
        return make_links(*PAGE)

    def test_current(self, links):
        assert filenames(select_wheels(links, 'numpy', '2.1.0', 'current', PY38_WIN)) == [
            'numpy-2.1.0-cp38-cp38-win_amd64.whl',
            'numpy-2.1.0-py3-none-any.whl',
        ]

    def test_current_other_interpreter(self, links):
        target = TargetPython(py_version_info=(3, 12, 0), platforms=['win_amd64'])
        assert filenames(select_wheels(links, 'numpy', '2.1.0', 'current', target)) == [
            'numpy-2.1.0-cp312-cp312-win_amd64.whl',
            'numpy-2.1.0-py3-none-any.whl',
        ]

    def test_current_other_platform(self, links):
        target = TargetPython(py_version_info=(3, 8, 10), platforms=['manylinux_2_17_x86_64'])
        assert filenames(select_wheels(links, 'numpy', '2.1.0', 'current', target)) == [
            'numpy-2.1.0-cp38-cp38-manylinux_2_17_x86_64.whl',
            'numpy-2.1.0-py3-none-any.whl',
        ]

    def test_current_requires_python(self, links):
        # the cp312 wheel declares >=3.12, a 3.8 target does not get it
        assert 'numpy-2.1.0-cp312-cp312-win_amd64.whl' not in filenames(
            select_wheels(links, 'numpy', '2.1.0', 'current', PY38_WIN))

    def test_py3_any(self, links):
        assert filenames(select_wheels(links, 'numpy', '2.1.0', 'py3-any', PY38_WIN)) == [
            'numpy-2.1.0-py3-none-any.whl',
        ]

    def test_all(self, links):
        assert filenames(select_wheels(links, 'numpy', '2.1.0', 'all', PY38_WIN)) == [
            'numpy-2.1.0-cp312-cp312-win_amd64.whl',
            'numpy-2.1.0-cp38-cp38-manylinux_2_17_x86_64.whl',
            'numpy-2.1.0-cp38-cp38-win_amd64.whl',
            'numpy-2.1.0-py3-none-any.whl',
        ]


class TestSelectFilters:
    """The name, the version and the file type of a link."""

    @pytest.fixture
    def links(self):
        return make_links(*PAGE)

    def test_name_any_style(self, links):
        assert filenames(select_wheels(links, 'NumPy', '2.1.0', 'py3-any')) == [
            'numpy-2.1.0-py3-none-any.whl',
        ]

    def test_version_equal(self, links):
        # 2.1 and 2.1.0 are the same version
        assert filenames(select_wheels(links, 'numpy', '2.1', 'py3-any')) == [
            'numpy-2.1.0-py3-none-any.whl',
        ]

    def test_other_version(self, links):
        assert filenames(select_wheels(links, 'numpy', '2.0.0', 'current', PY38_WIN)) == [
            'numpy-2.0.0-cp38-cp38-win_amd64.whl',
        ]

    def test_pure_python3_of_any_minor(self):
        links = make_links(
            {'filename': 'foo-1.0-py38-none-any.whl'},
            {'filename': 'foo-1.0-py2-none-any.whl'},
            {'filename': 'foo-1.0-py2.py3-none-any.whl'},
            {'filename': 'foo-1.0-cp38-none-any.whl'},
        )
        assert filenames(select_wheels(links, 'foo', '1.0', 'py3-any')) == [
            'foo-1.0-py2.py3-none-any.whl',
            'foo-1.0-py38-none-any.whl',
        ]


class TestSelectYanked:
    """The yanked files, PEP 592."""

    def test_yanked_skipped(self):
        links = make_links(
            {'filename': 'foo-1.0-py3-none-any.whl', 'yanked': 'broken'},
            {'filename': 'foo-1.0-py2.py3-none-any.whl'},
        )
        assert filenames(select_wheels(links, 'foo', '1.0', 'py3-any')) == [
            'foo-1.0-py2.py3-none-any.whl',
        ]

    def test_all_yanked_accepted(self):
        links = make_links({'filename': 'foo-1.0-py3-none-any.whl', 'yanked': 'broken'})
        with logger.mock_capture_writer() as capture:
            selected = select_wheels(links, 'foo', '1.0', 'py3-any')
        assert filenames(selected) == ['foo-1.0-py3-none-any.whl']
        assert capture.concat() == (
            'WARNING | Every wheel of "foo==1.0" that matches "py3-any" is yanked: '
            'foo-1.0-py3-none-any.whl\n')

    def test_all_keeps_yanked(self):
        links = make_links({'filename': 'foo-1.0-py3-none-any.whl', 'yanked': 'broken'})
        with logger.mock_capture_writer() as capture:
            selected = select_wheels(links, 'foo', '1.0', 'all')
        assert filenames(selected) == ['foo-1.0-py3-none-any.whl']
        assert capture.concat() == (
            'INFO | Yanked wheels of "foo==1.0" are kept: foo-1.0-py3-none-any.whl\n')


class TestSelectRequiresPython:
    """The `data-requires-python` of a link."""

    def test_unsatisfied_skipped(self):
        links = make_links(
            {'filename': 'foo-1.0-cp38-cp38-win_amd64.whl', 'requires_python': '>=3.9'},
            {'filename': 'foo-1.0-py3-none-any.whl', 'requires_python': '>=3.8'},
        )
        assert filenames(select_wheels(links, 'foo', '1.0', 'current', PY38_WIN)) == [
            'foo-1.0-py3-none-any.whl',
        ]

    def test_invalid_value_kept(self):
        # a broken specifier of an index does not hide the file, like pip
        links = make_links(
            {'filename': 'foo-1.0-py3-none-any.whl', 'requires_python': 'lolwat'})
        original = logger._level
        logger.set_level('DEBUG')
        try:
            with logger.mock_capture_writer() as capture:
                selected = select_wheels(links, 'foo', '1.0', 'current', PY38_WIN)
        finally:
            logger.set_level(original)
        assert filenames(selected) == ['foo-1.0-py3-none-any.whl']
        assert capture.concat() == (
            'DEBUG | Ignoring invalid Requires-Python of "foo-1.0-py3-none-any.whl": '
            "'lolwat', Invalid specifier: 'lolwat'\n")

    def test_not_evaluated_by_the_other_modes(self):
        links = make_links(
            {'filename': 'foo-1.0-py3-none-any.whl', 'requires_python': '>=99'})
        assert filenames(select_wheels(links, 'foo', '1.0', 'py3-any')) == [
            'foo-1.0-py3-none-any.whl',
        ]
        assert filenames(select_wheels(links, 'foo', '1.0', 'all')) == [
            'foo-1.0-py3-none-any.whl',
        ]


class TestSelectErrors:
    """The failure of a selection."""

    def test_no_version(self):
        links = make_links({'filename': 'foo-1.0-py3-none-any.whl'})
        with pytest.raises(WheelNotFoundError) as e:
            select_wheels(links, 'foo', '2.0', 'py3-any')
        assert str(e.value) == 'No wheel of "foo==2.0" in the index'

    def test_no_mode_match(self):
        links = make_links({'filename': 'foo-1.0-cp38-cp38-win_amd64.whl'})
        with pytest.raises(WheelNotFoundError) as e:
            select_wheels(links, 'foo', '1.0', 'py3-any')
        assert str(e.value) == (
            'No wheel of "foo==1.0" matches "py3-any", the version has: '
            'foo-1.0-cp38-cp38-win_amd64.whl')

    def test_empty_index(self):
        with pytest.raises(WheelNotFoundError) as e:
            select_wheels([], 'foo', '1.0', 'current', PY38_WIN)
        assert str(e.value) == 'No wheel of "foo==1.0" in the index'

    def test_invalid_mode(self):
        with pytest.raises(ValueError) as e:
            select_wheels([], 'foo', '1.0', 'everything')
        assert str(e.value) == (
            "Invalid download mode: 'everything', expected one of ('current', 'py3-any', 'all')")

    def test_invalid_version(self):
        with pytest.raises(WheelNotFoundError) as e:
            select_wheels([], 'foo', 'not-a-version', 'py3-any')
        assert str(e.value) == (
            'Invalid version of "foo": \'not-a-version\', Invalid version: \'not-a-version\'')
