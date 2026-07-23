class MockGitRepoBase:
    """
    Base class for mock git repositories.

    Mirrors the __init__ interface of GitRepoBase in
    :py:class:`alasio.git.stage.base.GitRepoBase` so that mock
    subclasses can be used as drop-in replacements in tests.
    """

    def __init__(self, path):
        """
        Args:
            path (str): Absolute path to the repository root.
        """
        self.path: str = path
