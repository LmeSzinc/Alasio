"""
The project root of a mod, and the folder of its golden screenshots.

A project root is a folder declaring a mod entry (`module/config/_index/mod_entry.yaml`),
which covers both layouts of a mod: a mod inside the Alasio repository
(`<repo>/ExampleMod`) and a mod that is the repository itself (`<repo>` as a whole).

Screenshots are collected by hand under `<project root>/collection`: the `image`
decorator of alasio.testing.screenshot binds one to a test, and the golden test
review page of the backend serves them from the same folder.

This module imports neither pytest nor the image stack, so a runtime consumer
reads the project root and the collection folder with the very rule the pytest
plugin uses, instead of duplicating it.
"""
from alasio.config.entry.mod_entry import MOD_ENTRY_PATH, ModEntry
from alasio.ext.path import PathStr

__all__ = [
    'COLLECTION',
    'PROJECT_ROOT_MANAGER',
    'ProjectRootError',
    'ProjectRootManager',
]

# Folder of the collected screenshots, relative to the project root
COLLECTION = 'collection'


class ProjectRootError(Exception):
    """
    Raised if a path has no project root above it
    """
    pass


class ProjectRootManager:
    """
    The project roots of the mods of the running session.

    A real mod has many test files and they all live under the one root of the
    mod, so a root is discovered once and reused: resolving a test file under a
    known root walks no folder, it is a sub-path check.

    Attributes:
        roots (list[PathStr]): The project roots discovered so far
    """

    def __init__(self):
        self.roots = []

    def get_project_root(self, test_file):
        """
        Get the project root of a test file, which is the nearest ancestor of the
        test file declaring a mod entry

        Args:
            test_file (str): Path of the test file, absolute like the path pytest
                reports, the result of a relative path depends on the working folder

        Returns:
            PathStr:

        Raises:
            ProjectRootError: If no project root is found
        """
        path = PathStr.new(test_file).abspath()

        # The test file belongs to a mod discovered by an earlier test file
        root = self.get_known_root(path)
        if root is not None:
            return root

        # First test file of this mod: walk up once and remember the root
        root = self.find_root(path, test_file)
        self.roots.append(root)
        return root

    def get_known_root(self, path):
        """
        Get the known project root holding a path, the deepest one if several do

        Args:
            path (str): Normalized absolute path

        Returns:
            PathStr | None: The root, None if no known root holds the path
        """
        path = PathStr.new(path)
        matched = None
        for root in self.roots:
            if path.subpath_to(root) is None:
                continue
            if matched is None or len(root) > len(matched):
                matched = root
        return matched

    @staticmethod
    def find_root(path, test_file=None):
        """
        Walk up from a path to the nearest folder declaring a mod entry

        Args:
            path (str): Normalized absolute path
            test_file (str): Path to report in the error, `path` if not given

        Returns:
            PathStr:

        Raises:
            ProjectRootError: If no project root is found
        """
        folder = PathStr.new(path)
        while folder:
            if ModEntry.is_mod(folder):
                return folder
            parent = folder.uppath(1)
            if parent == folder:
                break
            folder = parent

        raise ProjectRootError(
            f'No project root above the test file "{test_file or path}", a project root is '
            f'a folder declaring "{MOD_ENTRY_PATH}"'
        )


# The project roots of the mods of the running session
PROJECT_ROOT_MANAGER = ProjectRootManager()
