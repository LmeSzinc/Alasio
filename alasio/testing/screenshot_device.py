"""
The offline device of a screenshot test.

A game script module reads the game from `device.image`, so the test of a module
runs it on a device holding the screenshot of the test instead of a connected
device: the device never connects anywhere, it never takes a screenshot, and it
never clicks anything, it records the actions of the module under test so that
the test can assert on them.

The device is independent of pytest, a test only needs a screenshot to build it:

    device = ScreenshotDevice(image, file='map/fleet_bar/not_expanded.webp')
    main = FleetPreparation(config, device=device)

The pytest plugin of the screenshot tests, which binds the screenshot of a test
with the `image` decorator and builds the modules a test asks for, is
alasio.testing.screenshot.
"""
from alasio.device.base import DeviceBase
from alasio.device.config import DeviceConfig


class ScreenshotTestError(Exception):
    """
    Raised if a screenshot test is not set up as it should be
    """
    pass


class ScreenshotNotProvided(ScreenshotTestError):
    """
    Raised if the module under test takes a screenshot the test did not provide
    """
    pass


class ScreenshotDevice(DeviceBase):
    """
    A device holding the screenshot of a test, that never connects anywhere.

    Attributes:
        file (str): Path of the screenshot of the test
        clicks (list): Buttons clicked by the module under test
        drags (list): (p1, p2) of the drags of the module under test
        sleeps (list): Seconds the module under test waited for
    """

    def __init__(self, image, file=None):
        """
        Args:
            image (np.ndarray): The screenshot of the test
            file (str): Path of the screenshot, for error messages
        """
        super().__init__(DeviceConfig())
        self.file = file
        self.clicks = []
        self.drags = []
        self.sleeps = []
        # Set the image like the dev tools do, so the values cached on a
        # screenshot are computed on the screenshot of the test
        self.set_image(image)

    def screenshot(self):
        """
        Raises:
            ScreenshotNotProvided: Always, a test provides exactly one screenshot
        """
        raise ScreenshotNotProvided(
            f'"{self.file}" is the only screenshot of this test, but the module under test '
            f'asked for another screenshot. Add a screenshot of that page to the test, or '
            f'reuse the screenshot of the first one (skip_first_screenshot).'
        )

    def click(self, button, *args, **kwargs):
        """
        Record a click, do nothing: a screenshot test does not click
        """
        self.clicks.append(button)

    def stuck_record_add(self, button):
        """
        Do nothing: stuck detection belongs to a connected device, a screenshot
        is never stuck
        """
        pass

    def drag(self, p1, p2, *args, **kwargs):
        """
        Record a drag, do nothing
        """
        self.drags.append((p1, p2))

    swipe = drag

    def sleep(self, second):
        """
        Record a wait, do nothing
        """
        self.sleeps.append(second)
