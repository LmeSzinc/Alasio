import pytest

from alasio.base.base import LoopState, ModuleBase
from alasio.base.timer import Timer
from alasio.device.base import DeviceBase
from alasio.device.config import DeviceConfig
from alasio.testing.patch_time import PatchTime

pytestmark = [
    pytest.mark.parallel,
]


class FakeImage:
    """
    Stand-in of a screenshot or a hierarchy
    """
    pass


class FakeDevice(DeviceBase):
    """
    A device recording the captures it is asked for
    """

    def __init__(self):
        super().__init__(DeviceConfig())
        self.screenshots = 0
        self.hierarchies = 0

    def screenshot(self):
        self.screenshots += 1
        self.image = FakeImage()
        self._on_new_screenshot()
        return self.image

    def dump_hierarchy(self):
        self.hierarchies += 1
        self.hierarchy = FakeImage()
        self._on_new_hierarchy()
        return self.hierarchy


class StubModule(ModuleBase):
    """
    A module of the loop tests, it carries the device only
    """
    device: DeviceBase

    def __init__(self, device):
        self.device = device


class TestLoopState:
    """Tests for the LoopState payload yielded by loop()"""

    def test_defaults(self):
        """All the fields default to an empty state"""
        state = LoopState()
        assert state.image is None
        assert state.hierarchy is None
        assert state.timeout is None
        assert state.count == 0

    def test_holds_the_device_state(self):
        """The state carries the screenshot and hierarchy of the device"""
        device = FakeDevice()
        device.screenshot()
        device.dump_hierarchy()
        module = StubModule(device)

        state = next(module.loop())

        assert isinstance(state, LoopState)
        assert state.image is device.image
        assert state.hierarchy is device.hierarchy

    def test_empty_without_capture(self):
        """The state carries None when the device has no screenshot or hierarchy"""
        device = FakeDevice()
        module = StubModule(device)

        state = next(module.loop())

        assert state.image is None
        assert state.hierarchy is None
        assert state.count == 0


class TestLoopScreenshot:
    """Tests for the screenshot option of loop()"""

    def test_skip_first_reuses_the_previous_screenshot(self):
        """The first iteration does not take a screenshot when skip_first is set"""
        device = FakeDevice()
        device.screenshot()
        module = StubModule(device)

        state = next(module.loop())

        assert device.screenshots == 1
        assert state.image is device.image
        assert state.count == 0

    def test_screenshot_on_each_later_iteration(self):
        """Every iteration but the first takes a screenshot"""
        device = FakeDevice()
        module = StubModule(device)
        it = module.loop()

        first = next(it)
        assert device.screenshots == 0
        assert first.count == 0

        second = next(it)
        assert device.screenshots == 1
        assert second.image is device.image
        assert second.count == 1

        third = next(it)
        assert device.screenshots == 2
        assert third.count == 2

    def test_no_skip_first_screenshots_before_the_first_iteration(self):
        """skip_first=False takes a screenshot before the first iteration"""
        device = FakeDevice()
        module = StubModule(device)

        state = next(module.loop(skip_first=False))

        assert device.screenshots == 1
        assert state.image is device.image
        assert state.count == 1

    def test_screenshot_disabled(self):
        """screenshot=False never takes a screenshot"""
        device = FakeDevice()
        device.screenshot()
        image = device.image
        module = StubModule(device)
        it = module.loop(screenshot=False, skip_first=False)

        first = next(it)
        second = next(it)

        assert device.screenshots == 1
        assert first.image is image
        assert second.image is image
        assert first.count == 0
        assert second.count == 0


class TestLoopHierarchy:
    """Tests for the hierarchy option of loop()"""

    def test_hierarchy_disabled_by_default(self):
        """loop() does not dump the hierarchy unless asked for"""
        device = FakeDevice()
        module = StubModule(device)
        it = module.loop(skip_first=False)

        state = next(it)

        assert device.hierarchies == 0
        assert state.hierarchy is None

    def test_hierarchy_on_each_iteration(self):
        """hierarchy=True dumps the hierarchy on each iteration"""
        device = FakeDevice()
        module = StubModule(device)
        it = module.loop(screenshot=False, hierarchy=True, skip_first=False)

        first = next(it)
        assert device.hierarchies == 1
        assert first.hierarchy is device.hierarchy
        assert first.count == 1

        second = next(it)
        assert device.hierarchies == 2
        assert second.count == 2

    def test_hierarchy_skips_the_first_iteration(self):
        """The first iteration reuses the hierarchy dumped by the caller"""
        device = FakeDevice()
        device.dump_hierarchy()
        hierarchy = device.hierarchy
        module = StubModule(device)

        state = next(module.loop(screenshot=False, hierarchy=True))

        assert device.hierarchies == 1
        assert state.hierarchy is hierarchy
        assert state.count == 0

    def test_screenshot_and_hierarchy(self):
        """A screenshot and a hierarchy dump of one iteration are one capture"""
        device = FakeDevice()
        module = StubModule(device)
        it = module.loop(screenshot=True, hierarchy=True, skip_first=False)

        first = next(it)
        assert (device.screenshots, device.hierarchies) == (1, 1)
        assert first.count == 1

        second = next(it)
        assert (device.screenshots, device.hierarchies) == (2, 2)
        assert second.count == 2

    def test_no_capture(self):
        """Both options disabled yields the cached state of the device"""
        device = FakeDevice()
        device.screenshot()
        device.dump_hierarchy()
        module = StubModule(device)
        it = module.loop(screenshot=False, hierarchy=False, skip_first=False)

        state = next(it)

        assert (device.screenshots, device.hierarchies) == (1, 1)
        assert state.image is device.image
        assert state.hierarchy is device.hierarchy
        assert state.count == 0


class TestLoopTimeout:
    """Tests for the timeout option of loop()"""

    def test_no_timeout(self):
        """loop() has no timeout by default"""
        device = FakeDevice()
        module = StubModule(device)

        assert next(module.loop()).timeout is None

    def test_timeout_in_seconds(self):
        """The seconds of timeout become a Timer"""
        device = FakeDevice()
        module = StubModule(device)

        state = next(module.loop(timeout=2))

        assert isinstance(state.timeout, Timer)
        assert state.timeout.limit == 2
        assert state.timeout.count == 4

    def test_timeout_in_seconds_and_count(self):
        """A (second, count) timeout keeps both of them"""
        device = FakeDevice()
        module = StubModule(device)

        state = next(module.loop(timeout=(2, 3)))

        assert isinstance(state.timeout, Timer)
        assert state.timeout.limit == 2
        assert state.timeout.count == 3

    def test_timeout_as_timer(self):
        """A Timer timeout is started and yielded as the same object"""
        device = FakeDevice()
        module = StubModule(device)
        timer = Timer(2, count=3)

        state = next(module.loop(timeout=timer))

        assert state.timeout is timer
        assert timer.started() is True

    def test_timeout_stops_the_loop(self):
        """The loop returns before the screenshot of an iteration past the timeout"""
        device = FakeDevice()
        module = StubModule(device)

        with PatchTime(1) as patch:
            it = module.loop(timeout=Timer(10))
            next(it)
            patch.shift(11)

            with pytest.raises(StopIteration):
                next(it)

        assert device.screenshots == 0

    def test_yields_the_running_timer(self):
        """The yielded timer reports the elapsed time of the loop"""
        device = FakeDevice()
        module = StubModule(device)

        with PatchTime(1) as patch:
            it = module.loop(timeout=Timer(10))

            first = next(it)
            assert first.timeout.current_time() == 0

            patch.shift(5)
            second = next(it)
            assert second.timeout.current_time() == 5


class TestLoopShortcuts:
    """Tests for the shortcuts of loop()"""

    def test_loop_screenshot(self):
        """loop_screenshot() is loop() with the screenshot enabled"""
        device = FakeDevice()
        module = StubModule(device)
        it = module.loop_screenshot(skip_first=False)

        state = next(it)

        assert (device.screenshots, device.hierarchies) == (1, 0)
        assert state.image is device.image
        assert state.hierarchy is None
        assert state.count == 1

    def test_loop_hierarchy(self):
        """loop_hierarchy() dumps the hierarchy and takes no screenshot"""
        device = FakeDevice()
        module = StubModule(device)
        it = module.loop_hierarchy(skip_first=False)

        state = next(it)

        assert (device.screenshots, device.hierarchies) == (0, 1)
        assert state.image is None
        assert state.hierarchy is device.hierarchy
        assert state.count == 1

    def test_loop_screenshot_hierarchy(self):
        """loop_screenshot_hierarchy() takes a screenshot and dumps the hierarchy"""
        device = FakeDevice()
        module = StubModule(device)
        it = module.loop_screenshot_hierarchy(skip_first=False)

        state = next(it)

        assert (device.screenshots, device.hierarchies) == (1, 1)
        assert state.image is device.image
        assert state.hierarchy is device.hierarchy
        assert state.count == 1

    def test_shortcuts_skip_the_first_iteration(self):
        """skip_first is passed through to loop() by every shortcut"""
        for name in ('loop_screenshot', 'loop_hierarchy', 'loop_screenshot_hierarchy'):
            device = FakeDevice()
            module = StubModule(device)

            state = next(getattr(module, name)())

            assert (device.screenshots, device.hierarchies) == (0, 0), name
            assert state.count == 0, name

    def test_shortcuts_pass_the_timeout(self):
        """timeout is passed through to loop() by every shortcut"""
        for name in ('loop_screenshot', 'loop_hierarchy', 'loop_screenshot_hierarchy'):
            device = FakeDevice()
            module = StubModule(device)
            timer = Timer(2, count=3)

            state = next(getattr(module, name)(timeout=timer))

            assert state.timeout is timer, name
            assert timer.started() is True, name
