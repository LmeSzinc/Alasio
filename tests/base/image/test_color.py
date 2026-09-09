import cv2
import numpy as np
import pytest

from alasio.base.image.color import (
    color_mask, color_similarity_2d, extract_letters, extract_white_letters, rgb2luma, rgb565_to_rgb888
)


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


@pytest.fixture(scope="module")
def rgb888_image():
    """
    Fixture to create the RGB888 test image once for all tests.
    Contains every possible RGB888 color, covering all input cases.
    """
    return create_rgb888()


def color_blocks(image, size=125):
    """
    Yield non-overlapping size x size blocks covering the whole image.
    125x125 = 15625 pixels, which is below the 30000-pixel branch threshold.

    Args:
        image (np.ndarray): Input image
        size (int): Block size in pixels

    Yields:
        np.ndarray: Image block
    """
    for y in range(0, image.shape[0], size):
        for x in range(0, image.shape[1], size):
            yield image[y:y + size, x:x + size]


class TestRgb2Luma:
    """Tests for rgb2luma"""

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


class TestRgb565ToRgb888:
    """Tests for rgb565_to_rgb888"""

    @staticmethod
    def reference(arr):
        """
        Simple reference version from the comments in color.py

        Args:
            arr (np.ndarray): RGB565 image, uint16

        Returns:
            np.ndarray: RGB888 image, uint8
        """
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

    @pytest.fixture(scope="class")
    def rgb565_image(self):
        """
        Fixture to create the RGB565 test image once for all tests in this class.
        This contains all possible RGB565 colors (0-65535) in a 256x256 image.
        """
        return create_rgb565()

    def test_rgb565_to_rgb888(self, rgb565_image):
        """
        Test rgb565_to_rgb888 by comparing the optimized implementation
        with the simple, commented reference implementation.
        """
        # Get result from the optimized implementation
        result_fast = rgb565_to_rgb888(rgb565_image)
        result_reference = self.reference(rgb565_image.copy())

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


class TestColorSimilarity2d:
    """color_similarity_2d must match the unoptimized reference algorithm"""

    @staticmethod
    def reference(image, color):
        """
        Unoptimized reference algorithm, copied from the comments in color.py

        Args:
            image (np.ndarray): 2D BGR image
            color (tuple): (r, g, b)

        Returns:
            np.ndarray: uint8
        """
        r, g, b = cv2.split(cv2.subtract(image, (*color, 0)))
        positive = cv2.max(cv2.max(r, g), b)
        r, g, b = cv2.split(cv2.subtract((*color, 0), image))
        negative = cv2.max(cv2.max(r, g), b)
        return cv2.subtract(255, cv2.add(positive, negative))

    @pytest.mark.parametrize("color", [
        (128, 100, 200),
        (0, 0, 0),
        (255, 255, 255),
        (10, 245, 3),
    ])
    def test_matches_reference(self, rgb888_image, color):
        # Large image path (per-channel, >= 30000 pixels)
        result = color_similarity_2d(rgb888_image, color)
        reference = self.reference(rgb888_image, color)
        assert np.array_equal(result, reference), f"color={color}"
        # Small image path (< 30000 pixels): every 125x125 block together
        # covers the full color space
        for index, block in enumerate(color_blocks(rgb888_image)):
            result = color_similarity_2d(block, color)
            reference = self.reference(block, color)
            assert np.array_equal(result, reference), \
                f"color={color} block {index}"


class TestColorMask:
    """color_mask must match color_similarity_2d followed by cv2.inRange"""

    @staticmethod
    def reference(image, color, threshold):
        """
        Unoptimized reference algorithm, copied from the comments in color.py

        Args:
            image (np.ndarray): 2D BGR image
            color (tuple): (r, g, b)
            threshold (int):

        Returns:
            np.ndarray: uint8
        """
        similarity = color_similarity_2d(image, color)
        return cv2.inRange(similarity, 255 - threshold, 255)

    @pytest.mark.parametrize("color", [
        (128, 100, 200),
        (0, 0, 0),
        (255, 255, 255),
        (10, 245, 3),
    ])
    @pytest.mark.parametrize("threshold", [1, 30, 255])
    def test_matches_reference(self, rgb888_image, color, threshold):
        # Large image path (per-channel, >= 30000 pixels)
        result = color_mask(rgb888_image, color, threshold)
        reference = self.reference(rgb888_image, color, threshold)
        assert np.array_equal(result, reference), \
            f"color={color} threshold={threshold}"
        # Small image path (< 30000 pixels): every 125x125 block together
        # covers the full color space
        for index, block in enumerate(color_blocks(rgb888_image)):
            result = color_mask(block, color, threshold)
            reference = self.reference(block, color, threshold)
            assert np.array_equal(result, reference), \
                f"color={color} threshold={threshold} block {index}"


class TestExtractLetters:
    """extract_letters must match the unoptimized reference algorithm"""

    @staticmethod
    def reference(image, letter, threshold):
        """
        Unoptimized reference algorithm, copied from the comments in color.py

        Args:
            image (np.ndarray): 2D BGR image
            letter (tuple): Letter RGB.
            threshold (int):

        Returns:
            np.ndarray: uint8
        """
        r, g, b = cv2.split(cv2.subtract(image, (*letter, 0)))
        positive = cv2.max(cv2.max(r, g), b)
        r, g, b = cv2.split(cv2.subtract((*letter, 0), image))
        negative = cv2.max(cv2.max(r, g), b)
        return cv2.multiply(cv2.add(positive, negative), 255.0 / threshold)

    @pytest.mark.parametrize("letter", [
        (255, 255, 255),
        [255, 255, 255],
        (100, 150, 200),
        (0, 0, 0),
    ])
    @pytest.mark.parametrize("threshold", [64, 128, 255])
    def test_matches_reference(self, rgb888_image, letter, threshold):
        # Large image path (per-channel, >= 30000 pixels)
        result = extract_letters(rgb888_image, letter=letter, threshold=threshold)
        reference = self.reference(rgb888_image, tuple(letter), threshold)
        assert np.array_equal(result, reference), \
            f"letter={letter} threshold={threshold}"
        # Small image path (< 30000 pixels): every 125x125 block together
        # covers the full color space
        for index, block in enumerate(color_blocks(rgb888_image)):
            result = extract_letters(block, letter=letter, threshold=threshold)
            reference = self.reference(block, tuple(letter), threshold)
            assert np.array_equal(result, reference), \
                f"letter={letter} threshold={threshold} block {index}"


class TestExtractWhiteLetters:
    """extract_white_letters must match the unoptimized reference algorithm"""

    @staticmethod
    def reference(image, threshold):
        """
        Unoptimized reference algorithm, copied from the comments in color.py

        Args:
            image (np.ndarray): 2D BGR image
            threshold (int):

        Returns:
            np.ndarray: uint8
        """
        r, g, b = cv2.split(cv2.subtract((255, 255, 255, 0), image))
        minimum = cv2.min(cv2.min(r, g), b)
        maximum = cv2.max(cv2.max(r, g), b)
        maximum = cv2.multiply(maximum, 0.5)
        minimum = cv2.multiply(minimum, 0.5)
        return cv2.multiply(cv2.add(maximum, cv2.subtract(maximum, minimum)), 255.0 / threshold)

    @pytest.mark.parametrize("threshold", [64, 128, 255])
    def test_matches_reference(self, rgb888_image, threshold):
        result = extract_white_letters(rgb888_image, threshold=threshold)
        reference = self.reference(rgb888_image, threshold)
        assert np.array_equal(result, reference), f"threshold={threshold}"
