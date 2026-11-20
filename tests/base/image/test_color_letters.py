"""
Tests of extract_letters() of alasio.base.image.color.

The function turns an image into the distance of every pixel to the letter
color, scaled so that a difference of threshold reaches 255. It has a shortcut
for a white letter, which is the same formula written on the inverted image,
and the two implementations the other 2D functions of the module have, chosen
on the pixel count.
"""
import cv2
import numpy as np
import pytest

from alasio.base.image.color import extract_letters
from tests.base.image.color_helper import BRANCH_PIXELS, color_blocks, diff_ladder

pytestmark = [
    pytest.mark.parallel,
]

LETTERS = [
    pytest.param((255, 255, 255), id='white_tuple'),
    pytest.param([255, 255, 255], id='white_list'),
    pytest.param((100, 150, 200), id='mid'),
    pytest.param((0, 0, 0), id='black'),
]

THRESHOLDS = [
    pytest.param(1, id='one'),
    pytest.param(2, id='two'),
    pytest.param(64, id='64'),
    pytest.param(128, id='128'),
    pytest.param(255, id='unscaled'),
]


class TestExtractLetters:
    """extract_letters() must be the scaled distance of the module"""

    @staticmethod
    def reference(image, letter, threshold):
        """
        The unoptimized distance from the comments of the module.

        Args:
            image (np.ndarray): uint8 image of shape (height, width, 3)
            letter (tuple): (r, g, b)
            threshold (int):

        Returns:
            np.ndarray: uint8, 0 for a pixel of the letter color
        """
        r, g, b = cv2.split(cv2.subtract(image, (*letter, 0)))
        positive = cv2.max(cv2.max(r, g), b)
        r, g, b = cv2.split(cv2.subtract((*letter, 0), image))
        negative = cv2.max(cv2.max(r, g), b)
        return cv2.multiply(cv2.add(positive, negative), 255.0 / threshold)

    @pytest.mark.parametrize('letter', LETTERS)
    def test_every_color(self, rgb888_image, letter):
        """The per-channel path over an image holding every RGB888 color"""
        result = extract_letters(rgb888_image, letter=letter, threshold=128)
        assert result.shape == rgb888_image.shape[:2]
        assert result.dtype == np.uint8
        np.testing.assert_array_equal(result, self.reference(rgb888_image, tuple(letter), 128))

    def test_small_branch(self, rgb888_image):
        """
        The 3 channel path, over the same colors in blocks below 30000 pixels.

        The blocks together cover the image, so this is the whole color space
        again, on the other implementation.
        """
        letter = (100, 150, 200)
        for index, block in enumerate(color_blocks(rgb888_image)):
            result = extract_letters(block, letter=letter, threshold=128)
            reference = self.reference(block, letter, 128)
            np.testing.assert_array_equal(result, reference, err_msg=f'block {index}')

    @pytest.mark.parametrize('threshold', THRESHOLDS)
    @pytest.mark.parametrize(
        'rows',
        [pytest.param(1, id='small'), pytest.param(118, id='large')],
    )
    def test_threshold_ladder(self, rows, threshold):
        """A difference of i from black is scaled to i * 255 / threshold"""
        ladder = diff_ladder(rows)
        result = extract_letters(ladder, letter=(0, 0, 0), threshold=threshold)
        values = np.arange(256, dtype=np.float64) * (255.0 / threshold)
        expected = np.clip(np.rint(values), 0, 255).astype(np.uint8)
        np.testing.assert_array_equal(result[0], expected)

    @pytest.mark.parametrize('threshold', THRESHOLDS)
    def test_white_letter_ladder(self, threshold):
        """The white shortcut is the general formula on the inverted image"""
        ladder = diff_ladder(118)
        result = extract_letters(ladder, letter=(255, 255, 255), threshold=threshold)
        # a pixel (i, i, i) is 255 - i away from white
        values = (255 - np.arange(256, dtype=np.float64)) * (255.0 / threshold)
        expected = np.clip(np.rint(values), 0, 255).astype(np.uint8)
        np.testing.assert_array_equal(result[0], expected)

    def test_letter_is_black(self):
        """A pixel of the letter color has no distance to it"""
        image = np.full((4, 4, 3), (100, 150, 200), dtype=np.uint8)
        np.testing.assert_array_equal(
            extract_letters(image, letter=(100, 150, 200), threshold=128), np.zeros((4, 4))
        )

    def test_far_corner_saturates(self):
        """A distance past the scale saturates at 255 instead of wrapping"""
        image = np.full((4, 4, 3), (0, 0, 0), dtype=np.uint8)
        np.testing.assert_array_equal(
            extract_letters(image, letter=(255, 255, 255), threshold=1), np.full((4, 4), 255)
        )

    @pytest.mark.parametrize('width', [BRANCH_PIXELS - 1, BRANCH_PIXELS], ids=['small', 'large'])
    def test_branch_boundary(self, width):
        """The pixel count that selects the branch does not change the answer"""
        image = np.zeros((1, width, 3), dtype=np.uint8)
        image[0, :, 0] = np.arange(width) % 256
        image[0, :, 1] = np.arange(width) % 251
        image[0, :, 2] = np.arange(width) % 241
        letter = (100, 150, 200)
        result = extract_letters(image, letter=letter, threshold=128)
        np.testing.assert_array_equal(result, self.reference(image, letter, 128))
