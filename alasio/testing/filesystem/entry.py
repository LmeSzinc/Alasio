"""
Mocks of os.DirEntry and the iterator returned by os.scandir().

The entry information (is_dir / is_file / stat) is resolved once when
the directory is scanned, like the real os.DirEntry caches it.
"""


class FakeDirEntry:
    """
    Mock of os.DirEntry, all information is resolved at scandir() time.
    """
    __slots__ = ('name', 'path', '_is_dir', '_stat')

    def __init__(self, name, path, is_dir, stat_result):
        """
        Args:
            name (str): Name of the entry
            path (str): Full normalized path of the entry
            is_dir (bool): Whether the entry is a directory
            stat_result (os.stat_result): Stat result of the entry
        """
        self.name = name
        self.path = path
        self._is_dir = is_dir
        self._stat = stat_result

    def is_dir(self, follow_symlinks=True):
        """
        Args:
            follow_symlinks (bool): Accepted for compatibility,
                there are no symlinks in the fake filesystem

        Returns:
            bool: Whether the entry is a directory
        """
        return self._is_dir

    def is_file(self, follow_symlinks=True):
        """
        Args:
            follow_symlinks (bool): Accepted for compatibility

        Returns:
            bool: Whether the entry is a file
        """
        return not self._is_dir

    def is_symlink(self):
        """
        Returns:
            bool: Always False, symlinks are not supported
        """
        return False

    def stat(self, follow_symlinks=True):
        """
        Args:
            follow_symlinks (bool): Accepted for compatibility

        Returns:
            os.stat_result: Stat result of the entry
        """
        return self._stat

    def __repr__(self):
        return f'<FakeDirEntry {self.path!r}>'


class FakeScandirIterator:
    """
    Mock of the iterator returned by os.scandir().

    Supports both iteration and context manager usage.
    """
    def __init__(self, entries):
        """
        Args:
            entries (list[FakeDirEntry]): Entries of the directory
        """
        self._iterator = iter(entries)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._iterator)
