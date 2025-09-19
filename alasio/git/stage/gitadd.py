import os
import stat

from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_bytes, atomic_write
from alasio.ext.path.calc import is_abspath, subpath_to, to_posix
from alasio.git.eol import eol_crlf_remove
from alasio.git.stage.hashobj import blob_hash, encode_loosedata
from alasio.git.stage.index import GitIndex, GitIndexEntry
from alasio.logger import logger


def convert_file_mode(mode):
    """
    Convert filemode 6xx to 644, 7xx to 755, and keep others unchanged.

    Windows st.st_mode returns 666 but file can only be 644 or 755
    """
    rwx = mode & 0o777
    other = mode & ~0o777

    user_perms = rwx & 0o700
    if user_perms == 0o700:
        rwx = 0o755
    elif user_perms == 0o600:
        rwx = 0o644
    else:
        rwx = 0o644
    return other | rwx


class GitAdd(GitIndex):
    def __init__(self, path):
        """
        Args:
            path (str): Absolute path to repo, repo should contain .git folder
        """
        self.path = PathStr.new(path)
        # filepath to .git/index
        file = self.path.joinpath('.git/index')
        # track if entry modified
        self.entry_modified = False
        super().__init__(file)

    def filepath_loose(self, sha1):
        """
        Get filepath of a loose object from sha1

        Args:
            sha1 (str, bytes):
        """
        if isinstance(sha1, bytes):
            sha1 = sha1.decode()
        folder = sha1[:2]
        file = sha1[2:]
        return self.path.joinpath(f'.git/objects/{folder}/{file}')

    def _stage_restore(self, path):
        """
        Remove file from staging area, Equivalent to `git restore --stage <file>`
        This is an internal method that only add one file

        Args:
            path (str): Relative path, must use "/" as sep, must not startswith "/"
                example: "assets/cn/xxx.png"
        """
        entry = self.dict_entry.pop((path, 0), None)
        if entry is not None:
            logger.info(f'stage_restore, entry={entry}')
            self.entry_modified = True

    def _stage_add(self, path):
        """
        Add file to staging area, Equivalent to `git add <file>`
        This is an internal method that only add one file

        Args:
            path (str): Relative path, must use "/" as sep, must not startswith "/"
                example: "assets/cn/xxx.png"

        Returns:
            bool: True if success.
                False if file not exist or file is the same in staging area
        """
        abspath = self.path.joinpath(path)
        path = to_posix(path)

        try:
            st = os.stat(abspath)
        except FileNotFoundError:
            # remove from index
            self._stage_restore(path)
            return False

        mode = convert_file_mode(st.st_mode)
        if stat.S_ISREG(mode):
            # File
            pass
        elif stat.S_ISDIR(mode):
            # Directory, can't add directory directly
            return False
        elif stat.S_ISLNK(mode):
            # Symlink
            pass
        else:
            # This should not happen
            return False

        entry = self.dict_entry.get((path, 0), None)
        if entry is not None:
            # note that this is different from `git add`
            # Class GitAdd does not aim to be a fully implemented git, but a
            # tool to improve develop experience when using code generator.
            # We just confirm the file is tracked by git, no need to confirm
            # git is tracking the latest version.
            # By tracking the file, your IDE will show it on git
            # visualization and do the real `git add` before you commit.
            return False

        # add file
        try:
            data = atomic_read_bytes(abspath)
        except FileNotFoundError:
            # race condition that file not exist anymore
            self._stage_restore(path)
            return False

        data = eol_crlf_remove(path, data)
        sha1 = blob_hash(data)

        # write loose file
        logger.info(f'stage_add, loose object sha1={sha1}')
        content = encode_loosedata(data)
        atomic_write(self.filepath_loose(sha1), content)

        # add index entry
        entry = GitIndexEntry(
            ctime_s=int(st.st_ctime), ctime_ns=st.st_ctime_ns % 1000000000,
            mtime_s=int(st.st_mtime), mtime_ns=st.st_mtime_ns % 1000000000,
            dev=0, ino=0, mode=mode,
            uid=0, gid=0, size=st.st_size,
            sha1=bytes.fromhex(sha1), path=path,
        )
        logger.info(f'stage_add, entry={entry}')
        self.dict_entry[(path, 0)] = entry
        self.entry_modified = True
        return True

    def stage_add(self, file):
        """
        Add file to staging area.
        If file exist, equivalent to `git add <file>`
        If not, equivalent to `git restore --stage <file>`

        Args:
            file (str): filepath or a list of filepath,
                If filepath is an absolute path, it should be a sub path of repo root,
                otherwise stage_add will do nothing

        Returns:
            bool: If added
        """
        path = subpath_to(file, self.path)
        if path == file:
            if is_abspath(file):
                # not sub path to repo root
                logger.warning(f'stage_add failed, file is not subpath to repo root {file}')
                return False
            else:
                # relative path
                path = file
        else:
            # abspath and sub path to repo root
            pass

        return self._stage_add(path)

    def __enter__(self):
        self.index_read()
        self.entry_modified = False
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.entry_modified:
            logger.info(f'stage_add write file: {self.file}')
            self.index_write()
        self.entry_modified = False


if __name__ == '__main__':
    """
    Usage example
    """
    git = GitAdd(r'E:\ProgramData\Pycharm\AzurLaneAutoScript')
    with git:
        git.stage_add(f'assets/jp/reward/MISSION_EMPTY.png')
    # IDE will notice MISSION_EMPTY.png is git tracked
