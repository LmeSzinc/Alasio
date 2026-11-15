"""
Tests of the GUI language resolution.

A connection asks for the language the user picked for the alasio interface,
while the names, helps and task titles of a config come from its mod, which
translates its own ``gui_language`` only: a mod may support fewer languages than
the interface, or other ones. The request is resolved against that set without
asking the user again -- the requested language, else en-US, else the first
language the mod declares -- so a mod that does not carry the user's language
still renders readable content.
"""
from collections import defaultdict

import pytest

from alasio.config.const import Const
from alasio.config.entry.loader import ModLoader
from alasio.ext import env
from alasio.ext.deep import deep_iter_depth2
from alasio.ext.path import PathStr
from alasio.testing.filesystem import fs  # noqa: F401

MOD_ENTRY_FILE = 'module/config/_index/mod_entry.yaml'


def write_mod(fs, root, folder, name, gui_language):
    """
    Declare a mod with the given GUI languages.

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture
        root (PathStr): The project root
        folder (str): Folder of the mod
        name (str): Name the declaration gives the mod
        gui_language (tuple[str]): Languages of the declaration, None to
            leave the field out and take the default of the model

    Returns:
        PathStr: The root of the mod
    """
    mod_root = root.joinpath(folder)
    lines = ['Mod:', f'  name: {name}']
    if gui_language is not None:
        lines.append('  gui_language: [' + ', '.join(gui_language) + ']')
    fs.create_file(mod_root.joinpath(MOD_ENTRY_FILE), contents='\n'.join(lines) + '\n')
    return mod_root


@pytest.fixture
def build_mod(fs, monkeypatch):
    """A factory of loaded mods that declare the given GUI languages."""
    root = PathStr.new(fs.root_dir.path).joinpath('project')
    fs.create_dir(root)
    monkeypatch.setattr(env, 'PROJECT_ROOT', root)

    def build(*gui_language):
        write_mod(fs, root, 'mod_a', 'a', gui_language)
        return ModLoader(root, mod_folders=('mod_a',)).dict_mod['a']

    return build


class TestResolveGuiLang:
    """The requested language is resolved against the declared ones."""

    def test_declared_language_is_kept(self, build_mod):
        """A language the mod translates is answered as requested."""
        mod = build_mod('zh-CN', 'en-US', 'ja-JP')

        assert mod.resolve_gui_lang('ja-JP') == 'ja-JP'
        assert mod.resolve_gui_lang('zh-CN') == 'zh-CN'

    def test_falls_back_to_the_default_language(self, build_mod):
        """A language the mod does not translate uses en-US."""
        mod = build_mod('zh-CN', 'en-US')

        assert mod.resolve_gui_lang('es-ES') == 'en-US'
        assert mod.resolve_gui_lang('ko-KR') == 'en-US'

    def test_falls_back_to_the_first_language_without_the_default(self, build_mod):
        """Without en-US the first declared language is used."""
        mod = build_mod('ja-JP', 'zh-CN')

        assert mod.resolve_gui_lang('es-ES') == 'ja-JP'
        assert mod.resolve_gui_lang('ko-KR') == 'ja-JP'

    def test_declared_order_decides_the_fallback(self, build_mod):
        """The declared order is the one the fallback follows."""
        assert build_mod('zh-CN', 'ja-JP').resolve_gui_lang('es-ES') == 'zh-CN'

    def test_an_empty_declaration_answers_the_default(self, build_mod):
        """A mod that declares no language at all still answers a tag."""
        mod = build_mod()

        assert mod.resolve_gui_lang('es-ES') == Const.GUI_LANGUAGE_DEFAULT

    def test_resolution_is_idempotent(self, build_mod):
        """Resolving an already resolved language changes nothing."""
        mod = build_mod('zh-CN', 'en-US')

        assert mod.resolve_gui_lang(mod.resolve_gui_lang('es-ES')) == 'en-US'


class TestGetGuiNav:
    """
    The nav of a mod is read in the requested language, without a fallback.

    The loader takes the language as is: a card the requested language has no
    name for is named by its key, so the caller decides which language to ask
    for (ConnState.content_lang) and the loader never picks one itself.
    """

    def test_names_of_an_unsupported_language(self, monkeypatch):
        """A language ExampleMod does not translate names the cards by key."""
        # the mod folders resolve against PROJECT_ROOT, which other tests move
        monkeypatch.setattr(env, 'PROJECT_ROOT', env.ALASIO_ROOT)
        loader = ModLoader(env.ALASIO_ROOT, mod_folders=('ExampleMod',))
        mod = loader.dict_mod['example_mod']
        assert 'es-ES' not in mod.entry.gui_language

        nav = loader.get_gui_nav('example_mod', 'es-ES')

        assert nav
        for nav_name, cards in nav.items():
            for card_name, card in cards.items():
                expected = nav_name if card_name == '_info' else card_name
                assert card['i18n'] == expected

    def test_no_fallback_to_the_default_language(self, monkeypatch):
        """The names of en-US are not borrowed for another language."""
        monkeypatch.setattr(env, 'PROJECT_ROOT', env.ALASIO_ROOT)
        loader = ModLoader(env.ALASIO_ROOT, mod_folders=('ExampleMod',))

        es = loader.get_gui_nav('example_mod', 'es-ES')
        en = loader.get_gui_nav('example_mod', 'en-US')

        assert es['alas']['card-Alas-Emulator']['i18n'] == 'card-Alas-Emulator'
        assert en['alas']['card-Alas-Emulator']['i18n'] == 'Emulator Settings'

    def test_names_of_a_supported_language(self, monkeypatch):
        """The translated name of a supported language is used as is."""
        monkeypatch.setattr(env, 'PROJECT_ROOT', env.ALASIO_ROOT)
        loader = ModLoader(env.ALASIO_ROOT, mod_folders=('ExampleMod',))

        nav = loader.get_gui_nav('example_mod', 'zh-CN')

        assert nav['alas']['card-Alas-Emulator']['i18n'] == '模拟器设置'


class TestGetGuiConfig:
    """
    The config view is read in exactly the requested language.

    The loader carries no language choice of its own: the caller resolves the
    request against the gui_language of the mod (ModBase.resolve_gui_lang) and
    the loader answers what the data of that language holds, nothing else.
    """

    @pytest.fixture
    def loader(self, monkeypatch):
        """
        A loader over the real ExampleMod, with the user config stubbed out.

        The config store is not part of this test: every (task, group, arg) is
        answered by a self-nesting mapping, so the view is built from the real
        generated indexes only.
        """

        def nested():
            return defaultdict(nested)

        # the mod folders resolve against PROJECT_ROOT, which other tests move
        monkeypatch.setattr(env, 'PROJECT_ROOT', env.ALASIO_ROOT)
        loader = ModLoader(env.ALASIO_ROOT, mod_folders=('ExampleMod',))
        mod = loader.dict_mod['example_mod']
        monkeypatch.setattr(mod, 'config_read', lambda config_name, ref: nested())
        return loader

    def test_a_mod_nav_without_the_language_has_no_names(self, loader):
        """A nav built from the mod's own translations answers no name at all.

        No fallback is applied: the translations of one arg are an open set of
        fields (name, help, option_i18n and custom ones), so there is nothing
        the loader could synthesize for a language the data does not carry.
        Choosing another language belongs to the caller.
        """
        view = loader.get_gui_config('example_mod', 'alas', 'general', 'es-ES')
        reference = loader.get_gui_config('example_mod', 'alas', 'general', 'en-US')

        assert view
        assert view != reference
        names = [data.get('name') for _, name, data in deep_iter_depth2(view) if name == '_info']
        assert names
        assert all(name is None for name in names), names

    def test_the_library_part_keeps_its_own_translations(self, loader):
        """A view merging several i18n files reads each one as the data holds it.

        The alasio groups of the 'alas' nav ship Spanish, the groups of the mod
        do not: the view is the faithful read of both, not one language forced
        onto the whole nav.
        """
        view = loader.get_gui_config('example_mod', 'alas', 'alas', 'es-ES')

        names = [data.get('name') for _, name, data in deep_iter_depth2(view) if name == '_info']
        assert 'Ajustes de emulador' in names
        assert None in names

    def test_the_default_language_names_every_row(self, loader):
        """en-US is complete, which is why the caller falls back to it."""
        view = loader.get_gui_config('example_mod', 'alas', 'general', 'en-US')

        names = [data.get('name') for _, name, data in deep_iter_depth2(view) if name == '_info']
        assert names
        assert all(isinstance(name, str) and name for name in names), names

    def test_the_requested_language_is_the_one_read(self, loader):
        """A translated language answers its own strings."""
        view = loader.get_gui_config('example_mod', 'alas', 'alas', 'zh-CN')

        names = [group_data['name'] for _, name, group_data in deep_iter_depth2(view) if name == '_info']
        assert '模拟器设置' in names

    def test_every_resolved_language_names_every_row(self, loader):
        """Resolving first, the way the topic does, never yields a blank row.

        This is the contract the connection relies on: the caller passes a
        language the mod declares (ConnState.content_lang), so the names are
        present.
        """
        mod = loader.dict_mod['example_mod']

        for lang in ('zh-CN', 'en-US', 'es-ES', 'ko-KR'):
            resolved = mod.resolve_gui_lang(lang)
            view = loader.get_gui_config('example_mod', 'alas', 'alas', resolved)
            rows = [group_data for _, name, group_data in deep_iter_depth2(view) if name == '_info']

            assert rows, f'no rows for {lang}'
            for row in rows:
                assert isinstance(row.get('name'), str)
                assert row['name'], f'blank name for {lang} at {resolved}'


class TestShippedLanguages:
    """The library declares the same tags the interface and the data use."""

    def test_the_library_declares_every_gui_language(self):
        """The library translates exactly the languages the frontend offers."""
        from alasio.config.entry.mod_entry import ModEntry

        # the order differs on purpose: Const.GUI_LANGUAGE is the preference
        # order of the interface, the declaration is the translation order of
        # the library's own content
        assert set(ModEntry.alasio.gui_language) == set(Const.GUI_LANGUAGE)

    def test_the_shipped_translations_carry_the_declared_languages(self):
        """Every declared language is present in the generated translations.

        A language declared but absent from the generated data renders blank
        labels, which is what a misspelled tag in the declaration looks like.
        """
        from alasio.config.entry.mod import Mod
        from alasio.config.entry.mod_entry import ModEntry

        mod = Mod(ModEntry.alasio)
        nav = mod.nav_i18n_json('alasio/device_i18n.json')
        group = nav['Emulator']['Serial']

        for lang in mod.entry.gui_language:
            assert lang in group, f'{lang} missing from device_i18n.json'
