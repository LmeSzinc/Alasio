"""Tests for GameStateBase class."""

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

    def test_instance_has_defaults(self):
        """Instance should have default values for server and lang."""
        state = GameStateBase()
        assert state.server == 'cn'
        assert state.lang == 'zh-CN'

    def test_dict_defaults_contains_server_and_lang(self):
        """dict_defaults should include server and lang."""
        state = GameStateBase()
        defaults = state.dict_defaults
        assert 'server' in defaults
        assert 'lang' in defaults
        assert defaults['server'] == 'cn'
        assert defaults['lang'] == 'zh-CN'

    def test_dict_defaults_does_not_contain_private(self):
        """dict_defaults should exclude private/protected attrs and callables."""
        state = GameStateBase()
        defaults = state.dict_defaults
        # Private attrs
        assert '_subclasses' not in defaults
        # dict_defaults itself is a cached_property, should be excluded
        assert 'dict_defaults' not in defaults
        # Methods should be excluded
        assert 'set_server' not in defaults
        assert 'set_lang' not in defaults
        assert 'match' not in defaults
        assert 'when' not in defaults
        assert '_match' not in defaults
        # Singleton methods from metaclass
        assert 'singleton_clear' not in defaults
        assert 'singleton_instance' not in defaults


class TestGameStateSingleton:
    """Tests for singleton behavior of GameStateBase."""

    def test_singleton_identity(self):
        """GameStateBase() should return the same instance."""
        s1 = GameStateBase()
        s2 = GameStateBase()
        assert s1 is s2

    def test_singleton_identity_multiple_calls(self):
        """Multiple calls to GameStateBase() should all return the same instance."""
        instances = [GameStateBase() for _ in range(10)]
        assert all(i is instances[0] for i in instances)

    def test_subclass_independent_singleton(self):
        """Each subclass of GameStateBase should have its own singleton."""
        class GameStateA(GameStateBase):
            pass

        class GameStateB(GameStateBase):
            pass

        a1 = GameStateA()
        a2 = GameStateA()
        b1 = GameStateB()
        b2 = GameStateB()
        assert a1 is a2
        assert b1 is b2
        assert a1 is not b1

    def test_singleton_clear_creates_new_instance(self):
        """After singleton_clear, a new instance should be returned."""
        class TempGameState(GameStateBase):
            pass

        first = TempGameState()
        TempGameState.singleton_clear()
        second = TempGameState()
        assert first is not second

    def test_singleton_instance_method(self):
        """singleton_instance should return the current instance or None."""
        class TempGameState(GameStateBase):
            pass

        assert TempGameState.singleton_instance() is None
        instance = TempGameState()
        assert TempGameState.singleton_instance() is instance


class TestGameStateInheritance:
    """Tests for subclassing GameStateBase with custom defaults."""

    def test_subclass_custom_defaults(self):
        """Subclass should be able to override server and lang defaults."""
        class CustomGameState(GameStateBase):
            server = 'en'
            lang = 'en-US'

        state = CustomGameState()
        assert state.server == 'en'
        assert state.lang == 'en-US'

    def test_subclass_with_extra_attrs(self):
        """Subclass should be able to add extra attributes."""
        class ExtraGameState(GameStateBase):
            region = 'na'
            channel = 'google'

        state = ExtraGameState()
        assert state.server == 'cn'
        assert state.lang == 'zh-CN'
        assert state.region == 'na'
        assert state.channel == 'google'

    def test_subclass_dict_defaults_reflects_overrides(self):
        """dict_defaults should reflect subclass overrides."""
        class CustomGameState(GameStateBase):
            server = 'en'
            extra = 42

        state = CustomGameState()
        assert state.dict_defaults['server'] == 'en'
        assert state.dict_defaults['lang'] == 'zh-CN'
        assert state.dict_defaults['extra'] == 42

    def test_subclass_does_not_affect_parent(self):
        """Modifying subclass state should not affect GameStateBase."""
        class CustomGameState(GameStateBase):
            server = 'en'

        # Reset GameStateBase singleton to default
        gs = GameStateBase()
        gs.server = 'cn'

        custom = CustomGameState()
        custom.server = 'jp'

        assert GameStateBase().server == 'cn'
        assert CustomGameState().server == 'jp'

    def test_multi_level_inheritance(self):
        """Multi-level inheritance should work correctly."""
        class Level1(GameStateBase):
            server = 'en'
            custom_field = 'level1'

        class Level2(Level1):
            server = 'jp'
            lang = 'ja-JP'

        state = Level2()
        assert state.server == 'jp'
        assert state.lang == 'ja-JP'
        assert state.custom_field == 'level1'
        assert Level2().dict_defaults == {
            'server': 'jp',
            'lang': 'ja-JP',
            'custom_field': 'level1',
        }


class TestGameStateMatch:
    """Tests for match and _match methods."""

    def test_match_single_condition_true(self):
        """match should return True if single condition matches default."""
        assert GameStateBase.match(server='cn')
        assert GameStateBase.match(lang='zh-CN')

    def test_match_single_condition_false(self):
        """match should return False if single condition doesn't match."""
        assert not GameStateBase.match(server='en')
        assert not GameStateBase.match(lang='en-US')

    def test_match_multiple_conditions_all_match(self):
        """match should return True if all conditions match."""
        assert GameStateBase.match(server='cn', lang='zh-CN')

    def test_match_multiple_conditions_one_fails(self):
        """match should return False if any condition fails (AND logic)."""
        assert not GameStateBase.match(server='cn', lang='en-US')
        assert not GameStateBase.match(server='en', lang='zh-CN')
        assert not GameStateBase.match(server='en', lang='en-US')

    def test_match_nonexistent_key(self):
        """match should return False for non-existent attributes."""
        assert not GameStateBase.match(nonexistent='value')
        assert not GameStateBase.match(foo='bar')

    def test_match_mixed_existent_and_nonexistent(self):
        """match should return False if any key doesn't exist."""
        assert not GameStateBase.match(server='cn', nonexistent='value')
        assert not GameStateBase.match(server='cn', lang='zh-CN', fake='attr')

    def test_match_empty_kwargs(self):
        """match with empty kwargs should return True (vacuous truth)."""
        assert GameStateBase.match()

    @pytest.mark.parametrize("server, lang, expected", [
        ('cn', 'zh-CN', True),
        ('cn', 'en-US', False),
        ('en', 'zh-CN', False),
        ('en', 'en-US', False),
        ('jp', 'ja-JP', False),
        ('tw', 'zh-TW', False),
    ])
    def test_match_parametrized_combinations(self, server, lang, expected):
        """match with various server/lang combinations."""
        assert GameStateBase.match(server=server, lang=lang) == expected

    def test_match_after_set_server(self):
        """match should reflect instance-set values."""
        class GS(GameStateBase):
            pass

        state = GS()
        state.server = 'en'
        assert GS.match(server='en')
        assert not GS.match(server='cn')

    def test_match_with_multiple_conditions_after_set(self):
        """match with multiple conditions should reflect instance-set values."""
        class GS(GameStateBase):
            pass

        state = GS()
        state.server = 'en'
        state.lang = 'en-US'
        assert GS.match(server='en', lang='en-US')
        assert not GS.match(server='en', lang='zh-CN')

    def test_match_classmethod_vs_instance_method(self):
        """Both match (classmethod) and _match (instance) should produce same results."""
        state = GameStateBase()
        assert GameStateBase.match(server='cn') == state._match(server='cn')
        assert GameStateBase.match(server='en') == state._match(server='en')

    def test_match_on_subclass_with_custom_defaults(self):
        """match should work on subclasses with custom defaults."""
        class GS(GameStateBase):
            server = 'en'
            lang = 'en-US'

        assert GS.match(server='en')
        assert GS.match(lang='en-US')
        assert not GS.match(server='cn')
        assert not GS.match(server='cn', lang='en-US')


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
        """@when on a subclass should use the subclass singleton for matching."""
        class GS(GameStateBase):
            server = 'jp'

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
            server = 'jp'

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
            server = 'cn'

        class GSEn(GameStateBase):
            server = 'en'

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

    def test_when_with_set_server_on_subclass(self):
        """@when should reflect set_server changes on the subclass."""
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
        GS.set_server('en')

        # Now condition matches: cases[0] empty cond sets fallback_func,
        # cases[1]: match(server='en') -> True -> returns func() immediately
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
            server = 'jp'

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
        GS.set_server('en')

        # Now handle_en should match, handle_cn should not
        assert handler.handle_en(2) == 'en:2'
        assert handler.handle_cn(2) is None

        # Change back to 'cn' by directly setting
        gs = GS()
        gs.server = 'cn'

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
        GS.set_server('en')

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
        GS.set_server('en')
        assert handler.handle(2) == 2
        assert call_log == [2]
        call_log.clear()

        # Change to 'jp'
        GS.set_server('jp')
        assert handler.handle(3) == 3
        assert call_log == [3]
        call_log.clear()

        # Change to 'tw' → no match → fallback
        GS.set_server('tw')
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
        GS.set_server('en')
        assert my_func('b') == 'ok:b'
        assert call_log == ['b']
        call_log.clear()

        # Change to 'jp' — no match → fallback
        GS.set_server('jp')
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

        assert GS().server == 'cn'
        my_func()
        # State should not change after call
        assert GS().server == 'cn'

    def test_when_with_custom_state_after_clear(self):
        """@when should work after singleton_clear on the state class."""
        class GS(GameStateBase):
            pass

        GS.set_server('en')

        @GS.when(server='en')
        def my_func():
            return 'en_result'

        assert my_func() == 'en_result'

        GS.singleton_clear()

        # After clear, the singleton is a new instance with default 'cn'
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
