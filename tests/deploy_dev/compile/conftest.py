"""
Fixtures of the compiler: a project of the fake filesystem and a mock network.

A compile reads a pyproject.toml and writes a requirements file (the fake
filesystem of alasio.testing.filesystem holds both), and reads the indexes of
the project (the in-memory index of tests/deploy_dev/pypi_mock.py, one per
base url, routed by the client of this module).

Nothing reaches the network and nothing reaches the disk.
"""
import httpx2
import pytest

from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pypi_mock import INDEX_URL, MockIndex, make_wheel, metadata_text

__all__ = [
    'INDEX_URL',
    'MOCK_INDEX_URL',
    'PROJECT_NAME',
    'MockIndex',
    'MockNetwork',
    'index',
    'make_wheel',
    'metadata_text',
    'network',
    'project',
]

# The second index of the tests, the role py38deps has for the shipped runtime
MOCK_INDEX_URL = 'https://backport.example/simple'

# The project of the tests, the name a direct pin is annotated with
PROJECT_NAME = 'demo_project'


class MockNetwork:
    """
    The indexes of one test, routed by base url.

    Attributes:
        indexes (dict[str, MockIndex]): {base url: index}
        requests (list[str]): Urls of every request, in order
    """

    def __init__(self):
        self.indexes = {}

    def index(self, base=INDEX_URL):
        """
        The index of one base url, created on the first use.

        Args:
            base (str): Base url of the index. Defaults to INDEX_URL

        Returns:
            MockIndex: The index
        """
        index = self.indexes.get(base)
        if index is None:
            index = self.indexes[base] = MockIndex(base)
        return index

    @property
    def requests(self):
        """
        Returns:
            list[str]: Urls of every request of every index, in order
        """
        return [
            url
            for index in self.indexes.values()
            for url in index.requests
        ]

    def handler(self, request):
        """
        Route one request to the index of its base url.

        Args:
            request (httpx2.Request): The request

        Returns:
            httpx2.Response: The answer of the index, 404 for an unknown host
        """
        url = str(request.url)
        for base in sorted(self.indexes, key=len, reverse=True):
            if url.startswith(base):
                return self.indexes[base].handler(request)
        return httpx2.Response(404)

    @property
    def client(self):
        """
        Returns:
            httpx2.AsyncClient: The client the compiler sends with
        """
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self.handler))


@pytest.fixture(autouse=True)
def primary_index(monkeypatch):
    """
    Read the primary index of a compile from the mock network.

    The compiler takes the official PyPI index when the project declares none,
    the value of ``compiler.DEFAULT_INDEX``: the tests replace it, so no test
    reaches the network.

    Args:
        monkeypatch: The pytest monkeypatch fixture
    """
    from alasio.deploy_dev.compile import compiler as compiler_module

    monkeypatch.setattr(compiler_module, 'DEFAULT_INDEX', INDEX_URL)


@pytest.fixture
def network():
    """
    The mock network of one test.

    Returns:
        MockNetwork: The network
    """
    return MockNetwork()


@pytest.fixture
def project(fs, monkeypatch):
    """
    A project of the fake filesystem, set as env.PROJECT_ROOT.

    Args:
        fs: The fake filesystem fixture
        monkeypatch: The pytest monkeypatch fixture

    Returns:
        PathStr: Root of the project
    """
    root = PathStr.new(fs.root_dir.path).joinpath('project')
    fs.create_file(root.joinpath('pyproject.toml'), contents='')
    monkeypatch.setattr(env, 'PROJECT_ROOT', root)
    return root


def write_pyproject(root, content):
    """
    Write the pyproject.toml of a project.

    Args:
        root (PathStr): Root of the project
        content (str): Content of the file

    Returns:
        PathStr: Path of the file
    """
    file = root.joinpath('pyproject.toml')
    file.atomic_write(content.encode('utf-8'))
    return file
