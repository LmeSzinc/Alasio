"""
Tests for the unified ConfigArg event entry
(alasio/backend/topic/_config_event.py): viewport dispatch and the one-line
forwarding to the TaskQueue linkage (the source itself decides whether the
scheduler settings changed, see test_que_source.py TestOnConfigEventLinkage).
"""

from unittest.mock import MagicMock, patch

import pytest

from alasio.backend.topic import _config_event
from alasio.backend.topic._gui_config import GuiConfigSource
from alasio.backend.topic.que import TaskQueueSource
from alasio.config.entry.model import ConfigSetEvent


def other_event():
    """
    An unrelated ConfigSetEvent

    Returns:
        ConfigSetEvent:
    """
    return ConfigSetEvent(task='General', group='General', arg='Foo', value=1)


@pytest.fixture(autouse=True)
def cleanup_task_queue():
    """Clear TaskQueueSource named singletons after each test"""
    yield
    TaskQueueSource.singleton_clear()


class TestOnConfigEventDispatch:
    def test_dispatch_called_once_with_payload(self):
        """on_config_event dispatches exactly once with (config_name, event)"""
        event = other_event()
        with patch.object(GuiConfigSource, 'dispatch_config', MagicMock()) as dispatch, \
                patch.object(TaskQueueSource, 'on_config_event', MagicMock()) as linkage:
            _config_event.on_config_event('alas', event)
        dispatch.assert_called_once_with('alas', event)

    def test_task_queue_linkage_forwarded(self):
        """
        the event is forwarded to the TaskQueue source of the config
        (the linkage decides whether the scheduler settings changed).
        """
        event = other_event()
        with patch.object(TaskQueueSource, 'on_config_event', MagicMock()) as linkage:
            _config_event.on_config_event('alas', event)
        linkage.assert_called_once_with(event)
