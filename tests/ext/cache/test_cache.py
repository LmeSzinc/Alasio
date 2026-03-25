import threading
import time
import pytest
from alasio.ext.cache.cache import cached_property, cached_property_threadsafe


class MockInstance:
    def __init__(self):
        self.count = 0
        self.slow_count = 0

    @cached_property
    def value(self):
        self.count += 1
        return f"value_{self.count}"

    @cached_property_threadsafe
    def slow_value(self):
        time.sleep(0.1)
        self.slow_count += 1
        return f"slow_value_{self.slow_count}"


class MockSlots:
    __slots__ = ("a",)


def test_cached_property():
    obj = MockInstance()
    
    # First access
    assert obj.value == "value_1"
    assert obj.count == 1
    
    # Second access (cached)
    assert obj.value == "value_1"
    assert obj.count == 1
    
    # Check if replaced in __dict__
    assert "value" in obj.__dict__
    assert obj.__dict__["value"] == "value_1"


def test_cache_operation_methods():
    obj = MockInstance()
    
    # has
    assert cached_property.has(obj, "value") is False
    obj.value
    assert cached_property.has(obj, "value") is True
    
    # get
    assert cached_property.get(obj, "value") == "value_1"
    assert cached_property.get(obj, "non_existent", "default") == "default"
    
    # set
    cached_property.set(obj, "value", "manual_value")
    assert obj.value == "manual_value"
    
    # pop
    val = cached_property.pop(obj, "value")
    assert val == "manual_value"
    assert cached_property.has(obj, "value") is False
    
    # recalculate after pop
    assert obj.value == "value_2"
    assert obj.count == 2


def test_warmup():
    obj = MockInstance()
    assert cached_property.has(obj, "value") is False
    
    # Warmup
    success = cached_property.warm(obj, "value")
    assert success is True
    assert cached_property.has(obj, "value") is True
    assert obj.count == 1
    
    # Already warmup
    success = cached_property.warm(obj, "value")
    assert success is True
    assert obj.count == 1


def test_warmup_inheritance():
    class Parent:
        def __init__(self):
            self.parent_count = 0

        @cached_property
        def parent_val(self):
            self.parent_count += 1
            return "parent"

    class Child(Parent):
        def __init__(self):
            super().__init__()
            self.child_count = 0

        @cached_property
        def child_val(self):
            self.child_count += 1
            return "child"

    obj = Child()
    assert cached_property.has(obj, "parent_val") is False
    assert cached_property.has(obj, "child_val") is False

    # Warmup parent property from child instance
    success = cached_property.warm(obj, "parent_val")
    assert success is True
    assert cached_property.has(obj, "parent_val") is True
    assert obj.parent_val == "parent"
    assert obj.parent_count == 1

    # Warmup child property
    success = cached_property.warm(obj, "child_val")
    assert success is True
    assert cached_property.has(obj, "child_val") is True
    assert obj.child_val == "child"
    assert obj.child_count == 1


def test_threadsafe_cached_property_basic():
    obj = MockInstance()
    
    # First access
    assert obj.slow_value == "slow_value_1"
    assert obj.slow_count == 1
    
    # Second access
    assert obj.slow_value == "slow_value_1"
    assert obj.slow_count == 1


def test_threadsafe_cached_property_concurrency():
    obj = MockInstance()
    results = []

    def access_value():
        results.append(obj.slow_value)

    threads = [threading.Thread(target=access_value) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All threads should get the same value
    assert len(results) == 5
    assert all(r == "slow_value_1" for r in results)
    # The function should be called only once
    assert obj.slow_count == 1


def test_slots_error():
    # cached_property should raise TypeError on objects with __slots__ but no __dict__
    class SlotsWithCached:
        __slots__ = ("_val",)
        
        @cached_property
        def val(self):
            return 1
            
    obj = SlotsWithCached()
    with pytest.raises(TypeError, match="No '__dict__' attribute"):
        _ = obj.val


def test_cache_operation_no_dict():
    # An object without __dict__
    obj = 1 
    
    assert cached_property.has(obj, "any") is False
    assert cached_property.get(obj, "any", "def") == "def"
    assert cached_property.pop(obj, "any") is None
    
    with pytest.raises(TypeError, match="No '__dict__' attribute"):
        cached_property.set(obj, "any", 1)
        
    assert cached_property.warm(obj, "any") is False


class Meta(type):
    def __getattr__(self, name):
        if name.startswith("__") or name.startswith("_pytest"):
            return super().__getattr__(name)
        raise RuntimeError(f"Meta.__getattr__ called for {name}")

    def __setattr__(self, name, value):
        if name.startswith("__") or name.startswith("_pytest") or name in ["__abstractmethods__", "_abc_impl"]:
             return super().__setattr__(name, value)
        raise RuntimeError(f"Meta.__setattr__ called for {name}")


class MockBase(metaclass=Meta):
    def __init__(self):
        self.attr_calls = 0

    def __getattr__(self, name):
        if name.startswith("__") or name.startswith("_pytest"):
            raise AttributeError(name)
        self.attr_calls += 1
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if name == "attr_calls" or name.startswith("__") or name.startswith("_pytest"):
            return super().__setattr__(name, value)
        # We don't want to break everything, but let's track unexpected sets
        raise RuntimeError(f"MockBase.__setattr__ called for {name}")


class MockNoSideEffects(MockBase):
    @cached_property
    def cached_val(self):
        return "ok"

    @cached_property_threadsafe
    def cached_val_ts(self):
        return "ok_ts"


def test_no_side_effects():
    obj = MockNoSideEffects()
    
    # Check initial state
    assert obj.attr_calls == 0
    
    # CacheOperation.has should NOT trigger __getattr__
    assert cached_property.has(obj, "cached_val") is False
    assert obj.attr_calls == 0
    
    # Access cached_val
    # This might trigger __getattribute__ but should NOT trigger __getattr__ 
    # since it actually finds the descriptor.
    assert obj.cached_val == "ok"
    assert obj.attr_calls == 0
    
    # Second access (now in __dict__)
    assert obj.cached_val == "ok"
    assert obj.attr_calls == 0
    
    # CacheOperation.get
    assert cached_property.get(obj, "cached_val") == "ok"
    assert obj.attr_calls == 0
    
    # CacheOperation.set
    # This should manipulate __dict__ directly, bypassing __setattr__
    cached_property.set(obj, "manual", 123)
    assert obj.manual == 123
    assert obj.attr_calls == 0
    
    # CacheOperation.pop
    val = cached_property.pop(obj, "manual")
    assert val == 123
    assert obj.attr_calls == 0
    
    # CacheOperation.warm
    success = cached_property.warm(obj, "cached_val_ts")
    assert success is True
    assert obj.cached_val_ts == "ok_ts"
    assert obj.attr_calls == 0
