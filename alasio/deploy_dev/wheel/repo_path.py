import fnmatch
from pathlib import PurePosixPath


def _match_glob(path, pattern):
    """
    Match a POSIX path against a glob pattern with Path.glob() semantics.

    ``*`` and ``?`` do not cross a directory separator, ``**`` matches zero
    or more directories. A pattern that ends with a directory wildcard or a
    separator matches directories only, and therefore no file.

    Args:
        path (str): POSIX path relative to the repo root
        pattern (str): POSIX glob pattern

    Returns:
        bool: True if the path matches the pattern
    """
    if pattern.endswith('/'):
        return False
    pattern_parts = tuple(part for part in pattern.split('/') if part and part != '.')
    if not pattern_parts or pattern_parts[-1] == '**':
        return False
    path_parts = tuple(part for part in path.split('/') if part)
    return _match_glob_parts(path_parts, pattern_parts)


def _match_glob_parts(path_parts, pattern_parts):
    if not pattern_parts:
        return not path_parts
    head = pattern_parts[0]
    if head == '**':
        # ** matches zero or more directories
        if _match_glob_parts(path_parts, pattern_parts[1:]):
            return True
        return bool(path_parts) and _match_glob_parts(path_parts[1:], pattern_parts)
    if not path_parts:
        return False
    if not fnmatch.fnmatchcase(path_parts[0], head):
        return False
    return _match_glob_parts(path_parts[1:], pattern_parts[1:])


class RepoPath:
    """
    A pathlib.Path-like path backed by a git repository.

    pyproject_metadata calls joinpath(), is_file(), read_text(), glob() and
    relative_to() on project_dir. This class provides those methods and reads
    the content from the commit instead of the working tree.
    """

    def __init__(self, repo, commit, path='', files=None):
        """
        Args:
            repo (GitRepo): Repository object
            commit (str): Commit sha1
            path (str): POSIX path relative to the repo root
            files (dict[str, FileEntry], optional): Cached file list of the
                commit, shared by the paths derived from this one
        """
        self.repo = repo
        self.commit = commit
        self.path = PurePosixPath(path).as_posix()
        if self.path == '.':
            self.path = ''
        self._files = files

    @property
    def files(self):
        """
        Files of the commit, read once and shared by the derived paths.

        Returns:
            dict[str, FileEntry]: {filepath: FileEntry}
        """
        if self._files is None:
            list_repo_files = getattr(self.repo, 'list_repo_files', None)
            if list_repo_files is not None:
                self._files = list_repo_files(self.commit)
            else:
                self._files = self.repo.list_files(self.commit)
        return self._files

    def _child(self, path):
        return self.__class__(self.repo, self.commit, path, files=self.files)

    def joinpath(self, *parts):
        """
        Join path components, like pathlib.Path.joinpath().

        Args:
            *parts (str): Path components

        Returns:
            RepoPath: Joined path
        """
        path = PurePosixPath(self.path).joinpath(*parts).as_posix()
        return self._child(path)

    def is_file(self):
        """
        Check if the path is a file of the commit.

        Returns:
            bool: True if the commit has a file at this path
        """
        return self.path in self.files

    def read_bytes(self):
        """
        Read the content of the file from the commit.

        Returns:
            bytes: File content

        Raises:
            FileNotFoundError: If the commit has no file at this path
        """
        entry = self.files.get(self.path)
        if entry is None:
            raise FileNotFoundError(self.path)
        return self.repo.cat(entry.sha1).decoded

    def read_text(self, encoding='utf-8', errors='strict'):
        """
        Read the text of the file from the commit.

        Args:
            encoding (str): Text encoding. Defaults to 'utf-8'.
            errors (str): Error handling strategy. Defaults to 'strict'.

        Returns:
            str: File content

        Raises:
            FileNotFoundError: If the commit has no file at this path
        """
        text = self.read_bytes().decode(encoding, errors)
        # pathlib.Path.read_text() opens in text mode with universal newlines
        return text.replace('\r\n', '\n').replace('\r', '\n')

    def glob(self, pattern):
        """
        Iterate the files of the commit matching a glob pattern.

        The pattern is relative to this path and follows pathlib.Path.glob()
        semantics. Only files are yielded, directories are not.

        Args:
            pattern (str): POSIX glob pattern

        Yields:
            RepoPath: Matched file path
        """
        prefix = f'{self.path}/' if self.path else ''
        for path in self.files:
            if not path.startswith(prefix):
                continue
            relative = path[len(prefix):]
            if _match_glob(relative, pattern):
                yield self._child(path)

    def relative_to(self, other):
        """
        Return this path relative to another path.

        Args:
            other (RepoPath | str): Base path

        Returns:
            RepoPath: Relative path

        Raises:
            ValueError: If this path is not below the base path
        """
        other_path = other.path if isinstance(other, RepoPath) else str(other)
        relative = PurePosixPath(self.path).relative_to(PurePosixPath(other_path))
        return self._child(relative.as_posix())

    def as_posix(self):
        """
        Return the POSIX path string.

        Returns:
            str: POSIX path
        """
        return self.path

    def __fspath__(self):
        return self.path

    def __str__(self):
        return self.path or '.'

    def __repr__(self):
        return f'RepoPath({self.path!r})'

    def __eq__(self, other):
        if isinstance(other, RepoPath):
            return self.path == other.path
        return NotImplemented

    def __hash__(self):
        return hash(self.path)

    def __lt__(self, other):
        if isinstance(other, RepoPath):
            return self.path < other.path
        return NotImplemented
