"""
The FakeFilesystem class: an in-memory filesystem and the mock of the
os / builtins file functions.
"""
import builtins
import errno
import io
import os
import time

from .base import IS_WINDOWS, FakeDir, FakeFile, _normpath
from .entry import FakeDirEntry, FakeScandirIterator
from .file_object import FakeFileObject

# O_ACCMODE is not exposed on Windows, the value is always 3
# (O_RDONLY=0, O_WRONLY=1, O_RDWR=2)
try:
    O_ACCMODE = os.O_ACCMODE
except AttributeError:
    O_ACCMODE = 3


class FakeFilesystem:
    """
    An in-memory filesystem for tests.

    Files and directories are stored in flat dicts keyed by normalized
    absolute path, so path lookups are O(1) dict hits instead of the
    per-segment tree walks of pyfakefs.

    activate() replaces the real file functions with the fake ones:

    - builtins.open() and io.open(), text and binary modes
    - os.path.exists / isfile / isdir / islink / lexists / getsize
    - os.stat / lstat / fstat
    - os.makedirs / mkdir / rmdir / unlink / remove / rename / replace
    - os.scandir / listdir
    - os.open / write / close / fsync, low level fd operations
    - os.utime / chmod / getcwd / chdir

    Everything is served from memory, the real disk is never touched.
    """

    def __init__(self, cwd=None):
        """
        Args:
            cwd (str, optional): Initial working directory.
                Defaults to None, use the real current directory.
        """
        if cwd is None:
            cwd = os.getcwd()
        self._cwd = _normpath(cwd, cwd)
        # the drive root on Windows, "/" on POSIX
        root = self._cwd[:2] if IS_WINDOWS else '/'
        self._ino = 0
        now = time.time()
        self.root_dir = FakeDir(
            path=root, mode=0o777, ino=self._next_ino(), nlink=2,
            atime=now, mtime=now, ctime=now,
        )
        # normalized absolute path -> FakeFile
        self._files: "dict[str, FakeFile]" = {}
        # normalized absolute path -> FakeDir
        self._dirs: "dict[str, FakeDir]" = {root: self.root_dir}
        # fd -> FakeFileObject
        self._fds: "dict[int, FakeFileObject]" = {}
        self._next_fd = 3
        # saved originals of activate() without monkeypatch
        self._saved = []
        # create the cwd chain, so os.getcwd() exists in the fake fs
        if self._cwd != root:
            self._create_parents(self._cwd)
            now = time.time()
            self._dirs[self._cwd] = FakeDir(
                path=self._cwd, mode=0o777, ino=self._next_ino(), nlink=2,
                atime=now, mtime=now, ctime=now,
            )

    def __repr__(self):
        return (
            f'<FakeFilesystem root={self.root_dir.path!r} cwd={self._cwd!r} '
            f'files={len(self._files)} dirs={len(self._dirs)}>'
        )

    """
    Internal helpers
    """

    def _next_ino(self):
        """
        Returns:
            int: Next inode number
        """
        self._ino += 1
        return self._ino

    def _take_fd(self):
        """
        Returns:
            int: Next file descriptor number
        """
        fd = self._next_fd
        self._next_fd += 1
        return fd

    def _normpath(self, path):
        """
        Normalize a path against the fake cwd.

        Args:
            path (str): Path to normalize

        Returns:
            str: Normalized absolute path
        """
        return _normpath(path, self._cwd)

    @staticmethod
    def _child_prefix(dirpath):
        """
        The key prefix of the direct and indirect children of a directory.

        Args:
            dirpath (str): Normalized directory path

        Returns:
            str: Key prefix, "/" for the POSIX root
        """
        return '/' if dirpath == '/' else f'{dirpath}/'

    def _has_children(self, dirpath):
        """
        Args:
            dirpath (str): Normalized directory path

        Returns:
            bool: Whether the directory has any child
        """
        prefix = self._child_prefix(dirpath)
        return (
            any(key.startswith(prefix) for key in self._files)
            or any(key.startswith(prefix) for key in self._dirs)
        )

    def _create_parents(self, path):
        """
        Create the missing parent directories of a path.

        Args:
            path (str): Normalized absolute path

        Raises:
            NotADirectoryError: If a parent path is a file
        """
        parent, sep, _ = path.rpartition('/')
        if not sep:
            return
        missing = []
        while parent not in self._dirs:
            if parent in self._files:
                raise NotADirectoryError(errno.ENOTDIR, 'Not a directory', parent)
            missing.append(parent)
            parent, sep, _ = parent.rpartition('/')
            if not sep:
                break
        now = time.time()
        for folder in reversed(missing):
            self._dirs[folder] = FakeDir(
                path=folder, mode=0o777, ino=self._next_ino(), nlink=2,
                atime=now, mtime=now, ctime=now,
            )

    def _get_entry(self, path):
        """
        Get the record at a path.

        Args:
            path (str): Normalized absolute path

        Returns:
            FakeFile | FakeDir | None: Record at the path, None if missing
        """
        file = self._files.get(path)
        if file is not None:
            return file
        return self._dirs.get(path)

    """
    Test data helpers
    """

    def create_file(self, path, contents=b'', st_mode=None, encoding=None, errors=None):
        """
        Create a file, the parent directories are created automatically.

        Args:
            path (str): Path of the file
            contents (str | bytes): File content. Defaults to b''.
            st_mode (int, optional): Full file mode like os.stat uses,
                e.g. 0o100755. Defaults to None, use 0o666.
            encoding (str): Text encoding when contents is str.
                Defaults to None, use utf-8.
            errors (str): Error handling of encoding when contents is str.
                Defaults to None, use 'strict'.

        Returns:
            FakeFile: The created file record

        Raises:
            FileExistsError: If the path already exists
            IsADirectoryError: If the path is a directory
            NotADirectoryError: If a parent path is a file
        """
        path = self._normpath(path)
        if path in self._dirs:
            raise IsADirectoryError(errno.EISDIR, 'Is a directory', path)
        if path in self._files:
            raise FileExistsError(errno.EEXIST, 'File exists', path)
        self._create_parents(path)
        if isinstance(contents, str):
            contents = contents.encode(encoding or 'utf-8', errors or 'strict')
        elif not isinstance(contents, bytes):
            raise TypeError(f'contents must be str or bytes, not {type(contents).__name__}')
        mode = 0o666 if st_mode is None else st_mode & 0o7777
        now = time.time()
        file = FakeFile(
            path=path, content=contents, mode=mode, ino=self._next_ino(), nlink=1,
            atime=now, mtime=now, ctime=now,
        )
        self._files[path] = file
        return file

    def create_dir(self, path, st_mode=None):
        """
        Create a directory, the parent directories are created automatically.

        Args:
            path (str): Path of the directory
            st_mode (int, optional): Full directory mode like os.stat uses,
                e.g. 0o40755. Defaults to None, use 0o777.

        Returns:
            FakeDir: The created directory record

        Raises:
            FileExistsError: If the path already exists
            NotADirectoryError: If a parent path is a file
        """
        path = self._normpath(path)
        if path in self._dirs or path in self._files:
            raise FileExistsError(errno.EEXIST, 'File exists', path)
        self._create_parents(path)
        mode = 0o777 if st_mode is None else st_mode & 0o7777
        now = time.time()
        folder = FakeDir(
            path=path, mode=mode, ino=self._next_ino(), nlink=2,
            atime=now, mtime=now, ctime=now,
        )
        self._dirs[path] = folder
        return folder

    def get_object(self, path):
        """
        Get the record at a path.

        Args:
            path (str): Path of the file or directory

        Returns:
            FakeFile | FakeDir: Record at the path

        Raises:
            FileNotFoundError: If the path does not exist
        """
        path = self._normpath(path)
        entry = self._get_entry(path)
        if entry is None:
            raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)
        return entry

    def get_file(self, path):
        """
        Get the file record at a path.

        Args:
            path (str): Path of the file

        Returns:
            FakeFile: File record at the path

        Raises:
            FileNotFoundError: If the path is not a file
        """
        path = self._normpath(path)
        file = self._files.get(path)
        if file is None:
            raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)
        return file

    def get_dir(self, path):
        """
        Get the directory record at a path.

        Args:
            path (str): Path of the directory

        Returns:
            FakeDir: Directory record at the path

        Raises:
            FileNotFoundError: If the path is not a directory
        """
        path = self._normpath(path)
        folder = self._dirs.get(path)
        if folder is None:
            raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)
        return folder

    def remove(self, path):
        """
        Remove a file, or an empty directory.

        Args:
            path (str): Path to remove

        Raises:
            FileNotFoundError: If the path does not exist
            OSError: If the path is a non-empty directory
        """
        path = self._normpath(path)
        if path in self._files:
            del self._files[path]
            return
        if path in self._dirs:
            if self._has_children(path):
                raise OSError(errno.ENOTEMPTY, 'Directory not empty', path)
            del self._dirs[path]
            return
        raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)

    def rmtree(self, path):
        """
        Recursively remove a directory and its content, or a file.

        Args:
            path (str): Path to remove

        Raises:
            FileNotFoundError: If the path does not exist
            OSError: If the path is the root directory
        """
        path = self._normpath(path)
        if path in self._files:
            del self._files[path]
            return
        if path not in self._dirs:
            raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)
        if path == self.root_dir.path:
            raise OSError(errno.EBUSY, 'Cannot remove the root directory', path)
        prefix = self._child_prefix(path)
        for key in [key for key in self._files if key.startswith(prefix)]:
            del self._files[key]
        for key in [key for key in self._dirs if key.startswith(prefix)]:
            del self._dirs[key]
        del self._dirs[path]

    """
    open()
    """

    def open(self, file, mode='r', buffering=-1, encoding=None, errors=None,
             newline=None, closefd=True, opener=None):
        """
        Mock of the builtin open().

        Args:
            file (str): Path of the file
            mode (str): Open mode, any of r / w / a / x with optional
                b / t / +. Defaults to 'r'.
            buffering (int): Accepted for compatibility, ignored.
            encoding (str): Text encoding. Defaults to None, use utf-8.
            errors (str): Error handling of encoding.
                Defaults to None, use 'strict'.
            newline (str): Newline handling of text mode.
                Defaults to None, universal newlines.
            closefd (bool): Accepted for compatibility, ignored.
            opener (callable): Accepted for compatibility, ignored.

        Returns:
            FakeFileObject: The opened file object

        Raises:
            FileNotFoundError: If the file does not exist
            FileExistsError: If mode x is used on an existing file
            IsADirectoryError: If the path is a directory
            ValueError: If the mode is invalid
        """
        file = self._normpath(file)
        mode = str(mode)
        if not mode:
            raise ValueError(f'invalid mode: {mode!r}')
        action = mode[0]
        if action not in 'rwax':
            raise ValueError(f'invalid mode: {mode!r}')
        for char in mode[1:]:
            if char not in 'bt+':
                raise ValueError(f'invalid mode: {mode!r}')
        binary = 'b' in mode
        plus = '+' in mode
        if not binary:
            encoding = encoding or 'utf-8'
            errors = errors or 'strict'
        else:
            encoding = None
            errors = None
            newline = None

        entry = self._files.get(file)
        if entry is None and file in self._dirs:
            raise IsADirectoryError(errno.EISDIR, 'Is a directory', file)
        if action == 'r':
            if entry is None:
                raise FileNotFoundError(errno.ENOENT, 'No such file or directory', file)
        elif action == 'w':
            if entry is None:
                entry = self.create_file(file, st_mode=0o666)
            else:
                entry.content = b''
        elif action == 'a':
            if entry is None:
                entry = self.create_file(file, st_mode=0o666)
        elif action == 'x':
            if entry is not None:
                raise FileExistsError(errno.EEXIST, 'File exists', file)
            entry = self.create_file(file, st_mode=0o666)

        readable = action == 'r' or plus
        writable = action != 'r' or plus
        if action == 'a':
            position = len(entry.content)
        else:
            position = 0
        fobj = FakeFileObject(
            self, entry, mode, binary=binary, readable=readable, writable=writable,
            append=(action == 'a'), position=position, encoding=encoding,
            errors=errors, newline=newline, fd=self._take_fd(),
        )
        self._fds[fobj._fd] = fobj
        return fobj

    """
    os.path functions
    """

    def exists(self, path):
        """
        Mock of os.path.exists().

        Args:
            path (str):

        Returns:
            bool: Whether the path exists
        """
        return self._get_entry(self._normpath(path)) is not None

    def isfile(self, path):
        """
        Mock of os.path.isfile().

        Args:
            path (str):

        Returns:
            bool: Whether the path is a file
        """
        return self._normpath(path) in self._files

    def isdir(self, path):
        """
        Mock of os.path.isdir().

        Args:
            path (str):

        Returns:
            bool: Whether the path is a directory
        """
        return self._normpath(path) in self._dirs

    def islink(self, path):
        """
        Mock of os.path.islink(), symlinks are not supported.

        Args:
            path (str):

        Returns:
            bool: Always False
        """
        return False

    def lexists(self, path):
        """
        Mock of os.path.lexists(), no symlinks so same as exists().

        Args:
            path (str):

        Returns:
            bool: Whether the path exists
        """
        return self.exists(path)

    def getsize(self, path):
        """
        Mock of os.path.getsize().

        Args:
            path (str):

        Returns:
            int: Size of the file in bytes

        Raises:
            FileNotFoundError: If the path does not exist
        """
        return self.stat(path).st_size

    def stat(self, path, follow_symlinks=True, **kwargs):
        """
        Mock of os.stat().

        Args:
            path (str):
            follow_symlinks (bool): Accepted for compatibility
            **kwargs: Accepted for compatibility

        Returns:
            os.stat_result:

        Raises:
            FileNotFoundError: If the path does not exist
        """
        path = self._normpath(path)
        entry = self._get_entry(path)
        if entry is None:
            raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)
        return entry.stat()

    """
    os directory functions
    """

    def makedirs(self, path, mode=0o777, exist_ok=False):
        """
        Mock of os.makedirs(), creates the parent directories recursively.

        Args:
            path (str):
            mode (int): Permission bits of the created directories.
                Defaults to 0o777.
            exist_ok (bool): Don't raise if the path exists.
                Defaults to False.

        Raises:
            FileExistsError: If the path exists and exist_ok is False
            NotADirectoryError: If a parent path is a file
        """
        path = self._normpath(path)
        if path in self._dirs:
            if exist_ok:
                return
            raise FileExistsError(errno.EEXIST, 'File exists', path)
        if path in self._files:
            raise FileExistsError(errno.EEXIST, 'File exists', path)
        self._create_parents(path)
        now = time.time()
        self._dirs[path] = FakeDir(
            path=path, mode=mode & 0o7777, ino=self._next_ino(), nlink=2,
            atime=now, mtime=now, ctime=now,
        )

    def mkdir(self, path, mode=0o777):
        """
        Mock of os.mkdir(), the parent directory must exist.

        Args:
            path (str):
            mode (int): Permission bits of the directory. Defaults to 0o777.

        Raises:
            FileExistsError: If the path exists
            FileNotFoundError: If the parent directory does not exist
        """
        path = self._normpath(path)
        if path in self._dirs or path in self._files:
            raise FileExistsError(errno.EEXIST, 'File exists', path)
        parent, sep, _ = path.rpartition('/')
        if not sep:
            parent = '/'
        if parent not in self._dirs:
            raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)
        now = time.time()
        self._dirs[path] = FakeDir(
            path=path, mode=mode & 0o7777, ino=self._next_ino(), nlink=2,
            atime=now, mtime=now, ctime=now,
        )

    def rmdir(self, path):
        """
        Mock of os.rmdir(), the directory must be empty.

        Args:
            path (str):

        Raises:
            FileNotFoundError: If the path does not exist
            NotADirectoryError: If the path is a file
            OSError: If the directory is not empty
        """
        path = self._normpath(path)
        if path in self._files:
            raise NotADirectoryError(errno.ENOTDIR, 'Not a directory', path)
        if path not in self._dirs:
            raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)
        if self._has_children(path):
            raise OSError(errno.ENOTEMPTY, 'Directory not empty', path)
        del self._dirs[path]

    def unlink(self, path):
        """
        Mock of os.unlink() and os.remove().

        Args:
            path (str):

        Raises:
            FileNotFoundError: If the path does not exist
            IsADirectoryError: If the path is a directory
        """
        path = self._normpath(path)
        if path in self._files:
            del self._files[path]
            return
        if path in self._dirs:
            raise IsADirectoryError(errno.EISDIR, 'Is a directory', path)
        raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)

    def rename(self, src, dst):
        """
        Mock of os.rename().

        On Windows renaming over an existing file raises, on POSIX the
        file is replaced, like the real os.

        Args:
            src (str): Source path
            dst (str): Target path

        Raises:
            FileNotFoundError: If the source does not exist
            FileExistsError: On Windows, if the target exists
        """
        self._move(src, dst, overwrite=not IS_WINDOWS)

    def replace(self, src, dst):
        """
        Mock of os.replace(), the target is always replaced.

        Args:
            src (str): Source path
            dst (str): Target path

        Raises:
            FileNotFoundError: If the source does not exist
        """
        self._move(src, dst, overwrite=True)

    def _move(self, src, dst, overwrite):
        """
        Move a file or a directory, used by rename() and replace().

        Args:
            src (str): Source path
            dst (str): Target path
            overwrite (bool): Whether to replace an existing target
        """
        src = self._normpath(src)
        dst = self._normpath(dst)
        if src in self._files:
            if dst in self._dirs:
                raise IsADirectoryError(errno.EISDIR, 'Is a directory', dst)
            if dst in self._files:
                if not overwrite:
                    raise FileExistsError(errno.EEXIST, 'File exists', dst)
                del self._files[dst]
            file = self._files.pop(src)
            file.path = dst
            self._files[dst] = file
            return
        if src in self._dirs:
            if src == self.root_dir.path:
                raise OSError(errno.EBUSY, 'Cannot move the root directory', src)
            if dst in self._files:
                raise NotADirectoryError(errno.ENOTDIR, 'Not a directory', dst)
            if dst in self._dirs:
                if not overwrite:
                    raise FileExistsError(errno.EEXIST, 'File exists', dst)
                if self._has_children(dst):
                    raise OSError(errno.ENOTEMPTY, 'Directory not empty', dst)
                del self._dirs[dst]
            folder = self._dirs.pop(src)
            folder.path = dst
            self._dirs[dst] = folder
            # move the descendants, keys are snapshotted first
            prefix = self._child_prefix(src)
            for key in [key for key in self._files if key.startswith(prefix)]:
                new_key = dst + key[len(src):]
                self._files[new_key] = self._files.pop(key)
                self._files[new_key].path = new_key
            for key in [key for key in self._dirs if key.startswith(prefix)]:
                new_key = dst + key[len(src):]
                self._dirs[new_key] = self._dirs.pop(key)
                self._dirs[new_key].path = new_key
            return
        raise FileNotFoundError(errno.ENOENT, 'No such file or directory', src)

    def scandir(self, path):
        """
        Mock of os.scandir(), returns an iterator of FakeDirEntry.

        Args:
            path (str):

        Returns:
            FakeScandirIterator: Iterator of the directory entries

        Raises:
            FileNotFoundError: If the path does not exist
            NotADirectoryError: If the path is a file
        """
        path = self._normpath(path)
        if path not in self._dirs:
            if path in self._files:
                raise NotADirectoryError(errno.ENOTDIR, 'Not a directory', path)
            raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)
        prefix = self._child_prefix(path)
        entries = []
        for key in self._files:
            if key.startswith(prefix):
                name = key[len(prefix):]
                if '/' not in name:
                    entries.append(FakeDirEntry(name, key, False, self._files[key].stat()))
        for key in self._dirs:
            if key.startswith(prefix):
                name = key[len(prefix):]
                if '/' not in name:
                    entries.append(FakeDirEntry(name, key, True, self._dirs[key].stat()))
        return FakeScandirIterator(entries)

    def listdir(self, path):
        """
        Mock of os.listdir(), names are sorted for deterministic tests.

        Args:
            path (str):

        Returns:
            list[str]: Names of the directory entries

        Raises:
            FileNotFoundError: If the path does not exist
            NotADirectoryError: If the path is a file
        """
        path = self._normpath(path)
        if path not in self._dirs:
            if path in self._files:
                raise NotADirectoryError(errno.ENOTDIR, 'Not a directory', path)
            raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)
        prefix = self._child_prefix(path)
        names = []
        for key in self._files:
            if key.startswith(prefix):
                name = key[len(prefix):]
                if '/' not in name:
                    names.append(name)
        for key in self._dirs:
            if key.startswith(prefix):
                name = key[len(prefix):]
                if '/' not in name:
                    names.append(name)
        return sorted(names)

    """
    Low level fd functions
    """

    def os_open(self, path, flags, mode=0o777):
        """
        Mock of os.open(), returns a fake file descriptor.

        Args:
            path (str):
            flags (int): Open flags, e.g. os.O_CREAT | os.O_WRONLY
            mode (int): Permission bits of a created file. Defaults to 0o777.

        Returns:
            int: Fake file descriptor

        Raises:
            FileNotFoundError: If the file does not exist and O_CREAT is not set
            FileExistsError: If O_CREAT | O_EXCL is used on an existing file
            IsADirectoryError: If the path is a directory
        """
        path = self._normpath(path)
        accmode = flags & O_ACCMODE
        readable = accmode == os.O_RDONLY or accmode == os.O_RDWR
        writable = accmode == os.O_WRONLY or accmode == os.O_RDWR
        if path in self._files:
            entry = self._files[path]
            if flags & os.O_CREAT and flags & os.O_EXCL:
                raise FileExistsError(errno.EEXIST, 'File exists', path)
            if flags & os.O_TRUNC:
                entry.content = b''
        elif path in self._dirs:
            raise IsADirectoryError(errno.EISDIR, 'Is a directory', path)
        else:
            if not flags & os.O_CREAT:
                raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)
            entry = self.create_file(path, st_mode=mode & 0o7777)
        append = bool(flags & os.O_APPEND)
        fobj = FakeFileObject(
            self, entry, '', binary=True, readable=readable, writable=writable,
            append=append, position=len(entry.content) if append else 0,
            encoding=None, errors=None, newline=None, fd=self._take_fd(),
        )
        self._fds[fobj._fd] = fobj
        return fobj._fd

    def os_write(self, fd, data):
        """
        Mock of os.write().

        Args:
            fd (int): Fake file descriptor
            data (bytes): Data to write

        Returns:
            int: Number of bytes written

        Raises:
            OSError: If the fd is not open
        """
        fobj = self._fds.get(fd)
        if fobj is None:
            raise OSError(errno.EBADF, 'Bad file descriptor')
        return fobj.write(data)

    def os_close(self, fd):
        """
        Mock of os.close().

        Args:
            fd (int): Fake file descriptor

        Raises:
            OSError: If the fd is not open
        """
        fobj = self._fds.get(fd)
        if fobj is None:
            raise OSError(errno.EBADF, 'Bad file descriptor')
        fobj.close()

    def os_fsync(self, fd):
        """
        Mock of os.fsync(), a no-op because content is already in memory.

        Args:
            fd (int): Fake file descriptor

        Raises:
            OSError: If the fd is not open
        """
        if fd not in self._fds:
            raise OSError(errno.EBADF, 'Bad file descriptor')

    def os_fstat(self, fd):
        """
        Mock of os.fstat().

        Args:
            fd (int): Fake file descriptor

        Returns:
            os.stat_result: Stat result of the open file

        Raises:
            OSError: If the fd is not open
        """
        fobj = self._fds.get(fd)
        if fobj is None:
            raise OSError(errno.EBADF, 'Bad file descriptor')
        return fobj._entry.stat()

    """
    Other os functions
    """

    def utime(self, path, times=None, ns=None):
        """
        Mock of os.utime().

        Args:
            path (str):
            times (tuple[float, float]): (atime, mtime) to set.
                Defaults to None, use the current time.
            ns (tuple[int, int]): (atime, mtime) in nanoseconds.
                Defaults to None.

        Raises:
            FileNotFoundError: If the path does not exist
        """
        path = self._normpath(path)
        entry = self._get_entry(path)
        if entry is None:
            raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)
        if ns is not None:
            atime, mtime = ns[0] / 1e9, ns[1] / 1e9
        elif times is not None:
            atime, mtime = times
        else:
            atime = mtime = time.time()
        entry.atime = atime
        entry.mtime = mtime

    def chmod(self, path, mode, **kwargs):
        """
        Mock of os.chmod().

        Args:
            path (str):
            mode (int): New permission bits
            **kwargs: Accepted for compatibility

        Raises:
            FileNotFoundError: If the path does not exist
        """
        path = self._normpath(path)
        entry = self._get_entry(path)
        if entry is None:
            raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)
        entry.mode = mode & 0o7777

    def getcwd(self):
        """
        Mock of os.getcwd().

        Returns:
            str: The fake current working directory
        """
        return self._cwd

    def chdir(self, path):
        """
        Mock of os.chdir().

        Args:
            path (str):

        Raises:
            FileNotFoundError: If the path does not exist
            NotADirectoryError: If the path is a file
        """
        path = self._normpath(path)
        if path not in self._dirs:
            if path in self._files:
                raise NotADirectoryError(errno.ENOTDIR, 'Not a directory', path)
            raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)
        self._cwd = path

    """
    Activate / deactivate the mock
    """

    def _iter_patches(self):
        """
        Yield the (module, name, fake) targets to patch.

        Yields:
            tuple[module, str, callable]: Patch targets
        """
        yield builtins, 'open', self.open
        yield io, 'open', self.open
        yield os.path, 'exists', self.exists
        yield os.path, 'isfile', self.isfile
        yield os.path, 'isdir', self.isdir
        yield os.path, 'islink', self.islink
        yield os.path, 'lexists', self.lexists
        yield os.path, 'getsize', self.getsize
        yield os, 'stat', self.stat
        yield os, 'lstat', self.stat
        yield os, 'fstat', self.os_fstat
        yield os, 'scandir', self.scandir
        yield os, 'listdir', self.listdir
        yield os, 'makedirs', self.makedirs
        yield os, 'mkdir', self.mkdir
        yield os, 'rmdir', self.rmdir
        yield os, 'unlink', self.unlink
        yield os, 'remove', self.unlink
        yield os, 'rename', self.rename
        yield os, 'replace', self.replace
        yield os, 'open', self.os_open
        yield os, 'write', self.os_write
        yield os, 'close', self.os_close
        yield os, 'fsync', self.os_fsync
        yield os, 'utime', self.utime
        yield os, 'chmod', self.chmod
        yield os, 'getcwd', self.getcwd
        yield os, 'chdir', self.chdir

    def activate(self, monkeypatch=None):
        """
        Patch builtins.open, io.open and the os / os.path functions.

        Args:
            monkeypatch (pytest.MonkeyPatch | None): When given, the
                patches are registered on it and undone automatically.
                When None, the patches are undone by deactivate().

        Returns:
            FakeFilesystem: Self
        """
        if monkeypatch is None:
            for module, name, fake in self._iter_patches():
                original = getattr(module, name)
                setattr(module, name, fake)
                self._saved.append((module, name, original))
        else:
            for module, name, fake in self._iter_patches():
                monkeypatch.setattr(module, name, fake)
        return self

    def deactivate(self):
        """
        Undo the patches installed by activate() without monkeypatch.
        """
        for module, name, original in reversed(self._saved):
            setattr(module, name, original)
        self._saved.clear()

    def __enter__(self):
        return self.activate()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.deactivate()
        return False
