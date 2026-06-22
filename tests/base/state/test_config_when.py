"""
Tests for Config.when() decorator for method dispatch.

Config.when() dispatches to the correct method based on self.config attribute values.
"""
import pytest

from alasio.base.state import Config, _ConfigDispatcher
from alasio.logger import logger


class SimpleConfig:
    """Simple config class for testing."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class TestConfigWhenBasic:
    """Basic tests for Config.when() matching behavior."""

    def setup_method(self):
        Config.REGISTRY.clear()

    def test_single_condition_match(self):
        """@Config.when with matching condition should call and return the method."""

        class MyTask:
            config = SimpleConfig(
                DEVICE_SCREENSHOT_METHOD='adb',
            )

            @Config.when(DEVICE_SCREENSHOT_METHOD='adb')
            def screenshot(self):
                return 'adb_screenshot'

        task = MyTask()
        assert task.screenshot() == 'adb_screenshot'

    def test_single_condition_no_match(self):
        """@Config.when with non-matching condition should call last defined function and log warning."""
        call_count = 0

        class MyTask:
            config = SimpleConfig(
                DEVICE_SCREENSHOT_METHOD='uiautomator2',
            )

            @Config.when(DEVICE_SCREENSHOT_METHOD='adb')
            def screenshot(self):
                nonlocal call_count
                call_count += 1
                return 'adb_screenshot'

        task = MyTask()
        with logger.mock_capture_writer() as capture:
            result = task.screenshot()
        assert result == 'adb_screenshot'
        assert call_count == 1
        assert capture.backend.any_contains('no condition matched')

    def test_fallback_empty_when(self):
        """@Config.when() without kwargs should always call the method."""
        call_count = 0

        class MyTask:
            config = SimpleConfig(
                DEVICE_SCREENSHOT_METHOD='adb',
            )

            @Config.when()
            def screenshot(self):
                nonlocal call_count
                call_count += 1
                return 'fallback'

        task = MyTask()
        result = task.screenshot()
        assert result == 'fallback'
        assert call_count == 1

    def test_multiple_keywords_all_match(self):
        """@Config.when with multiple kwargs should require all to match."""
        call_count = 0

        class MyTask:
            config = SimpleConfig(
                DEVICE_SCREENSHOT_METHOD='adb',
                EMULATOR='bluestacks',
            )

            @Config.when(DEVICE_SCREENSHOT_METHOD='adb', EMULATOR='bluestacks')
            def screenshot(self):
                nonlocal call_count
                call_count += 1
                return 'adb_bluestacks'

        task = MyTask()
        assert task.screenshot() == 'adb_bluestacks'
        assert call_count == 1

    def test_multiple_keywords_partial_match(self):
        """@Config.when with multiple kwargs should fail if any doesn't match."""
        call_count = 0

        class MyTask:
            config = SimpleConfig(
                DEVICE_SCREENSHOT_METHOD='adb',
                EMULATOR='mumu',
            )

            @Config.when(DEVICE_SCREENSHOT_METHOD='adb', EMULATOR='bluestacks')
            def screenshot(self):
                nonlocal call_count
                call_count += 1
                return 'result'

        task = MyTask()
        with logger.mock_capture_writer():
            result = task.screenshot()
        assert result == 'result'
        assert call_count == 1


class TestConfigWhenChained:
    """Tests for chained @Config.when() decorators."""

    def setup_method(self):
        Config.REGISTRY.clear()

    def test_chained_conditions_first_matches(self):
        """First matching chained condition should be used."""
        call_log = []

        class MyTask:
            config = SimpleConfig(
                DEVICE_SCREENSHOT_METHOD='adb',
            )

            @Config.when(DEVICE_SCREENSHOT_METHOD='uiautomator2')
            @Config.when(DEVICE_SCREENSHOT_METHOD='adb')
            @Config.when()
            def screenshot(self):
                call_log.append('screenshot')
                return 'ok'

        task = MyTask()
        result = task.screenshot()
        assert result == 'ok'
        assert call_log == ['screenshot']

    def test_chained_conditions_fallback_when_no_match(self):
        """Fallback should be called when no chained condition matches."""
        call_log = []

        class MyTask:
            config = SimpleConfig(
                DEVICE_SCREENSHOT_METHOD='nonexistent',
            )

            @Config.when(DEVICE_SCREENSHOT_METHOD='adb')
            @Config.when()
            def screenshot(self):
                call_log.append('fallback')
                return 'fb'

        task = MyTask()
        result = task.screenshot()
        assert result == 'fb'
        assert call_log == ['fallback']

    def test_no_fallback_calls_last_defined_function(self):
        """Without fallback, last defined function should be called and warning logged."""
        call_log = []

        class MyTask:
            config = SimpleConfig(
                DEVICE_SCREENSHOT_METHOD='nonexistent',
            )

            @Config.when(DEVICE_SCREENSHOT_METHOD='adb')
            @Config.when(DEVICE_SCREENSHOT_METHOD='uiautomator2')
            def screenshot(self):
                call_log.append('screenshot')
                return 'ok'

        task = MyTask()
        with logger.mock_capture_writer() as capture:
            result = task.screenshot()
        assert result == 'ok'
        assert call_log == ['screenshot']
        assert capture.backend.any_contains('no condition matched')


class TestConfigWhenInstanceMethod:
    """Tests for @Config.when on instance methods."""

    def setup_method(self):
        Config.REGISTRY.clear()

    def test_preserves_self(self):
        """@Config.when should correctly forward self."""

        class MyTask:
            config = SimpleConfig(
                DEVICE_SCREENSHOT_METHOD='adb',
            )

            def __init__(self):
                self.prefix = 'pre_'

            @Config.when(DEVICE_SCREENSHOT_METHOD='adb')
            def screenshot(self):
                return f'{self.prefix}screenshot'

        task = MyTask()
        assert task.screenshot() == 'pre_screenshot'

    def test_propagates_arguments(self):
        """Arguments should be forwarded to the decorated method."""

        class MyTask:
            config = SimpleConfig(
                DEVICE_SCREENSHOT_METHOD='adb',
            )

            @Config.when(DEVICE_SCREENSHOT_METHOD='adb')
            def add(self, a, b):
                return a + b

        task = MyTask()
        assert task.add(1, 2) == 3
        assert task.add(10, 20) == 30

    def test_multiple_methods_independent(self):
        """Multiple @Config.when-decorated methods on the same class should be independent."""

        class MyTask:
            config = SimpleConfig(
                DEVICE_SCREENSHOT_METHOD='adb',
            )

            @Config.when(DEVICE_SCREENSHOT_METHOD='adb')
            def method_a(self):
                return 'a'

            @Config.when(DEVICE_SCREENSHOT_METHOD='adb')
            def method_b(self):
                return 'b'

        task = MyTask()
        assert task.method_a() == 'a'
        assert task.method_b() == 'b'

    def test_different_instances_same_class(self):
        """Different instances of the same class should work."""

        class MyTask:
            config = SimpleConfig(
                DEVICE_SCREENSHOT_METHOD='adb',
            )

            @Config.when(DEVICE_SCREENSHOT_METHOD='adb')
            def handle(self):
                return 'ok'

        a = MyTask()
        b = MyTask()
        assert a.handle() == 'ok'
        assert b.handle() == 'ok'

    def test_runtime_config_change(self):
        """Changing config at runtime should switch which method fires."""
        call_log = []

        class MyTask:
            config = SimpleConfig(
                DEVICE_SCREENSHOT_METHOD='adb',
            )

            @Config.when(DEVICE_SCREENSHOT_METHOD='uiautomator2')
            @Config.when(DEVICE_SCREENSHOT_METHOD='adb')
            @Config.when()
            def screenshot(self):
                call_log.append('screenshot')
                return 'ok'

        task = MyTask()

        # Default is 'adb', matches second condition
        assert task.screenshot() == 'ok'
        assert call_log == ['screenshot']
        call_log.clear()

        # Change to 'uiautomator2' at runtime
        task.config.DEVICE_SCREENSHOT_METHOD = 'uiautomator2'
        assert task.screenshot() == 'ok'
        assert call_log == ['screenshot']
        call_log.clear()

        # Change to unknown, should use fallback
        task.config.DEVICE_SCREENSHOT_METHOD = 'nonexistent'
        assert task.screenshot() == 'ok'
        assert call_log == ['screenshot']


class TestConfigWhenClassAndStaticMethod:
    """Tests for @Config.when with classmethod and staticmethod."""

    def setup_method(self):
        Config.REGISTRY.clear()

    def test_when_on_classmethod(self):
        """@Config.when should work with classmethods."""

        class MyTask:
            config = SimpleConfig(
                DEVICE_SCREENSHOT_METHOD='adb',
            )

            @classmethod
            @Config.when(DEVICE_SCREENSHOT_METHOD='adb')
            def class_method(cls):
                return f'class_method_on_{cls.__name__}'

        result = MyTask.class_method()
        assert result == 'class_method_on_MyTask'


class TestConfigWhenEdgeCases:
    """Edge cases for Config.when()."""

    def setup_method(self):
        Config.REGISTRY.clear()

    def test_empty_cases_list(self):
        """A dispatcher with no cases and no instance should raise TypeError."""

        def dummy():
            pass

        dispatcher = _ConfigDispatcher(dummy)
        dispatcher.cases.clear()
        with pytest.raises(TypeError, match='must be called as a bound method'):
            dispatcher()

    def test_unbound_call_raises(self):
        """Calling dispatcher without instance should raise TypeError."""

        class MyTask:
            config = SimpleConfig(
                DEVICE_SCREENSHOT_METHOD='adb',
            )

            @Config.when(DEVICE_SCREENSHOT_METHOD='adb')
            def screenshot(self):
                return 'ok'

        # Access the unbound dispatcher directly
        unbound = MyTask.__dict__['screenshot']
        with pytest.raises(TypeError, match='must be called as a bound method'):
            unbound()

    def test_missing_config_raises(self):
        """Instance without config attribute should raise AttributeError."""

        class NoConfigTask:
            @Config.when(DEVICE_SCREENSHOT_METHOD='adb')
            def screenshot(self):
                return 'ok'

        task = NoConfigTask()
        with pytest.raises(AttributeError):
            task.screenshot()

    def test_preserves_function_name(self):
        """The dispatcher should preserve the original function's name."""

        class MyTask:
            config = SimpleConfig(
                DEVICE_SCREENSHOT_METHOD='adb',
            )

            @Config.when(DEVICE_SCREENSHOT_METHOD='adb')
            def my_special_method(self):
                return 'ok'

        task = MyTask()
        bound = task.my_special_method
        # After binding via __get__, __name__ may not be directly accessible
        # Check that the original function name is preserved
        assert hasattr(bound, '__name__')
        assert 'my_special_method' in str(bound)

    def test_multiple_fallbacks_only_last_used(self):
        """If multiple fallbacks are defined, only the last one should be used."""
        call_log = []

        class MyTask:
            config = SimpleConfig(
                DEVICE_SCREENSHOT_METHOD='adb',
            )

            @Config.when()
            @Config.when()
            def screenshot(self):
                call_log.append('fallback')
                return 'last_fallback'

        task = MyTask()
        result = task.screenshot()
        assert result == 'last_fallback'
        assert call_log == ['fallback']


class TestConfigWhenNoMemoryLeak:
    """Test that @Config.when does not leak memory with dynamically defined methods."""

    def setup_method(self):
        Config.REGISTRY.clear()

    def test_single_when_no_leak(self):
        """Repeated factory calls should not grow cases."""

        def make():
            class MyTask:
                config = SimpleConfig(
                    DEVICE_SCREENSHOT_METHOD='adb',
                )

                @Config.when(DEVICE_SCREENSHOT_METHOD='adb')
                def screenshot(self):
                    return 'ok'

            return MyTask()

        make()
        key = list(Config.REGISTRY.keys())[0]
        dispatcher = Config.REGISTRY[key]
        assert len(dispatcher.cases) == 1

        for _ in range(10):
            make()

        assert len(dispatcher.cases) == 1

    def test_chained_when_no_leak(self):
        """Repeated factory calls with chained when should not grow cases."""

        def make():
            class MyTask:
                config = SimpleConfig(
                    DEVICE_SCREENSHOT_METHOD='adb',
                )

                @Config.when(DEVICE_SCREENSHOT_METHOD='uiautomator2')
                @Config.when(DEVICE_SCREENSHOT_METHOD='adb')
                @Config.when()
                def screenshot(self):
                    return 'ok'

            return MyTask()

        make()
        key = list(Config.REGISTRY.keys())[0]
        dispatcher = Config.REGISTRY[key]
        assert len(dispatcher.cases) == 3

        for _ in range(10):
            make()

        assert len(dispatcher.cases) == 3
