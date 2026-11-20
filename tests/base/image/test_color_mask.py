"""
Tests of color_mask() of alasio.base.image.color.

The function is the tolerance of color_similarity_2d() cut at a threshold, 255
for a pixel within the threshold and 0 for the rest, and it chooses between the
same two implementations on the pixel count of the image. The reference below
computes the tolerance on its own, so a mistake shared with
color_similarity_2d() would not cancel out.
"""
import cv2
import numpy as np
import pytest

from alasio.base.image.color import color_mask
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

THRESHOLDS = [
    pytest.param(0, id='exact'),
    pytest.param(1, id='one'),
    pytest.param(30, id='default'),
    pytest.param(128, id='half'),
    pytest.param(254, id='almost'),
    pytest.param(255, id='all'),
]


class TestColorMask:
    """color_mask() must be the thresholded tolerance of the module"""

    @staticmethod
    def reference(image, color, threshold):
        """
        The unoptimized tolerance of the module, thresholded.

        Args:
            image (np.ndarray): uint8 image of shape (height, width, 3)
            color (tuple): (r, g, b)
            threshold (int): Pixels with a tolerance lesser or equal are matched

        Returns:
            np.ndarray: uint8, 255 for a matched pixel and 0 for the rest
        """
        r, g, b = cv2.split(cv2.subtract(image, (*color, 0)))
        positive = cv2.max(cv2.max(r, g), b)
        r, g, b = cv2.split(cv2.subtract((*color, 0), image))
        negative = cv2.max(cv2.max(r, g), b)
        tolerance = cv2.add(positive, negative)
        return np.where(tolerance <= threshold, 255, 0).astype(np.uint8)

    @pytest.mark.parametrize('color', COLORS)
    def test_every_color(self, rgb888_image, color):
        """The per-channel path over an image holding every RGB888 color"""
        result = color_mask(rgb888_image, color)
        assert result.shape == rgb888_image.shape[:2]
        assert result.dtype == np.uint8
        np.testing.assert_array_equal(result, self.reference(rgb888_image, color, 30))

    def test_small_branch(self, rgb888_image):
        """
        The 3 channel path, over the same colors in blocks below 30000 pixels.

        The blocks together cover the image, so this is the whole color space
        again, on the other implementation.
        """
        color = (128, 100, 200)
        for index, block in enumerate(color_blocks(rgb888_image)):
            result = color_mask(block, color, 30)
            reference = self.reference(block, color, 30)
            np.testing.assert_array_equal(result, reference, err_msg=f'block {index}')

    @pytest.mark.parametrize('threshold', THRESHOLDS)
    @pytest.mark.parametrize(
        'rows',
        [pytest.param(1, id='small'), pytest.param(118, id='large')],
    )
    def test_threshold_ladder(self, rows, threshold):
        """A tolerance of i is matched when i is less than or equal to the threshold"""
        ladder = diff_ladder(rows)
        result = color_mask(ladder, (0, 0, 0), threshold)
        expected = np.tile(np.where(np.arange(256) <= threshold, 255, 0).astype(np.uint8), (rows, 1))
        np.testing.assert_array_equal(result, expected)

    def test_mask_is_binary(self, rgb888_image):
        """A mask holds 0 and 255 only"""
        values = np.unique(color_mask(rgb888_image[:64, :64], (128, 100, 200), 30))
        assert set(values.tolist()) <= {0, 255}

    @pytest.mark.parametrize('width', [BRANCH_PIXELS - 1, BRANCH_PIXELS], ids=['small', 'large'])
    def test_branch_boundary(self, width):
        """The pixel count that selects the branch does not change the answer"""
        image = np.zeros((1, width, 3), dtype=np.uint8)
        image[0, :, 0] = np.arange(width) % 256
        image[0, :, 1] = np.arange(width) % 251
        image[0, :, 2] = np.arange(width) % 241
        result = color_mask(image, (128, 100, 200), 30)
        np.testing.assert_array_equal(result, self.reference(image, (128, 100, 200), 30))
