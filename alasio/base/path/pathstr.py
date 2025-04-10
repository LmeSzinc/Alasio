from alasio.base.path.atomic import *
from alasio.base.path.calc import *
from alasio.base.path.op import *


class PathStr(str):
    """
    A faster alternative of Pathlib

    If you really need high performance path calculations, use path functions instead

    joinpath:
        function joinpath(root, path)     0.26us
        PathStr  root.joinpath(path)      0.71us
        os path  os.path.join(root, path) 2.55us
        pathlib  root.joinpath(path)      2.94us
    """
    __slots__ = ()

    @classmethod
    def new(cls, path: str) -> "PathStr":
        """
        Create a PathStr object from str
        Don't do "PathStr(path)", path should be normalized first
        """
        return cls(normpath(path))

    @classmethod
    def cwd(cls) -> "PathStr":
        """
        Create a PathStr object from current path
        """
        return cls(os.getcwd())

    def chdir_here(self) -> "PathStr":
        """
        Change dir to this path

        Examples:
            PathStr.new(__file__).uppath(3).chdir_here()
        """
        os.chdir(self)
        return self

    """
    Path calculations
    """

    def normpath(self) -> "PathStr":
        """
        Equivalent to os.path.normpath(self)
        """
        return PathStr(normpath(self))

    def uppath(self, up: int = 1) -> "PathStr":
        """
        Equivalent to os.path.join(self, '../')

        Args:
            up: Directory upward level
        """
        return PathStr(uppath(self, up=up))

    def joinpath(self, path) -> "PathStr":
        """
        Equivalent to os.path.join(self, path)
        but "./" and "../" is not available, to do "../" use uppath() instead
        """
        return PathStr(joinnormpath(self, path))

    def __truediv__(self, other: str) -> "PathStr":
        """
        root = PathStr.new(__file__).uppath(3)
        path = root / "alas.json"
        """
        return PathStr(joinnormpath(self, other))

    def abspath(self) -> "PathStr":
        """
        Equivalent to os.path.abspath(self, path)

        Note that, to improve performance, this method should be used less
        Reuse root folder is recommended:
            root = PathStr.new(__file__).uppath(3)
            path = root.joinpath('config/alas.json')
        instead of creating everytime:
            path = PathStr.new('config/alas.json').abspath()
        """
        return PathStr(abspath(self))

    def is_abspath(self) -> bool:
        """
        Equivalent to os.path.isabs(self, path)
        """
        return is_abspath(self)

    @property
    def name(self) -> str:
        """
        /abc/def.png -> def.png
        /abc/def     -> def
        /abc/.git    -> .git
        """
        return get_name(self)

    @property
    def stem(self) -> str:
        """
        /abc/def.png -> def
        /abc/def     -> def
        /abc/.git    -> ""
        """
        return get_stem(self)

    @property
    def suffix(self) -> str:
        """
        /abc/def.png -> .png
        /abc/def     -> ""
        /abc/.git    -> .git
        """
        return get_suffix(self)

    def with_name(self, name: str) -> "PathStr":
        """
        /abc/def.png -> /abc/xxx
        /abc/def     -> /abc/xxx
        /abc/.git    -> /abc/xxx
        """
        return PathStr(with_name(self, name))

    def with_stem(self, name: str) -> "PathStr":
        """
        /abc/def.png -> /abc/xxx.png
        /abc/def     -> /abc/xxx
        /abc/.git    -> /abc/xxx.git
        """
        return PathStr(with_stem(self, name))

    def with_suffix(self, name: str) -> "PathStr":
        """
        /abc/def.png -> /abc/def.xxx
        /abc/def     -> /abc/def.xxx
        /abc/.git    -> /abc/.xxx
        """
        return PathStr(with_suffix(self, name))

    @property
    def is_tmp_file(self) -> bool:
        return is_tmp_file(self)

    def to_tmp_file(self) -> "PathStr":
        """
        Convert a filename or directory name to tmp
        filename.sTD2kF.tmp -> filename
        """
        return PathStr(to_tmp_file(self))

    def to_nontmp_file(self) -> "PathStr":
        """
        Convert a tmp filename or directory name to original file
        filename.sTD2kF.tmp -> filename
        """
        return PathStr(to_nontmp_file(self))

    """
    File read/write

    Note that always using atomic method is recommended,
    Call simple read/write on tmp files only.
    """

    def _file_write(self, data: Union[str, bytes]) -> "PathStr":
        """
        Write data into file, auto create directory
        Auto determines write mode based on the type of data.

        Args:
            data: String or bytes to write
        """
        file_write(self, data)
        return self

    def _file_write_stream(self, data_generator) -> "PathStr":
        """
        Only creates a file if the generator yields at least one data chunk.
        Auto determines write mode based on the type of first chunk.

        Args:
            data_generator: An iterable that yields data chunks (str or bytes)
        """
        file_write_stream(self, data_generator)
        return self

    def atomic_write(self, data: Union[str, bytes]) -> "PathStr":
        """
        Atomic file write with minimal IO operation
        and handles cases where file might be read by another process.

        os.replace() is an atomic operation among all OS,
        we write to temp file then do os.replace()

        Args:
            data: String or bytes to write
        """
        atomic_write(self, data)
        return self

    def atomic_write_stream(self, data_generator) -> "PathStr":
        """
        Atomic file write with streaming data support.
        Handles cases where file might be read by another process.

        os.replace() is an atomic operation among all OS,
        we write to temp file then do os.replace()

        Args:
            data_generator: An iterable that yields data chunks (str or bytes)
        """
        atomic_write_stream(self, data_generator)
        return self

    def atomic_read_text(
            self,
            encoding: str = 'utf-8',
            errors: str = 'strict'
    ) -> str:
        """
        Atomic file read with minimal IO operation

        Args:
            encoding:
            errors: 'strict', 'ignore', 'replace' and any other errors mode in open()
        """
        return atomic_read_text(self, encoding=encoding, errors=errors)

    def atomic_read_text_stream(
            self,
            encoding: str = 'utf-8',
            errors: str = 'strict',
            chunk_size: int = 8192
    ) -> Iterable[str]:
        """
        Args:
            encoding:
            errors: 'strict', 'ignore', 'replace' and any other errors mode in open()
            chunk_size:
        """
        return atomic_read_text_stream(self, encoding=encoding, errors=errors, chunk_size=chunk_size)

    def atomic_read_bytes(self) -> bytes:
        """
        Atomic file read with minimal IO operation
        """
        return atomic_read_bytes(self)

    def atomic_read_bytes_stream(self, chunk_size: int = 8192) -> Iterable[bytes]:
        """
        Args:
            chunk_size:
        """
        return atomic_read_bytes_stream(self, chunk_size=chunk_size)

    def _file_remove(self) -> "PathStr":
        """
        Remove a file non-atomic
        """
        file_remove(self)
        return self

    def atomic_remove(self) -> "PathStr":
        """
        Atomic file remove
        """
        atomic_remove(self)
        return self

    def _folder_rmtree(self, may_symlinks=True):
        """
        Recursively remove a folder and its content

        Args:
            may_symlinks: Default to True
                False if you already know it's not a symlink

        Returns:
            bool: If success
        """
        return folder_rmtree(self, may_symlinks=may_symlinks)

    def atomic_rmtree(self) -> "PathStr":
        """
        Atomic folder rmtree
        Rename folder as temp folder and remove it,
        folder can be removed by atomic_failure_cleanup at next startup if remove gets interrupted
        """
        atomic_rmtree(self)
        return self

    def atomic_replace(self, replace_to: str) -> "PathStr":
        """
        Replace file or directory

        Raises:
            PermissionError: (Windows only) If another process is still reading the file and all retries failed
        """
        atomic_replace(self, replace_to)
        return self

    def atomic_failure_cleanup(self, recursive: bool = False) -> "PathStr":
        """
        Cleanup remaining temp file under given path.
        In most cases there should be no remaining temp files unless write process get interrupted.

        This method should only be called at startup
        to avoid deleting temp files that another process is writing.
        """
        atomic_failure_cleanup(self, recursive=recursive)
        return self

    """
    Iter folder
    """

    def iter_files(self: str, suffix: str = ''):
        """
        Iter full filepath of files in folder with the good performance

        Args:
            suffix: If suffix is given, iter files with suffix only
                If suffix is empty, iter all files

        Yields:
            PathStr: Full path
        """
        for path in iter_files(self, suffix=suffix):
            yield PathStr(path)

    def iter_filenames(self: str, suffix: str = ''):
        """
        Iter filename of files in folder with the good performance

        Args:
            suffix: If suffix is given, iter files with suffix only
                If suffix is empty, iter all files

        Yields:
            str: Filename
        """
        yield from iter_filenames(self, suffix=suffix)

    def iter_folders(self):
        """
        Iter full filepath of directories in folder with the good performance

        Yields:
            PathStr: Full path
        """
        for path in iter_folders(self):
            yield PathStr(path)

    def iter_foldernames(self):
        """
        Iter name of directories in folder with the good performance

        Yields:
            str: Folder name
        """
        yield from iter_foldernames(self)

    """
    Wrap os module, imitating Pathlib
    """

    def exists(self) -> bool:
        return os.path.exists(self)

    def isfile(self) -> bool:
        return os.path.isfile(self)

    def isdir(self) -> bool:
        return os.path.isdir(self)

    def islink(self) -> bool:
        return os.path.islink(self)

    def stat(self, follow_symlinks=True) -> os.stat_result:
        return os.stat(self, follow_symlinks=follow_symlinks)

    def makedirs(self, mode=0o777, exist_ok=True):
        os.makedirs(self, mode=mode, exist_ok=exist_ok)

    """
    Other
    """

    def md5(self):
        """
        Returns:
            str: 9c1ff3057fbdd2de7acabfa9515a1641
                or "" if file not eixst
        """
        import hashlib
        md5_hash = hashlib.md5()
        updated = False
        for chunk in atomic_read_bytes_stream(self):
            md5_hash.update(chunk)
            updated = True
        # File not exist
        if not updated:
            return ''

        d = md5_hash.hexdigest()
        return d
