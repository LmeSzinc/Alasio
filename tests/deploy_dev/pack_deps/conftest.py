"""
Fixtures of the pack_deps tool: an in-memory PyPI simple index and a mod.

The index is the shared double of tests/deploy_dev/pypi_mock.py: the pages, the
files, the byte ranges and the PEP 658 metadata file of a wheel. The mod is a
folder of the fake filesystem declaring the mod entry and the requirements file
(the lock) the closure is read over.

Nothing reaches the network and nothing reaches the disk: the requests go
through an httpx2 MockTransport, the files live in the fake filesystem.
"""
import pytest

from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pypi_mock import INDEX_URL, IndexEntry, MockIndex, make_wheel, metadata_text

__all__ = [
    'INDEX_URL',
    'METADATA_SUFFIX',
    'MOD_NAME',
    'UNREACHABLE_INDEX',
    'IndexEntry',
    'MockIndex',
    'index',
    'make_wheel',
    'metadata_text',
    'mod_root',
    'write_requirements',
]

# The suffix PEP 658 appends to the url of a wheel for its metadata file
METADATA_SUFFIX = '.metadata'

# The mod of the tests
MOD_NAME = 'example_mod'

# The index the declaration of the tests names: a host that never answers, so a
# request that reaches it is a bug of the code under test and not a slow test
UNREACHABLE_INDEX = 'http://unreachable.example/simple'


@pytest.fixture
def index():
    """
    The mock index of one test.

    Returns:
        MockIndex: The index
    """
    return MockIndex()


@pytest.fixture
def mod_root(fs, monkeypatch):
    """
    A mod of the fake filesystem, set as env.PROJECT_ROOT.

    The folder declares the mod entry (``module/config/_index/mod_entry.yaml``)
    and the requirements file the closure is read over.

    Returns:
        PathStr: Root of the mod
    """
    root = PathStr.new(fs.root_dir.path).joinpath('mod')
    fs.create_file(
        root.joinpath('module/config/_index/mod_entry.yaml'),
        contents=(
            f'Mod:\n  name: {MOD_NAME}\n'
            f'Update:\n  pypi_mirrors: {UNREACHABLE_INDEX}\n  pack_deps: []\n'))
    fs.create_file(root.joinpath('requirements.txt'), contents='')
    monkeypatch.setattr(env, 'PROJECT_ROOT', root)
    return root


def write_requirements(root, pins, source=INDEX_URL):
    """
    Write the requirements file of a mod.

    Every pin carries the '# from' annotation the compiler writes, so the tool
    reads the pin from the given index: the requirements file is the only
    source of an index, see PackDepsManager.

    Args:
        root (PathStr): Root of the mod
        pins (dict[str, str]): {name: version} of the pins
        source (str): Index of the annotations. Defaults to INDEX_URL, the mock
            index; '' writes a pin without an annotation, which reads PyPI

    Returns:
        PathStr: Path of the requirements file
    """
    lines = []
    for name, version in pins.items():
        lines.append(f'{name}=={version}')
        if source:
            lines.append(f'    # from {source}')
    file = root.joinpath('requirements.txt')
    file.atomic_write(('\n'.join(lines) + '\n').encode('utf-8'))
    return file
