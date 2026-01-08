import pytest
import threading
from typing import List

from alasio.ext.singleton import Singleton, SingletonNamed


# ==============================================================================
# Test Fixtures and Helper Classes
# ==============================================================================

# --- Define base classes for testing ---

class BaseService(metaclass=Singleton):
    """A standard class using the Singleton metaclass."""

    def __init__(self, value: int = 0):
        self.value = value


class SubService(BaseService):
    """A subclass to test that it has its own separate instance."""
    pass


class NamedService(metaclass=SingletonNamed):
    """A standard class using the SingletonNamed metaclass."""

    def __init__(self, name: str, value: int = 0):
        self.name = name
        self.value = value


class SubNamedService(NamedService):
    """A subclass to test that it has its own separate named instance cache."""
    pass


# --- Pytest fixture for automatic cleanup ---

@pytest.fixture(autouse=True)
def cleanup_singletons():
    """
    This fixture automatically runs after each test function.
    It clears all singleton instances to ensure tests are isolated.
    """
    yield
    # Teardown code: executed after each test
    BaseService.singleton_clear()
    SubService.singleton_clear()
    NamedService.singleton_clear()
    SubNamedService.singleton_clear()


# ==============================================================================
# Tests for the `Singleton` metaclass
# ==============================================================================

class TestSingleton:
    """Tests for the global Singleton pattern."""

    def test_single_instance_creation(self):
        """Verify that multiple calls return the exact same object."""
        instance1 = BaseService()
        instance2 = BaseService()
        assert instance1 is instance2

    def test_init_is_called_only_once(self):
        """
        Verify that the __init__ method is only called for the first
        instantiation, and subsequent arguments are ignored.
        """
        instance1 = BaseService(value=100)
        assert instance1.value == 100

        # This call should return the existing instance; its __init__ won't be run.
        instance2 = BaseService(value=200)
        assert instance2.value == 100, "The value should not have been updated."
        assert instance1 is instance2

    def test_subclasses_have_separate_instances(self):
        """Verify that a subclass has its own unique singleton instance."""
        base_instance = BaseService()
        sub_instance = SubService()

        assert base_instance is not sub_instance
        assert isinstance(base_instance, BaseService)
        assert isinstance(sub_instance, SubService)

    def test_singleton_clear_all(self):
        """Verify that singleton_clear_all() allows a new instance to be created."""
        instance1 = BaseService()
        BaseService.singleton_clear()
        instance2 = BaseService()

        assert instance1 is not instance2, "A new instance should have been created after clearing."

    def test_thread_safety_is_deterministic(self):
        """
        A deterministic test to prove thread-safety.

        This test uses threading.Event to force a specific execution order:
        1. Thread 1 acquires the lock and enters __init__.
        2. Thread 1 signals it's inside, then waits.
        3. Thread 2 starts and blocks on the lock, waiting for Thread 1 to release it.
        4. Thread 1 is allowed to proceed, finishes __init__, and releases the lock.
        5. Thread 2 acquires the lock, but should find the instance already
           created by Thread 1 and return it immediately without calling __init__ again.
        """
        init_call_count = 0
        instances_from_threads = []

        # Events to control the execution flow of threads
        thread1_inside_init = threading.Event()
        main_thread_can_unblock_thread1 = threading.Event()

        class SlowInitService(metaclass=Singleton):
            def __init__(self):
                nonlocal init_call_count
                init_call_count += 1
                # Signal that thread 1 is inside the critical section
                thread1_inside_init.set()
                # Wait for the main thread's signal to continue
                main_thread_can_unblock_thread1.wait()

        def thread_target(results_list: List):
            instance = SlowInitService()
            results_list.append(instance)

        # Thread 1 will get the lock first
        t1 = threading.Thread(target=thread_target, args=(instances_from_threads,))
        t1.start()

        # Wait until thread 1 is confirmed to be inside the __init__ method
        assert thread1_inside_init.wait(timeout=1), "Thread 1 did not enter __init__ in time."

        # Now that thread 1 holds the lock, start thread 2. It will block.
        t2 = threading.Thread(target=thread_target, args=(instances_from_threads,))
        t2.start()

        # Let thread 1 complete its __init__ and release the lock
        main_thread_can_unblock_thread1.set()

        # Wait for both threads to finish
        t1.join(timeout=1)
        t2.join(timeout=1)

        SlowInitService.singleton_clear()  # Cleanup this special class

        assert init_call_count == 1, "The __init__ method was called more than once."
        assert len(instances_from_threads) == 2, "Both threads should have returned an instance."
        assert instances_from_threads[0] is instances_from_threads[1], "Threads received different instances."


# ==============================================================================
# Tests for the `SingletonNamed` metaclass
# ==============================================================================

class TestSingletonNamed:
    """Tests for the named Singleton pattern."""

    def test_same_name_is_singleton(self):
        """Verify that the same name returns the same instance."""
        instance_a1 = NamedService("A")
        instance_a2 = NamedService("A")
        assert instance_a1 is instance_a2

    def test_different_names_are_different_instances(self):
        """Verify that different names return different instances."""
        instance_a = NamedService("A")
        instance_b = NamedService("B")
        assert instance_a is not instance_b

    def test_init_is_called_once_per_name(self):
        """Verify that __init__ is called only on the first request for a given name."""
        instance_a1 = NamedService("A", value=123)
        assert instance_a1.value == 123

        instance_a2 = NamedService("A", value=456)
        assert instance_a2.value == 123, "The value should not have been updated for name 'A'."

        instance_b = NamedService("B", value=789)
        assert instance_b.value == 789, "The new named instance 'B' should have its value set."

    def test_subclasses_have_separate_named_caches(self):
        """Verify that subclasses have their own independent cache of named instances."""
        base_instance_a = NamedService("A")
        sub_instance_a = SubNamedService("A")

        assert base_instance_a is not sub_instance_a

    def test_singleton_remove(self):
        """Verify that a specific named instance can be removed and recreated."""
        instance_a1 = NamedService("A")
        instance_b1 = NamedService("B")

        # Test removal of an existing key
        assert NamedService.singleton_remove("A") is True

        # Test removal of a non-existent key
        assert NamedService.singleton_remove("C") is False

        # Get instance 'A' again, it should be a new object
        instance_a2 = NamedService("A")
        assert instance_a1 is not instance_a2

        # Instance 'B' should not have been affected
        instance_b2 = NamedService("B")
        assert instance_b1 is instance_b2

    def test_singleton_clear_all_for_named(self):
        """Verify that all named instances are removed after a clear."""
        instance_a1 = NamedService("A")
        instance_b1 = NamedService("B")

        NamedService.singleton_clear()

        instance_a2 = NamedService("A")
        instance_b2 = NamedService("B")

        assert instance_a1 is not instance_a2
        assert instance_b1 is not instance_b2

    def test_thread_safety_for_same_name_is_deterministic(self):
        """A deterministic test for thread-safety when creating the same named instance."""
        init_call_count = 0
        instances_from_threads = []
        instance_name = "thread-safe-test"

        # Events to control thread execution
        thread1_inside_init = threading.Event()
        main_thread_can_unblock_thread1 = threading.Event()

        class SlowInitNamedService(metaclass=SingletonNamed):
            def __init__(self, name: str):
                nonlocal init_call_count
                init_call_count += 1
                self.name = name
                thread1_inside_init.set()
                main_thread_can_unblock_thread1.wait()

        def thread_target(results_list: List):
            instance = SlowInitNamedService(instance_name)
            results_list.append(instance)

        t1 = threading.Thread(target=thread_target, args=(instances_from_threads,))
        t1.start()

        assert thread1_inside_init.wait(timeout=1), "Thread 1 did not enter __init__ in time."

        t2 = threading.Thread(target=thread_target, args=(instances_from_threads,))
        t2.start()

        main_thread_can_unblock_thread1.set()
        t1.join(timeout=1)
        t2.join(timeout=1)

        SlowInitNamedService.singleton_clear()  # Cleanup

        assert init_call_count == 1, "The __init__ method was called more than once for the same name."
        assert len(instances_from_threads) == 2, "Both threads should have returned an instance."
        assert instances_from_threads[0] is instances_from_threads[1], "Threads received different instances."
