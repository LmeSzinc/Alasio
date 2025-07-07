import os
import stat

from alasio.ext.gitpython.stage.hashobj import git_file_hash
from alasio.ext.gitpython.stage.index import GitIndex, GitIndexEntry
from alasio.ext.path import PathStr
from alasio.ext.path.calc import is_abspath, subpath_to, to_posix
from alasio.logger import logger


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

    def _stage_add(self, path):
        """
        Add file/path to staging area, Equivalent to `git add <file>`
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
            # file not exist, no need to add
            return False

        mode = st.st_mode
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
            sha1 = git_file_hash(abspath)
        except FileNotFoundError:
            # race condition that file not exist anymore
            return False
        sha1 = bytes.fromhex(sha1)

        entry = GitIndexEntry(
            ctime_s=int(st.st_ctime), ctime_ns=st.st_ctime_ns % 1000000000,
            mtime_s=int(st.st_mtime), mtime_ns=st.st_mtime_ns % 1000000000,
            dev=0, ino=0, mode=st.st_mode,
            uid=0, gid=0, size=st.st_size,
            sha1=sha1, path=path,
        )
        logger.info(f'stage_add, entry={entry}')
        self.dict_entry[(path, 0)] = entry
        self.entry_modified = True
        return True

    def stage_add(self, file):
        """
        Add file/path to staging area, Equivalent to:
        - git add <file>
        - git add <file1> <file2>
        - git add <path>
        - git add .

        Args:
            file (str, list[str], tuple[str]): filepath or a list of filepath,
                Filepath can be a file, or a path, or just "." to add all.
                If filepath is an absolute path, it should be a sub path of repo root,
                otherwise stage_add will do nothing

        Returns:
            int: number of files added
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

        self._stage_add(path)

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
