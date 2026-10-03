import os
from stat import S_IWRITE

from alasio.backport import removesuffix
from alasio.ext.path.atomic import IS_WINDOWS, folder_rmtree_empty
from alasio.ext.path.calc import joinpath, normpath
from alasio.logger import logger

# Folder holding the bytecode compiled from the sources of the folder
# above it, PEP 3147
PYCACHE = '__pycache__'
# Suffix of the bytecode files
PYC_SUFFIX = '.pyc'


class CleanupPyc:
    """
    Remove the orphan pyc files under a folder, subfolders included.

    A pyc file of a __pycache__ folder is an orphan when its source file
    does not exist: the source, e.g. a module removed or renamed by a
    package update, can never be imported again (python refuses to
    import a __pycache__ pyc file without its source) and the pyc file
    is a residue. A pyc file whose source exists is kept, removing it
    only makes python compile the source again.

    Every folder is read once: the listing of a folder is the sources of
    its own __pycache__ folder, so the source check runs on names
    already read, no second listing and no stat call per pyc file is
    needed.

    Only the pyc files of the __pycache__ folders are checked, PEP 3147:
        - the legacy pyc files placed next to their source, e.g. a
          sourceless module, are never touched: without a source they
          are importable there, they are not garbage
        - a __pycache__ folder is never removed as a whole, it may hold
          the pyc files of valid sources or of an other interpreter; a
          __pycache__ folder without any file is removed, os.rmdir()
          only removes an empty folder
        - a symbolic link is not followed, e.g. a pyc file linked from
          an other tree is left as is

    The removal is best effort and idempotent: a file that can not be
    removed is logged as a warning and counted in failed, the run goes
    on with the other files, and the next run retries the file. A
    cleanup that goes well logs nothing.

    The scope is decided by the caller, the class holds no state of what
    a former run deleted: e.g. the parent folders of the files deleted
    by an update (immediate cleanup, in the replace window) or the
    folders of the files listed in a version ledger (deferred cleanup,
    after a restart).

    Attributes:
        root (str): Normalized absolute path of the folder to clean
        removed (int): Number of orphan pyc files removed by the last
            cleanup()
        failed (int): Number of orphan pyc files that could not be
            removed, e.g. a file held by a running process, they are
            left and the next run retries
    """

    def __init__(self, root):
        """
        Args:
            root (str): Absolute path of the folder to clean, the folder
                itself is never removed
        """
        self.root = normpath(root)
        self.removed = 0
        self.failed = 0

    def cleanup(self):
        """
        Remove the orphan pyc files under the root folder.

        The counters are reset, then the counts of this run are
        accumulated into them: removed and failed.
        """
        self.removed = 0
        self.failed = 0
        folders = [self.root]
        while folders:
            folders += self._cleanup_pycache(folders.pop())

    def _cleanup_pycache(self, folder):
        """
        Remove the orphan pyc files of one folder and return the folders
        to walk next.

        The source names come from the listing of the folder, the pyc
        files from the listing of its __pycache__ folder. A folder
        without a __pycache__ folder has no pyc file to check. The
        __pycache__ folder is not returned: it holds no source, and it
        is removed when it is left empty.

        Args:
            folder (str): Normalized absolute path of the folder

        Returns:
            list[str]: Normalized paths of the subfolders to walk
        """
        subfolders = []
        sources = set()
        pycache = ''
        try:
            entries = os.scandir(folder)
        except (FileNotFoundError, NotADirectoryError):
            return subfolders
        with entries:
            for entry in entries:
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if not is_dir:
                    sources.add(entry.name)
                elif entry.name == PYCACHE:
                    pycache = joinpath(folder, entry.name)
                else:
                    subfolders.append(joinpath(folder, entry.name))
        if not pycache:
            return subfolders
        if IS_WINDOWS:
            # The paths of Windows are not case sensitive: "Mod.py" is
            # the source of "mod.cpython-38.pyc" too
            sources = {name.lower() for name in sources}
        try:
            entries = os.scandir(pycache)
        except (FileNotFoundError, NotADirectoryError):
            return subfolders
        with entries:
            for entry in entries:
                name = entry.name
                if not name.endswith(PYC_SUFFIX):
                    continue
                if self._source_exists(name, sources):
                    continue
                try:
                    is_file = entry.is_file(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if not is_file:
                    # A symbolic link is not followed, a folder is not
                    # a pyc file
                    continue
                if self._remove(joinpath(pycache, name)):
                    self.removed += 1
                else:
                    self.failed += 1
        # A __pycache__ folder left without any file is a residue,
        # remove it: os.rmdir() only removes an empty folder, the folder
        # still holding a pyc file of a valid source, or any file of no
        # pyc, is kept
        folder_rmtree_empty(pycache)
        return subfolders

    @staticmethod
    def _source_exists(name, sources):
        """
        Check if the source file of a pyc file is in the sources of its
        folder.

        The name of a pyc file holds the module name, the cache tag of
        the interpreter and the manual optimization, all separated by
        dots, e.g. "mod.cpython-38.pyc", "mod.cpython-38.opt-1.pyc" and
        "core.cpython-38-pytest-8.3.5.pyc" (pytest rewrites the modules
        it compiles with an own tag). The tag can not be told from a
        dotted module name, e.g. "pkg.mod.cpython-38.pyc" holds both:
        every dotted prefix of the name is a candidate module name and
        the pyc file is an orphan only when no candidate "<prefix>.py"
        is a source of the folder. The answer is biased towards
        "exists": a wrong "exists" only keeps the pyc for the next
        check, a wrong "missing" would remove a pyc file whose source is
        still there.

        Args:
            name (str): File name of the pyc file
            sources (set[str]): Names of the sources of the folder,
                lower-cased on Windows

        Returns:
            bool: True if a source of the pyc exists
        """
        if IS_WINDOWS:
            # The paths of Windows are not case sensitive
            name = name.lower()
        prefix = ''
        for part in removesuffix(name, PYC_SUFFIX).split('.'):
            if not part:
                continue
            prefix = f'{prefix}.{part}' if prefix else part
            if f'{prefix}.py' in sources:
                return True
        # A name without any module part, e.g. a file named ".pyc", is
        # left alone rather than guessed
        return not prefix

    @staticmethod
    def _remove(file):
        """
        Remove one orphan pyc file, best effort.

        Args:
            file (str): Normalized path of the pyc file

        Returns:
            bool: True if the file was removed, False if it is left,
                e.g. held by a running process, the next run retries
        """
        try:
            os.unlink(file)
            return True
        except FileNotFoundError:
            # Already removed by an other process
            return True
        except OSError as e:
            error = e
        if IS_WINDOWS:
            # Windows rejects the removal of a read-only file, clear the
            # read-only attribute (S_IWRITE) and try again
            try:
                os.chmod(file, S_IWRITE)
                os.unlink(file)
                return True
            except FileNotFoundError:
                return True
            except OSError as e:
                error = e
        logger.warning(f'Failed to remove orphan pyc file: {file}: {error}')
        return False
