"""
Tests of rgb565_to_rgb888() of alasio.base.image.color.

A device that sends RGB565 is converted once per frame, so the function has no
branch on its content and every one of the 65536 inputs is a possible value of
a screenshot. The reference is the bit replication of the commented code in the
module, which is the exact RGB565 expansion; the implementation scales instead
of shifting, so a level may round one step away from it, never more.
"""
import cv2
import numpy as np
import pytest

from alasio.base.image.color import rgb565_to_rgb888

pytestmark = [
    pytest.mark.parallel,
]

# A row of pixels is used instead of a single pixel for the conversions the
# function is expected to handle, see test_single_pixel for the 1x1 image
ROW = 4


def rgb565_reference(image):
    """
    The bit replication the module documents as the reference expansion.

    Args:
        image (np.ndarray): uint16 image of shape (height, width)

    Returns:
        np.ndarray: uint8 image of shape (height, width, 3)
    """
    r = (image & 0b1111100000000000) >> (11 - 3)
    g = (image & 0b0000011111100000) >> (5 - 2)
    b = (image & 0b0000000000011111) << 3
    r |= (r & 0b11100000) >> 5
    g |= (g & 0b11000000) >> 6
    b |= (b & 0b11100000) >> 5
    return cv2.merge([r.astype(np.uint8), g.astype(np.uint8), b.astype(np.uint8)])


class TestRgb565ToRgb888:
    """rgb565_to_rgb888() must be the exact expansion, one level included"""

    def test_every_value(self, rgb565_image):
        """Every one of the 65536 inputs is expanded like the bit replication"""
        result = rgb565_to_rgb888(rgb565_image)
        reference = rgb565_reference(rgb565_image)
        assert result.shape == (256, 256, 3)
        assert result.dtype == np.uint8
        diff = np.abs(result.astype(np.int16) - reference.astype(np.int16))
        assert diff.max() <= 1

    @pytest.mark.parametrize(
        'value, expected',
        [
            (0x0000, [0, 0, 0]),
            (0xFFFF, [255, 255, 255]),
            (0xF800, [255, 0, 0]),
            (0x07E0, [0, 255, 0]),
            (0x001F, [0, 0, 255]),
            (0x0001, [0, 0, 8]),
            (0x8410, [132, 130, 132]),
        ],
        ids=['black', 'white', 'red', 'green', 'blue', 'lowest_blue', 'mid_gray'],
    )
    def test_one_value(self, value, expected):
        """The fields of a value land on their own channel"""
        image = np.full((1, ROW), value, dtype=np.uint16)
        result = rgb565_to_rgb888(image)
        assert result.shape == (1, ROW, 3)
        np.testing.assert_array_equal(result, np.tile(expected, (1, ROW, 1)))

    def test_single_pixel(self):
        """
        A 1x1 image is converted, not left as the uninitialized buffer.

        opencv reads a matrix of a single pixel as a scalar, and the masked
        channels of the fast path then answer a scalar array instead of the
        buffer, which the function has to route around.
        """
        reference = rgb565_reference(np.array([[0xF800]], dtype=np.uint16))
        result = rgb565_to_rgb888(np.array([[0xF800]], dtype=np.uint16))
        assert result.shape == (1, 1, 3)
        assert result.dtype == np.uint8
        np.testing.assert_array_equal(result, reference)

    def test_does_not_modify_the_source(self, rgb565_image):
        """The conversion reads the image, the source stays untouched"""
        source = rgb565_image[:8, :8].copy()
        original = source.copy()
        rgb565_to_rgb888(source)
        np.testing.assert_array_equal(source, original)
