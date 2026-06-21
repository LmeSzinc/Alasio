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
        defaults = GameStateBase.dict_defaults
        assert 'server' in defaults
        assert 'lang' in defaults
        assert defaults['server'] == 'cn'
        assert defaults['lang'] == 'zh-CN'

    def test_dict_defaults_does_not_contain_methods(self):
        """dict_defaults should exclude methods."""
        defaults = GameStateBase.dict_defaults
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

        assert CustomGameState.dict_defaults['server'] == 'en'
        assert CustomGameState.dict_defaults['lang'] == 'zh-CN'
        assert CustomGameState.dict_defaults['extra'] == 42

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
        assert Level2.dict_defaults == {
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


class TestGameStateWhen:
    """Tests for when decorator with _StateDispatcher."""

    def test_single_condition_match(self):
        """@when with matching condition should call and return the function."""
        call_count = 0

        @GameStateBase.when(server='cn')
        def my_func():
            nonlocal call_count
            call_count += 1
            return 42

        result = my_func()
        assert result == 42
        assert call_count == 1

    def test_single_condition_no_match(self):
        """@when with non-matching condition should return None and not call."""
        call_count = 0

        @GameStateBase.when(server='en')
        def my_func():
            nonlocal call_count
            call_count += 1
            return 42

        result = my_func()
        assert result is None
        assert call_count == 0

    def test_fallback_empty_when(self):
        """@when() without kwargs should always call the function."""
        call_count = 0

        @GameStateBase.when()
        def my_func():
            nonlocal call_count
            call_count += 1
            return 99

        result = my_func()
        assert result == 99
        assert call_count == 1

    def test_fallback_called_when_condition_misses(self):
        """Fallback should be called when no other conditions match."""
        call_log = []

        @GameStateBase.when(server='en')
        @GameStateBase.when()
        def my_func():
            call_log.append('fallback')
            return 'fb'

        # Default server='cn', condition server='en' won't match
        result = my_func()
        assert result == 'fb'
        assert call_log == ['fallback']

    def test_condition_wins_over_fallback(self):
        """Matching condition should execute instead of fallback."""
        call_log = []

        @GameStateBase.when(server='cn')
        @GameStateBase.when()
        def my_func():
            call_log.append('condition')
            return 'matched'

        # Default server='cn' matches the condition
        result = my_func()
        assert result == 'matched'
        assert call_log == ['condition']

    def test_chained_conditions_first_matches(self):
        """First matching condition in chain should be called."""
        call_log = []

        @GameStateBase.when(server='en')
        @GameStateBase.when(server='cn')
        @GameStateBase.when()
        def my_func():
            call_log.append('matched')
            return 'ok'

        # Default server='cn' matches the second condition
        result = my_func()
        assert result == 'ok'
        assert call_log == ['matched']

    def test_chained_conditions_second_matches(self):
        """When first condition doesn't match but second does."""
        call_log = []

        @GameStateBase.when(server='jp')
        @GameStateBase.when(server='cn')
        @GameStateBase.when()
        def my_func():
            call_log.append('matched')
            return 'ok'

        # Default server='cn' doesn't match 'jp', then matches 'cn'
        result = my_func()
        assert result == 'ok'
        assert call_log == ['matched']

    def test_no_fallback_no_match_returns_none(self):
        """Without fallback and no matching condition, should return None."""
        call_count = 0

        @GameStateBase.when(server='en')
        @GameStateBase.when(server='jp')
        def my_func():
            nonlocal call_count
            call_count += 1
            return 'should_not_happen'

        result = my_func()
        assert result is None
        assert call_count == 0

    def test_propagates_arguments(self):
        """Arguments should be forwarded to the decorated function."""

        @GameStateBase.when(server='cn')
        def add(a, b):
            return a + b

        assert add(1, 2) == 3
        assert add(10, 20) == 30

    def test_propagates_kwargs(self):
        """Keyword arguments should be forwarded to the decorated function."""

        @GameStateBase.when(server='cn')
        def greet(name, greeting='Hello'):
            return f'{greeting}, {name}!'

        assert greet('World') == 'Hello, World!'
        assert greet('Alasio', greeting='Hi') == 'Hi, Alasio!'

    def test_returns_none_for_explicitly_null(self):
        """A decorated function that returns None should still return None."""

        @GameStateBase.when(server='cn')
        def returns_none():
            return None

        result = returns_none()
        assert result is None

    def test_multiple_conditions_and_logic(self):
        """@when with multiple kwargs should require all conditions to match."""
        call_count = 0

        @GameStateBase.when(server='cn', lang='zh-CN')
        def my_func():
            nonlocal call_count
            call_count += 1
            return 'ok'

        # Default matches both
        assert my_func() == 'ok'
        assert call_count == 1

    def test_multiple_conditions_partial_match(self):
        """@when with multiple kwargs should fail if any condition doesn't match."""
        call_count = 0

        @GameStateBase.when(server='cn', lang='en-US')
        def my_func():
            nonlocal call_count
            call_count += 1
            return 'ok'

        # Default lang is 'zh-CN', not 'en-US'
        assert my_func() is None
        assert call_count == 0

    def test_when_with_lang_condition(self):
        """@when with lang condition should work."""
        call_count = 0

        @GameStateBase.when(lang='zh-CN')
        def my_func():
            nonlocal call_count
            call_count += 1
            return 'cn_func'

        assert my_func() == 'cn_func'
        assert call_count == 1

        @GameStateBase.when(lang='en-US')
        def other_func():
            nonlocal call_count
            call_count += 1
            return 'en_func'

        assert other_func() is None
        assert call_count == 1  # not called

    def test_when_on_subclass(self):
        """@when on a subclass should use the subclass state for matching."""

        class GS(GameStateBase):
            server: str = 'jp'

        call_count = 0

        @GS.when(server='jp')
        def my_func():
            nonlocal call_count
            call_count += 1
            return 'jp_ok'

        assert my_func() == 'jp_ok'
        assert call_count == 1

    def test_when_on_subclass_no_match(self):
        """@when on a subclass should not match if conditions don't match subclass defaults."""

        class GS(GameStateBase):
            server: str = 'jp'

        call_count = 0

        @GS.when(server='cn')
        def my_func():
            nonlocal call_count
            call_count += 1
            return 'cn_but_gs_is_jp'

        assert my_func() is None
        assert call_count == 0

    def test_when_on_different_subclasses_independent(self):
        """@when on different subclasses should be independent."""

        class GSCn(GameStateBase):
            server: str = 'cn'

        class GSEn(GameStateBase):
            server: str = 'en'

        call_log = []

        @GSCn.when(server='cn')
        def cn_func():
            call_log.append('cn')
            return 'cn'

        @GSEn.when(server='en')
        def en_func():
            call_log.append('en')
            return 'en'

        assert cn_func() == 'cn'
        assert call_log == ['cn']
        assert en_func() == 'en'
        assert call_log == ['cn', 'en']

    def test_when_with_assigned_server_on_subclass(self):
        """@when should reflect server assignment changes on the subclass."""

        class GS(GameStateBase):
            pass

        call_log = []

        @GS.when(server='en')
        @GS.when()
        def my_func():
            call_log.append('fallback')
            return 'fb'

        # Default is 'cn', should use fallback
        assert my_func() == 'fb'
        assert call_log == ['fallback']

        call_log.clear()

        # Change server to 'en'
        GS.server = 'en'

        # Now condition matches
        result = my_func()
        assert result == 'fb'
        assert call_log == ['fallback']

    def test_when_returns_none_without_fallback(self):
        """When no conditions match and no fallback is defined, return None."""

        @GameStateBase.when(server='en')
        def my_func():
            return 'only_for_en'

        result = my_func()
        assert result is None

    def test_when_decorator_on_classmethod(self):
        """@when should work with classmethods (@classmethod must be outer)."""

        class MyClass:
            @classmethod
            @GameStateBase.when(server='cn')
            def class_method(cls):
                return f'class_method_on_{cls.__name__}'

        result = MyClass.class_method()
        assert result == 'class_method_on_MyClass'

    def test_when_decorator_on_staticmethod(self):
        """@when should work with staticmethods (@staticmethod must be outer)."""

        class MyClass:
            @staticmethod
            @GameStateBase.when(server='cn')
            def static_method():
                return 'static_result'

        result = MyClass.static_method()
        assert result == 'static_result'


class TestGameStateWhenOnMethod:
    """Tests for @when decorating instance methods of regular classes."""

    def test_instance_method_condition_match(self):
        """@when on instance method should work when condition matches."""

        class MyHandler:
            @GameStateBase.when(server='cn')
            def handle(self, value):
                return f'handled_{value}'

        obj = MyHandler()
        assert obj.handle('test') == 'handled_test'

    def test_instance_method_condition_no_match(self):
        """@when on instance method should return None when condition doesn't match."""

        class MyHandler:
            @GameStateBase.when(server='en')
            def handle(self, value):
                return f'handled_{value}'

        obj = MyHandler()
        assert obj.handle('test') is None

    def test_instance_method_preserves_self(self):
        """@when on instance method should correctly forward self."""

        class MyHandler:
            def __init__(self):
                self.prefix = 'pre_'

            @GameStateBase.when(server='cn')
            def handle(self, value):
                return f'{self.prefix}{value}'

        obj = MyHandler()
        assert obj.handle('data') == 'pre_data'

    def test_instance_method_chained_conditions(self):
        """Chained @when on instance method should work."""

        class MyHandler:
            @GameStateBase.when(server='en')
            @GameStateBase.when(server='cn')
            def handle(self, x):
                return x * 2

        obj = MyHandler()
        assert obj.handle(21) == 42

    def test_instance_method_chained_with_fallback(self):
        """@when with fallback on instance method should fallback when no match."""

        class MyHandler:
            @GameStateBase.when(server='en')
            @GameStateBase.when()
            def handle(self, x):
                return x + 1

        obj = MyHandler()
        # Default server='cn', condition 'en' won't match, fallback used
        assert obj.handle(10) == 11

    def test_instance_method_condition_matches_before_fallback(self):
        """@when condition should match before fallback on instance method."""

        class MyHandler:
            @GameStateBase.when(server='cn')
            @GameStateBase.when()
            def handle(self, x):
                return x * 10

        obj = MyHandler()
        # Default server='cn' matches the condition
        assert obj.handle(5) == 50

    def test_instance_method_with_subclass_state(self):
        """@when on instance method with subclass state should work."""

        class GS(GameStateBase):
            server: str = 'jp'

        class MyHandler:
            @GS.when(server='jp')
            def handle(self, value):
                return value.upper()

        obj = MyHandler()
        assert obj.handle('hello') == 'HELLO'

    def test_multiple_instance_methods_independent(self):
        """Multiple @when-decorated methods on same class should be independent."""

        class MyHandler:
            @GameStateBase.when(server='cn')
            def method_a(self, x):
                return f'a{x}'

            @GameStateBase.when(server='cn')
            def method_b(self, x):
                return f'b{x}'

        obj = MyHandler()
        assert obj.method_a(1) == 'a1'
        assert obj.method_b(2) == 'b2'

    def test_different_instances_same_class(self):
        """Different instances of the same class with @when should work."""

        class MyHandler:
            @GameStateBase.when(server='cn')
            def handle(self, value):
                return value

        a = MyHandler()
        b = MyHandler()
        assert a.handle('x') == 'x'
        assert b.handle('y') == 'y'


class TestGameStateWhenOnMethodRuntimeChange:
    """Tests for runtime state changes affecting @when on instance methods."""

    def test_runtime_change_switches_which_method_executes(self):
        """Changing server at runtime should switch which @when method fires."""

        class GS(GameStateBase):
            pass

        class MyHandler:
            @GS.when(server='en')
            def handle_en(self, x):
                return f'en:{x}'

            @GS.when(server='cn')
            def handle_cn(self, x):
                return f'cn:{x}'

        handler = MyHandler()

        # Default server is 'cn'
        assert handler.handle_cn(1) == 'cn:1'
        assert handler.handle_en(1) is None

        # Change server to 'en' at runtime
        GS.server = 'en'

        # Now handle_en should match, handle_cn should not
        assert handler.handle_en(2) == 'en:2'
        assert handler.handle_cn(2) is None

        # Change back to 'cn' by directly setting the class attribute
        GS.server = 'cn'

        assert handler.handle_cn(3) == 'cn:3'
        assert handler.handle_en(3) is None

    def test_runtime_change_with_fallback(self):
        """Changing server at runtime should switch between condition and fallback."""

        class GS(GameStateBase):
            pass

        call_log = []

        class MyHandler:
            @GS.when(server='en')
            @GS.when()
            def handle(self, x):
                call_log.append(x)
                return x * 10

        handler = MyHandler()

        # Default server 'cn', condition 'en' won't match → fallback
        assert handler.handle(5) == 50
        assert call_log == [5]
        call_log.clear()

        # Change server to 'en' at runtime
        GS.server = 'en'

        # Now condition 'en' matches (same function, but dispatch path changed)
        assert handler.handle(7) == 70
        assert call_log == [7]

    def test_runtime_change_multiple_conditions(self):
        """Changing server should switch between multiple @when conditions."""

        class GS(GameStateBase):
            pass

        call_log = []

        class MyHandler:
            @GS.when(server='jp')
            @GS.when(server='en')
            @GS.when()
            def handle(self, x):
                call_log.append(x)
                return x

        handler = MyHandler()

        # Default 'cn' → no match on 'jp' or 'en' → fallback
        assert handler.handle(1) == 1
        assert call_log == [1]
        call_log.clear()

        # Change to 'en'
        GS.server = 'en'
        assert handler.handle(2) == 2
        assert call_log == [2]
        call_log.clear()

        # Change to 'jp'
        GS.server = 'jp'
        assert handler.handle(3) == 3
        assert call_log == [3]
        call_log.clear()

        # Change to 'tw' → no match → fallback
        GS.server = 'tw'
        assert handler.handle(4) == 4
        assert call_log == [4]

    def test_runtime_change_standalone_function(self):
        """Changing server at runtime should switch dispatch of standalone @when functions."""

        class GS(GameStateBase):
            pass

        call_log = []

        @GS.when(server='en')
        @GS.when(server='cn')
        @GS.when()
        def my_func(label):
            call_log.append(label)
            return f'ok:{label}'

        # Default 'cn' matches second condition
        assert my_func('a') == 'ok:a'
        assert call_log == ['a']
        call_log.clear()

        # Change to 'en' — first condition matches
        GS.server = 'en'
        assert my_func('b') == 'ok:b'
        assert call_log == ['b']
        call_log.clear()

        # Change to 'jp' — no match → fallback
        GS.server = 'jp'
        assert my_func('c') == 'ok:c'
        assert call_log == ['c']


class TestGameStateWhenIntegration:
    """Integration tests for when decorator with state changes."""

    def test_when_isolation_between_functions(self):
        """Different functions with @when should be isolated."""
        results = []

        @GameStateBase.when(server='cn')
        def func_a():
            results.append('a')
            return 'A'

        @GameStateBase.when(server='cn')
        def func_b():
            results.append('b')
            return 'B'

        assert func_a() == 'A'
        assert func_b() == 'B'
        assert results == ['a', 'b']

    def test_when_does_not_mutate_global_state(self):
        """Calling a @when-decorated function should not modify GameState."""

        class GS(GameStateBase):
            pass

        @GS.when(server='cn')
        def my_func():
            return 'ok'

        assert GS.server == 'cn'
        my_func()
        # State should not change after call
        assert GS.server == 'cn'

    def test_when_after_reset(self):
        """@when should work after resetting the state field to default."""

        class GS(GameStateBase):
            pass

        GS.server = 'en'

        @GS.when(server='en')
        def my_func():
            return 'en_result'

        assert my_func() == 'en_result'

        # Reset to default 'cn'
        GS.reset_field('server')

        @GS.when(server='cn')
        def other_func():
            return 'cn_result'

        assert other_func() == 'cn_result'


class TestGameStateDispatcherEdgeCases:
    """Edge cases for _StateDispatcher and when decorator."""

    def test_dispatcher_registry_key_uniqueness(self):
        """Different functions should have unique keys in FUNC_REGISTRY."""
        key_before = len(_StateDispatcher.FUNC_REGISTRY)

        @GameStateBase.when(server='cn')
        def func_a():
            return 'a'

        @GameStateBase.when(server='en')
        def func_b():
            return 'b'

        assert len(_StateDispatcher.FUNC_REGISTRY) == key_before + 2

    def test_dispatcher_wraps_function_name(self):
        """The dispatcher should preserve the original function's name."""

        @GameStateBase.when(server='cn')
        def my_special_function():
            return 'ok'

        assert my_special_function.__name__ == 'my_special_function'

    def test_dispatcher_wraps_function_doc(self):
        """The dispatcher should preserve the original function's docstring."""

        @GameStateBase.when(server='cn')
        def my_documented_func():
            """This is my documented function."""
            return 'ok'

        assert my_documented_func.__doc__ == 'This is my documented function.'

    def test_empty_cases_list(self):
        """A _StateDispatcher with no cases should return None on call."""

        def dummy():
            pass

        dispatcher = _StateDispatcher(dummy, GameStateBase)
        dispatcher.cases.clear()

        # Without cases, __call__ should return None
        assert dispatcher() is None

    def test_multiple_fallbacks_only_last_used(self):
        """If multiple fallbacks are defined, only the last one should be used."""
        call_log = []

        @GameStateBase.when()
        @GameStateBase.when()
        def my_func():
            call_log.append('fallback')
            return 'last_fallback'

        result = my_func()
        assert result == 'last_fallback'
        assert call_log == ['fallback']
