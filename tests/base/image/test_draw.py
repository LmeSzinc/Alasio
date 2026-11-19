import numpy as np
import pytest

from alasio.base.image import draw
from alasio.base.image.draw import get_bbox, get_bbox_reversed, image_paint, image_paste, resize, resize_maxpx
from alasio.base.image.imfile import ImageNotSupported

pytestmark = [
    pytest.mark.parallel,
]

CHANNELS = [
    pytest.param(1, id='grayscale'),
    pytest.param(3, id='rgb'),
    pytest.param(4, id='rgba'),
]

# A single value color 200, adapted to the channels of the image
SCALAR_COLORS = [
    pytest.param(1, 200, id='grayscale'),
    pytest.param(2, (200, 200), id='grayscale_alpha'),
    pytest.param(3, (200, 200, 200), id='rgb'),
    pytest.param(4, (200, 200, 200, 255), id='rgba'),
]


def make_image(height, width, channel, color=0):
    """
    Create a solid uint8 test image.

    Args:
        height (int): Image height
        width (int): Image width
        channel (int): 1 for grayscale, otherwise the channel count
        color (int | tuple): Fill color, a scalar or a tuple of channel values

    Returns:
        np.ndarray: Solid uint8 image
    """
    shape = (height, width) if channel == 1 else (height, width, channel)
    return np.full(shape, color, dtype=np.uint8)


def fill_area(image, area, color):
    """
    Fill a rectangle of an image.

    Args:
        image (np.ndarray): Image to fill
        area (tuple): Rectangle, (x1, y1, x2, y2)
        color (int | tuple): Fill color
    """
    x1, y1, x2, y2 = area
    image[y1:y2, x1:x2] = color


def white(channel):
    """
    Get the white color of a channel count.

    Args:
        channel (int): 1 for grayscale, otherwise the channel count

    Returns:
        int | tuple: 255 for grayscale, a tuple of 255 otherwise
    """
    return 255 if channel == 1 else (255,) * channel


def black(channel):
    """
    Get the black color of a channel count.

    Args:
        channel (int): 1 for grayscale, otherwise the channel count

    Returns:
        int | tuple: 0 for grayscale, a tuple of 0 otherwise
    """
    return 0 if channel == 1 else (0,) * channel


def gray(value, channel):
    """
    Get a gray color of a channel count, so color conversion keeps the value.

    Args:
        value (int): Gray value
        channel (int): 1 for grayscale, otherwise the channel count

    Returns:
        int | tuple: The gray value for grayscale, a tuple of it otherwise
    """
    return value if channel == 1 else (value,) * channel


def paint_color(channel):
    """
    Get a color with a different value per channel, to detect channel swaps.

    Args:
        channel (int): 1 for grayscale, otherwise the channel count

    Returns:
        int | tuple: Color matching the channel count
    """
    if channel == 1:
        return 128
    else:
        return tuple(range(10, 10 + channel))


def make_block_image(height, width, block=2):
    """
    Create an image where every block x block square has a single value.

    Nearest-neighbor downscaling by `block` samples one pixel of every square,
    no matter which pixel the opencv backend picks, so the result is stable
    across platforms while still differing from bilinear averaging.

    Args:
        height (int): Image height
        width (int): Image width
        block (int): Side length of the constant squares

    Returns:
        np.ndarray: uint8 image
    """
    cells_y = (height + block - 1) // block
    cells_x = (width + block - 1) // block
    cells = (np.arange(cells_y * cells_x) % 256).astype(np.uint8)
    cells = cells.reshape(cells_y, cells_x)
    image = np.repeat(np.repeat(cells, block, axis=0), block, axis=1)
    return image[:height, :width]


class TestResize:
    """Tests for resize()"""

    @pytest.mark.parametrize("channel", CHANNELS)
    def test_resize_upscale_nearest(self, channel):
        """Upscaling by 2 should repeat every pixel as a 2x2 block"""
        image = make_image(2, 2, channel)
        for value, (y, x) in enumerate(((0, 0), (0, 1), (1, 0), (1, 1)), start=1):
            image[y, x] = value if channel == 1 else (value,) * channel
        result = resize(image, (4, 4))
        expected = np.repeat(np.repeat(image, 2, axis=0), 2, axis=1)
        assert result.shape == expected.shape
        assert result.dtype == np.uint8
        np.testing.assert_array_equal(result, expected)

    def test_resize_downscale_nearest(self):
        """Downscaling should pick the nearest pixel instead of averaging"""
        image = make_block_image(4, 4)
        result = resize(image, (2, 2))
        np.testing.assert_array_equal(result, image[::2, ::2])

    def test_resize_size_is_width_height(self):
        """The size argument is (width, height), like PIL.Image.resize()"""
        image = make_image(2, 4, 1)
        result = resize(image, (8, 4))
        assert result.shape == (4, 8)


class TestResizeMaxpx:
    """Tests for resize_maxpx()"""

    @pytest.mark.parametrize("channel", CHANNELS)
    def test_no_resize_returns_same_image(self, channel):
        """An image within the limit is returned unchanged, not copied"""
        image = make_image(10, 20, channel)
        assert resize_maxpx(image, 20) is image

    @pytest.mark.parametrize("shape, expected_shape", [
        ((10, 20), (5, 10)),
        ((20, 10), (10, 5)),
        ((20, 20), (10, 10)),
    ], ids=['width', 'height', 'square'])
    def test_resize_longer_edge(self, shape, expected_shape):
        """The longer edge should become maxpx and the shorter edge scaled"""
        image = make_block_image(*shape)
        result = resize_maxpx(image, 10)
        assert result.shape == expected_shape
        assert result.dtype == image.dtype
        # Every backend's nearest neighbor samples inside the same 2x2 block
        np.testing.assert_array_equal(result, image[::2, ::2])

    @pytest.mark.parametrize("shape, expected_shape", [
        ((1, 1000), (1, 100)),
        ((1000, 1), (100, 1)),
    ], ids=['wide', 'tall'])
    def test_extreme_aspect_ratio_keeps_one_pixel(self, shape, expected_shape):
        """Rounding the short edge must not produce a zero-sized image"""
        image = make_image(shape[0], shape[1], 1)
        result = resize_maxpx(image, 100)
        assert result.shape == expected_shape


class TestImagePaste:
    """Tests for image_paste()"""

    @pytest.mark.parametrize("origin", [(0, 0), (5, 2), (8, 7)], ids=['top_left', 'inside', 'bottom_right'])
    @pytest.mark.parametrize("channel", CHANNELS)
    def test_paste(self, channel, origin):
        """Pasting should overwrite the target area and keep the rest intact"""
        x, y = origin
        image = make_image(3, 4, channel, color=200)
        background = make_image(10, 12, channel, color=7)
        result = image_paste(image, background, origin)
        expected = make_image(10, 12, channel, color=7)
        expected[y:y + 3, x:x + 4] = image
        assert result is None
        np.testing.assert_array_equal(background, expected)
        # The source image is not modified
        np.testing.assert_array_equal(image, make_image(3, 4, channel, color=200))

    @pytest.mark.parametrize("channel", CHANNELS)
    def test_paste_readonly_image(self, channel):
        """A read-only source image should be pasted without being modified"""
        image = make_image(3, 4, channel, color=200)
        image.setflags(write=False)
        background = make_image(10, 12, channel, color=7)
        image_paste(image, background, (5, 2))
        expected = make_image(10, 12, channel, color=7)
        expected[2:5, 5:9] = 200
        np.testing.assert_array_equal(background, expected)
        assert not image.flags.writeable
        np.testing.assert_array_equal(image, make_image(3, 4, channel, color=200))


class TestImagePaint:
    """Tests for image_paint()"""

    @pytest.mark.parametrize("channel", CHANNELS)
    def test_paint_writable_in_place(self, channel):
        """Painting a writable image should update it in place and return it"""
        image = make_image(10, 10, channel, color=1)
        color = paint_color(channel)
        result = image_paint(image, (2, 3, 6, 8), color)
        expected = make_image(10, 10, channel, color=1)
        fill_area(expected, (2, 3, 6, 8), color)
        assert result is image
        np.testing.assert_array_equal(image, expected)

    @pytest.mark.parametrize("channel", CHANNELS)
    def test_paint_readonly_returns_copy(self, channel):
        """Painting a read-only image should return a painted copy"""
        image = make_image(10, 10, channel, color=1)
        image.setflags(write=False)
        color = paint_color(channel)
        result = image_paint(image, (2, 3, 6, 8), color)
        expected = make_image(10, 10, channel, color=1)
        fill_area(expected, (2, 3, 6, 8), color)
        assert result is not image
        np.testing.assert_array_equal(result, expected)
        # The read-only source image is unchanged
        np.testing.assert_array_equal(image, make_image(10, 10, channel, color=1))

    @pytest.mark.parametrize("channel, expected", SCALAR_COLORS)
    def test_paint_scalar_broadcasts(self, channel, expected):
        """A single value is gray, and opaque on an RGBA image"""
        image = make_image(10, 10, channel, color=1)
        result = image_paint(image, (2, 3, 6, 8), 200)
        checker = make_image(10, 10, channel, color=1)
        fill_area(checker, (2, 3, 6, 8), expected)
        assert result is image
        np.testing.assert_array_equal(image, checker)

    @pytest.mark.parametrize("channel, expected", SCALAR_COLORS)
    def test_paint_numpy_integer(self, channel, expected):
        """A numpy integer is a single value color too"""
        image = make_image(10, 10, channel, color=1)
        image_paint(image, (2, 3, 6, 8), np.uint8(200))
        checker = make_image(10, 10, channel, color=1)
        fill_area(checker, (2, 3, 6, 8), expected)
        np.testing.assert_array_equal(image, checker)

    @pytest.mark.parametrize("value, colors", [
        (255, {
            1: 255,
            2: (255, 255),
            3: (255, 255, 255),
            4: (255, 255, 255, 255),
        }),
        (0, {
            1: 0,
            2: (0, 0),
            3: (0, 0, 0),
            4: (0, 0, 0, 255),
        }),
    ], ids=['white', 'black'])
    @pytest.mark.parametrize("channel", [1, 2, 3, 4], ids=['grayscale', 'grayscale_alpha', 'rgb', 'rgba'])
    def test_paint_extreme_values(self, channel, value, colors):
        """White and black are plain values, alpha stays opaque"""
        image = make_image(10, 10, channel, color=7)
        image_paint(image, (2, 3, 6, 8), value)
        checker = make_image(10, 10, channel, color=7)
        fill_area(checker, (2, 3, 6, 8), colors[channel])
        np.testing.assert_array_equal(image, checker)

    @pytest.mark.parametrize(
        "channel, expected", [
            (1, 128),
            (2, (128, 128)),
            (3, (128, 128, 128)),
            (4, (128, 128, 128, 255)),
        ], ids=['grayscale', 'grayscale_alpha', 'rgb', 'rgba']
    )
    def test_paint_float_scalar_truncates(self, channel, expected):
        """A float value is truncated to an integer, like a numpy assignment"""
        image = make_image(10, 10, channel, color=1)
        image_paint(image, (2, 3, 6, 8), 128.9)
        checker = make_image(10, 10, channel, color=1)
        fill_area(checker, (2, 3, 6, 8), expected)
        np.testing.assert_array_equal(image, checker)

    def test_paint_numpy_float(self):
        """A numpy float is a single value color too"""
        image = make_image(10, 10, 3, color=1)
        image_paint(image, (2, 3, 6, 8), np.float32(200.7))
        checker = make_image(10, 10, 3, color=1)
        fill_area(checker, (2, 3, 6, 8), (200, 200, 200))
        np.testing.assert_array_equal(image, checker)

    @pytest.mark.parametrize("color", [256, -1, 300.0, float('nan'), float('inf')], ids=[
        'above', 'below', 'float_above', 'nan', 'inf'
    ])
    @pytest.mark.parametrize("channel", CHANNELS)
    def test_paint_scalar_out_of_range_raises(self, channel, color):
        """Numpy would wrap an out of range value silently, it must be rejected"""
        image = make_image(10, 10, channel, color=7)
        original = image.copy()
        with pytest.raises(ValueError, match='Cannot paint'):
            image_paint(image, (2, 3, 6, 8), color)
        np.testing.assert_array_equal(image, original)

    @pytest.mark.parametrize("color", [
        (256, 0, 0),
        (-1, 0, 0),
        [10, 20, 300],
        (0, 0, 0, 256),
    ], ids=['rgb_above', 'rgb_below', 'rgb_list', 'rgba_alpha'])
    def test_paint_channel_out_of_range_raises(self, color):
        """Every channel value has to fit into the dtype of the image"""
        image = make_image(10, 10, 4, color=7)
        original = image.copy()
        with pytest.raises(ValueError, match='Cannot paint'):
            image_paint(image, (2, 3, 6, 8), color)
        np.testing.assert_array_equal(image, original)

    def test_paint_uint16_image_allows_large_value(self):
        """The range check follows the dtype of the image, not a fixed uint8 range"""
        image = np.zeros((10, 10, 3), dtype=np.uint16)
        image_paint(image, (2, 3, 6, 8), 300)
        assert np.all(image[3:8, 2:6] == 300)
        with pytest.raises(ValueError, match='Cannot paint'):
            image_paint(image, (2, 3, 6, 8), 70000)

    def test_paint_float_image_has_no_range_check(self):
        """A float image has no integer channel range, so any value is accepted"""
        image = np.zeros((10, 10, 3), dtype=np.float32)
        image_paint(image, (2, 3, 6, 8), 300.0)
        assert np.all(image[3:8, 2:6] == 300.0)
        image_paint(image, (2, 3, 6, 8), (300.0, -300.0, 1.5))
        np.testing.assert_array_equal(image[3:8, 2:6], np.full((5, 4, 3), (300.0, -300.0, 1.5), dtype=np.float32))

    @pytest.mark.parametrize("color, luma", [
        ((255, 0, 0), 76),
        ((0, 255, 0), 150),
        ((0, 0, 255), 29),
        ((100, 150, 200), 141),
        ((255, 255, 255), 255),
    ], ids=['red', 'green', 'blue', 'mixed', 'white'])
    def test_paint_rgb_on_grayscale(self, color, luma):
        """An RGB color on a grayscale image should become its luma"""
        image = make_image(10, 10, 1, color=0)
        image_paint(image, (2, 3, 6, 8), color)
        expected = make_image(10, 10, 1, color=0)
        fill_area(expected, (2, 3, 6, 8), luma)
        np.testing.assert_array_equal(image, expected)

    def test_paint_rgba_on_grayscale(self):
        """An RGBA color on a grayscale image should drop alpha and become its luma"""
        image = make_image(10, 10, 1, color=0)
        image_paint(image, (2, 3, 6, 8), (0, 255, 0, 128))
        expected = make_image(10, 10, 1, color=0)
        fill_area(expected, (2, 3, 6, 8), 150)
        np.testing.assert_array_equal(image, expected)

    def test_paint_rgba_on_rgb(self):
        """An RGBA color on an RGB image should drop alpha"""
        image = make_image(10, 10, 3, color=1)
        image_paint(image, (2, 3, 6, 8), (10, 20, 30, 40))
        expected = make_image(10, 10, 3, color=1)
        fill_area(expected, (2, 3, 6, 8), (10, 20, 30))
        np.testing.assert_array_equal(image, expected)

    def test_paint_rgb_on_rgba(self):
        """An RGB color on an RGBA image should become opaque"""
        image = make_image(10, 10, 4, color=1)
        image_paint(image, (2, 3, 6, 8), (10, 20, 30))
        expected = make_image(10, 10, 4, color=1)
        fill_area(expected, (2, 3, 6, 8), (10, 20, 30, 255))
        np.testing.assert_array_equal(image, expected)

    def test_paint_list_color(self):
        """A color given as a list should be adapted like a tuple"""
        image = make_image(10, 10, 4, color=1)
        image_paint(image, (2, 3, 6, 8), [10, 20, 30])
        expected = make_image(10, 10, 4, color=1)
        fill_area(expected, (2, 3, 6, 8), (10, 20, 30, 255))
        np.testing.assert_array_equal(image, expected)

    @pytest.mark.parametrize("channel, color", [
        (1, (1, 2)),
        (2, (1, 2, 3)),
        (2, (1, 2, 3, 4)),
        (3, (1,)),
        (3, (1, 2)),
        (4, (1, 2)),
        (4, (1, 2, 3, 4, 5)),
    ], ids=['gray_ga', 'ga_rgb', 'ga_rgba', 'rgb_1', 'rgb_2', 'rgba_2', 'rgba_5'])
    def test_paint_unadaptable_color_raises(self, channel, color):
        """A color that can't be adapted to the image channels raises ValueError"""
        image = make_image(10, 10, channel, color=7)
        original = image.copy()
        with pytest.raises(ValueError, match='Cannot paint'):
            image_paint(image, (2, 3, 6, 8), color)
        # The failed paint leaves the image untouched
        np.testing.assert_array_equal(image, original)


class TestGetBbox:
    """Tests for get_bbox()"""

    @pytest.mark.parametrize("channel", CHANNELS)
    def test_bbox_of_blob(self, channel):
        """The bbox should cover the bright pixels only"""
        image = make_image(12, 15, channel, color=black(channel))
        fill_area(image, (3, 2, 9, 7), white(channel))
        assert get_bbox(image) == (3, 2, 9, 7)

    @pytest.mark.parametrize("channel", CHANNELS)
    def test_bbox_multiple_blobs(self, channel):
        """The bbox should be the union of all bright blobs"""
        image = make_image(12, 15, channel, color=black(channel))
        fill_area(image, (1, 1, 3, 3), white(channel))
        fill_area(image, (6, 5, 11, 9), white(channel))
        assert get_bbox(image) == (1, 1, 11, 9)

    @pytest.mark.parametrize("channel", CHANNELS)
    def test_bbox_full_image(self, channel):
        """A fully bright image should return the whole image"""
        image = make_image(12, 15, channel, color=white(channel))
        assert get_bbox(image) == (0, 0, 15, 12)

    @pytest.mark.parametrize("channel", CHANNELS)
    def test_bbox_single_pixel(self, channel):
        """A single bright pixel should have a 1x1 bbox"""
        image = make_image(12, 15, channel, color=black(channel))
        image[7, 4] = white(channel)
        assert get_bbox(image) == (4, 7, 5, 8)

    @pytest.mark.parametrize("channel", CHANNELS)
    def test_bbox_all_black_raises(self, channel):
        """An image without bright pixels has no bbox"""
        image = make_image(12, 15, channel, color=black(channel))
        with pytest.raises(ImageNotSupported, match='Cannot get bbox'):
            get_bbox(image)

    @pytest.mark.parametrize("channel", CHANNELS)
    def test_bbox_threshold_is_exclusive(self, channel):
        """color > threshold is content, color == threshold is background"""
        image = make_image(10, 10, channel, color=black(channel))
        fill_area(image, (2, 2, 5, 5), gray(100, channel))
        assert get_bbox(image, threshold=99) == (2, 2, 5, 5)
        with pytest.raises(ImageNotSupported, match='Cannot get bbox'):
            get_bbox(image, threshold=100)

    @pytest.mark.parametrize("channel", CHANNELS)
    def test_bbox_threshold_filters_dim_content(self, channel):
        """Content at or below the threshold should not extend the bbox"""
        image = make_image(10, 10, channel, color=black(channel))
        fill_area(image, (1, 1, 4, 4), gray(100, channel))
        fill_area(image, (5, 5, 8, 8), gray(200, channel))
        assert get_bbox(image, threshold=150) == (5, 5, 8, 8)

    def test_bbox_default_threshold(self):
        """With the default threshold only zero pixels are background"""
        image = np.zeros((10, 10), dtype=np.uint8)
        image[5, 6] = 1
        assert get_bbox(image) == (6, 5, 7, 6)

    def test_bbox_unsupported_channel(self):
        """Only 1, 3 and 4 channel images are supported"""
        image = make_image(10, 10, 2, color=white(2))
        with pytest.raises(ImageNotSupported, match='shape='):
            get_bbox(image)

    def test_bbox_empty_bounding_rect_raises(self, monkeypatch):
        """A zero-area bounding rect should raise instead of returning an empty bbox"""
        monkeypatch.setattr(draw.cv2, 'findContours', lambda *args, **kwargs: ([object()], None))
        monkeypatch.setattr(draw.cv2, 'boundingRect', lambda contour: (5, 5, 0, 0))
        image = make_image(10, 10, 1, color=255)
        with pytest.raises(ImageNotSupported, match='Empty bbox'):
            get_bbox(image)


class TestGetBboxReversed:
    """Tests for get_bbox_reversed()"""

    @pytest.mark.parametrize("channel", CHANNELS)
    def test_bbox_of_dark_blob(self, channel):
        """The bbox should cover the dark pixels only"""
        image = make_image(12, 15, channel, color=white(channel))
        fill_area(image, (3, 2, 9, 7), black(channel))
        assert get_bbox_reversed(image) == (3, 2, 9, 7)

    @pytest.mark.parametrize("channel", CHANNELS)
    def test_bbox_multiple_blobs(self, channel):
        """The bbox should be the union of all dark blobs"""
        image = make_image(12, 15, channel, color=white(channel))
        fill_area(image, (1, 1, 3, 3), black(channel))
        fill_area(image, (6, 5, 11, 9), black(channel))
        assert get_bbox_reversed(image) == (1, 1, 11, 9)

    @pytest.mark.parametrize("channel", CHANNELS)
    def test_bbox_full_image(self, channel):
        """An all dark image should return the whole image"""
        image = make_image(12, 15, channel, color=black(channel))
        assert get_bbox_reversed(image) == (0, 0, 15, 12)

    @pytest.mark.parametrize("channel", CHANNELS)
    def test_bbox_single_pixel(self, channel):
        """A single dark pixel should have a 1x1 bbox"""
        image = make_image(12, 15, channel, color=white(channel))
        image[7, 4] = black(channel)
        assert get_bbox_reversed(image) == (4, 7, 5, 8)

    @pytest.mark.parametrize("channel", CHANNELS)
    def test_bbox_all_white_raises(self, channel):
        """An image without dark pixels has no bbox"""
        image = make_image(12, 15, channel, color=white(channel))
        with pytest.raises(ImageNotSupported, match='Cannot get bbox'):
            get_bbox_reversed(image)

    @pytest.mark.parametrize("channel", CHANNELS)
    def test_bbox_threshold_is_exclusive(self, channel):
        """color < threshold is content, color == threshold is background"""
        image = make_image(10, 10, channel, color=gray(200, channel))
        fill_area(image, (2, 2, 5, 5), gray(100, channel))
        assert get_bbox_reversed(image, threshold=101) == (2, 2, 5, 5)
        with pytest.raises(ImageNotSupported, match='Cannot get bbox'):
            get_bbox_reversed(image, threshold=100)

    def test_bbox_threshold_zero_raises(self):
        """Nothing is darker than 0"""
        image = np.zeros((10, 10), dtype=np.uint8)
        with pytest.raises(ImageNotSupported, match='Cannot get bbox'):
            get_bbox_reversed(image, threshold=0)

    def test_bbox_unsupported_channel(self):
        """Only 1, 3 and 4 channel images are supported"""
        image = make_image(10, 10, 2, color=black(2))
        with pytest.raises(ImageNotSupported, match='shape='):
            get_bbox_reversed(image)

    def test_bbox_empty_bounding_rect_raises(self, monkeypatch):
        """A zero-area bounding rect should raise instead of returning an empty bbox"""
        monkeypatch.setattr(draw.cv2, 'findContours', lambda *args, **kwargs: ([object()], None))
        monkeypatch.setattr(draw.cv2, 'boundingRect', lambda contour: (5, 5, 0, 0))
        image = make_image(10, 10, 1, color=0)
        with pytest.raises(ImageNotSupported, match='Empty bbox'):
            get_bbox_reversed(image)
