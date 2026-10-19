import gzip
import io
import tarfile
import time
import zipfile
from contextlib import ExitStack, suppress

from alasio.ext.cache import cached_property
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import file_remove

# Permission of every member of the built archives.
# The content is packed from git objects, which record a file mode, but a pure
# Python library has no member that needs to be executable: one fixed mode
# keeps the archives reproducible, and keeps the 0o600 default of zipfile away
# from tools that honour the permission bits of a zip member.
FILE_MODE = 0o644

# Compression level of the wheel members.
# A wheel is compressed once by the build and decompressed many times by the
# devices, so the highest level is worth the build time.
ZIP_COMPRESS_LEVEL = 9

# Compression level of the source distribution.
GZIP_COMPRESS_LEVEL = 9

# The MS-DOS date of the ZIP format starts at 1980-01-01, an earlier timestamp
# cannot be stored in a zip member.
ZIP_MIN_MTIME = 315532800


def zip_date_time(mtime):
    """
    Convert a unix timestamp to the date_time of a ZIP member.

    ZIP stores a local time without a timezone, UTC is used so a build does
    not depend on the timezone of the machine that runs it.

    Args:
        mtime (int): Unix timestamp in UTC+0, the commit time

    Returns:
        tuple[int, int, int, int, int, int]:
            (year, month, day, hour, minute, second)
    """
    st = time.gmtime(max(int(mtime), ZIP_MIN_MTIME))
    return st.tm_year, st.tm_mon, st.tm_mday, st.tm_hour, st.tm_min, st.tm_sec


def _remove_tmp_file(file):
    """
    Remove the tmp file of a failed build, best effort.

    Args:
        file (str): Temporary file path
    """
    try:
        file_remove(file)
    except OSError:
        pass


def _open_wb(file):
    """
    Open a file for writing, create the parent folder only when it is missing.

    The folder of a build target usually exists, so the open is tried first
    and a missing folder is created on FileNotFoundError, no extra IO is done
    on the normal path.

    Args:
        file (str): File path to open

    Returns:
        io.BufferedWriter: Opened file
    """
    try:
        return open(file, 'wb')
    except FileNotFoundError:
        pass
    PathStr.new(file).uppath().makedirs(exist_ok=True)
    return open(file, 'wb')


class ZipFileExtend:
    """
    A zip archive whose members are written with a fixed metadata.

    The archive is written into a tmp file of the target, and the tmp file
    replaces the target only when the context exits without an exception: a
    failed build neither leaves a broken artifact nor a tmp file behind. The
    archive is opened by the first member, an unused context writes no file.

    zipfile.writestr() with a file name writes mode 0o600 and the current
    time: two builds of the same commit differ in bytes, and an extractor that
    honours the permission bits creates a file only the owner can write.
    Members are written with write_member() instead, which sets the mode, the
    commit time and the compression on every member.
    """

    def __init__(self, file, mtime):
        """
        Args:
            file (str): Path of the zip to write
            mtime (int): Unix timestamp in UTC+0, the commit time
        """
        self.file = PathStr.new(file)
        self.tmp = self.file.to_tmp_file()
        self.mtime = mtime

    def __enter__(self):
        return self

    @cached_property
    def zipfile(self):
        """
        The archive of the tmp file, opened by the first member.

        Returns:
            zipfile.ZipFile:
        """
        try:
            # the archive is written into the tmp file, not the target
            return zipfile.ZipFile(self.tmp, mode='w')
        except FileNotFoundError:
            pass
        self.tmp.uppath().makedirs(exist_ok=True)
        return zipfile.ZipFile(self.tmp, mode='w')

    def _member_info(self, path):
        """
        Build the ZipInfo of a member with the fixed metadata.

        Args:
            path (str): Member path in the archive

        Returns:
            zipfile.ZipInfo:
        """
        info = zipfile.ZipInfo(filename=path, date_time=zip_date_time(self.mtime))
        info.compress_type = zipfile.ZIP_DEFLATED
        # UNIX, so the same archive is produced on every OS and external_attr
        # is read as a posix mode by the extractors
        info.create_system = 3
        info.external_attr = FILE_MODE << 16
        return info

    def write_member(self, path, content):
        """
        Write a file with a fixed mode, timestamp and compression.

        The archive level arguments of zipfile.ZipFile only apply to
        writestr() called with a file name, they are ignored when a ZipInfo is
        passed, so the compression is set per member here.

        Args:
            path (str): Member path in the archive
            content (bytes): File content
        """
        self.zipfile.writestr(self._member_info(path), content, compresslevel=ZIP_COMPRESS_LEVEL)

    def __exit__(self, exc_type, exc_val, exc_tb):
        zip_file = cached_property.pop(self, 'zipfile')
        if zip_file is None:
            # no member was written, there is nothing to replace, but a failed
            # open may have left a tmp file behind
            _remove_tmp_file(self.tmp)
            return False
        try:
            # write the central directory of the tmp archive
            zip_file.close()
        except BaseException:
            _remove_tmp_file(self.tmp)
            raise
        if exc_type is not None:
            # the build failed, the target keeps its previous content
            _remove_tmp_file(self.tmp)
            return False
        self.tmp.atomic_replace(self.file)


class GzipFileExtend:
    """
    A gzip compressed tar archive whose members are written with a fixed
    metadata.

    The archive is written into a tmp file of the target, and the tmp file
    replaces the target only when the context exits without an exception: a
    failed build neither leaves a broken artifact nor a tmp file behind. The
    stream is opened by the first member, an unused context writes no file.

    gzip writes its header when the stream starts, so the header carries the
    commit time and no file name: the tmp file name of the build would change
    the bytes of every build. The tar members are written with write_member().
    """

    def __init__(self, file, mtime, compresslevel=GZIP_COMPRESS_LEVEL):
        """
        Args:
            file (str): Path of the targz to write
            mtime (int): Unix timestamp in UTC+0, the commit time
            compresslevel (int): Gzip compression level
        """
        self.file = PathStr.new(file)
        self.tmp = self.file.to_tmp_file()
        self.mtime = mtime
        self.compresslevel = compresslevel
        self._stack = None

    def __enter__(self):
        return self

    @cached_property
    def tar(self):
        """
        The tar archive of the tmp file, opened by the first member.

        Returns:
            tarfile.TarFile:
        """
        # the stack closes the resources in the reverse order of the open:
        # tar writes the end of the archive, gzip writes its trailer, then the
        # tmp file is closed
        stack = ExitStack()
        try:
            raw = stack.enter_context(_open_wb(self.tmp))
            gz = stack.enter_context(gzip.GzipFile(
                filename='', fileobj=raw, mode='wb',
                compresslevel=self.compresslevel, mtime=self.mtime,
            ))
            tar = stack.enter_context(tarfile.open(fileobj=gz, mode='w'))
        except BaseException:
            # the stream is half built, the original error wins
            with suppress(Exception):
                stack.close()
            raise
        self._stack = stack
        return tar

    def write_member(self, path, content):
        """
        Write a file into the tar archive with a fixed mode and timestamp.

        Args:
            path (str): Member path in the archive
            content (bytes): File content
        """
        info = tarfile.TarInfo(name=path)
        info.size = len(content)
        info.mtime = max(int(self.mtime), 0)
        info.mode = FILE_MODE
        # uid, gid and the owner names of TarInfo are 0 and empty by default,
        # the build must not record the user that runs it
        self.tar.addfile(info, fileobj=io.BytesIO(content))

    def __exit__(self, exc_type, exc_val, exc_tb):
        if cached_property.pop(self, 'tar') is None:
            # no member was written, there is nothing to replace, but a failed
            # open may have left a tmp file behind
            _remove_tmp_file(self.tmp)
            return False
        stack = self._stack
        self._stack = None
        try:
            # tar writes the end of the archive, gzip writes its trailer
            stack.close()
        except BaseException:
            _remove_tmp_file(self.tmp)
            raise
        if exc_type is not None:
            # the build failed, the target keeps its previous content
            _remove_tmp_file(self.tmp)
            return False
        self.tmp.atomic_replace(self.file)
