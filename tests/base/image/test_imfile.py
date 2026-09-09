import numpy as np
import pytest

from alasio.base.image.imfile import crop

# Test image is a gradient with unique uint8 pixels (IMAGE_W * IMAGE_H < 256),
# pixel value at (y, x) is y * IMAGE_W + x, so max value is IMAGE_W * IMAGE_H - 1
IMAGE_W = 20
IMAGE_H = 12


def make_image(channel):
    """
    Make a deterministic gradient test image.

    Args:
        channel (int): 1 for grayscale, 3 for RGB, 4 for RGBA

    Returns:
        np.ndarray: uint8 image of shape (IMAGE_H, IMAGE_W) for grayscale,
            or (IMAGE_H, IMAGE_W, channel) otherwise
    """
    base = np.arange(IMAGE_H * IMAGE_W, dtype=np.uint8).reshape(IMAGE_H, IMAGE_W)
    if channel == 1:
        return base
    else:
        # Repeat the same gradient on every channel
        return np.repeat(base[:, :, None], channel, axis=2)


def crop_expected(image, area):
    """
    Expected crop result by spec, crop like pillow:
    the result has the size of the crop area, filled with source pixels
    where the area overlaps the image, black (0) elsewhere.

    Args:
        image (np.ndarray): Source image
        area (tuple): Crop area, (x1, y1, x2, y2)

    Returns:
        np.ndarray: Expected crop result
    """
    x1, y1, x2, y2 = area
    height, width = y2 - y1, x2 - x1
    if image.ndim == 2:
        expected = np.zeros((height, width), dtype=image.dtype)
    else:
        expected = np.zeros((height, width, image.shape[2]), dtype=image.dtype)
    # Region of the crop area that overlaps the image
    y_top, y_bottom = max(y1, 0), min(y2, image.shape[0])
    x_left, x_right = max(x1, 0), min(x2, image.shape[1])
    if y_top < y_bottom and x_left < x_right:
        # Result pixel (row, col) shows source pixel (y1 + row, x1 + col)
        rows = slice(y_top - y1, y_bottom - y1)
        cols = slice(x_left - x1, x_right - x1)
        expected[rows, cols] = image[y_top:y_bottom, x_left:x_right]
    return expected


def assert_crop_result(image, area, crop_img):
    """
    Assert that crop_img equals the expected crop result, comparing
    the whole image matrix at once.

    Args:
        image (np.ndarray): Source image
        area (tuple): Crop area, (x1, y1, x2, y2)
        crop_img (np.ndarray): crop() result

    Raises:
        AssertionError: If crop_img is not as expected
    """
    assert crop_img.dtype == image.dtype
    np.testing.assert_array_equal(crop_img, crop_expected(image, area))


class TestCropCenter:
    """Tests for crop() with the crop area fully inside the image"""

    @pytest.mark.parametrize("channel", [1, 3, 4], ids=["grayscale", "rgb", "rgba"])
    def test_crop_center(self, channel):
        """Center crop should keep the source pixels inside the area unchanged"""
        image = make_image(channel)
        area = (3, 2, 15, 10)
        crop_img = crop(image, area)
        assert_crop_result(image, area, crop_img)


class TestCropBeyondEdge:
    """
    Tests for crop() with the crop area sticking out beyond one of the 4 edges,
    the result should pad black on the side beyond the image
    """

    @pytest.mark.parametrize("channel", [1, 3, 4], ids=["grayscale", "rgb", "rgba"])
    @pytest.mark.parametrize(
        "area",
        [
            (3, -2, 15, 5),  # beyond the top edge
            (3, 9, 15, 17),  # beyond the bottom edge
            (-3, 2, 15, 10),  # beyond the left edge
            (3, 2, 24, 10),  # beyond the right edge
        ],
        ids=["top", "bottom", "left", "right"],
    )
    def test_crop_beyond_edge(self, channel, area):
        """Crop should pad black where the area goes beyond the image"""
        image = make_image(channel)
        assert_crop_result(image, area, crop(image, area))


class TestCropBeyondCorner:
    """
    Tests for crop() with the crop area sticking out beyond two adjacent edges
    (the 4 corners), the result should pad black on both sides
    """

    @pytest.mark.parametrize("channel", [1, 3, 4], ids=["grayscale", "rgb", "rgba"])
    @pytest.mark.parametrize(
        "area",
        [
            (-2, -3, 8, 6),  # beyond the top-left corner
            (14, -3, 26, 6),  # beyond the top-right corner
            (-2, 8, 8, 16),  # beyond the bottom-left corner
            (14, 8, 26, 16),  # beyond the bottom-right corner
        ],
        ids=["top_left", "top_right", "bottom_left", "bottom_right"],
    )
    def test_crop_beyond_corner(self, channel, area):
        """Crop should pad black where the area goes beyond the image"""
        image = make_image(channel)
        assert_crop_result(image, area, crop(image, area))


class TestCropOutOfImage:
    """
    Tests for crop() with the crop area having no intersection with the image,
    fully beyond one of the 4 edges or one of the 4 corners, or exactly
    touching the image boundary, the result should be an all-black image
    of the crop area size
    """

    @pytest.mark.parametrize("channel", [1, 3, 4], ids=["grayscale", "rgb", "rgba"])
    @pytest.mark.parametrize(
        "area",
        [
            (3, -8, 15, -2),  # fully above the top edge
            (3, 14, 15, 20),  # fully below the bottom edge
            (-8, 2, -2, 10),  # fully left of the left edge
            (23, 2, 26, 10),  # fully right of the right edge
            (-8, -6, -2, -1),  # fully beyond the top-left corner
            (23, -6, 26, -1),  # fully beyond the top-right corner
            (-8, 15, -2, 18),  # fully beyond the bottom-left corner
            (23, 15, 26, 18),  # fully beyond the bottom-right corner
            (3, -8, 15, 0),  # touching the top edge, y2 == 0
            (3, 12, 15, 20),  # touching the bottom edge, y1 == IMAGE_H
            (-8, 2, 0, 10),  # touching the left edge, x2 == 0
            (20, 2, 24, 10),  # touching the right edge, x1 == IMAGE_W
            (-8, -8, 0, 0),  # touching the top-left corner
            (20, -8, 24, 0),  # touching the top-right corner
            (-8, 12, 0, 16),  # touching the bottom-left corner
            (20, 12, 24, 16),  # touching the bottom-right corner
        ],
        ids=[
            "top",
            "bottom",
            "left",
            "right",
            "top_left",
            "top_right",
            "bottom_left",
            "bottom_right",
            "top_touch",
            "bottom_touch",
            "left_touch",
            "right_touch",
            "top_left_touch",
            "top_right_touch",
            "bottom_left_touch",
            "bottom_right_touch",
        ],
    )
    def test_crop_out_of_image(self, channel, area):
        """Crop fully outside the image should return an all-black image"""
        image = make_image(channel)
        crop_img = crop(image, area)
        assert_crop_result(image, area, crop_img)
        # Nothing of the source image falls into the crop area
        assert crop_img.max() == 0


class TestCropCopy:
    """Tests for the copy parameter of crop()"""

    @pytest.mark.parametrize("channel", [1, 3, 4], ids=["grayscale", "rgb", "rgba"])
    def test_copy_true_new_matrix(self, channel):
        """
        copy=True should return a new image matrix,
        modifying the crop result must not change the source image
        """
        image = make_image(channel)
        area = (3, 2, 15, 10)
        crop_img = crop(image, area, copy=True)
        assert_crop_result(image, area, crop_img)
        # crop result pixel (0, 0) is source pixel (2, 3)
        crop_img[0, 0] = 255
        assert image.max() == IMAGE_W * IMAGE_H - 1
        assert np.all(image[2, 3] == 2 * IMAGE_W + 3)

    @pytest.mark.parametrize("channel", [1, 3, 4], ids=["grayscale", "rgb", "rgba"])
    def test_copy_false_shares_memory(self, channel):
        """
        copy=False should not copy the source pixels when the crop area
        is fully inside the image, the crop result is a view of the source image
        """
        image = make_image(channel)
        area = (3, 2, 15, 10)
        crop_img = crop(image, area, copy=False)
        assert_crop_result(image, area, crop_img)
        # crop result pixel (0, 0) is source pixel (2, 3)
        crop_img[0, 0] = 255
        assert image.max() == 255
        assert np.all(image[2, 3] == 255)

    @pytest.mark.parametrize("channel", [1, 3, 4], ids=["grayscale", "rgb", "rgba"])
    def test_copy_false_with_border_new_matrix(self, channel):
        """
        When the crop area goes beyond an image edge, even copy=False must
        return a new image matrix, because adding black borders
        cannot share the source image memory
        """
        image = make_image(channel)
        area = (3, 2, 24, 10)  # beyond the right edge
        crop_img = crop(image, area, copy=False)
        assert_crop_result(image, area, crop_img)
        # crop result pixel (0, 0) is source pixel (2, 3)
        crop_img[0, 0] = 255
        assert image.max() == IMAGE_W * IMAGE_H - 1
        assert np.all(image[2, 3] == 2 * IMAGE_W + 3)
