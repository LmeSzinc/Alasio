from alasio.ext.path.atomic import *
from alasio.ext.path.calc import *
from alasio.ext.path.iter import *


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
    def new(cls, path):
        """
        Create a PathStr object from str
        Don't do "PathStr(path)", path should be normalized first

        Args:
            path (str): Path string

        Returns:
            PathStr: Normalized path string
        """
        return cls(normpath(path))

    @classmethod
    def cwd(cls):
        """
        Create a PathStr object from current path

        Returns:
            PathStr: Current working directory
        """
        return cls(os.getcwd())

    def chdir_here(self):
        """
        Change dir to this path

        Examples:
            PathStr.new(__file__).uppath(3).chdir_here()

        Returns:
            PathStr: Self
        """
        os.chdir(self)
        return self

    """
    Path calculations
    """

    def normpath(self):
        """
        Equivalent to os.path.normpath(self)

        Returns:
            PathStr: Normalized path
        """
        return PathStr(normpath(self))

    def uppath(self, up=1):
        """
        Equivalent to os.path.join(self, '../')

        Args:
            up (int): Directory upward level. Defaults to 1.

        Returns:
            PathStr: Path to upper directory
        """
        return PathStr(uppath(self, up=up))

    def joinpath(self, path):
        """
        Equivalent to os.path.join(self, path)
        but "./" and "../" is not available, to do "../" use uppath() instead

        Args:
            path (str): Path to join

        Returns:
            PathStr: Joined path
        """
        return PathStr(joinnormpath(self, path))

    def __truediv__(self, other):
        """
        root = PathStr.new(__file__).uppath(3)
        path = root / "alas.json"

        Args:
            other (str): Path to join

        Returns:
            PathStr: Joined path
        """
        return PathStr(joinnormpath(self, other))

    def abspath(self):
        """
        Equivalent to os.path.abspath(self, path)

        Note that, to improve performance, this method should be used less
        Reuse root folder is recommended:
            root = PathStr.new(__file__).uppath(3)
            path = root.joinpath('config/alas.json')
        instead of creating everytime:
            path = PathStr.new('config/alas.json').abspath()

        Returns:
            PathStr: Absolute path
        """
        return PathStr(abspath(self))

    def is_abspath(self):
        """
        Equivalent to os.path.isabs(self, path)

        Returns:
            bool: Whether path is absolute
        """
        return is_abspath(self)

    @property
    def name(self):
        """
        /abc/def.png -> def.png
        /abc/def     -> def
        /abc/.git    -> .git

        Returns:
            str: Filename
        """
        return get_name(self)

    @property
    def stem(self):
        """
        /abc/def.png -> def
        /abc/def     -> def
        /abc/.git    -> ""

        Returns:
            str: Filename without last extension
        """
        return get_stem(self)

    @property
    def rootstem(self):
        """
        /abc/def.part1.png -> def
        /abc/def.png -> def
        /abc/def     -> def
        /abc/.git    -> ""

        Returns:
            str: Filename without any extension
        """
        return get_rootstem(self)

    @property
    def suffix(self):
        """
        /abc/def.png -> .png
        /abc/def     -> ""
        /abc/.git    -> .git

        Returns:
            str: Last extension
        """
        return get_suffix(self)

    @property
    def multisuffix(self):
        """
        /abc/def.part1.png -> .part1.png
        /abc/def.png -> .png
        /abc/def     -> ""
        /abc/.git    -> .git

        Returns:
            str: All extensions
        """
        return get_multisuffix(self)

    def with_name(self, name):
        """
        /abc/def.png -> /abc/xxx
        /abc/def     -> /abc/xxx
        /abc/.git    -> /abc/xxx

        Args:
            name (str): New filename

        Returns:
            PathStr: Path with new filename
        """
        return PathStr(with_name(self, name))

    def with_stem(self, name):
        """
        /abc/def.png -> /abc/xxx.png
        /abc/def     -> /abc/xxx
        /abc/.git    -> /abc/xxx.git

        Args:
            name (str): New stem (filename without extension)

        Returns:
            PathStr: Path with new stem
        """
        return PathStr(with_stem(self, name))

    def with_rootstem(self, name):
        """
        /abc/def.part1.png -> /abc/xxx.part1.png
        /abc/def.png -> /abc/xxx.png
        /abc/def     -> /abc/xxx
        /abc/.git    -> /abc/xxx.git

        Args:
            name (str): New stem (filename without extension)

        Returns:
            PathStr: Path with new stem
        """
        return PathStr(with_rootstem(self, name))

    def with_suffix(self, name):
        """
        /abc/def.png -> /abc/def.xxx
        /abc/def     -> /abc/def.xxx
        /abc/.git    -> /abc/.xxx

        Args:
            name (str): New extension

        Returns:
            PathStr: Path with new extension
        """
        return PathStr(with_suffix(self, name))

    def with_multisuffix(self, name):
        """
        /abc/def.part1.png -> /abc/def.xxx.xxx
        /abc/def     -> /abc/def.xxx
        /abc/.git    -> /abc/.xxx

        Args:
            name (str): New extension

        Returns:
            PathStr: Path with new extension
        """
        return PathStr(with_multisuffix(self, name))

    def to_posix(self):
        """
        Convert to posix path
        Note that this method returns str instead of PathStr,
        since path is no longer normalized.

        Returns:
            str:
        """
        return to_posix(self)

    def to_python_import(self):
        """
        Convert path to python dot-like import
        path/to/python.py -> path.to.python

        Returns:
            str:
        """
        return to_python_import(self)

    def subpath_to(self, root):
        """
        Calculate sub-path to `root`.
        If `self` is not sub-path to `root`, return `self`

        Args:
            root (PathStr):

        Returns:
            PathStr
        """
        return PathStr(subpath_to(self, root))

    @property
    def is_tmp_file(self):
        """
        Check if path is a temporary file

        Returns:
            bool: Whether path is a temporary file
        """
        return is_tmp_file(self)

    def to_tmp_file(self):
        """
        Convert a filename or directory name to tmp
        filename.sTD2kF.tmp -> filename

        Returns:
            PathStr: Temporary file path
        """
        return PathStr(to_tmp_file(self))

    def to_nontmp_file(self):
        """
        Convert a tmp filename or directory name to original file
        filename.sTD2kF.tmp -> filename

        Returns:
            PathStr: Original file path
        """
        return PathStr(to_nontmp_file(self))

    """
    File read/write

    Note that always using atomic method is recommended,
    Call simple read/write on tmp files only.
    """

    def _file_write(self, data):
        """
        Write data into file, auto create directory
        Auto determines write mode based on the type of data.

        Args:
            data (str | bytes): String or bytes to write

        Returns:
            PathStr: Self
        """
        file_write(self, data)
        return self

    def _file_write_stream(self, data_generator):
        """
        Only creates a file if the generator yields at least one data chunk.
        Auto determines write mode based on the type of first chunk.

        Args:
            data_generator (Iterable): An iterable that yields data chunks (str or bytes)

        Returns:
            PathStr: Self
        """
        file_write_stream(self, data_generator)
        return self

    def atomic_write(self, data):
        """
        Atomic file write with minimal IO operation
        and handles cases where file might be read by another process.

        os.replace() is an atomic operation among all OS,
        we write to temp file then do os.replace()

        Args:
            data (str | bytes): String or bytes to write

        Returns:
            PathStr: Self
        """
        atomic_write(self, data)
        return self

    def atomic_write_stream(self, data_generator):
        """
        Atomic file write with streaming data support.
        Handles cases where file might be read by another process.

        os.replace() is an atomic operation among all OS,
        we write to temp file then do os.replace()

        Args:
            data_generator (Iterable): An iterable that yields data chunks (str or bytes)

        Returns:
            PathStr: Self
        """
        atomic_write_stream(self, data_generator)
        return self

    def atomic_read_text(self, encoding='utf-8', errors='strict'):
        """
        Atomic file read with minimal IO operation

        Args:
            encoding (str): Text encoding. Defaults to 'utf-8'.
            errors (str): Error handling strategy. Defaults to 'strict'.
                'strict', 'ignore', 'replace' and any other errors mode in open()

        Returns:
            str: File content
        """
        return atomic_read_text(self, encoding=encoding, errors=errors)

    def atomic_read_text_stream(self, encoding='utf-8', errors='strict', chunk_size=8192):
        """
        Read text file content as stream

        Args:
            encoding (str): Text encoding. Defaults to 'utf-8'.
            errors (str): Error handling strategy. Defaults to 'strict'.
                'strict', 'ignore', 'replace' and any other errors mode in open()
            chunk_size (int): Size of chunks to read. Defaults to 8192.

        Returns:
            Iterable[str]: Generator yielding file content chunks
        """
        return atomic_read_text_stream(self, encoding=encoding, errors=errors, chunk_size=chunk_size)

    def atomic_read_bytes(self):
        """
        Atomic file read with minimal IO operation

        Returns:
            bytes: File content
        """
        return atomic_read_bytes(self)

    def atomic_read_bytes_stream(self, chunk_size=8192):
        """
        Read binary file content as stream

        Args:
            chunk_size (int): Size of chunks to read. Defaults to 8192.

        Returns:
            Iterable[bytes]: Generator yielding file content chunks
        """
        return atomic_read_bytes_stream(self, chunk_size=chunk_size)

    def _file_remove(self):
        """
        Remove a file non-atomic

        Returns:
            PathStr: Self
        """
        file_remove(self)
        return self

    def atomic_remove(self):
        """
        Atomic file remove

        Returns:
            PathStr: Self
        """
        atomic_remove(self)
        return self

    def _folder_rmtree(self, may_symlinks=True):
        """
        Recursively remove a folder and its content

        Args:
            may_symlinks (bool): Whether to handle symlinks. Defaults to True.
                False if you already know it's not a symlink

        Returns:
            bool: If success
        """
        return folder_rmtree(self, may_symlinks=may_symlinks)

    def atomic_rmtree(self):
        """
        Atomic folder rmtree
        Rename folder as temp folder and remove it,
        folder can be removed by atomic_failure_cleanup at next startup if remove gets interrupted

        Returns:
            PathStr: Self
        """
        atomic_rmtree(self)
        return self

    def atomic_replace(self, replace_to):
        """
        Replace file or directory

        Args:
            replace_to (str): Target path

        Returns:
            PathStr: Self

        Raises:
            PermissionError: (Windows only) If another process is still reading the file and all retries failed
        """
        atomic_replace(self, replace_to)
        return self

    def atomic_failure_cleanup(self, recursive=False):
        """
        Cleanup remaining temp file under given path.
        In most cases there should be no remaining temp files unless write process get interrupted.

        This method should only be called at startup
        to avoid deleting temp files that another process is writing.

        Args:
            recursive (bool): Whether to clean subdirectories. Defaults to False.

        Returns:
            PathStr: Self
        """
        atomic_failure_cleanup(self, recursive=recursive)
        return self

    """
    Iter folder
    """

    def iter_files(self, ext=''):
        """
        Iter full filepath of files in folder with the good performance

        Args:
            ext (str): If ext is given, iter files with extension only.
                If ext is empty, iter all files. Defaults to ''.

        Yields:
            PathStr: Full path
        """
        for path in iter_files(self, ext=ext):
            yield PathStr(path)

    def iter_filenames(self, ext=''):
        """
        Iter filename of files in folder with the good performance

        Args:
            ext (str): If ext is given, iter files with extension only.
                If ext is empty, iter all files. Defaults to ''.

        Yields:
            str: Filename
        """
        yield from iter_filenames(self, ext=ext)

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

    def exists(self):
        """
        Check if path exists

        Returns:
            bool: Whether path exists
        """
        return os.path.exists(self)

    def isfile(self):
        """
        Check if path is a file

        Returns:
            bool: Whether path is a file
        """
        return os.path.isfile(self)

    def isdir(self):
        """
        Check if path is a directory

        Returns:
            bool: Whether path is a directory
        """
        return os.path.isdir(self)

    def islink(self):
        """
        Check if path is a symbolic link

        Returns:
            bool: Whether path is a symbolic link
        """
        return os.path.islink(self)

    def stat(self, follow_symlinks=True):
        """
        Get file/directory stats

        Args:
            follow_symlinks (bool): Whether to follow symbolic links. Defaults to True.

        Returns:
            os.stat_result: File stats
        """
        return os.stat(self, follow_symlinks=follow_symlinks)

    def makedirs(self, mode=0o777, exist_ok=True):
        """
        Create directories recursively

        Args:
            mode (int): Directory permissions. Defaults to 0o777.
            exist_ok (bool): Don't raise error if directory exists. Defaults to True.
        """
        os.makedirs(self, mode=mode, exist_ok=exist_ok)

    """
    Other
    """

    def md5(self):
        """
        Calculate MD5 hash of file

        Returns:
            str: 9c1ff3057fbdd2de7acabfa9515a1641
                or "" if file not exist
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
