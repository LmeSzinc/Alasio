import cv2

from alasio.base.image.color import color_similarity, get_color, rgb2luma
from alasio.base.image.imfile import crop, image_channel, image_load, image_shape, image_size
from alasio.base.op import Area, Point, RGB
from alasio.ext import env
from alasio.ext.cache import cached_property
from alasio.logger import logger


class MatchResult:
    def __init__(
            self,
            match: bool,
            area: Area,
            move: "Point | None" = None,
            button: "Area | None" = None,
            error: str = '',
            similarity: float = 0.,
            colordiff: float = 255.,
    ):
        # whether match success
        self.match = match
        # matched area if matched
        # template area if not match
        self.area: Area = area
        # move vector from template area to template on image
        self.move: Point = move if move is not None else Point.zero()
        # click area
        self.button: Area = button if button is not None else area
        # image matching errors, empty string "" for no error
        # see errors below
        self.error = error
        # template match similarity
        # 0 to 1, higher means more similar
        self.similarity = similarity
        # average colordiff
        # 0 to 255, lower means more similar
        self.colordiff = colordiff

    """
    Errors for image matching
    We do pre-checks before template matching to avoid errors on actual opencv calling
    """
    # image is in width=0 or height=0
    ERROR_IMAGE_EMPTY = 'image_empty'
    # image size smaller than template size, which makes template matching impossible
    ERROR_IMAGE_TOO_SMALL = 'image_too_small'
    # image and template don't have same channel
    ERROR_IMAGE_CHANNEL_NOT_MATCH = 'image_channel_not_match'
    # image is not in RGB, which makes color matching impossible
    ERROR_IMAGE_NOT_RGB = 'image_not_rgb'

    def __bool__(self):
        return self.match

    def __hash__(self):
        return hash(id(self))

    def __str__(self):
        return f'MatchResult({self.match}, {self.area})'

    @classmethod
    def false_from_image(cls, image):
        # Create a false result from image
        size = image_size(image)
        return cls(match=False, area=Area.from_size(size))


class Template:
    def __init__(
            self,
            *,
            area: "tuple[int, int, int, int]",
            color: "tuple[int, int, int]",
            lang: str = '',
            frame: int = 1,
            search: "tuple[int, int, int, int] | None" = None,
            button: "tuple[int, int, int, int] | None" = None,
            # A match function that receives image and returns MatchResult
            # function will be patched onto match()
            # default to Template.match_template_luma_color
            match: "Callable[..., MatchResult] | None" = None,
            similarity: "float | None" = None,
            colordiff: "int | None" = None,
            # from mod root to template file
            file: str = '',
    ):
        self.area: Area = Area(area)
        self.color: RGB = RGB(color)

        # with defaults
        self.search: "Area | None" = Area(search) if search is not None else None
        self.button: Area = Area(button) if button is not None else area
        self.lang = lang
        self.frame = frame
        if match is None:
            self.match = self.DEFAULT_MATCH
        else:
            self.match = match
        self.similarity = similarity
        self.colordiff = colordiff

        # will be set at runtime
        # from mod root to template file
        self.file = file

    def __str__(self):
        return f'Template({self.file})'

    __repr__ = __str__

    def __eq__(self, other):
        return str(self) == str(other)

    def __hash__(self):
        return hash(str(self))

    def __bool__(self):
        # return True so safe to do "if BUTTON:"
        return True

    @classmethod
    def from_file(cls, file: str, area: "tuple[int, int, int, int]") -> "Template":
        """
        Create template from a file directly

        Args:
            file: Absolute filepath
            area:
        """
        image = image_load(file)
        color = RGB(get_color(image)).as_uint8()
        template = cls(area=area, color=color, file=file)
        cached_property.set(template, 'image', image)
        return template

    @classmethod
    def construct_filename(cls, path, name, lang='', frame=1, ext='.webp'):
        if lang:
            if frame > 1:
                return f'{path}/{name}.{lang}.{frame}{ext}'
            else:
                return f'{path}/{name}.{lang}{ext}'
        else:
            if frame > 1:
                return f'{path}/{name}.{frame}{ext}'
            else:
                return f'{path}/{name}{ext}'

    def set_file(self, path, name):
        """
        Args:
            path:
            name:
        """
        self.file = self.construct_filename(path, name, lang=self.lang, frame=self.frame)

    def match(self, image, search=None) -> MatchResult:
        """
        Match with priority, template-level > button-level > default
        This function will be patched if match is given
        If not, default to match_template_luma_color()

        Args:
            image: Screenshot
            search (str | int | Point | tuple[int, int] | Area | tuple[int, int, int, int]):
                See get_search()
        """
        return self.DEFAULT_MATCH(image, search=search)

    @cached_property
    def image(self):
        """
        Lazy loaded template image
        """
        if not self.file:
            # this shouldn't happen, "file" is dynamically set at runtime
            raise ValueError(f'Template has empty file: '
                             f'area={self.area}, color={self.color}, lang="{self.lang}", frame={self.frame}')
        file = env.PROJECT_ROOT / self.file
        image = image_load(file)
        return image

    @cached_property
    def image_luma(self):
        """
        Lazy loaded template image in luma
        """
        image = cached_property.get(self, 'image')
        if image is not None:
            # already has image
            image = rgb2luma(image)
            return image

        if not self.file:
            # this shouldn't happen, "file" is dynamically set at runtime
            raise ValueError(f'Template has empty file: '
                             f'area={self.area}, color={self.color}, lang="{self.lang}", frame={self.frame}')
        file = env.PROJECT_ROOT / self.file
        image = image_load(file)
        image = rgb2luma(image)
        return image

    def release(self):
        """
        Release cached resources
        """
        cached_property.pop(self, 'image')
        cached_property.pop(self, 'image_luma')

    def get_similarity(self, similarity=None) -> float:
        """
        Get similarity with priority
        runtime-usage > template-level > button-level > default
        """
        if similarity is not None:
            return similarity
        if self.similarity is not None:
            return self.similarity
        return self.DEFAULT_SIMILARITY

    def _get_colordiff(self, colordiff=None) -> float:
        """
        Get colordiff with priority
        runtime-usage > template-level > button-level > default
        """
        if colordiff is not None:
            return colordiff
        if self.colordiff is not None:
            return self.colordiff
        return self.DEFAULT_COLORDIFF

    def get_search(self, image, search=None) -> Area:
        """
        Get search area with priority
        runtime "search" input > template-level > button-level > default

        Args:
            image:
            search (str | int | Point | tuple[int, int] | Area | tuple[int, int, int, int]):
                - "full" to search the full image
                - An integer like 30
                    to search within area.outset(search)
                - A Point object or a tuple of (x, y)
                    to search within (area_x1 - x, area_y1 - y, area_x2 + x, area_y2 + y)
                - An Area object or a tuple of (x1, y1, x2, y2)
                    to search with the given area.
                    x and y can be negative for negative index
        """
        res = image_size(image)
        if search is None:
            search = self.search
            if search is None:
                # default outset
                return self.area.outset(self.DEFAULT_SEARCH_OUTSET)
            else:
                # template level or button level search
                return self.search.to_positive(res)
        else:
            # runtime search input
            if isinstance(search, tuple):
                length = len(search)
                if length == 2:
                    # search within area outset XY
                    return self.area.outset_xy(search)
                elif length == 4:
                    # given search
                    return Area(search).to_positive(res)
            elif isinstance(search, Point):
                # search within area outset XY
                return self.area.outset_xy(search)
            elif isinstance(search, Area):
                # given search
                return search.to_positive(res)
            elif search == 'full':
                # full image
                return Area.from_size(res)
        # invalid
        raise ValueError(f'Invalid `search` parameter: {search}')

    def _check_image(self, image, match_template=False, match_color=False) -> str:
        """
        Internal method to check if image is safe to match
        """
        width_i, height_i, channel_i = image_shape(image)
        if match_template:
            width_t, height_t, channel_t = image_shape(self.image)
            if channel_t != channel_i:
                logger.error(
                    f'Template match requires template and image have same channel'
                    f'template channel={channel_t} but image channel={channel_i}, file={self.file}')
                return MatchResult.ERROR_IMAGE_CHANNEL_NOT_MATCH
            if width_i <= 0 or height_i <= 0:
                return MatchResult.ERROR_IMAGE_EMPTY
            if width_t > width_i or height_t > height_i:
                # mute error, image may smaller than template when cropping on edge
                return MatchResult.ERROR_IMAGE_TOO_SMALL
        if match_color:
            if channel_i != 3:
                logger.error(
                    f'Template match requires image channel=3, but got image channel={channel_i}, file={self.file}')
                return MatchResult.ERROR_IMAGE_NOT_RGB
            if width_i <= 0 or height_i <= 0:
                return MatchResult.ERROR_IMAGE_EMPTY
        return ''

    def match_template(self, image, similarity=None, search=None) -> MatchResult:
        """
        Match template on image

        Args:
            image: Screenshot
            similarity (float): 0 to 1, higher means more similar
            search (str | int | Point | tuple[int, int] | Area | tuple[int, int, int, int]):
                See get_search()
        """
        similarity = self.get_similarity(similarity)
        search = self.get_search(search)
        image = crop(image, search, copy=False)
        error = self._check_image(image, match_template=True)
        if error:
            return MatchResult(match=False, area=self.area, error=error)

        # match template
        res = cv2.matchTemplate(self.image, image, cv2.TM_CCOEFF_NORMED)
        _, sim, _, point = cv2.minMaxLoc(res)
        if sim < similarity:
            return MatchResult(match=False, area=self.area, similarity=sim)
        move = Point(point) + self.search.upperleft - self.area.upperleft
        area = self.area.move(move)
        button = self.button.move(move)
        return MatchResult(match=True, area=area, move=move, button=button, similarity=sim)

    def match_color(self, image, colordiff=None, search=None) -> MatchResult:
        """
        Match average color on image

        Args:
            image: Screenshot
            colordiff (float): 0 to 255, lower means more similar
            search (str | int | Point | tuple[int, int] | Area | tuple[int, int, int, int]):
                See get_search()
        """
        colordiff = self.get_similarity(colordiff)
        search = self.get_search(search)
        image = crop(image, search, copy=False)
        error = self._check_image(image, match_template=True)
        if error:
            return MatchResult(match=False, area=self.area, error=error)

        color = get_color(image)
        diff = color_similarity(color, self.color)
        if diff > colordiff:
            return MatchResult(match=False, area=self.area, colordiff=diff)

        return MatchResult(match=True, area=self.area, colordiff=diff)

    def match_template_color(self, image, similarity=None, colordiff=None, search=None) -> MatchResult:
        """
        Match template and average color on image

        Args:
            image: Screenshot
            similarity (float): 0 to 1, higher means more similar
            colordiff (float): 0 to 255, lower means more similar
            search (str | int | Point | tuple[int, int] | Area | tuple[int, int, int, int]):
                See get_search()
        """
        similarity = self.get_similarity(similarity)
        colordiff = self.get_similarity(colordiff)
        search = self.get_search(search)
        image = crop(image, search, copy=False)
        error = self._check_image(image, match_template=True)
        if error:
            return MatchResult(match=False, area=self.area, error=error)

        # match template
        res = cv2.matchTemplate(self.image, image, cv2.TM_CCOEFF_NORMED)
        _, sim, _, point = cv2.minMaxLoc(res)
        if sim < similarity:
            return MatchResult(match=False, area=self.area, similarity=sim)
        move = Point(point) + self.search.upperleft - self.area.upperleft
        area = self.area.move(move)
        button = self.button.move(move)

        # calculate average color on matched area
        color = get_color(image, area)
        diff = color_similarity(color, self.color)
        if diff > colordiff:
            return MatchResult(match=False, area=area, move=move, button=button, similarity=sim, colordiff=diff)

        return MatchResult(match=True, area=area, move=move, button=button, similarity=sim, colordiff=diff)

    def match_template_luma(self, image, similarity=None, search=None) -> MatchResult:
        """
        Match template on the luma channel of image

        Args:
            image: Screenshot, or just the luma channel
            similarity (float): 0 to 1, higher means more similar
            search (str | int | Point | tuple[int, int] | Area | tuple[int, int, int, int]):
                See get_search()
        """
        similarity = self.get_similarity(similarity)
        search = self.get_search(search)
        image = crop(image, search, copy=False)
        if image_channel(image) != 1:
            image = rgb2luma(image)
        error = self._check_image(image, match_template=True)
        if error:
            return MatchResult(match=False, area=self.area, error=error)

        # match template
        res = cv2.matchTemplate(self.image_luma, image, cv2.TM_CCOEFF_NORMED)
        _, sim, _, point = cv2.minMaxLoc(res)
        if sim < similarity:
            return MatchResult(match=False, area=self.area, similarity=sim)
        move = Point(point) + self.search.upperleft - self.area.upperleft
        area = self.area.move(move)
        button = self.button.move(move)
        return MatchResult(match=True, area=area, move=move, button=button, similarity=sim)

    def match_template_luma_color(self, image, similarity=None, colordiff=None, search=None) -> MatchResult:
        """
        Match template on the luma channel of image and check average color of image
        This is a faster alternative of match_template(), having the correct match in most cases and being 2.5~3x fast

        Args:
            image: Screenshot
            similarity (float): 0 to 1, higher means more similar
            colordiff (float): 0 to 255, lower means more similar
            search (str | int | Point | tuple[int, int] | Area | tuple[int, int, int, int]):
                See get_search()
        """
        similarity = self.get_similarity(similarity)
        colordiff = self.get_similarity(colordiff)
        search = self.get_search(search)
        image = crop(image, search, copy=False)
        error = self._check_image(image, match_template=True)
        if error:
            return MatchResult(match=False, area=self.area, error=error)

        # match template
        res = cv2.matchTemplate(self.image_luma, image, cv2.TM_CCOEFF_NORMED)
        _, sim, _, point = cv2.minMaxLoc(res)
        if sim < similarity:
            return MatchResult(match=False, area=self.area, similarity=sim)
        move = Point(point) + self.search.upperleft - self.area.upperleft
        area = self.area.move(move)
        button = self.button.move(move)

        # calculate average color on matched area
        color = get_color(image, area)
        diff = color_similarity(color, self.color)
        if diff > colordiff:
            return MatchResult(match=False, area=area, move=move, button=button, similarity=sim, colordiff=diff)

        return MatchResult(match=True, area=area, move=move, button=button, similarity=sim, colordiff=diff)

    """
    Consts
    This is written in the last because DEFAULT_MATCH references class function
    """
    DEFAULT_SIMILARITY = 0.85
    DEFAULT_COLORDIFF = 30
    DEFAULT_SEARCH_OUTSET = 20
    DEFAULT_MATCH = match_template_luma_color
