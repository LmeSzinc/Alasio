"""
Tests of rgb2luma() of alasio.base.image.color.

The function is the BT.601 luma of the image, Y = 0.299 R + 0.587 G + 0.114 B,
evaluated with the fixed point coefficient table OpenCV uses for the grayscale
conversion. The tests below pin four things:

- the result is exactly cvtColor(RGB2GRAY), which is the same quantity,
- over all 16,777,216 colours it stays within one level of the exact formula,
- the average distance to that formula stays near the half level a byte
  conversion costs, which rules out a conversion that rounds twice,
- it is not the lightness of rgb2gray(), a different channel in the same module.

The bounds are deliberately loose. OpenCV picks its vectorised path per machine
build, and the exact number of colours that land one level off the formula moves
with it, so pinning a count would make the suite depend on the CPU rather than
on the code. A maximum and a mean are stable across those builds and still catch
a change of coefficient table or of rounding scheme.
"""
import cv2
import numpy as np
import pytest

from alasio.base.image.color import rgb2gray, rgb2luma

pytestmark = [
    pytest.mark.parallel,
]

# A byte conversion of a real number costs at most half a level, and the fixed
# point coefficient table adds a rounding of its own, so one level is the bound
# that holds for any build. The old two step float path sat exactly on 1.000.
MAX_DEVIATION = 1.0
# The mean distance separates the single rounding from a conversion that rounds
# twice: the fixed point table averages 0.250 of a level, the old float path
# averaged 0.333. Any vectorised build stays well inside this bound.
MAX_MEAN_DEVIATION = 0.30
TOTAL_COLOURS = 256 * 65536


def luma_exact(image):
    """
    The definition, Y = 0.299 R + 0.587 G + 0.114 B, in float64.

    Args:
        image (np.ndarray): uint8 image in RGB order

    Returns:
        np.ndarray: float64 luma of shape (height, width)
    """
    r, g, b = (channel.astype(np.float64) for channel in cv2.split(image))
    return 0.299 * r + 0.587 * g + 0.114 * b


def cube_chunks():
    """
    Every 8-bit RGB triple exactly once, split by the red channel so the whole
    cube is never resident at the same time.

    Yields:
        np.ndarray: (1, 65536, 3) uint8
    """
    index = np.arange(65536, dtype=np.uint32)
    green = (index >> 8) & 0xFF
    blue = index & 0xFF
    for red in range(256):
        block = np.empty((1, 65536, 3), dtype=np.uint8)
        block[0, :, 0] = red
        block[0, :, 1] = green
        block[0, :, 2] = blue
        yield block


@pytest.fixture(scope='module')
def deviation_report():
    """
    The whole colour cube, compared with the exact formula.

    Returns:
        dict: max_deviation, mean_deviation
    """
    worst = 0.0
    total = 0.0
    for block in cube_chunks():
        result = rgb2luma(block).astype(np.float64)
        delta = np.abs(result - luma_exact(block))
        worst = max(worst, float(delta.max()))
        total += float(delta.sum())
    return {
        'max_deviation': worst,
        'mean_deviation': total / TOTAL_COLOURS,
    }


class TestRgb2LumaFormula:
    """rgb2luma() against the exact BT.601 formula, over every colour"""

    def test_within_one_level_of_the_formula(self, deviation_report):
        """
        Every one of the 16,777,216 colours is within one level of the exact
        formula. That bound holds for any rounding OpenCV chooses: half a level
        is unavoidable when a real number becomes a byte, and the coefficient
        table adds a fraction on top.
        """
        worst = deviation_report['max_deviation']
        assert worst <= MAX_DEVIATION, worst

    def test_average_distance_is_about_half_a_level(self, deviation_report):
        """
        The mean distance is the half level of a nearest rounding, plus a little
        for the coefficient table. A conversion that rounds twice drifts to a
        third of a level and fails here, which is what makes this bound useful
        without pinning how many colours are one level off.
        """
        mean = deviation_report['mean_deviation']
        assert mean <= MAX_MEAN_DEVIATION, mean
        # and it has to be there at all: a rounding is not free
        assert mean > 0.2


class TestRgb2Luma:
    """rgb2luma() must be the BT.601 luma, within one level of the exact sum"""

    def test_is_the_grayscale_conversion(self, rgb888_image):
        """
        The result is exactly cvtColor(RGB2GRAY), the same quantity. It is the
        same call on the same machine, so this holds on every build, and it is
        what separates this table from the YUV one.
        """
        result = rgb2luma(rgb888_image)
        assert result.shape == rgb888_image.shape[:2]
        assert result.dtype == np.uint8
        np.testing.assert_array_equal(result, cv2.cvtColor(rgb888_image, cv2.COLOR_RGB2GRAY))

    def test_within_one_of_the_exact_sum(self, rgb888_image):
        """The fixed point table may only move the exact sum by one level"""
        result = rgb2luma(rgb888_image)
        diff = np.abs(result.astype(np.float64) - luma_exact(rgb888_image))
        assert diff.max() <= MAX_DEVIATION

    def test_both_paths_agree(self, rgb888_image):
        """
        The `fast` parameter is kept for call compatibility only, the two values
        must answer the same thing now.
        """
        np.testing.assert_array_equal(rgb2luma(rgb888_image), rgb2luma(rgb888_image, fast=False))

    @pytest.mark.parametrize(
        'pixel, expected',
        [
            ((255, 0, 0), 76),
            ((0, 255, 0), 150),
            ((0, 0, 255), 29),
            ((255, 255, 255), 255),
            ((0, 0, 0), 0),
            ((128, 128, 128), 128),
        ],
        ids=['red', 'green', 'blue', 'white', 'black', 'gray'],
    )
    def test_primary_colors(self, pixel, expected):
        """
        The primary colors give the luma of the document weights. These are away
        from any rounding boundary, so the values are the same on every build.
        """
        image = np.array([[pixel]], dtype=np.uint8)
        assert rgb2luma(image)[0, 0] == expected
        assert rgb2luma(image, fast=False)[0, 0] == expected

    def test_gray_stays_gray(self):
        """
        Each of the 256 gray levels maps to itself, which is the reason the three
        coefficients are summed to a power of two rather than rounded freely.
        """
        ramp = np.arange(256, dtype=np.uint8).reshape(1, 256, 1).repeat(3, axis=2)
        np.testing.assert_array_equal(rgb2luma(ramp)[0], np.arange(256, dtype=np.uint8))

    def test_is_not_the_lightness(self, rgb888_image):
        """
        rgb2gray() in the same module is (MAX + MIN) / 2, a different channel.
        A saturated color is the clearest case: red is 76 as luma and 128 as
        lightness.
        """
        red = np.array([[[255, 0, 0]]], dtype=np.uint8)
        assert rgb2luma(red)[0, 0] == 76
        assert rgb2gray(red)[0, 0] == 128
        assert rgb2luma(red)[0, 0] != rgb2gray(red)[0, 0]
        # on a real image the two are far apart, not a rounding difference
        difference = np.abs(rgb2luma(rgb888_image).astype(np.int16)
                            - rgb2gray(rgb888_image).astype(np.int16))
        assert difference.max() > 32

    def test_does_not_modify_the_source(self, rgb888_image):
        """A luma conversion reads the image, the source stays untouched"""
        source = rgb888_image[:8, :8].copy()
        original = source.copy()
        rgb2luma(source)
        rgb2luma(source, fast=False)
        np.testing.assert_array_equal(source, original)
