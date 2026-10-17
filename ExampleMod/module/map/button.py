"""
A simple asset of the map module.

A `Button` is an area of the screenshot together with the image of the asset on
it: it matches the image on the screenshot, checks the color of the area, and
carries the offset another button shifts it by.

This is the asset layer of the example mod, on the alasio image helpers. The
asset layer of the game script (`module/base/button.py` of the game script) also
carries a per server area, a gif template and a binary template; the example mod
has one server and no gif asset, so none of that is here.
"""
import cv2
from module.config.const import entry
from module.map.utils import area_offset

from alasio.base.image.color import color_similar, get_color
from alasio.base.image.imfile import crop, image_load
from alasio.base.op import Area
from alasio.ext.path import PathStr


class Button:
    """
    Args:
        area (Area | tuple): Area of the asset on the screenshot, (x1, y1, x2, y2)
        color (tuple): RGB color the area has when the asset is there
        button (Area | tuple): Area to click, `area` if not given
        file (str): Image of the asset, a path relative to the project root of
            the mod, e.g. './assets/cn/map/FLEET_1_CLEAR.png'
        name (str): Name of the asset, the stem of `file` if not given

    Attributes:
        image (np.ndarray): Template of the asset, loaded on the first match
    """

    def __init__(self, area, color=None, button=None, file=None, name=None):
        self.area = Area(area)
        self.color = color
        self._button = Area(button if button is not None else area)
        self._button_offset = None
        self.file = file
        self.name = name or (PathStr.new(file).stem if file else 'BUTTON')
        self.image = None

        # The assets of the game script are declared relative to the project root
        # of the mod, e.g. './assets/cn/map/FLEET_1_CLEAR.png', and the game script
        # runs with the mod root as the working folder. Resolve the file against
        # the project root, so a module does not depend on the working folder of
        # the process.
        if file and not PathStr.new(file).is_abspath():
            self.file = entry.root.joinpath(file)

    def __str__(self):
        return self.name

    __repr__ = __str__

    @property
    def button(self):
        """
        Returns:
            Area: Area to click, moved by the offset loaded from another button
        """
        if self._button_offset is None:
            return self._button
        else:
            return self._button_offset

    def load_offset(self, button):
        """
        Move the area of the button by the offset of another button.

        Some pages shift their assets, e.g. the W15 maps move the fleet bar up,
        and the assets are declared for the common place: the offset is loaded
        here once, then `button` reports the moved area.

        Args:
            button (Button): The button the offset is loaded from
        """
        offset = button.button[0] - button._button[0], button.button[1] - button._button[1]
        self._button_offset = area_offset(self._button, offset)

    def clear_offset(self):
        """
        Forget the offset loaded from another button
        """
        self._button_offset = None

    def ensure_template(self):
        """
        Load the template of the asset, the image file cropped to `area`
        """
        if self.image is None:
            self.image = image_load(self.file, self.area)

    def appear_on(self, image, threshold=10):
        """
        Check the color of the area: the asset is there when the color matches.

        Args:
            image (np.ndarray): Screenshot
            threshold (int): Tolerance of the color

        Returns:
            bool:
        """
        return color_similar(get_color(image, self.area), self.color, threshold=threshold)

    def match(self, image, offset=30, similarity=0.85):
        """
        Match the template of the asset on the screenshot.

        Args:
            image (np.ndarray): Screenshot
            offset (int | tuple): How far the asset may be from `area`: an int
                becomes a (-3, -offset, 3, offset) padding, a tuple is
                (x1, y1, x2, y2) padding
            similarity (float): 0 to 1, the match threshold

        Returns:
            bool:
        """
        self.ensure_template()

        if isinstance(offset, tuple):
            if len(offset) == 2:
                offset = (-offset[0], -offset[1], offset[0], offset[1])
        else:
            offset = (-3, -offset, 3, offset)
        search = Area((self.area[0] + offset[0], self.area[1] + offset[1],
                       self.area[2] + offset[2], self.area[3] + offset[3]))

        res = cv2.matchTemplate(self.image, crop(image, search, copy=False), cv2.TM_CCOEFF_NORMED)
        _, sim, _, point = cv2.minMaxLoc(res)
        self._button_offset = area_offset(self._button, (offset[0] + point[0], offset[1] + point[1]))
        return sim > similarity
