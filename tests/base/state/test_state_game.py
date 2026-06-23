"""
Tests for GameStateBase class.

States are class-based (no instantiation). Field assignment goes through _StateMeta.__setattr__
which validates via msgspec.convert against the struct model.
"""
import msgspec
import pytest

from alasio.base.state import GameStateBase, _StateDispatcher


class TestGameStateDefaults:
    """Tests for default values of GameStateBase."""

    def test_default_server(self):
        """GameStateBase should default to server='cn'."""
        assert GameStateBase.server == 'cn'

    def test_default_lang(self):
        """GameStateBase should default to lang='zh-CN'."""
        assert GameStateBase.lang == 'zh-CN'

    def test_dict_defaults_contains_server_and_lang(self):
        """dict_defaults should include server and lang."""
        defaults = GameStateBase.__dict_defaults__
        assert 'server' in defaults
        assert 'lang' in defaults
        assert defaults['server'] == 'cn'
        assert defaults['lang'] == 'zh-CN'

    def test_dict_defaults_does_not_contain_methods(self):
        """dict_defaults should exclude methods."""
        defaults = GameStateBase.__dict_defaults__
        # Methods should be excluded
        assert 'match' not in defaults
        assert 'when' not in defaults
        # dict_defaults itself is internal, not a state field
        assert 'dict_defaults' not in defaults
        assert 'struct_model' not in defaults

    def test_cannot_instantiate(self):
        """GameStateBase() should raise TypeError."""
        with pytest.raises(TypeError, match='cannot be instantiated'):
            GameStateBase()


class TestGameStateInheritance:
    """Tests for subclassing GameStateBase with custom defaults."""

    def test_subclass_custom_defaults(self):
        """Subclass should be able to override server and lang defaults."""

        class CustomGameState(GameStateBase):
            server: str = 'en'
            lang: str = 'en-US'

        assert CustomGameState.server == 'en'
        assert CustomGameState.lang == 'en-US'

    def test_subclass_dict_defaults_reflects_overrides(self):
        """dict_defaults should reflect subclass overrides."""

        class CustomGameState(GameStateBase):
            server: str = 'en'
            extra: int = 42

        assert CustomGameState.__dict_defaults__['server'] == 'en'
        assert CustomGameState.__dict_defaults__['lang'] == 'zh-CN'
        assert CustomGameState.__dict_defaults__['extra'] == 42

    def test_subclass_does_not_affect_parent(self):
        """Modifying subclass state should not affect GameStateBase."""

        class CustomGameState(GameStateBase):
            server: str = 'en'

        # Reset GameStateBase to default
        GameStateBase.reset_field('server')

        CustomGameState.server = 'jp'

        assert GameStateBase.server == 'cn'
        assert CustomGameState.server == 'jp'

    def test_multi_level_inheritance(self):
        """Multi-level inheritance should work correctly."""

        class Level1(GameStateBase):
            server: str = 'en'
            custom_field: str = 'level1'

        class Level2(Level1):
            server: str = 'jp'
            lang: str = 'ja-JP'

        assert Level2.server == 'jp'
        assert Level2.lang == 'ja-JP'
        assert Level2.custom_field == 'level1'
        assert Level2.__dict_defaults__ == {
            'server': 'jp',
            'lang': 'ja-JP',
            'custom_field': 'level1',
        }


class TestGameStateAssignment:
    """Tests for direct field assignment."""

    def test_direct_assignment(self):
        """Direct assignment should work."""

        class GS(GameStateBase):
            server: str = 'cn'

        GS.server = 'en'
        assert GS.server == 'en'

    def test_direct_assignment_literal_valid(self):
        """Direct assignment with valid Literal value should work."""
        from typing import Literal

        class GS(GameStateBase):
            server: Literal['cn', 'en', 'jp', 'tw'] = 'cn'

        GS.server = 'en'
        assert GS.server == 'en'

    def test_direct_assignment_literal_invalid_raises(self):
        """Direct assignment with invalid Literal value should raise ValidationError."""
        from typing import Literal

        class GS(GameStateBase):
            server: Literal['cn', 'en'] = 'cn'

        with pytest.raises(msgspec.ValidationError):
            GS.server = 'jp'

    def test_direct_assignment_type_mismatch_raises(self):
        """Direct assignment with wrong type should raise ValidationError."""

        class GS(GameStateBase):
            server: str = 'cn'

        with pytest.raises(msgspec.ValidationError):
            GS.server = 123

    def test_assignment_persists(self):
        """Value set via direct assignment should persist on the class."""
        from typing import Literal

        class GS(GameStateBase):
            server: Literal['cn', 'en'] = 'cn'

        GS.server = 'en'
        assert GS.server == 'en'
        # Parent class should not be affected
        assert GameStateBase.server == 'cn'

    def test_assignment_reflected_in_is_modified(self):
        """After assignment, is_modified should reflect the change."""
        from typing import Literal

        class GS(GameStateBase):
            server: Literal['cn', 'en'] = 'cn'

        GS.server = 'en'
        assert GS.is_modified('server')

    def test_assignment_overwrites_existing(self):
        """Multiple assignments should overwrite previous values."""
        from typing import Literal

        class GS(GameStateBase):
            server: Literal['cn', 'en', 'jp'] = 'cn'

        GS.server = 'en'
        GS.server = 'jp'
        assert GS.server == 'jp'
        assert GS.is_modified('server')

    def test_update_via_update_method(self):
        """update() should work for batch setting."""

        class GS(GameStateBase):
            server: str = 'cn'
            lang: str = 'zh-CN'

        GS.update(server='en', lang='en-US')
        assert GS.server == 'en'
        assert GS.lang == 'en-US'


class TestGameStateMatchServer:
    """Tests for GameStateBase.match_server()."""

    def setup_method(self):
        """Reset class-level state before each test."""
        GameStateBase.reset_all_fields()

    def test_match_server_single_match(self):
        """match_server with a single matching server should return True."""
        assert GameStateBase.match_server('cn') is True

    def test_match_server_single_no_match(self):
        """match_server with a single non-matching server should return False."""
        assert GameStateBase.match_server('en') is False

    def test_match_server_multiple_one_matches(self):
        """match_server with multiple args should return True if any matches."""
        assert GameStateBase.match_server('en', 'cn') is True

    def test_match_server_multiple_none_match(self):
        """match_server with multiple non-matching args should return False."""
        assert GameStateBase.match_server('en', 'jp') is False

    def test_match_server_no_args(self):
        """match_server with no args should return False."""
        assert GameStateBase.match_server() is False

    def test_match_server_after_change(self):
        """match_server should reflect runtime state changes."""
        GameStateBase.server = 'jp'
        assert GameStateBase.match_server('jp') is True
        assert GameStateBase.match_server('cn', 'en') is False

    def test_match_server_unpack_list(self):
        """A list can be unpacked into match_server."""
        assert GameStateBase.match_server(*['cn', 'en']) is True
        assert GameStateBase.match_server(*['en', 'jp']) is False

    def test_match_server_on_subclass(self):
        """match_server should respect subclass overrides."""
        class CustomGS(GameStateBase):
            server: str = 'en'

        assert CustomGS.match_server('en') is True
        assert CustomGS.match_server('cn') is False
        assert CustomGS.match_server('cn', 'en') is True


class TestGameStateMatchLang:
    """Tests for GameStateBase.match_lang()."""

    def setup_method(self):
        """Reset class-level state before each test."""
        GameStateBase.reset_all_fields()

    def test_match_lang_single_match(self):
        """match_lang with a single matching lang should return True."""
        assert GameStateBase.match_lang('zh-CN') is True

    def test_match_lang_single_no_match(self):
        """match_lang with a single non-matching lang should return False."""
        assert GameStateBase.match_lang('en-US') is False

    def test_match_lang_multiple_one_matches(self):
        """match_lang with multiple args should return True if any matches."""
        assert GameStateBase.match_lang('en-US', 'zh-CN') is True

    def test_match_lang_multiple_none_match(self):
        """match_lang with multiple non-matching args should return False."""
        assert GameStateBase.match_lang('en-US', 'ja-JP') is False

    def test_match_lang_no_args(self):
        """match_lang with no args should return False."""
        assert GameStateBase.match_lang() is False

    def test_match_lang_after_change(self):
        """match_lang should reflect runtime state changes."""
        GameStateBase.lang = 'en-US'
        assert GameStateBase.match_lang('en-US') is True
        assert GameStateBase.match_lang('zh-CN', 'ja-JP') is False

    def test_match_lang_unpack_list(self):
        """A list can be unpacked into match_lang."""
        assert GameStateBase.match_lang(*['zh-CN', 'en-US']) is True
        assert GameStateBase.match_lang(*['en-US', 'ja-JP']) is False

    def test_match_lang_on_subclass(self):
        """match_lang should respect subclass overrides."""
        class CustomGS(GameStateBase):
            lang: str = 'ja-JP'

        assert CustomGS.match_lang('ja-JP') is True
        assert CustomGS.match_lang('zh-CN') is False
        assert CustomGS.match_lang('zh-CN', 'ja-JP') is True
