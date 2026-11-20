"""
Tests of rgb2luma() of alasio.base.image.color.

The function has a fast path, a two step weighted sum of the channels, and a
standard path that takes the Y channel of the YUV conversion. The fast path is
allowed to differ from the standard one by one level, which is what the tests
below pin: the standard path is exactly the integer formula the module
documents, and the fast path stays within one level of it.
"""
import cv2
import numpy as np
import pytest

from alasio.base.image.color import rgb2luma

pytestmark = [
    pytest.mark.parallel,
]


def luma_formula(image):
    """
    The integer formula documented by rgb2luma().

    Y = (R * 4899 + G * 9617 + B * 1868 + 8192) >> 14

    Args:
        image (np.ndarray): uint8 image in RGB order

    Returns:
        np.ndarray: uint8 luma of shape (height, width)
    """
    r, g, b = (channel.astype(np.int32) for channel in cv2.split(image))
    return ((r * 4899 + g * 9617 + b * 1868 + 8192) >> 14).astype(np.uint8)


class TestRgb2Luma:
    """rgb2luma() must be the luma of the module in both of its paths"""

    def test_standard_is_the_formula(self, rgb888_image):
        """The standard path is exactly the integer formula, in every color"""
        result = rgb2luma(rgb888_image, fast=False)
        assert result.shape == rgb888_image.shape[:2]
        assert result.dtype == np.uint8
        np.testing.assert_array_equal(result, luma_formula(rgb888_image))

    def test_fast_is_within_one(self, rgb888_image):
        """
        The fast path rounds twice instead of once, so it may be one level off
        the formula, and the module states that tolerance.
        """
        result = rgb2luma(rgb888_image)
        assert result.shape == rgb888_image.shape[:2]
        assert result.dtype == np.uint8
        diff = np.abs(result.astype(np.int16) - luma_formula(rgb888_image).astype(np.int16))
        assert diff.max() <= 1

    def test_fast_matches_standard(self, rgb888_image):
        """The two paths agree within the one level the docstring promises"""
        fast = rgb2luma(rgb888_image)
        standard = rgb2luma(rgb888_image, fast=False)
        diff = np.abs(fast.astype(np.int16) - standard.astype(np.int16))
        assert diff.max() <= 1

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
        """The primary colors give the luma of the document weights"""
        image = np.array([[pixel]], dtype=np.uint8)
        assert rgb2luma(image)[0, 0] == expected
        assert rgb2luma(image, fast=False)[0, 0] == expected

    def test_does_not_modify_the_source(self, rgb888_image):
        """A luma conversion reads the image, the source stays untouched"""
        source = rgb888_image[:8, :8].copy()
        original = source.copy()
        rgb2luma(source)
        rgb2luma(source, fast=False)
        np.testing.assert_array_equal(source, original)
