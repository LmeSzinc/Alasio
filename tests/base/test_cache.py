import gc
import time
import weakref

import pytest

from alasio.base.cache import cached_on_hierarchy, cached_on_screenshot
from alasio.device.base import DeviceBase
from alasio.device.config import DeviceConfig
from alasio.logger import logger


class FakeImage:
    """
    Stand-in of a screenshot or a hierarchy, weak reference is supported
    """
    pass


class FakeDevice(DeviceBase):
    """
    A device with the real cache mechanism and no emulator
    """

    def __init__(self):
        super().__init__(DeviceConfig())

    def screenshot(self):
        self.image = FakeImage()
        self._image_time = time.time()
        self._on_new_screenshot()
        return self.image

    def dump_hierarchy(self):
        self.hierarchy = FakeImage()
        self._on_new_hierarchy()
        return self.hierarchy


class Task:
    """
    A worker object, like a MOD task or handler
    """

    def __init__(self, device):
        self.device = device
        self.compute_count = 0

    @cached_on_screenshot
    def is_in_map(self):
        self.compute_count += 1
        return self.device.image

    @cached_on_hierarchy
    def hierarchy_index(self):
        self.compute_count += 1
        return self.device.hierarchy


class MisuseTask(Task):
    """
    A task taking a screenshot inside the cached function, which is not supported
    """

    @cached_on_screenshot
    def is_in_map(self):
        self.device.screenshot()
        self.compute_count += 1
        return self.device.image


class TestCachedOnScreenshot:
    def test_compute_once_per_screenshot(self):
        device = FakeDevice()
        task = Task(device)

        value = task.is_in_map
        assert task.compute_count == 1
        assert task.is_in_map is value
        assert task.compute_count == 1

    def test_recompute_on_new_screenshot(self):
        device = FakeDevice()
        task = Task(device)

        first = task.is_in_map
        device.screenshot()
        second = task.is_in_map
        assert second is not first
        assert second is device.image
        assert task.compute_count == 2

    def test_value_is_a_plain_attribute(self):
        device = FakeDevice()
        task = Task(device)

        value = task.is_in_map
        assert task.__dict__['is_in_map'] is value
        assert cached_on_screenshot.has(task, 'is_in_map')
        assert cached_on_screenshot.get(task, 'is_in_map') is value

    def test_value_is_dropped_by_the_device(self):
        device = FakeDevice()
        task = Task(device)

        _ = task.is_in_map
        assert 'is_in_map' in task.__dict__
        device.screenshot()
        assert 'is_in_map' not in task.__dict__
        assert not cached_on_screenshot.has(task, 'is_in_map')

    def test_value_is_released_on_screenshot(self):
        device = FakeDevice()
        task = Task(device)
        device.screenshot()

        ref = weakref.ref(task.is_in_map)
        gc.collect()
        assert ref() is not None

        device.screenshot()
        gc.collect()
        assert ref() is None
        # the task itself is still alive
        assert task.device is device

    def test_all_instances_of_the_device_are_dropped(self):
        device = FakeDevice()
        task_a = Task(device)
        task_b = Task(device)

        _ = task_a.is_in_map
        _ = task_b.is_in_map
        device.screenshot()
        assert not cached_on_screenshot.has(task_a, 'is_in_map')
        assert not cached_on_screenshot.has(task_b, 'is_in_map')

    def test_recomputed_value_is_dropped_again(self):
        device = FakeDevice()
        task = Task(device)

        _ = task.is_in_map
        device.screenshot()
        _ = task.is_in_map
        assert cached_on_screenshot.has(task, 'is_in_map')
        device.screenshot()
        assert not cached_on_screenshot.has(task, 'is_in_map')

    def test_instance_is_not_kept_alive_by_the_device(self):
        device = FakeDevice()
        task = Task(device)
        _ = task.is_in_map

        ref = weakref.ref(task)
        del task
        gc.collect()
        assert ref() is None
        # the device still works
        assert device.screenshot() is device.image

    def test_manual_pop_recomputes(self):
        device = FakeDevice()
        task = Task(device)

        value = task.is_in_map
        cached_on_screenshot.pop(task, 'is_in_map')
        assert not cached_on_screenshot.has(task, 'is_in_map')

        assert task.is_in_map is value
        assert task.compute_count == 2

    def test_warm(self):
        device = FakeDevice()
        task = Task(device)

        assert not cached_on_screenshot.has(task, 'is_in_map')
        assert cached_on_screenshot.warm(task, 'is_in_map')
        assert cached_on_screenshot.has(task, 'is_in_map')
        assert task.compute_count == 1

    def test_set_is_managed(self):
        device = FakeDevice()
        task = Task(device)

        cached_on_screenshot.set(task, 'is_in_map', 'manual')
        assert task.is_in_map == 'manual'
        assert cached_on_screenshot.has(task, 'is_in_map')

        device.screenshot()
        assert not cached_on_screenshot.has(task, 'is_in_map')

    def test_assignment_is_not_managed(self):
        device = FakeDevice()
        task = Task(device)

        task.is_in_map = 'manual'
        device.screenshot()
        # not recommended, but the behavior is defined: a direct assignment is
        # not registered, so the device never drops it, use set() instead
        assert task.is_in_map == 'manual'

    def test_exception_is_not_cached(self):
        class FailTask(Task):
            @cached_on_screenshot
            def is_in_map(self):
                self.compute_count += 1
                raise ValueError

        device = FakeDevice()
        task = FailTask(device)

        with pytest.raises(ValueError):
            _ = task.is_in_map
        assert not cached_on_screenshot.has(task, 'is_in_map')

        with pytest.raises(ValueError):
            _ = task.is_in_map
        assert task.compute_count == 2


class TestCachedOnHierarchy:
    def test_compute_once_per_dump(self):
        device = FakeDevice()
        task = Task(device)

        value = task.hierarchy_index
        assert task.compute_count == 1
        assert task.hierarchy_index is value

    def test_recompute_on_new_dump(self):
        device = FakeDevice()
        task = Task(device)

        first = task.hierarchy_index
        device.dump_hierarchy()
        second = task.hierarchy_index
        assert second is not first
        assert second is device.hierarchy
        assert task.compute_count == 2

    def test_events_are_isolated(self):
        device = FakeDevice()
        task = Task(device)

        _ = task.is_in_map
        _ = task.hierarchy_index

        device.screenshot()
        assert not cached_on_screenshot.has(task, 'is_in_map')
        assert cached_on_hierarchy.has(task, 'hierarchy_index')

        device.dump_hierarchy()
        assert not cached_on_hierarchy.has(task, 'hierarchy_index')


class TestMisuse:
    def test_warning_on_screenshot_inside_the_cached_function(self):
        device = FakeDevice()
        task = MisuseTask(device)

        with logger.mock_capture_writer() as capture:
            _ = task.is_in_map

        assert capture.concat() == """\
WARNING | MisuseTask.is_in_map was computed across a new screenshot, do not trigger a new screenshot inside a cached function
"""

    def test_no_warning_on_the_normal_path(self):
        device = FakeDevice()
        task = Task(device)

        with logger.mock_capture_writer() as capture:
            _ = task.is_in_map
            device.screenshot()
            _ = task.is_in_map

        assert capture.concat() == ''


class TestContract:
    @pytest.mark.parametrize('decorator', [cached_on_screenshot, cached_on_hierarchy])
    def test_missing_device(self, decorator):
        class Broken:
            @decorator
            def value(self):
                return 1

        with pytest.raises(AttributeError) as e:
            _ = Broken().value
        assert str(e.value) == f'Broken must have a "device" attribute to use @{decorator.__name__}'

    @pytest.mark.parametrize('decorator', [cached_on_screenshot, cached_on_hierarchy])
    def test_instance_without_dict(self, decorator):
        class Slotted:
            __slots__ = ('device',)

            @decorator
            def value(self):
                return 1

        instance = Slotted()
        instance.device = FakeDevice()
        with pytest.raises(TypeError) as e:
            _ = instance.value
        assert str(e.value) == "No '__dict__' attribute on 'Slotted' instance to cache 'value' property."

    @pytest.mark.parametrize('decorator', [cached_on_screenshot, cached_on_hierarchy])
    def test_instance_without_weak_reference(self, decorator):
        class NoWeakref:
            __slots__ = ('__dict__', 'device')

            @decorator
            def value(self):
                return 1

        instance = NoWeakref()
        instance.device = FakeDevice()
        with pytest.raises(TypeError) as e:
            _ = instance.value
        assert str(e.value) == f'NoWeakref does not support weak references, cannot cache with @{decorator.__name__}'
        # nothing is cached and nothing is registered
        assert 'value' not in instance.__dict__
        assert instance.device._screenshot_caches == {}
        assert instance.device._hierarchy_caches == {}
