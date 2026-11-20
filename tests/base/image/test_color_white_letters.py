"""
Tests of extract_white_letters() of alasio.base.image.color.

The function keeps a gray pixel that is lighter than the others: it works on
the max and the min of the inverted image, so a colored pixel contributes its
minimum to the result and is discouraged. The scaling by threshold is the same
as extract_letters(), and the same two implementations are chosen on the pixel
count.
"""
import cv2
import numpy as np
import pytest

from alasio.base.image.color import extract_white_letters
from tests.base.image.color_helper import BRANCH_PIXELS, color_blocks

pytestmark = [
    pytest.mark.parallel,
]

THRESHOLDS = [
    pytest.param(1, id='one'),
    pytest.param(64, id='64'),
    pytest.param(128, id='128'),
    pytest.param(255, id='unscaled'),
]


class TestExtractWhiteLetters:
    """extract_white_letters() must be the white letter mask of the module"""

    @staticmethod
    def reference(image, threshold):
        """
        The unoptimized algorithm from the comments of the module.

        The 0.5 scaling uses cv2.convertScaleAbs() like the implementation, not
        cv2.multiply(): that is the rounding step of the function, and the two
        primitives round differently on some HALs, which would compare the
        choice of primitive rather than the optimization.

        Args:
            image (np.ndarray): uint8 image of shape (height, width, 3)
            threshold (int):

        Returns:
            np.ndarray: uint8, 0 for a white pixel
        """
        r, g, b = cv2.split(cv2.subtract((255, 255, 255, 0), image))
        minimum = cv2.min(cv2.min(r, g), b)
        maximum = cv2.max(cv2.max(r, g), b)
        maximum = cv2.convertScaleAbs(maximum, alpha=0.5)
        minimum = cv2.convertScaleAbs(minimum, alpha=0.5)
        return cv2.multiply(cv2.add(maximum, cv2.subtract(maximum, minimum)), 255.0 / threshold)

    @pytest.mark.parametrize('threshold', THRESHOLDS)
    def test_every_color(self, rgb888_image, threshold):
        """The per-channel path over an image holding every RGB888 color"""
        result = extract_white_letters(rgb888_image, threshold=threshold)
        assert result.shape == rgb888_image.shape[:2]
        assert result.dtype == np.uint8
        np.testing.assert_array_equal(result, self.reference(rgb888_image, threshold))

    def test_small_branch(self, rgb888_image):
        """
        The 3 channel path, over the same colors in blocks below 30000 pixels.

        The blocks together cover the image, so this is the whole color space
        again, on the other implementation.
        """
        for index, block in enumerate(color_blocks(rgb888_image)):
            result = extract_white_letters(block, threshold=128)
            reference = self.reference(block, 128)
            np.testing.assert_array_equal(result, reference, err_msg=f'block {index}')

    @pytest.mark.parametrize(
        'value',
        [pytest.param(0, id='black'), pytest.param(128, id='gray'), pytest.param(255, id='white')],
    )
    @pytest.mark.parametrize('threshold', [128, 255])
    def test_solid_image(self, value, threshold):
        """A solid gray image answers one value, the same in every pixel"""
        image = np.full((4, 4, 3), value, dtype=np.uint8)
        result = extract_white_letters(image, threshold=threshold)
        assert result.min() == result.max()
        np.testing.assert_array_equal(result, self.reference(image, threshold))

    def test_white_is_not_a_letter(self):
        """A white pixel is the letter itself, so it scores zero"""
        image = np.full((4, 4, 3), 255, dtype=np.uint8)
        np.testing.assert_array_equal(extract_white_letters(image, threshold=128), np.zeros((4, 4)))

    def test_color_is_not_a_letter(self):
        """A saturated color is not a white letter, whatever its brightest channel"""
        color = np.full((4, 4, 3), (0, 0, 255), dtype=np.uint8)
        # the darkest channel decides, so the blue pixel is as far from white
        # as a black one, not as close as a white one
        reference = self.reference(color, 128)
        assert reference.min() > 200
        np.testing.assert_array_equal(extract_white_letters(color, threshold=128), reference)

    @pytest.mark.parametrize('width', [BRANCH_PIXELS - 1, BRANCH_PIXELS], ids=['small', 'large'])
    def test_branch_boundary(self, width):
        """The pixel count that selects the branch does not change the answer"""
        image = np.zeros((1, width, 3), dtype=np.uint8)
        image[0, :, 0] = np.arange(width) % 256
        image[0, :, 1] = np.arange(width) % 251
        image[0, :, 2] = np.arange(width) % 241
        result = extract_white_letters(image, threshold=128)
        np.testing.assert_array_equal(result, self.reference(image, 128))
