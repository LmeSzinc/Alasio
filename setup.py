import logging
import os
import sys
from types import ModuleType

from setuptools import setup

# inject path, so we can import alasio
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

# patch alasio.logger, so we don't import structlog
my_logger = logging.getLogger("alasio")
mock_module = ModuleType("alasio.logger")
mock_module.logger = my_logger
sys.modules["alasio.logger"] = mock_module


def iter_files():
    from alasio.ext.backport import removeprefix
    from alasio.ext.path import PathStr
    from alasio.git.stage.gitref import GitRef
    from alasio.git.stage.gitreset import GitReset

    root = PathStr.new(__file__).uppath()

    head = GitRef(root).head_get()
    if not head:
        print('Empty repo head, adding all files')
        path = root.joinpath('alasio')
        for file in path.iter_files(recursive=True):
            file = file.subpath_to(path)
            if '__pycache__' in file:
                continue
            yield file
        return

    repo = GitReset(root)
    repo.read_lazy()
    for entry in repo.list_files(head).values():
        if not entry.path.startswith('alasio/'):
            continue
        path = removeprefix(entry.path, 'alasio/')
        yield path


files = list(set(iter_files()))
setup(
    package_data={
        "alasio": files,
    },
)
