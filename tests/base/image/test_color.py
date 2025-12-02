import cv2
import numpy as np
import pytest

from alasio.base.image.color import rgb2luma, rgb565_to_rgb888


def create_rgb888():
    """
    Create a 4096x4096 image containing every color in RGB888
    for testing purpose
    """
    size = 4096

    b = np.tile(np.arange(256, dtype=np.uint8), 65536)
    g = np.repeat(np.arange(256, dtype=np.uint8), 256)
    g = np.tile(g, 256)
    r = np.repeat(np.arange(256, dtype=np.uint8), 65536)

    b = b.reshape(size, size)
    g = g.reshape(size, size)
    r = r.reshape(size, size)

    img_array = cv2.merge([b, g, r])
    return img_array


def create_rgb565():
    """
    Create a 256x256 single-channel image (np.uint16).
    Contains every possible value for RGB565 (0 to 65535).

    Format:
    - Shape: (256, 256)
    - Type:  np.uint16 (Single Channel)
    - Bit Layout: [ RRRRR (5) | GGGGGG (6) | BBBBB (5) ]
    """
    size = 256  # 256 * 256 = 65536 pixels
    img_rgb565 = np.arange(65536, dtype=np.uint16)
    img_rgb565 = img_rgb565.reshape(size, size)
    return img_rgb565


def rgb565_to_rgb888_reference(arr):
    # Implement the simple reference version from the comments
    # Reference implementation (rgb565_to_rgb888() in color.py):
    r = (arr & 0b1111100000000000) >> (11 - 3)
    g = (arr & 0b0000011111100000) >> (5 - 2)
    b = (arr & 0b0000000000011111) << 3
    r |= (r & 0b11100000) >> 5
    g |= (g & 0b11000000) >> 6
    b |= (b & 0b11100000) >> 5
    r = r.astype(np.uint8)
    g = g.astype(np.uint8)
    b = b.astype(np.uint8)
    return cv2.merge([r, g, b])


class TestColor:
    """Test suite for rgb2luma functions"""

    @pytest.fixture(scope="class")
    def rgb888_image(self):
        """
        Fixture to create the RGB888 test image once for all tests in this class.
        This contains all possible RGB888 colors in a 4096x4096 image.
        """
        return create_rgb888()

    @pytest.fixture(scope="class")
    def rgb565_image(self):
        """
        Fixture to create the RGB565 test image once for all tests in this class.
        This contains all possible RGB565 colors (0-65535) in a 256x256 image.
        """
        return create_rgb565()

    def test_rgb2luma(self, rgb888_image):
        """
        Test if the functions are approximately equal (within tolerance).
        This is useful if exact equality fails due to floating point precision.
        """
        result1 = rgb2luma(rgb888_image)
        result2 = rgb2luma(rgb888_image, fast=False)

        # Test that both functions return the same output shape
        assert result1.shape == result2.shape, \
            f"Shape mismatch: {result1.shape} vs {result2.shape}"
        assert result1.shape == (4096, 4096), \
            f"Expected shape (4096, 4096), got {result1.shape}"
        assert result1.dtype == result2.dtype, \
            f"Dtype mismatch: {result1.dtype} vs {result2.dtype}"

        # Allow a small tolerance for potential rounding differences
        are_close = np.allclose(result1, result2, atol=1, rtol=0)

        if not are_close:
            diff = np.abs(result1.astype(np.int16) - result2.astype(np.int16))
            max_diff = np.max(diff)
            mean_diff = np.mean(diff)

            pytest.fail(
                f"Arrays are not close within tolerance!\n"
                f"Maximum difference: {max_diff}\n"
                f"Mean difference: {mean_diff:.4f}\n"
                f"Tolerance: atol=1"
            )

    def test_rgb565_to_rgb888(self, rgb565_image):
        """
        Test rgb565_to_rgb888 by comparing the optimized implementation
        with the simple, commented reference implementation.
        """
        # Get result from the optimized implementation
        result_fast = rgb565_to_rgb888(rgb565_image)
        result_reference = rgb565_to_rgb888_reference(rgb565_image.copy())

        # Test that both functions return the same output shape
        assert result_fast.shape == result_reference.shape, \
            f"Shape mismatch: {result_fast.shape} vs {result_reference.shape}"
        assert result_fast.shape == (256, 256, 3), \
            f"Expected shape (256, 256, 3), got {result_fast.shape}"
        assert result_fast.dtype == result_reference.dtype, \
            f"Dtype mismatch: {result_fast.dtype} vs {result_reference.dtype}"
        assert result_fast.dtype == np.uint8, \
            f"Expected dtype uint8, got {result_fast.dtype}"

        # Allow a small tolerance for potential rounding differences
        are_close = np.allclose(result_reference, result_fast, atol=1, rtol=0)

        if not are_close:
            diff = np.abs(result_reference.astype(np.int16) - result_fast.astype(np.int16))
            max_diff = np.max(diff)
            mean_diff = np.mean(diff)

            pytest.fail(
                f"Arrays are not close within tolerance!\n"
                f"Maximum difference: {max_diff}\n"
                f"Mean difference: {mean_diff:.4f}\n"
                f"Tolerance: atol=1"
            )
