"""
Tests of color_similarity_2d() of alasio.base.image.color.

The function computes the per pixel tolerance of a color, 255 for an exact
match, and chooses between two implementations on the pixel count of the
image: a 3 channel path below 30000 pixels and a per channel path above it.
The reference is the unoptimized algorithm of the commented code in the module,
and the images below cover every RGB888 color in both branches.
"""
import cv2
import numpy as np
import pytest

from alasio.base.image.color import color_similarity_2d
from tests.base.image.color_helper import BRANCH_PIXELS, color_blocks, diff_ladder

pytestmark = [
    pytest.mark.parallel,
]

# Colors of a different distance to the corners of the color cube
COLORS = [
    pytest.param((128, 100, 200), id='mid'),
    pytest.param((0, 0, 0), id='black'),
    pytest.param((255, 255, 255), id='white'),
    pytest.param((10, 245, 3), id='spread'),
]


class TestColorSimilarity2d:
    """color_similarity_2d() must be the tolerance of the module in every pixel"""

    @staticmethod
    def reference(image, color):
        """
        The unoptimized tolerance from the comments of the module.

        Args:
            image (np.ndarray): uint8 image of shape (height, width, 3)
            color (tuple): (r, g, b)

        Returns:
            np.ndarray: uint8, 255 for an exact match
        """
        r, g, b = cv2.split(cv2.subtract(image, (*color, 0)))
        positive = cv2.max(cv2.max(r, g), b)
        r, g, b = cv2.split(cv2.subtract((*color, 0), image))
        negative = cv2.max(cv2.max(r, g), b)
        return cv2.subtract(255, cv2.add(positive, negative))

    @pytest.mark.parametrize('color', COLORS)
    def test_every_color(self, rgb888_image, color):
        """The per-channel path over an image holding every RGB888 color"""
        result = color_similarity_2d(rgb888_image, color)
        assert result.shape == rgb888_image.shape[:2]
        assert result.dtype == np.uint8
        np.testing.assert_array_equal(result, self.reference(rgb888_image, color))

    def test_small_branch(self, rgb888_image):
        """
        The 3 channel path, over the same colors in blocks below 30000 pixels.

        The blocks together cover the image, so this is the whole color space
        again, on the other implementation.
        """
        color = (128, 100, 200)
        for index, block in enumerate(color_blocks(rgb888_image)):
            result = color_similarity_2d(block, color)
            reference = self.reference(block, color)
            np.testing.assert_array_equal(result, reference, err_msg=f'block {index}')

    @pytest.mark.parametrize(
        'rows',
        [pytest.param(1, id='small'), pytest.param(118, id='large')],
    )
    def test_diff_ladder(self, rows):
        """A tolerance of i gives 255 - i, for every i of the uint8 range"""
        ladder = diff_ladder(rows)
        result = color_similarity_2d(ladder, (0, 0, 0))
        expected = np.tile((255 - np.arange(256)).astype(np.uint8), (rows, 1))
        np.testing.assert_array_equal(result, expected)

    def test_exact_match_is_255(self):
        """A pixel of the color itself is a perfect match"""
        image = np.full((4, 4, 3), (1, 2, 3), dtype=np.uint8)
        np.testing.assert_array_equal(color_similarity_2d(image, (1, 2, 3)), np.full((4, 4), 255))

    def test_opposite_corner_is_zero(self):
        """Black against white is the largest difference there is"""
        image = np.zeros((4, 4, 3), dtype=np.uint8)
        np.testing.assert_array_equal(color_similarity_2d(image, (255, 255, 255)), np.zeros((4, 4)))

    @pytest.mark.parametrize('width', [BRANCH_PIXELS - 1, BRANCH_PIXELS], ids=['small', 'large'])
    def test_branch_boundary(self, width):
        """The pixel count that selects the branch does not change the answer"""
        image = np.zeros((1, width, 3), dtype=np.uint8)
        image[0, :, 0] = np.arange(width) % 256
        image[0, :, 1] = np.arange(width) % 251
        image[0, :, 2] = np.arange(width) % 241
        result = color_similarity_2d(image, (128, 100, 200))
        np.testing.assert_array_equal(result, self.reference(image, (128, 100, 200)))
