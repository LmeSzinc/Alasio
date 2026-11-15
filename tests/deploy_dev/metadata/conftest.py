"""
Fixtures of the metadata reader: an in-memory index and a wheel builder.

The index is the shared double of tests/deploy_dev/pypi_mock.py, which answers
the pages, the files, the byte ranges and the PEP 658 metadata file of a wheel.
Nothing reaches the network and nothing reaches the disk.
"""
import pytest

from tests.deploy_dev.pypi_mock import INDEX_URL, MockIndex

__all__ = ['INDEX_URL', 'index']


@pytest.fixture
def index():
    """
    The mock index of one test.

    Returns:
        MockIndex: The index
    """
    return MockIndex()
