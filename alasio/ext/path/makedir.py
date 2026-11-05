import os
import stat

from alasio.ext.cache import cached_property

from .calc import is_abspath, joinpath, normpath


def _convert_path(filepaths):
    """
    Convert relative filepaths to absolute normalized paths

    Args:
        filepaths (list[str]): List of filepaths

    Yields:
        str: Absolute normalized filepath
    """
    root = normpath(os.getcwd())
    for filepath in filepaths:
        filepath = normpath(filepath)
        if is_abspath(filepath):
            yield filepath
        else:
            yield joinpath(root, filepath)


def _iter_parent_folder(filepath):
    """
    Iter parent folders of a normalized filepath, from deep to shallow.
    C:/folder/file
        - C:/folder

    The root folder always exists, it is not yielded.

    Args:
        filepath (str): Normalized filepath

    Yields:
        str: Parent folder, from deep to shallow
    """
    folder, sep, _ = filepath.rpartition('/')
    while sep and folder:
        # A drive root is "C:/", while "C:" is drive-relative in the OS.
        # The root always exists, it is not a folder to create.
        if len(folder) == 2 and folder[1] == ':' and folder[0].isalpha():
            return
        yield folder
        folder, sep, _ = folder.rpartition('/')


class BatchMakedirs:
    """
    Create the parent folders of a batch of filepaths with minimal IO
    operations.

    Reference os.makedirs(), but check the folders from shallow to deep with
    deduplication, instead of calling os.makedirs() in a loop.

    The filepaths are converted to absolute normalized paths in __init__, so
    the parent folders of the instance are the folders the run creates, and a
    caller reads them before anything is created. The conversion is relative
    to the current directory at construction time, the run of an instance
    built earlier keeps the folders it was built for.

    An entry in the way of a folder is removed first: a file, a symbolic link
    (the link itself, its target is untouched), a fifo, a socket. A link that
    points at a folder is the folder, and the folders below it are created
    through the link.

    Usage:
        BatchMakedirs([f'{root}/a/b/c.txt']).run()
        BatchMakedirs(paths).parent_folders  # ['{root}/a', '{root}/a/b']
    """

    def __init__(self, filepaths):
        """
        Args:
            filepaths (Iterable[str]): Filepaths to create the parent folders
                of, relative to the current directory or absolute
        """
        # one conversion for both the run and the parent folders: the paths
        # of the instance never change after it was built
        self.filepaths = list(_convert_path(filepaths))

    @cached_property
    def parent_folders(self):
        """
        The folders to create: every parent folder of every filepath, with
        the ancestors, deduplicated and sorted from shallow to deep.

        A filepath without a separator (a file at the root of the current
        directory) contributes nothing, and the root folders themselves are
        never listed: they exist already. A folder read before is skipped with
        all of its ancestors, the deeper branches still join the list.

        Returns:
            list[str]: Absolute normalized folders, the filepaths themselves
                excluded
        """
        return self.parent_folders_of(self.filepaths)

    @staticmethod
    def parent_folders_of(filepaths):
        """
        The parent folders of filepaths, in the namespace of the input.

        Unlike the parent_folders property, nothing is resolved against the
        current directory: the paths have to be normalized ('/'-separated)
        already, and they may be relative. That is what the pack format needs,
        whose record paths are relative to the root of its target.

        Args:
            filepaths (Iterable[str]): Normalized filepaths, '/'-separated,
                relative or absolute

        Returns:
            list[str]: Deduplicated parent folders in the form of the input,
                sorted from shallow to deep
        """
        folders = set()
        for filepath in filepaths:
            for folder in _iter_parent_folder(filepath):
                if folder in folders:
                    # All of its ancestors are already added
                    break
                folders.add(folder)
        return sorted(folders, key=lambda f: f.count('/'))

    def run(self):
        """
        Create the folders of parent_folders, from shallow to deep.

        A folder that already exists costs one os.stat, and os.stat() follows a
        symbolic link: a link to a folder is the folder, the folders below it
        are created through the link. Any other entry at the path is in the way
        of the folder and is removed first, whatever its kind: a file, a link
        (the link itself, never its target), a fifo or a socket an interrupted
        process left behind. An entry os.stat() cannot follow (a dangling link)
        is found by the mkdir that reports the name as existing.

        Two processes may create the same folders at once (two jobs of one
        backend, two backends of one machine). An entry that appeared while
        this instance worked is never removed: the mkdir that reports the name
        as existing is read again, and a folder there, from another process or
        from this one, is left as it is with everything below it. Only an
        entry that is not a folder is replaced.

        Raises:
            OSError: If a folder cannot be created, such as permission denied,
                or an entry in the way cannot be removed
        """
        for folder in self.parent_folders:
            try:
                # One call to check whether the path is a folder
                st = os.stat(folder)
            except FileNotFoundError:
                # No folder there: either nothing, or a dangling link the stat
                # cannot follow, which the mkdir reports as an existing name
                try:
                    os.mkdir(folder)
                except FileExistsError:
                    # The name appeared between the stat and the mkdir. A folder
                    # is another process creating it (or the same tree being
                    # built by this one): it is not ours to remove, and
                    # everything below it is already someone's work. A link to
                    # a folder is that same folder, the process that made it
                    # meant it to stay
                    if not os.path.isdir(folder):
                        self._replace_with_folder(folder)
                continue
            if stat.S_ISDIR(st.st_mode):
                # Folder already exists, a link to a folder included
                continue
            self._replace_with_folder(folder)

    @staticmethod
    def _replace_with_folder(folder):
        """
        Remove the entry at the folder path and create a folder in its place.

        os.unlink() removes the entry itself: a symbolic link goes without its
        target, which stays where it is. It never removes a folder, on any
        platform, so an entry another process replaced with a folder in the
        meantime is left as it is: the folder the caller wants is already
        there.

        Args:
            folder (str): Absolute normalized folder path

        Raises:
            OSError: If the entry cannot be removed, or the folder cannot be
                created
        """
        try:
            os.unlink(folder)
        except FileNotFoundError:
            # Another process removed the entry in between
            pass
        except OSError:
            # The entry became a folder in between: it is not in the way any
            # more, and the mkdir below would only say so
            if os.path.isdir(folder):
                return
            raise
        try:
            os.mkdir(folder)
        except FileExistsError:
            # Another process created the folder in between, unless another
            # kind of entry took the name again
            if not os.path.isdir(folder):
                raise
