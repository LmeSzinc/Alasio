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
from alasio.config.entry.loader import ModLoader, get_i18n_data
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
    """The nav of a mod is built in a language the mod translates."""

    def test_names_of_an_unsupported_language(self, monkeypatch):
        """A language ExampleMod does not translate still names every card."""
        # the mod folders resolve against PROJECT_ROOT, which other tests move
        monkeypatch.setattr(env, 'PROJECT_ROOT', env.ALASIO_ROOT)
        loader = ModLoader(env.ALASIO_ROOT, mod_folders=('ExampleMod',))
        mod = loader.dict_mod['example_mod']
        assert 'es-ES' not in mod.entry.gui_language

        nav = loader.get_gui_nav('example_mod', 'es-ES')

        assert nav
        for cards in nav.values():
            for card_name, card in cards.items():
                assert isinstance(card['i18n'], str)
                assert card['i18n'], f'empty name of {card_name}'
                # the name of the fallback language, not the raw key
                assert card['i18n'] != card_name

    def test_names_of_a_supported_language(self, monkeypatch):
        """The translated name of a supported language is used as is."""
        monkeypatch.setattr(env, 'PROJECT_ROOT', env.ALASIO_ROOT)
        loader = ModLoader(env.ALASIO_ROOT, mod_folders=('ExampleMod',))

        nav = loader.get_gui_nav('example_mod', 'zh-CN')

        assert nav['alas']['card-Alas-Emulator']['i18n'] == '模拟器设置'


class TestGetGuiConfig:
    """The config view of a mod is built in a language the mod translates."""

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

    @pytest.mark.parametrize('nav', ['alas', 'general', 'main'])
    def test_a_language_the_mod_does_not_translate(self, loader, nav):
        """An unsupported language renders exactly the default one, not blanks."""
        loader_mod = loader.dict_mod['example_mod']
        assert 'es-ES' not in loader_mod.entry.gui_language

        view = loader.get_gui_config('example_mod', 'alas', nav, 'es-ES')
        reference = loader.get_gui_config('example_mod', 'alas', nav, 'en-US')

        assert view
        assert view == reference

    def test_names_are_never_empty(self, loader):
        """Every row keeps a readable name instead of an empty label."""
        view = loader.get_gui_config('example_mod', 'alas', 'alas', 'es-ES')

        rows = [group_data for _, name, group_data in deep_iter_depth2(view) if name == '_info']
        assert rows
        for row in rows:
            assert isinstance(row['name'], str)
            assert row['name']


class TestGetI18nData:
    """The translations of one arg fall back to the default language."""

    # {group}.{arg}.{lang}.{field}
    I18N = {
        'Group': {
            'Arg': {
                'zh-CN': {'name': '参数', 'help': '说明'},
                'en-US': {'name': 'Argument', 'help': 'Help'},
            },
            'Untranslated': {'en-US': {'name': 'Only English'}},
        }
    }

    def test_exact_language(self):
        """The translations of the requested language are used."""
        assert get_i18n_data(self.I18N, ['Group', 'Arg'], 'zh-CN') == {'name': '参数', 'help': '说明'}

    def test_falls_back_to_the_default_language(self):
        """An arg without the requested language uses en-US."""
        assert get_i18n_data(self.I18N, ['Group', 'Arg'], 'es-ES') == {
            'name': 'Argument',
            'help': 'Help',
        }

    def test_an_arg_the_default_language_misses_is_empty(self):
        """Nothing is invented when neither language carries the arg."""
        assert get_i18n_data(self.I18N, ['Group', 'Missing'], 'zh-CN') == {}
        assert get_i18n_data(self.I18N, ['Missing', 'Arg'], 'en-US') == {}

    def test_the_result_is_always_a_dict(self):
        """A lookup miss answers a dict, so update() cannot fail on a str."""
        assert get_i18n_data({}, ['Group', 'Arg'], 'en-US') == {}


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
