"""
Tests of the per pixel helpers and the scalar color distances of
alasio.base.image.color: rgbmax(), rgbmin(), rgbminmax(), rgb2gray(),
rgb2hsv(), get_color(), color_similarity(), color_similar() and
color_similar_1d().

The 2D functions that scan a whole screenshot have a file of their own
(test_color_luma.py, test_color_rgb565.py, test_color_similarity.py,
test_color_mask.py, test_color_letters.py and test_color_white_letters.py), so
the exhaustive color space scans of the module run side by side instead of one
after another.
"""
import cv2
import numpy as np
import pytest

from alasio.base.image.color import (
    color_similar, color_similar_1d, color_similarity, get_color, rgb2gray, rgb2hsv, rgbmax, rgbmin, rgbminmax
)

pytestmark = [
    pytest.mark.parallel,
]


def channel_extremes(image):
    """
    The per pixel minimum and maximum of the three channels.

    The reference of rgbmin(), rgbmax() and rgbminmax(), computed with numpy
    rather than with the cv2.max() / cv2.min() of the implementation.

    Args:
        image (np.ndarray): uint8 image of shape (height, width, 3)

    Returns:
        np.ndarray: minimum, shape (height, width)
        np.ndarray: maximum, shape (height, width)
    """
    channel_0, channel_1, channel_2 = cv2.split(image)
    minimum = np.minimum(np.minimum(channel_0, channel_1), channel_2)
    maximum = np.maximum(np.maximum(channel_0, channel_1), channel_2)
    return minimum, maximum


def gray_neighbours(minimum, maximum):
    """
    The two values (MIN + MAX) / 2 may round to.

    An even sum has one answer, the midpoint is an integer. An odd sum is a
    tie, and rounding a tie is the business of `cv2.addWeighted`: its scalar
    path rounds the tie to the even neighbour, its SIMD kernels round it up, so
    the same color answers the next level on a machine whose opencv took the
    other path. Both neighbours are the midpoint within one level, a value
    beside them is a different conversion.

    Args:
        minimum (np.ndarray): uint8 per pixel minimum
        maximum (np.ndarray): uint8 per pixel maximum

    Returns:
        np.ndarray: uint8, the floor of the midpoint, shape of minimum
        np.ndarray: uint8, the ceiling of the midpoint, shape of minimum
    """
    total = minimum.astype(np.int16) + maximum
    return (total >> 1).astype(np.uint8), ((total + 1) >> 1).astype(np.uint8)


class TestRgbMax:
    """rgbmax() must be the per pixel maximum of the three channels"""

    def test_every_color(self, rgb888_image):
        """Every RGB888 color gives the maximum of its channels"""
        result = rgbmax(rgb888_image)
        assert result.shape == rgb888_image.shape[:2]
        assert result.dtype == np.uint8
        np.testing.assert_array_equal(result, channel_extremes(rgb888_image)[1])

    def test_pure_channels(self):
        """A pixel with one bright channel answers that channel"""
        image = np.array([[[255, 0, 0], [0, 255, 0], [0, 0, 255], [1, 2, 3]]], dtype=np.uint8)
        assert rgbmax(image).tolist() == [[255, 255, 255, 3]]


class TestRgbMin:
    """rgbmin() must be the per pixel minimum of the three channels"""

    def test_every_color(self, rgb888_image):
        """Every RGB888 color gives the minimum of its channels"""
        result = rgbmin(rgb888_image)
        assert result.shape == rgb888_image.shape[:2]
        assert result.dtype == np.uint8
        np.testing.assert_array_equal(result, channel_extremes(rgb888_image)[0])

    def test_pure_channels(self):
        """A pixel with one dark channel answers that channel"""
        image = np.array([[[255, 0, 0], [0, 255, 0], [0, 0, 255], [1, 2, 3]]], dtype=np.uint8)
        assert rgbmin(image).tolist() == [[0, 0, 0, 1]]


class TestRgbMinMax:
    """rgbminmax() must answer both the minimum and the maximum of the channels"""

    def test_every_color(self, rgb888_image):
        """Every RGB888 color gives its minimum and its maximum"""
        minimum, maximum = rgbminmax(rgb888_image)
        expected_minimum, expected_maximum = channel_extremes(rgb888_image)
        assert minimum.shape == rgb888_image.shape[:2]
        assert maximum.shape == rgb888_image.shape[:2]
        assert minimum.dtype == np.uint8
        assert maximum.dtype == np.uint8
        np.testing.assert_array_equal(minimum, expected_minimum)
        np.testing.assert_array_equal(maximum, expected_maximum)

    def test_order_of_the_result(self):
        """The minimum is the first value and the maximum the second"""
        image = np.array([[[10, 200, 30]]], dtype=np.uint8)
        minimum, maximum = rgbminmax(image)
        assert minimum.tolist() == [[10]]
        assert maximum.tolist() == [[200]]


class TestRgb2Gray:
    """rgb2gray() must be (MAX(r, g, b) + MIN(r, g, b)) / 2"""

    def test_every_color(self, rgb888_image):
        """Every RGB888 color gives the middle of its extreme channels"""
        result = rgb2gray(rgb888_image)
        assert result.shape == rgb888_image.shape[:2]
        assert result.dtype == np.uint8
        low, high = gray_neighbours(*channel_extremes(rgb888_image))
        # an odd sum is a tie, which may answer either neighbour
        missed = (result != low) & (result != high)
        assert not missed.any(), f'{int(missed.sum())} pixels are not the midpoint'
        # an even sum is no tie, the midpoint is exactly that value
        np.testing.assert_array_equal(result[low == high], low[low == high])

    @pytest.mark.parametrize(
        'pixel, expected',
        [
            ((10, 200, 30), (105,)),
            ((0, 0, 0), (0,)),
            ((255, 255, 255), (255,)),
            ((0, 255, 0), (127, 128)),  # 127.5, a tie the conversion rounds its own way
            ((0, 1, 0), (0, 1)),  # 0.5, the same tie at the other end
        ],
        ids=['mid', 'black', 'white', 'half_up', 'half_down'],
    )
    def test_one_pixel(self, pixel, expected):
        """A pixel gives the middle of its minimum and maximum"""
        image = np.array([[pixel]], dtype=np.uint8)
        assert rgb2gray(image)[0, 0] in expected


class TestRgb2Hsv:
    """rgb2hsv() must be the HSV of the module, in its own units"""

    # Hue of opencv is 0~180, the module reports 0~360, saturation and value
    # are 0~255 in opencv and 0~100 here
    SCALE = np.array([360 / 180, 100 / 255, 100 / 255])

    def test_sample_of_colors(self, rgb888_image):
        """
        A stride sample of the color space.

        The conversion of opencv is a lookup of the input, so a sample of the
        colors is the same statement as the whole space and costs a sixteenth
        of it. The primary colors below pin the hue sectors, and
        test_gray_has_no_hue pins the saturation.
        """
        sample = rgb888_image[::8, ::8]
        result = rgb2hsv(sample)
        expected = cv2.cvtColor(sample, cv2.COLOR_RGB2HSV).astype(float) * self.SCALE
        assert result.shape == sample.shape
        assert result.dtype == np.float64
        np.testing.assert_array_equal(result, expected)

    @pytest.mark.parametrize(
        'pixel, expected',
        [
            ((255, 0, 0), (0.0, 100.0, 100.0)),
            ((255, 255, 0), (60.0, 100.0, 100.0)),
            ((0, 255, 0), (120.0, 100.0, 100.0)),
            ((0, 255, 255), (180.0, 100.0, 100.0)),
            ((0, 0, 255), (240.0, 100.0, 100.0)),
            ((255, 0, 255), (300.0, 100.0, 100.0)),
            ((255, 255, 255), (0.0, 0.0, 100.0)),
            ((0, 0, 0), (0.0, 0.0, 0.0)),
            ((128, 128, 128), (0.0, 0.0, 50.19607843137255)),
        ],
        ids=['red', 'yellow', 'green', 'cyan', 'blue', 'magenta', 'white', 'black', 'gray'],
    )
    def test_primary_colors(self, pixel, expected):
        """The hue sectors pin the RGB order and the unit of each channel"""
        image = np.array([[pixel]], dtype=np.uint8)
        assert tuple(rgb2hsv(image)[0, 0]) == pytest.approx(expected)

    def test_ranges(self, rgb888_image):
        """Hue is in 0~360, saturation and value are in 0~100"""
        result = rgb2hsv(rgb888_image[::128, ::128])
        assert result[..., 0].min() >= 0
        assert result[..., 0].max() < 360
        assert result[..., 1].min() >= 0
        assert result[..., 1].max() <= 100
        assert result[..., 2].min() >= 0
        assert result[..., 2].max() <= 100

    def test_gray_has_no_hue(self):
        """A gray pixel has no saturation"""
        values = np.arange(256, dtype=np.uint8)
        image = np.repeat(np.repeat(values[:, None], 3, axis=1)[None, :, :], 2, axis=0)
        result = rgb2hsv(image)
        np.testing.assert_array_equal(result[..., 1], np.zeros_like(result[..., 1]))
        np.testing.assert_array_equal(result[..., 0], np.zeros_like(result[..., 0]))


class TestGetColor:
    """get_color() must be the average of an area, in the channel order of the image"""

    def test_whole_image(self, rgb888_image):
        """Every channel of the image holds each value the same number of times"""
        color = get_color(rgb888_image)
        assert len(color) == 3
        assert color == pytest.approx((127.5, 127.5, 127.5))

    def test_area(self, rgb888_image):
        """The area is averaged on its own, the rest of the image is ignored"""
        area = (3, 2, 15, 10)
        color = get_color(rgb888_image, area)
        expected = rgb888_image[2:10, 3:15].reshape(-1, 3).mean(axis=0)
        assert color == pytest.approx(tuple(expected))

    def test_area_outside_the_image(self):
        """An area past the edge is padded black by the crop, and averaged with it"""
        image = np.full((4, 4, 3), 100, dtype=np.uint8)
        # the 4x4 area holds the 2x2 pixels inside the image, the rest is black
        assert get_color(image, (2, 2, 6, 6)) == pytest.approx((25.0, 25.0, 25.0))

    def test_grayscale(self):
        """A grayscale image reports its single channel, the other two are zero"""
        image = np.full((4, 4), 7, dtype=np.uint8)
        assert get_color(image) == (7.0, 0.0, 0.0)

    def test_channel_order_is_kept(self):
        """The three channels are not reordered, the image is read as it is"""
        image = np.full((2, 2, 3), (1, 2, 3), dtype=np.uint8)
        assert get_color(image) == (1.0, 2.0, 3.0)


class TestColorSimilarity:
    """color_similarity() must be the Photoshop tolerance of two colors"""

    @pytest.mark.parametrize(
        'color1, color2, expected',
        [
            ((1, 2, 3), (1, 2, 3), 0),
            ((110, 100, 100), (100, 100, 100), 10),
            ((100, 100, 100), (110, 100, 100), 10),
            ((110, 90, 100), (100, 100, 100), 20),
            ((12, 198, 31), (10, 200, 30), 4),
            ((0, 0, 0), (255, 255, 255), 255),
            ((255, 0, 0), (0, 255, 0), 510),
        ],
        ids=[
            'same',
            'one_channel_up',
            'one_channel_up_reversed',
            'one_up_one_down',
            'two_channels',
            'black_white',
            'red_green',
        ],
    )
    def test_tolerance(self, color1, color2, expected):
        """The tolerance is the largest positive difference plus the largest negative one"""
        assert color_similarity(color1, color2) == expected

    @pytest.mark.parametrize(
        'color1, color2',
        [
            ((110, 90, 100), (100, 100, 100)),
            ((255, 0, 0), (0, 255, 0)),
            ((1, 2, 3), (4, 5, 6)),
        ],
        ids=['mixed', 'opposite', 'small'],
    )
    def test_symmetric(self, color1, color2):
        """The order of the two colors does not change the tolerance"""
        assert color_similarity(color1, color2) == color_similarity(color2, color1)


class TestColorSimilar:
    """color_similar() must be color_similarity() cut at a threshold"""

    @pytest.mark.parametrize(
        'color1, color2, threshold, expected',
        [
            ((100, 100, 100), (100, 100, 100), 10, True),
            ((110, 100, 100), (100, 100, 100), 10, True),
            ((110, 100, 100), (100, 100, 100), 9, False),
            ((111, 100, 100), (100, 100, 100), 10, False),
            ((90, 100, 130), (100, 100, 100), 10, False),
            ((90, 100, 130), (100, 100, 100), 40, True),
        ],
        ids=['same', 'on_threshold', 'below_threshold', 'above_threshold', 'two_channels', 'two_channels_wide'],
    )
    def test_threshold(self, color1, color2, threshold, expected):
        """A tolerance equal to the threshold is similar, one above is not"""
        assert color_similar(color1, color2, threshold) is expected

    def test_default_threshold(self):
        """The default threshold is 10"""
        assert color_similar((110, 100, 100), (100, 100, 100)) is True
        assert color_similar((111, 100, 100), (100, 100, 100)) is False

    @pytest.mark.parametrize('threshold', [0, 1, 10, 30, 128, 255, 510])
    def test_agrees_with_similarity(self, threshold):
        """The two functions answer the same question"""
        pairs = [
            ((1, 2, 3), (1, 2, 3)),
            ((110, 90, 100), (100, 100, 100)),
            ((255, 0, 0), (0, 255, 0)),
            ((12, 198, 31), (10, 200, 30)),
        ]
        for color1, color2 in pairs:
            assert color_similar(color1, color2, threshold) == (color_similarity(color1, color2) <= threshold)


class TestColorSimilar1D:
    """color_similar_1d() must be the per pixel tolerance of a list of colors"""

    @staticmethod
    def reference(image, color):
        """
        The tolerance of color_similarity(), one row at a time.

        Args:
            image (np.ndarray): uint8 array of shape (pixel, 3)
            color (tuple): (r, g, b)

        Returns:
            np.ndarray: int32, the tolerance of every row
        """
        diff = image.astype(int) - np.array(color, dtype=int)
        return np.maximum(diff, 0).max(axis=1) - np.minimum(diff, 0).min(axis=1)

    def test_every_difference(self):
        """A gray of i against black has the tolerance i, for every i"""
        values = np.arange(256, dtype=np.uint8)
        image = np.repeat(values[:, None], 3, axis=1)
        result = color_similar_1d(image, (0, 0, 0), 10)
        assert result.dtype == np.bool_
        np.testing.assert_array_equal(result, values <= 10)

    def test_against_reference(self):
        """The result is the tolerance of every row, cut at the threshold"""
        image = np.array(
            [
                [1, 2, 3],
                [11, 2, 3],
                [1, 12, 3],
                [1, 2, 13],
                [110, 90, 100],
                [255, 0, 0],
            ],
            dtype=np.uint8,
        )
        color = (1, 2, 3)
        tolerance = self.reference(image, color)
        for threshold in (0, 5, 10, 20, 255):
            result = color_similar_1d(image, color, threshold)
            np.testing.assert_array_equal(result, tolerance <= threshold)

    def test_threshold_boundary(self):
        """A tolerance equal to the threshold is similar, one above is not"""
        image = np.array([[10, 10, 10], [11, 11, 11]], dtype=np.uint8)
        np.testing.assert_array_equal(color_similar_1d(image, (0, 0, 0), 10), [True, False])

    def test_default_threshold(self):
        """The default threshold is 10"""
        image = np.array([[10, 10, 10], [11, 11, 11]], dtype=np.uint8)
        np.testing.assert_array_equal(color_similar_1d(image, (0, 0, 0)), [True, False])
