import gc
import weakref

from alasio.device.base import DeviceBase
from alasio.device.config import DeviceConfig


class FakeImage:
    """
    Stand-in of a screenshot or a hierarchy
    """
    pass


class FakeTask:
    """
    An object holding cached values on a device
    """

    def __init__(self, device):
        self.device = device


class TestEpochs:
    def test_starts_at_zero(self):
        device = DeviceBase(DeviceConfig())
        assert device._screenshot_epoch == 0
        assert device._hierarchy_epoch == 0

    def test_new_screenshot_advances_the_screenshot_epoch(self):
        device = DeviceBase(DeviceConfig())
        device._on_new_screenshot()
        device._on_new_screenshot()
        assert device._screenshot_epoch == 2
        assert device._hierarchy_epoch == 0

    def test_new_hierarchy_advances_the_hierarchy_epoch(self):
        device = DeviceBase(DeviceConfig())
        device._on_new_hierarchy()
        assert device._hierarchy_epoch == 1
        assert device._screenshot_epoch == 0


class TestSetImage:
    def test_sets_image_and_time(self):
        device = DeviceBase(DeviceConfig())
        image = FakeImage()

        device.set_image(image)
        assert device.image is image
        assert device._image_time > 0
        assert device._screenshot_epoch == 1

    def test_drops_the_cached_values(self):
        device = DeviceBase(DeviceConfig())
        task = FakeTask(device)
        task.__dict__['value'] = FakeImage()
        device._screenshot_caches[weakref.ref(task)] = {'value'}

        device.set_image(FakeImage())
        assert 'value' not in task.__dict__
        assert device._screenshot_caches == {}


class TestSetHierarchy:
    def test_sets_hierarchy(self):
        device = DeviceBase(DeviceConfig())
        hierarchy = FakeImage()

        device.set_hierarchy(hierarchy)
        assert device.hierarchy is hierarchy
        assert device._hierarchy_epoch == 1

    def test_drops_the_cached_values(self):
        device = DeviceBase(DeviceConfig())
        task = FakeTask(device)
        task.__dict__['value'] = FakeImage()
        device._hierarchy_caches[weakref.ref(task)] = {'value'}

        device.set_hierarchy(FakeImage())
        assert 'value' not in task.__dict__
        assert device._hierarchy_caches == {}


class TestOnIdle:
    def test_resets_the_image_and_drops_the_cached_values(self):
        device = DeviceBase(DeviceConfig())
        task = FakeTask(device)
        task.__dict__['value'] = FakeImage()
        device._screenshot_caches[weakref.ref(task)] = {'value'}
        device.set_image(FakeImage())

        device.on_idle()
        assert device.image is None
        assert device._image_time == 0
        assert 'value' not in task.__dict__
        assert device._screenshot_epoch == 2


class TestRegistry:
    def test_keys_are_weak_references(self):
        device = DeviceBase(DeviceConfig())
        task = FakeTask(device)
        device._screenshot_caches[weakref.ref(task)] = {'value'}

        (ref,) = device._screenshot_caches
        assert isinstance(ref, weakref.ref)
        assert ref() is task

    def test_registry_is_cleared_on_every_event(self):
        device = DeviceBase(DeviceConfig())
        task = FakeTask(device)
        task.__dict__['value'] = FakeImage()
        device._screenshot_caches[weakref.ref(task)] = {'value'}

        device._on_new_screenshot()
        assert device._screenshot_caches == {}
        assert 'value' not in task.__dict__

    def test_dead_instance_is_skipped(self):
        device = DeviceBase(DeviceConfig())
        task = FakeTask(device)
        task.__dict__['value'] = FakeImage()
        device._screenshot_caches[weakref.ref(task)] = {'value'}

        ref = weakref.ref(task)
        del task
        gc.collect()
        assert ref() is None

        device._on_new_screenshot()
        assert device._screenshot_caches == {}

    def test_missing_attrname_is_skipped(self):
        device = DeviceBase(DeviceConfig())
        task = FakeTask(device)
        device._screenshot_caches[weakref.ref(task)] = {'value'}

        device._on_new_screenshot()
        assert device._screenshot_caches == {}
