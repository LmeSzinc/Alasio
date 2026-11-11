"""
Tests for the CI-only rule of the network mark, see tests/conftest.py.
"""

from alasio.ext import env
from tests import conftest


class FakeItem:
    """Minimal stand-in of pytest.Item for the network hook"""

    def __init__(self, network):
        self.network = network
        self.added = []

    def get_closest_marker(self, name):
        """Report the mark of the item, the way pytest.Item does"""
        if self.network and name == 'network':
            return object()
        return None

    def add_marker(self, marker):
        """Record the marker the hook added"""
        self.added.append(marker)


class TestNetworkMark:
    """Tests for pytest_collection_modifyitems() of the root conftest."""

    def test_skipped_outside_ci(self, monkeypatch):
        """A network test is skipped when the machine is not a CI."""
        monkeypatch.setattr(env, 'CI', False)
        item = FakeItem(network=True)
        conftest.pytest_collection_modifyitems([item])
        assert len(item.added) == 1
        assert item.added[0].mark.name == 'skip'

    def test_skip_reason(self, monkeypatch):
        """The skip line of the report says why nothing ran."""
        monkeypatch.setattr(env, 'CI', False)
        item = FakeItem(network=True)
        conftest.pytest_collection_modifyitems([item])
        assert item.added[0].mark.kwargs['reason'] == conftest.NETWORK_SKIP_REASON

    def test_kept_on_ci(self, monkeypatch):
        """A network test keeps running when the machine is a CI."""
        monkeypatch.setattr(env, 'CI', True)
        item = FakeItem(network=True)
        conftest.pytest_collection_modifyitems([item])
        assert item.added == []

    def test_unmarked_item_is_not_touched(self, monkeypatch):
        """A test without the mark is never skipped, on CI or not."""
        monkeypatch.setattr(env, 'CI', False)
        item = FakeItem(network=False)
        conftest.pytest_collection_modifyitems([item])
        assert item.added == []
