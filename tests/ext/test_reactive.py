
import gc
from unittest.mock import MagicMock

import pytest

from alasio.ext.reactive import ReactiveCallback, reactive, reactive_source
from alasio.ext.singleton import Singleton


# ---- Test Helper Classes (Now as Singletons) ----

class Calculator(ReactiveCallback, metaclass=Singleton):
    """
    A helper class for testing basic reactive functionalities.
    As a singleton, there will only ever be one instance of Calculator.
    Tests must ensure its state is reset.
    """

    def __init__(self, a=1, b=2):
        # NOTE: __init__ is only called when the singleton is first created.
        self._a = a
        self._b = b
        self.callback_log = []
        # Clear any potential caches from a previous (now-cleared) instance
        self._clear_reactive_caches()

    def _clear_reactive_caches(self):
        # Helper to manually clear caches if needed, though singleton destruction is cleaner.
        for name in ['a', 'b', 'sum', 'product', 'complex_expr']:
            cache_attr = f'_reactive_cache_{name}'
            if hasattr(self, cache_attr):
                delattr(self, cache_attr)

    @reactive_source
    def a(self):
        return self._a

    @reactive_source
    def b(self):
        return self._b

    @reactive
    def sum(self):
        return self.a + self.b

    @reactive
    def product(self):
        return self.a * self.b

    @reactive
    def complex_expr(self):
        return self.sum * 10 + self.product

    def reactive_callback(self, name, old, new):
        self.callback_log.append((name, old, new))


class DeepDataProcessor(ReactiveCallback, metaclass=Singleton):
    """
    Helper class for testing nested data, now implemented as a singleton.
    """

    def __init__(self):
        self._data = {
            'user': {'profile': {'name': 'Alice', 'email': 'alice@example.com'}},
            'version': 1
        }
        self.greeting_compute_mock = MagicMock()

    @reactive_source
    def raw_data(self):
        return self._data

    @reactive
    def username(self):
        return self.raw_data['user']['profile']['name']

    @reactive
    def greeting_message(self):
        self.greeting_compute_mock()
        return f"Welcome, {self.username}!"


# ---- Pytest Fixtures ----

@pytest.fixture(autouse=True)
def cleanup_singletons():
    """
    This fixture automatically runs for every test. It cleans up singleton
    instances *after* the test has finished, ensuring each test starts with
    a clean slate. This is crucial for test isolation.
    """
    # Let the test run
    yield
    # After the test, clear all known singleton instances
    Calculator.singleton_clear_all()
    DeepDataProcessor.singleton_clear_all()
    # Note: Locally defined singletons inside tests must be cleaned up manually.


@pytest.fixture
def calc():
    """
    Provides the Calculator singleton. Thanks to the `cleanup_singletons`
    fixture, this will be a fresh instance for each test.
    """
    return Calculator(a=3, b=4)


@pytest.fixture
def processor():
    """Provides the DeepDataProcessor singleton, guaranteed to be fresh."""
    return DeepDataProcessor()


# ---- Test Cases ----

def test_initial_computation(calc):
    """Tests if initial values are computed correctly on first access."""
    assert calc.a == 3
    assert calc.b == 4
    assert calc.sum == 7
    assert calc.product == 12
    assert calc.complex_expr == (7 * 10 + 12)


def test_dependency_propagation(calc):
    """Tests that changes to a source propagate correctly through the dependency graph."""
    assert calc.complex_expr == 82
    calc.a = 5
    assert calc.a == 5
    assert calc.b == 4
    assert calc.sum == 9
    assert calc.complex_expr == 110


def test_cross_object_reactivity_with_singletons():
    """
    Tests that a reactive property in one singleton can depend on a property
    in another singleton, and that changes propagate correctly.
    """

    # 1. Setup: Define two singleton classes.
    class SourceProvider(ReactiveCallback, metaclass=Singleton):
        def __init__(self):
            self._value = 10

        @reactive_source
        def value(self):
            return self._value

    class DependentConsumer(ReactiveCallback, metaclass=Singleton):
        def __init__(self):
            # Because SourceProvider is a singleton, this will always get
            # the one and only instance.
            self.provider = SourceProvider()
            self.compute_count = 0

        @reactive
        def derived_value(self):
            self.compute_count += 1
            return self.provider.value * 10

    try:
        # 2. Initial State: Instantiate singletons and check values.
        source = SourceProvider()
        consumer = DependentConsumer()
        assert source.value == 10
        assert consumer.derived_value == 100
        assert consumer.compute_count == 1

        # 3. Change Propagation: Modify the source singleton.
        source.value = 20

        # The consumer singleton should update automatically.
        assert consumer.derived_value == 200
        assert consumer.compute_count == 2
    finally:
        # Manually clean up locally defined singletons.
        SourceProvider.singleton_clear_all()
        DependentConsumer.singleton_clear_all()


def test_deep_dictionary_dependency(processor):
    """
    Tests dependency tracking on nested data, ensuring updates only happen
    when the relevant slice of data's value changes.
    """
    assert processor.username == 'Alice'
    assert processor.greeting_message == 'Welcome, Alice!'
    processor.greeting_compute_mock.assert_called_once()

    # Irrelevant change
    new_data_irrelevant = processor.raw_data.copy()
    new_data_irrelevant['version'] = 2
    processor.raw_data = new_data_irrelevant
    assert processor.username == 'Alice'
    assert processor.greeting_message == 'Welcome, Alice!'
    processor.greeting_compute_mock.assert_called_once()  # No new call

    # Relevant change
    new_data_relevant = processor.raw_data.copy()
    new_data_relevant['user']['profile']['name'] = 'Bob'
    DeepDataProcessor.raw_data.mutate(processor)
    assert processor.username == 'Bob'
    assert processor.greeting_message == 'Welcome, Bob!'
    assert processor.greeting_compute_mock.call_count == 2


def test_weakref_observer_cleanup_with_singleton_source():
    """
    Tests that a non-singleton observer is correctly garbage collected even
    when it observes a long-lived singleton.
    """

    # The source is a singleton, representing a long-lived state object.
    class Source(metaclass=Singleton):
        @reactive_source
        def value(self): return 0

    # The observer is a regular class, representing an ephemeral UI component, for example.
    class EphemeralObserver:
        def __init__(self, source): self.source = source

        @reactive
        def observed_value(self): return self.source.value

    try:
        source_obj = Source()
        observer_obj = EphemeralObserver(source_obj)
        assert observer_obj.observed_value == 0

        source_value_descriptor = Source.value
        assert len(source_value_descriptor.observers) == 1

        # Delete the only reference to the ephemeral observer.
        del observer_obj
        gc.collect()

        # The observer should be gone from the singleton's observer list.
        assert len(source_value_descriptor.observers) == 0
    finally:
        # Clean up the singleton used in this test.
        Source.singleton_clear_all()