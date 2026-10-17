"""
The reduced game script module base of the example mod.

The module base of the game script carries the device, the config and the OCR of
the whole mod, and every task of the mod is built on it. The recognition modules
only use the two image helpers below, so this mod carries only those, with the
implementation of the game script `module/base/base.py`:

- `appear()` matches an asset on the screenshot, by template or by color
- `image_crop()` crops the area of an asset from the screenshot

Left out, because they need the rest of the game script (device methods,
hierarchy dump, OCR, statistics): `ensure_button()` and the hierarchy branch of
`appear()`, `loop()`, the click and wait helpers, `stat` and `emotion`.

`appear(button, offset=True)` reads `config.BUTTON_OFFSET` like the game script
does, which the config of this mod does not carry yet: pass the offset itself,
as the recognition modules do.
"""
from module.config.config import Config
from module.map.button import Button

from alasio.base.base import ModuleBase as AlasioModuleBase
from alasio.base.image.imfile import crop
from alasio.base.timer import Timer
from alasio.device.base import DeviceBase


class ModuleBase(AlasioModuleBase):
    """
    Attributes:
        config (Config): The example mod config
        device (DeviceBase): The device of the module, the scheduler device in
            production and a ScreenshotDevice in the screenshot tests
    """
    config: Config
    device: DeviceBase

    def __init__(self, config, device=None, task=''):
        super().__init__(config=config, device=device, task=task)

        self.interval_timer = {}

    def appear(self, button, offset=0, interval=0, similarity=0.85, threshold=10):
        """
        Args:
            button (Button, Template):
            offset (bool, int):
            interval (int, float): interval between two active events.
            similarity (int, float): 0 to 1.
            threshold (int, float): 0 to 255 if not use offset, smaller means more similar

        Returns:
            bool:
        """
        self.device.stuck_record_add(button)

        if interval:
            if button.name in self.interval_timer:
                if self.interval_timer[button.name].limit != interval:
                    self.interval_timer[button.name] = Timer(interval)
            else:
                self.interval_timer[button.name] = Timer(interval)
            if not self.interval_timer[button.name].reached():
                return False

        if offset:
            if isinstance(offset, bool):
                offset = self.config.BUTTON_OFFSET
            appear = button.match(self.device.image, offset=offset, similarity=similarity)
        else:
            appear = button.appear_on(self.device.image, threshold=threshold)

        if appear and interval:
            self.interval_timer[button.name].reset()

        return appear

    def image_crop(self, button, copy=True):
        """Extract the area from image.

        Args:
            button(Button, tuple): Button instance or area tuple.
            copy:
        """
        if isinstance(button, Button):
            return crop(self.device.image, button.area, copy=copy)
        elif hasattr(button, 'area'):
            return crop(self.device.image, button.area, copy=copy)
        else:
            return crop(self.device.image, button, copy=copy)
