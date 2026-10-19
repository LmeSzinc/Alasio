import os

import pytest

from alasio.deploy_dev.wheel.repo_path import RepoPath

FILES = {
    'LICENSE': b'license\n',
    'LICENSE.txt': b'license txt\n',
    'README.md': b'# readme\n',
    'licenses/A.txt': b'A\n',
    'licenses/sub/B.txt': b'B\n',
    'pkg/__init__.py': b'',
    'pkg/core.py': b'def run():\n    return 1\n',
}


class TestRepoPathBasics:
    """Tests for the Path-like methods of RepoPath."""

    @pytest.fixture
    def repo(self, make_repo):
        return make_repo(FILES)

    def test_root_and_joinpath(self, repo):
        """The root path is empty and joinpath keeps POSIX separators."""
        root = RepoPath(repo, 'c1')
        assert root.as_posix() == ''
        assert str(root) == '.'
        assert repr(root) == "RepoPath('')"

        child = root.joinpath('licenses', 'A.txt')
        assert child.as_posix() == 'licenses/A.txt'
        assert str(child) == 'licenses/A.txt'
        assert repr(child) == "RepoPath('licenses/A.txt')"

    def test_dot_path_is_root(self, repo):
        """A '.' path is normalized to the root path."""
        assert RepoPath(repo, 'c1', '.').as_posix() == ''

    def test_files_are_shared(self, repo):
        """Derived paths share the cached file list."""
        root = RepoPath(repo, 'c1')
        child = root.joinpath('LICENSE')
        assert root.files is child.files
        assert set(root.files) == set(FILES)

    def test_is_file(self, repo):
        """is_file() is true only for files of the commit."""
        root = RepoPath(repo, 'c1')
        assert root.joinpath('LICENSE').is_file()
        assert root.joinpath('pkg/__init__.py').is_file()
        assert not root.joinpath('missing').is_file()
        assert not root.is_file()

    def test_read_bytes_and_text(self, repo):
        """read_bytes() and read_text() read the commit content."""
        root = RepoPath(repo, 'c1')
        assert root.joinpath('LICENSE').read_bytes() == b'license\n'
        assert root.joinpath('README.md').read_text() == '# readme\n'
        assert root.joinpath('README.md').read_text(encoding='utf-8') == '# readme\n'

        with pytest.raises(FileNotFoundError):
            root.joinpath('missing').read_bytes()
        with pytest.raises(FileNotFoundError):
            root.joinpath('missing').read_text()

    def test_read_text_encoding(self, make_repo):
        """read_text() decodes with the requested encoding."""
        repo = make_repo({'café.txt': 'café\n'.encode('utf-8')})
        root = RepoPath(repo, 'c1')
        assert root.joinpath('café.txt').read_text() == 'café\n'

    def test_read_text_universal_newlines(self, make_repo):
        """read_text() normalizes CRLF and CR like Path.read_text()."""
        repo = make_repo({'x.bin': b'a\r\nb\r'})
        root = RepoPath(repo, 'c1')
        assert root.joinpath('x.bin').read_text() == 'a\nb\n'

    def test_fspath(self, repo):
        """RepoPath can be used as an os.PathLike."""
        root = RepoPath(repo, 'c1')
        assert os.fspath(root.joinpath('LICENSE')) == 'LICENSE'


class TestRepoPathGlob:
    """Tests for RepoPath.glob(), matching pathlib.Path.glob() file results."""

    @pytest.fixture
    def root(self, make_repo):
        return RepoPath(make_repo(FILES), 'c1')

    @pytest.mark.parametrize('pattern, expected', [
        ('LICENSE*', ['LICENSE', 'LICENSE.txt']),
        ('*.txt', ['LICENSE.txt']),
        ('licenses/*', ['licenses/A.txt']),
        ('licenses/**', []),
        ('**', []),
        ('', []),
        ('**/B.txt', ['licenses/sub/B.txt']),
        ('**/*.txt', ['LICENSE.txt', 'licenses/A.txt', 'licenses/sub/B.txt']),
        ('licenses/**/*.txt', ['licenses/A.txt', 'licenses/sub/B.txt']),
        ('pkg/*.py', ['pkg/__init__.py', 'pkg/core.py']),
    ])
    def test_glob(self, root, pattern, expected):
        """Glob results are relative to the root and contain files only."""
        assert sorted(path.as_posix() for path in root.glob(pattern)) == expected

    def test_glob_returns_repo_paths(self, root):
        """Glob results are RepoPath objects sharing the file list."""
        paths = list(root.glob('pkg/*.py'))
        assert all(isinstance(path, RepoPath) for path in paths)
        assert all(path.files is root.files for path in paths)

    def test_glob_from_subdir(self, root):
        """Glob patterns are relative to the RepoPath they are called on."""
        sub = root.joinpath('licenses')
        assert sorted(path.as_posix() for path in sub.glob('*.txt')) == ['licenses/A.txt']
        assert sorted(path.as_posix() for path in sub.glob('**/*.txt')) == [
            'licenses/A.txt', 'licenses/sub/B.txt']


class TestRepoPathRelativeTo:
    """Tests for RepoPath.relative_to()."""

    @pytest.fixture
    def root(self, make_repo):
        return RepoPath(make_repo(FILES), 'c1')

    def test_relative_to_root(self, root):
        """A path keeps its repo path when made relative to the root."""
        assert root.joinpath('licenses/A.txt').relative_to(root).as_posix() == 'licenses/A.txt'

    def test_relative_to_subdir(self, root):
        """A path below a subdirectory is relative to it."""
        sub = root.joinpath('licenses')
        assert sub.joinpath('A.txt').relative_to(sub).as_posix() == 'A.txt'

    def test_relative_to_unrelated_path(self, root):
        """A path outside the base path raises ValueError."""
        with pytest.raises(ValueError):
            root.joinpath('LICENSE').relative_to(root.joinpath('licenses'))


class TestRepoPathEquality:
    """Tests for equality, hashing and ordering of RepoPath."""

    @pytest.fixture
    def root(self, make_repo):
        return RepoPath(make_repo(FILES), 'c1')

    def test_equality_and_hash(self, root):
        """Two RepoPath of the same path are equal and hash the same."""
        first = root.joinpath('LICENSE')
        second = root.joinpath('LICENSE')
        assert first == second
        assert hash(first) == hash(second)

    def test_ordering(self, root):
        """RepoPath can be sorted by its path."""
        first = root.joinpath('LICENSE')
        second = root.joinpath('licenses/A.txt')
        assert first != second
        assert sorted([second, first]) == [first, second]
