# register the fs fixture of the in-memory fake filesystem
from alasio.testing.filesystem import fs  # noqa: F401


def join(fs, *parts):
    """
    Build a normalized absolute path under the fake root.

    Args:
        fs (FakeFilesystem): Fake filesystem
        *parts (str): Path parts

    Returns:
        str: Absolute path string
    """
    path = fs.root_dir.path
    for part in parts:
        path = f'{path}/{part}'
    return path
