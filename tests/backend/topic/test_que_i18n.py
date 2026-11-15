"""
Tests for TaskQueueI18nSource (alasio/backend/topic/que.py).

The source is a thin adapter: the display name of every task is resolved by
the loader (MOD_LOADER.get_queue_i18n), which reads the queue index of the mod
in exactly the language it is given. The tests pin that delegation -- argument
order included -- and that the source serves the loader answer as is.
"""

import pytest

from alasio.backend.topic.que import TaskQueueI18nSource
from alasio.config.entry.loader import MOD_LOADER


@pytest.fixture(autouse=True)
def cleanup_sources():
    """Drop the keyed source instances after each test"""
    yield
    TaskQueueI18nSource.singleton_clear()


@pytest.fixture
def loader(monkeypatch):
    """A fake loader in place of MOD_LOADER.get_queue_i18n, recording its calls"""
    calls = []
    answer = {'TaskA': 'Task A', 'TaskB': 'TaskB'}

    def fake(mod_name, lang):
        calls.append((mod_name, lang))
        return answer

    monkeypatch.setattr(MOD_LOADER, 'get_queue_i18n', fake)
    return calls, answer


class TestOnInit:
    """on_init answers the loader output of (mod, lang)."""

    def test_delegates_with_mod_name_and_lang(self, loader):
        """The loader gets the mod name and the language, in that order."""
        calls, _ = loader
        source = TaskQueueI18nSource('test_mod', 'zh-CN')

        source.on_init()

        assert calls == [('test_mod', 'zh-CN')]

    def test_serves_the_loader_answer_as_is(self, loader):
        """No reshaping of the loader output: it is already {task: name}."""
        _, answer = loader
        source = TaskQueueI18nSource('test_mod', 'es-ES')

        assert source.on_init() == answer
        assert source.on_init() is answer

    def test_an_unknown_mod_answers_an_empty_view(self, monkeypatch):
        """A mod the loader does not know has no task names."""
        monkeypatch.setattr(MOD_LOADER, 'get_queue_i18n', lambda mod_name, lang: {})
        source = TaskQueueI18nSource('ghost_mod', 'en-US')

        assert source.on_init() == {}
