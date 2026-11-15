"""
Tests of the GUI language of a connection (ConnState).

``set_lang`` carries the language the user picked for the alasio interface and
stores the tag the backend negotiated for it. The language the content of a
config is rendered in is derived from that per mod (``content_lang``): a mod
translates its own ``gui_language`` only, so it is not always the same value.
"""
import pytest

from alasio.backend.reactive.event import RpcValueError
from alasio.backend.topic.state import ConnState
from alasio.config.entry.loader import MOD_LOADER

CONN_ID = 'conn_lang'


class FakeMod:
    """
    A mod that declares the given GUI languages.

    Only ``resolve_gui_lang`` is read by the connection state, the resolution
    rule itself is covered by tests/config/entry/test_gui_lang.py.
    """

    def __init__(self, gui_language):
        """
        Args:
            gui_language (tuple[str]): Languages the mod translates
        """
        self.gui_language = list(gui_language)

    def resolve_gui_lang(self, lang):
        if lang in self.gui_language:
            return lang
        if 'en-US' in self.gui_language:
            return 'en-US'
        return next(iter(self.gui_language), 'en-US')


@pytest.fixture(autouse=True)
def cleanup_topics():
    """Drop the per-connection topic instances after each test"""
    yield
    ConnState.singleton_clear()


@pytest.fixture
def mods(monkeypatch):
    """A mod table in place of the mods the loader found"""
    table = {
        'mod_a': FakeMod(['zh-CN', 'en-US']),
        'mod_b': FakeMod(['ja-JP']),
    }
    monkeypatch.setattr(MOD_LOADER, 'dict_mod', table)
    return table


async def set_lang(lang, conn_id=CONN_ID):
    """
    Call the set_lang rpc the way the ws server does.

    Args:
        lang (str): Requested interface language
        conn_id (str): Connection of the caller

    Returns:
        ConnState: The state of the connection
    """
    topic = ConnState(conn_id, None)
    await ConnState.rpc_methods['set_lang'].call_async(topic, {'lang': lang})
    return topic


async def open_mod(mod_name, conn_id=CONN_ID):
    """
    Point the connection at a mod, the way set_config does.

    Args:
        mod_name (str): Mod of the opened config
        conn_id (str): Connection of the caller

    Returns:
        ConnState: The state of the connection
    """
    topic = ConnState(conn_id, None)
    nav = await topic.nav_state
    nav.mod_name = mod_name
    await topic.nav_state.mutate()
    return topic


class TestSetLang:
    """set_lang stores the negotiated interface language."""

    @pytest.mark.trio
    async def test_stores_a_supported_language(self):
        """A supported tag is stored as requested."""
        topic = await set_lang('es-ES')

        nav = await topic.nav_state
        assert nav.lang == 'es-ES'

    @pytest.mark.trio
    async def test_stores_the_negotiated_tag_not_the_request(self):
        """A variant tag resolves to the language it negotiates to."""
        topic = await set_lang('zh-Hans')

        nav = await topic.nav_state
        # not 'zh-Hans': every lookup of the interface language is keyed by the
        # available tag
        assert nav.lang == 'zh-CN'

    @pytest.mark.trio
    async def test_rejects_an_unsupported_language(self):
        """A language the interface does not offer is refused, not stored."""
        with pytest.raises(RpcValueError, match='does not match any available languages'):
            await set_lang('ko-KR')

        nav = await ConnState(CONN_ID, None).nav_state
        assert nav.lang == 'en-US'


class TestContentLang:
    """content_lang resolves the interface language against the mod."""

    @pytest.mark.trio
    async def test_a_language_the_mod_translates(self, mods):
        """A mod that carries the interface language renders in it."""
        await set_lang('zh-CN')
        topic = await open_mod('mod_a')

        assert await topic.content_lang == 'zh-CN'

    @pytest.mark.trio
    async def test_falls_back_to_the_default_language(self, mods):
        """A mod without the interface language renders in en-US."""
        await set_lang('es-ES')
        topic = await open_mod('mod_a')

        assert await topic.content_lang == 'en-US'

    @pytest.mark.trio
    async def test_falls_back_to_the_first_declared_language(self, mods):
        """Without en-US the first language of the mod is used."""
        await set_lang('es-ES')
        topic = await open_mod('mod_b')

        assert await topic.content_lang == 'ja-JP'

    @pytest.mark.trio
    async def test_without_a_mod_the_interface_language_is_kept(self, mods):
        """No config opened: nothing comes from a mod yet."""
        topic = await set_lang('es-ES')

        assert await topic.content_lang == 'es-ES'

    @pytest.mark.trio
    async def test_an_unknown_mod_keeps_the_interface_language(self, mods):
        """A mod the loader does not know is not resolved against anything."""
        await set_lang('zh-TW')
        topic = await open_mod('ghost_mod')

        assert await topic.content_lang == 'zh-TW'

    @pytest.mark.trio
    async def test_follows_the_interface_language(self, mods):
        """Changing the interface language re-derives the content language."""
        await set_lang('zh-CN')
        topic = await open_mod('mod_a')
        assert await topic.content_lang == 'zh-CN'

        await set_lang('es-ES')

        assert await topic.content_lang == 'en-US'
