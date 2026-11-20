"""
Tests of the image file functions on the in-memory filesystem.

The gif loader and the non ascii paths are covered on the real disk instead,
see test_imfile_real.py: cv2.VideoCapture reads the disk itself, and opencv
resolves a path with the encoding of its build.
"""
import os

import cv2
import numpy as np
import pytest

import alasio.base.image.imfile as imfile
from alasio.base.image.imfile import (
    ImageBroken, ImageNotSupported, crop, cvt_color_decode, cvt_color_encode, image_channel, image_copy, image_decode,
    image_encode, image_fixup, image_fixup_any, image_load, image_save, image_shape, image_size
)
from alasio.ext.path.atomic import atomic_read_bytes
from alasio.testing.filesystem import fs  # noqa: F401

pytestmark = [
    pytest.mark.parallel,
]

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


class TestCropSingleChannelAxis:
    """Tests for cropping a grayscale image with an explicit channel axis"""

    @pytest.mark.parametrize(
        "area",
        [
            (3, 2, 15, 10),  # inside the image
            (-2, -3, 8, 6),  # beyond the top-left corner
            (23, 2, 26, 10),  # fully outside the right edge
        ],
        ids=["inside", "border", "outside"],
    )
    def test_shape_preserved(self, area):
        """Cropping must not drop the explicit channel axis of a single channel image"""
        image = make_image(1)[:, :, np.newaxis]
        crop_img = crop(image, area)
        assert crop_img.shape == (area[3] - area[1], area[2] - area[0], 1)
        np.testing.assert_array_equal(crop_img[:, :, 0], crop(image[:, :, 0], area))


class TestCropDtype:
    """Tests for the dtype of the crop result"""

    @pytest.mark.parametrize("dtype", [np.uint8, np.uint16, np.float32])
    @pytest.mark.parametrize(
        "area",
        [(3, 2, 15, 10), (-2, -3, 8, 6), (23, 2, 26, 10)],
        ids=["inside", "border", "outside"],
    )
    def test_dtype_preserved(self, dtype, area):
        """The crop result keeps the dtype of the source image"""
        image = np.arange(IMAGE_H * IMAGE_W, dtype=dtype).reshape(IMAGE_H, IMAGE_W)
        crop_img = crop(image, area)
        assert crop_img.dtype == dtype
        assert crop_img.shape == (area[3] - area[1], area[2] - area[0])


class TestCropFloatArea:
    """Tests for a crop area with fractional coordinates"""

    def test_area_rounded(self):
        """Crop coordinates are rounded before cropping the image"""
        image = make_image(1)
        crop_img = crop(image, (3.4, 2.6, 15.5, 9.5))
        # round(3.4) = 3, round(2.6) = 3, round(15.5) = 16, round(9.5) = 10
        assert_crop_result(image, (3, 3, 16, 10), crop_img)


class TestCropZeroSize:
    """Tests for a crop area with zero width or height"""

    @pytest.mark.parametrize(
        "area",
        [
            (3, 2, 3, 10),  # zero width inside the image
            (3, 2, 15, 2),  # zero height inside the image
            (3, 2, 3, 2),  # zero width and height
            (25, 2, 25, 8),  # zero width outside the image
        ],
        ids=["zero_width", "zero_height", "zero_size", "zero_width_outside"],
    )
    def test_zero_size(self, area):
        """A zero size crop returns an empty image of the requested size"""
        image = make_image(3)
        crop_img = crop(image, area)
        assert crop_img.shape == (area[3] - area[1], area[2] - area[0], 3)
        assert_crop_result(image, area, crop_img)


class TestCropInvalidArea:
    """Tests for a crop area with reversed coordinates"""

    @pytest.mark.parametrize(
        "area",
        [
            (10, 2, 5, 8),  # right less than left, inside the image
            (3, 8, 5, 2),  # lower less than upper, inside the image
            (30, 2, 25, 8),  # right less than left, outside the image
            (3, -8, 5, -12),  # lower less than upper, outside the image
        ],
        ids=["left_inside", "top_inside", "left_outside", "top_outside"],
    )
    def test_reversed_area_raises(self, area):
        """Reversed coordinates are refused like pillow instead of returning an empty image"""
        with pytest.raises(ValueError):
            crop(make_image(1), area)

    def test_reversed_area_raises_when_not_copying(self):
        """Reversed coordinates are refused for copy=False too"""
        with pytest.raises(ValueError):
            crop(make_image(1), (10, 2, 5, 8), copy=False)


class TestCropFullImage:
    """Tests for cropping the whole image"""

    @pytest.mark.parametrize("channel", [1, 3, 4], ids=["grayscale", "rgb", "rgba"])
    def test_full_image_copy_false_shares_memory(self, channel):
        """A crop of the whole image is a view of the source image with copy=False"""
        image = make_image(channel)
        crop_img = crop(image, (0, 0, IMAGE_W, IMAGE_H), copy=False)
        assert_crop_result(image, (0, 0, IMAGE_W, IMAGE_H), crop_img)
        crop_img[0, 0] = 255
        assert np.all(image[0, 0] == 255)


class TestImageChannel:
    """Tests for image_channel()"""

    @pytest.mark.parametrize(
        "shape, expected",
        [
            ((12, 20), 1),
            ((12, 20, 1), 1),
            ((12, 20, 3), 3),
            ((12, 20, 4), 4),
        ],
        ids=["grayscale", "grayscale_axis", "rgb", "rgba"],
    )
    def test_channel(self, shape, expected):
        """image_channel() reports 1 for grayscale and the last dimension otherwise"""
        assert image_channel(np.zeros(shape, dtype=np.uint8)) == expected


class TestImageSize:
    """Tests for image_size()"""

    @pytest.mark.parametrize(
        "shape",
        [(12, 20), (12, 20, 1), (12, 20, 3), (12, 20, 4)],
        ids=["grayscale", "grayscale_axis", "rgb", "rgba"],
    )
    def test_size(self, shape):
        """image_size() returns width and height"""
        assert image_size(np.zeros(shape, dtype=np.uint8)) == (20, 12)


class TestImageShape:
    """Tests for image_shape()"""

    @pytest.mark.parametrize(
        "shape, expected",
        [
            ((12, 20), (20, 12, 1)),
            ((12, 20, 1), (20, 12, 1)),
            ((12, 20, 3), (20, 12, 3)),
            ((12, 20, 4), (20, 12, 4)),
        ],
        ids=["grayscale", "grayscale_axis", "rgb", "rgba"],
    )
    def test_shape(self, shape, expected):
        """image_shape() returns width, height and channel"""
        assert image_shape(np.zeros(shape, dtype=np.uint8)) == expected


class TestImageCopy:
    """Tests for image_copy()"""

    @pytest.mark.parametrize("channel", [1, 3, 4], ids=["grayscale", "rgb", "rgba"])
    def test_copy(self, channel):
        """image_copy() returns an equal image that shares no memory"""
        image = make_image(channel)
        copied = image_copy(image)
        assert copied is not image
        assert not np.shares_memory(copied, image)
        np.testing.assert_array_equal(copied, image)
        copied[0, 0] = 255
        assert np.all(image[0, 0] == 0)


# BGR pixels with a different value per position and channel, to detect channel swaps
BGR_PIXELS = np.array(
    [
        [[1, 2, 3], [4, 5, 6]],
        [[7, 8, 9], [10, 11, 12]],
    ],
    dtype=np.uint8,
)
# Alpha pixels matching BGR_PIXELS
ALPHA_PIXELS = np.array([[21, 22], [23, 24]], dtype=np.uint8)
BGRA_PIXELS = np.dstack([BGR_PIXELS, ALPHA_PIXELS])
# Grayscale pixels with a different value per position
GRAY_PIXELS = np.array([[31, 32, 33], [34, 35, 36]], dtype=np.uint8)


def encode_raw(image, ext='png', encode=None):
    """
    Encode an image with OpenCV directly, in OpenCV channel order.

    Args:
        image (np.ndarray): BGR, BGRA or grayscale image
        ext (str): File extension
        encode (list): Encode params

    Returns:
        bytes: Encoded image
    """
    if encode is None:
        ok, buf = cv2.imencode(f'.{ext}', image)
    else:
        ok, buf = cv2.imencode(f'.{ext}', image, encode)
    assert ok
    return buf.tobytes()


def decode_raw(data):
    """
    Decode image bytes with OpenCV directly, keeping the alpha channel.

    Args:
        data (bytes | np.ndarray): Encoded image

    Returns:
        np.ndarray: BGR, BGRA or grayscale image
    """
    if not isinstance(data, np.ndarray):
        data = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    assert image is not None
    return image


def decode_file(path):
    """
    Decode an image file with OpenCV directly, keeping the alpha channel.

    Args:
        path (str): Image file path

    Returns:
        np.ndarray: BGR, BGRA or grayscale image
    """
    return decode_raw(atomic_read_bytes(path))


def write_encoded(fs, path, image, ext='png', encode=None):
    """
    Write an image encoded with OpenCV directly.

    Args:
        fs (FakeFilesystem): Fake filesystem of the test
        path (str): Target file path
        image (np.ndarray): BGR, BGRA or grayscale image
        ext (str): File extension
        encode (list): Encode params

    Returns:
        bytes: Content written
    """
    content = encode_raw(image, ext=ext, encode=encode)
    fs.create_file(path, contents=content)
    return content


class TestCvtColorDecode:
    """Tests for cvt_color_decode()"""

    def test_bgr_to_rgb(self):
        """A 3 channel image is converted from BGR to RGB"""
        rgb = cvt_color_decode(BGR_PIXELS.copy())
        np.testing.assert_array_equal(rgb, BGR_PIXELS[:, :, ::-1])

    def test_bgra_to_rgb(self):
        """A 4 channel image is converted from BGRA to RGB, dropping alpha"""
        rgb = cvt_color_decode(BGRA_PIXELS.copy())
        np.testing.assert_array_equal(rgb, BGRA_PIXELS[:, :, [2, 1, 0]])

    def test_grayscale_unchanged(self):
        """A grayscale image is returned unchanged"""
        gray = cvt_color_decode(GRAY_PIXELS.copy())
        np.testing.assert_array_equal(gray, GRAY_PIXELS)

    @pytest.mark.parametrize(
        "channel, expected",
        [
            (3, BGR_PIXELS[:, 1:2, ::-1]),
            (4, BGRA_PIXELS[:, 1:2, [2, 1, 0]]),
        ],
        ids=["rgb", "rgba"],
    )
    def test_area(self, channel, expected):
        """The area is cropped before the color conversion"""
        image = BGR_PIXELS if channel == 3 else BGRA_PIXELS
        decoded = cvt_color_decode(image.copy(), area=(1, 0, 2, 2))
        np.testing.assert_array_equal(decoded, expected)

    def test_grayscale_area(self):
        """The area is cropped from a grayscale image"""
        decoded = cvt_color_decode(GRAY_PIXELS.copy(), area=(1, 0, 3, 2))
        np.testing.assert_array_equal(decoded, GRAY_PIXELS[:, 1:3])

    def test_grayscale_area_keeps_channel_axis(self):
        """Cropping a grayscale image with an explicit channel axis keeps the axis"""
        image = GRAY_PIXELS[:, :, np.newaxis]
        decoded = cvt_color_decode(image.copy(), area=(-1, -1, 3, 3))
        assert decoded.shape == (4, 4, 1)

    def test_area_outside(self):
        """An area outside the image returns a black image of the area size"""
        decoded = cvt_color_decode(BGR_PIXELS.copy(), area=(3, 3, 5, 5))
        assert decoded.shape == (2, 2, 3)
        assert not decoded.any()

    @pytest.mark.parametrize("channel", [2, 5], ids=["two_channels", "five_channels"])
    def test_unsupported_channel(self, channel):
        """An image without 1, 3 or 4 channels is refused"""
        with pytest.raises(ImageNotSupported):
            cvt_color_decode(np.zeros((2, 2, channel), dtype=np.uint8))


class TestImageDecode:
    """Tests for image_decode()"""

    def test_bgr_png(self):
        """A BGR png is decoded to RGB"""
        decoded = image_decode(np.frombuffer(encode_raw(BGR_PIXELS), dtype=np.uint8))
        np.testing.assert_array_equal(decoded, BGR_PIXELS[:, :, ::-1])

    def test_bgra_png(self):
        """A BGRA png is decoded to RGB, dropping alpha"""
        decoded = image_decode(np.frombuffer(encode_raw(BGRA_PIXELS), dtype=np.uint8))
        np.testing.assert_array_equal(decoded, BGRA_PIXELS[:, :, [2, 1, 0]])

    def test_grayscale_png(self):
        """A grayscale png is decoded to a 2D grayscale image"""
        decoded = image_decode(np.frombuffer(encode_raw(GRAY_PIXELS), dtype=np.uint8))
        np.testing.assert_array_equal(decoded, GRAY_PIXELS)

    def test_area(self):
        """The area is cropped while decoding"""
        decoded = image_decode(np.frombuffer(encode_raw(BGR_PIXELS), dtype=np.uint8), area=(1, 0, 2, 2))
        np.testing.assert_array_equal(decoded, BGR_PIXELS[:, 1:2, ::-1])

    def test_broken_data(self):
        """Data that is not an image raises ImageBroken"""
        with pytest.raises(ImageBroken):
            image_decode(np.frombuffer(b'broken', dtype=np.uint8))

    @pytest.mark.parametrize(
        "data",
        [np.empty(0, dtype=np.uint8), np.empty((0, 5), dtype=np.uint8)],
        ids=["empty", "empty_rows"],
    )
    def test_empty_data(self, data):
        """An empty array raises ImageBroken instead of a cv2 error"""
        with pytest.raises(ImageBroken, match='Empty image data'):
            image_decode(data)


class TestImageLoad:
    """Tests for image_load()"""

    @pytest.mark.parametrize("channel", [1, 3, 4], ids=["grayscale", "rgb", "rgba"])
    def test_load_png(self, fs, channel):
        """A png is loaded through the file content, not cv2.imread"""
        image = make_image(channel)
        path = '/images/a.png'
        fs.create_file(path, contents=image_encode(image, ext='png').tobytes())
        loaded = image_load(path)
        if channel == 4:
            # image_load drops alpha
            np.testing.assert_array_equal(loaded, image[:, :, :3])
        else:
            np.testing.assert_array_equal(loaded, image)

    def test_load_png_area(self, fs):
        """The area is cropped while loading"""
        image = make_image(3)
        path = '/images/a.png'
        fs.create_file(path, contents=image_encode(image, ext='png').tobytes())
        loaded = image_load(path, area=(3, 2, 15, 10))
        np.testing.assert_array_equal(loaded, image[2:10, 3:15])

    def test_load_uppercase_extension(self, fs):
        """The extension of a png does not need to be lowercase"""
        image = make_image(3)
        path = '/images/A.PNG'
        fs.create_file(path, contents=image_encode(image, ext='png').tobytes())
        np.testing.assert_array_equal(image_load(path), image)

    def test_load_missing_file(self, fs):
        """A missing file raises FileNotFoundError"""
        with pytest.raises(FileNotFoundError):
            image_load('/images/missing.png')

    def test_load_empty_file(self, fs):
        """An empty file raises ImageBroken"""
        fs.create_file('/images/empty.png', contents=b'')
        with pytest.raises(ImageBroken):
            image_load('/images/empty.png')

    def test_load_broken_file(self, fs):
        """A file that is not an image raises ImageBroken"""
        fs.create_file('/images/broken.png', contents=b'broken')
        with pytest.raises(ImageBroken):
            image_load('/images/broken.png')

    @pytest.mark.parametrize("name", ['a.gif', 'a.GIF', 'a.Gif'])
    def test_gif_dispatched(self, monkeypatch, name):
        """A gif is loaded with gif_load_fframe, whatever the case of the extension"""
        calls = []

        def fake_gif_load(file, area=None):
            calls.append((file, area))
            return 'first frame'

        monkeypatch.setattr(imfile, 'gif_load_fframe', fake_gif_load)
        loaded = image_load(f'/images/{name}', area=(1, 2, 3, 4))
        assert loaded == 'first frame'
        assert calls == [(f'/images/{name}', (1, 2, 3, 4))]

    def test_gif_not_in_directory_name(self, fs):
        """A directory named .gif does not make a png be loaded as a gif"""
        image = make_image(3)
        path = '/images/a.gif.png'
        fs.create_file(path, contents=image_encode(image, ext='png').tobytes())
        np.testing.assert_array_equal(image_load(path), image)


class TestCvtColorEncode:
    """Tests for cvt_color_encode()"""

    def test_rgb_to_bgr(self):
        """A 3 channel image is converted from RGB to BGR"""
        rgb = BGR_PIXELS[:, :, ::-1].copy()
        bgr = cvt_color_encode(rgb)
        np.testing.assert_array_equal(bgr, BGR_PIXELS)

    def test_rgba_to_bgra(self):
        """A 4 channel image is converted from RGBA to BGRA"""
        rgba = BGRA_PIXELS[:, :, [2, 1, 0, 3]].copy()
        bgra = cvt_color_encode(rgba)
        np.testing.assert_array_equal(bgra, BGRA_PIXELS)

    def test_grayscale_unchanged(self):
        """A grayscale image is returned unchanged"""
        gray = GRAY_PIXELS.copy()
        np.testing.assert_array_equal(cvt_color_encode(gray), gray)

    def test_grayscale_axis_unchanged(self):
        """A grayscale image with an explicit channel axis is returned unchanged"""
        gray = GRAY_PIXELS[:, :, np.newaxis].copy()
        encoded = cvt_color_encode(gray)
        assert encoded.shape == gray.shape
        np.testing.assert_array_equal(encoded, gray)

    @pytest.mark.parametrize("channel", [2, 5], ids=["two_channels", "five_channels"])
    def test_unsupported_channel(self, channel):
        """An image without 1, 3 or 4 channels is refused"""
        with pytest.raises(ImageNotSupported):
            cvt_color_encode(np.zeros((2, 2, channel), dtype=np.uint8))


class TestImageEncode:
    """Tests for image_encode()"""

    def test_default_png(self):
        """The default extension is png and the encoded data is RGB"""
        image = make_image(3)
        data = image_encode(image)
        assert isinstance(data, np.ndarray)
        assert data.dtype == np.uint8
        assert data.ndim == 1
        np.testing.assert_array_equal(decode_raw(data), image[:, :, ::-1])
        np.testing.assert_array_equal(image_decode(data), image)

    def test_rgba_png_keeps_alpha(self):
        """A png keeps the alpha channel"""
        image = make_image(4)
        data = image_encode(image, ext='png')
        raw = decode_raw(data)
        assert raw.shape == (IMAGE_H, IMAGE_W, 4)
        np.testing.assert_array_equal(raw, image[:, :, [2, 1, 0, 3]])

    @pytest.mark.parametrize("ext", ['png', 'webp', 'tif', 'tiff'], ids=["png", "webp", "tif", "tiff"])
    def test_lossless(self, ext):
        """Lossless formats roundtrip the pixels exactly"""
        image = make_image(3)
        data = image_encode(image, ext=ext)
        np.testing.assert_array_equal(image_decode(data), image)

    @pytest.mark.parametrize("ext", ['jpg', 'jpeg', 'JPG'], ids=["jpg", "jpeg", "uppercase"])
    def test_jpeg(self, ext):
        """The jpg aliases produce a decodable RGB image"""
        image = make_image(3)
        data = image_encode(image, ext=ext)
        decoded = image_decode(data)
        assert decoded.shape == image.shape
        assert decoded.dtype == np.uint8

    def test_uppercase_png(self):
        """An uppercase extension is accepted"""
        image = make_image(3)
        data = image_encode(image, ext='PNG')
        np.testing.assert_array_equal(image_decode(data), image)

    def test_encode_params(self):
        """Encode params are passed to OpenCV"""
        image = make_image(3)
        uncompressed = image_encode(image, ext='png', encode=[cv2.IMWRITE_PNG_COMPRESSION, 0])
        compressed = image_encode(image, ext='png', encode=[cv2.IMWRITE_PNG_COMPRESSION, 9])
        assert len(uncompressed) > len(compressed)
        np.testing.assert_array_equal(image_decode(uncompressed), image)
        np.testing.assert_array_equal(image_decode(compressed), image)

    @pytest.mark.parametrize("ext", ['bmp', 'gif', 'txt', ''], ids=["bmp", "gif", "txt", "empty"])
    def test_unsupported_extension(self, ext):
        """An extension without default encode params is refused"""
        with pytest.raises(ImageNotSupported):
            image_encode(make_image(3), ext=ext)

    @pytest.mark.parametrize("channel", [2, 5], ids=["two_channels", "five_channels"])
    def test_unsupported_channel(self, channel):
        """An image without 1, 3 or 4 channels is refused"""
        with pytest.raises(ImageNotSupported):
            image_encode(np.zeros((2, 2, channel), dtype=np.uint8))


class TestImageSave:
    """Tests for image_save()"""

    @pytest.mark.parametrize("channel", [1, 3, 4], ids=["grayscale", "rgb", "rgba"])
    def test_save_png(self, fs, channel):
        """A png is written and can be loaded again"""
        image = make_image(channel)
        path = '/images/a.png'
        assert image_save(path, image) is True
        loaded = image_load(path)
        if channel == 4:
            np.testing.assert_array_equal(loaded, image[:, :, :3])
        else:
            np.testing.assert_array_equal(loaded, image)

    def test_save_creates_parent_folder(self, fs):
        """A missing parent folder is created"""
        path = '/images/deep/a.png'
        assert image_save(path, make_image(3)) is True
        assert atomic_read_bytes(path)

    def test_save_uppercase_extension(self, fs):
        """An uppercase extension is accepted"""
        image = make_image(3)
        path = '/images/A.PNG'
        assert image_save(path, image) is True
        np.testing.assert_array_equal(image_load(path), image)

    def test_save_without_extension(self, fs):
        """A file without an extension is refused"""
        with pytest.raises(ImageNotSupported):
            image_save('/images/a', make_image(3))

    def test_save_writes_by_default(self, fs):
        """Without skip_same the file is written every time"""
        image = make_image(3)
        path = '/images/a.png'
        assert image_save(path, image) is True
        assert image_save(path, image) is True

    def test_skip_same_existing(self, fs, monkeypatch):
        """skip_same does not write when the existing content is the same"""
        image = make_image(3)
        path = '/images/a.png'
        fs.create_file(path, contents=image_encode(image, ext='png').tobytes())

        def fail(*args, **kwargs):
            raise AssertionError('the file should not be written')

        monkeypatch.setattr(imfile, 'atomic_write', fail)
        assert image_save(path, image, skip_same=True) is False

    def test_skip_same_missing(self, fs):
        """skip_same writes a missing file"""
        image = make_image(3)
        path = '/images/a.png'
        assert image_save(path, image, skip_same=True) is True
        np.testing.assert_array_equal(image_load(path), image)

    def test_skip_same_changed(self, fs):
        """skip_same writes when the existing content is different"""
        image = make_image(3)
        path = '/images/a.png'
        fs.create_file(path, contents=image_encode(image * 0, ext='png').tobytes())
        assert image_save(path, image, skip_same=True) is True
        np.testing.assert_array_equal(image_load(path), image)

    def test_save_rgba_keeps_alpha(self, fs):
        """A saved png keeps the alpha channel"""
        image = make_image(4)
        path = '/images/a.png'
        assert image_save(path, image) is True
        raw = decode_raw(atomic_read_bytes(path))
        assert raw.shape == (IMAGE_H, IMAGE_W, 4)
        np.testing.assert_array_equal(raw, image[:, :, [2, 1, 0, 3]])


class TestImageFixup:
    """Tests for image_fixup()"""

    def test_fixup_png(self, fs):
        """A png is re-encoded with the best compression"""
        image = make_image(3)
        path = '/images/a.png'
        content = write_encoded(fs, path, image[:, :, ::-1], encode=[cv2.IMWRITE_PNG_COMPRESSION, 0])
        assert image_fixup(path) is True
        new_content = atomic_read_bytes(path)
        assert len(new_content) < len(content)
        assert new_content != content
        np.testing.assert_array_equal(image_load(path), image)
        assert image_fixup(path) is False

    def test_fixup_drops_alpha(self, fs):
        """A png with transparency is fixed up as RGB, the alpha channel is dropped"""
        image = make_image(4)
        path = '/images/a.png'
        write_encoded(fs, path, image[:, :, [2, 1, 0, 3]], encode=[cv2.IMWRITE_PNG_COMPRESSION, 0])
        assert image_fixup(path) is True
        raw = decode_file(path)
        assert raw.shape == (IMAGE_H, IMAGE_W, 3)
        np.testing.assert_array_equal(raw, image[:, :, [2, 1, 0]])
        # the image is not lossy, a second fixup has nothing to change
        assert image_fixup(path) is False

    def test_fixup_uppercase_extension(self, fs):
        """The extension of the file does not need to be lowercase"""
        image = make_image(3)
        path = '/images/A.PNG'
        write_encoded(fs, path, image[:, :, ::-1], encode=[cv2.IMWRITE_PNG_COMPRESSION, 0])
        assert image_fixup(path) is True
        np.testing.assert_array_equal(image_load(path), image)

    def test_fixup_jpeg_not_rewritten(self, fs):
        """A jpeg is not re-encoded because every encode would be lossy"""
        image = make_image(3)
        path = '/images/a.jpg'
        content = encode_raw(image[:, :, ::-1], ext='jpg', encode=[cv2.IMWRITE_JPEG_QUALITY, 90])
        fs.create_file(path, contents=content)
        assert image_fixup(path) is False
        assert atomic_read_bytes(path) == content

    def test_fixup_missing_file(self, fs):
        """A missing file has nothing to fixup"""
        assert image_fixup('/images/missing.png') is False

    def test_fixup_empty_file(self, fs):
        """An empty file has nothing to fixup"""
        fs.create_file('/images/empty.png', contents=b'')
        assert image_fixup('/images/empty.png') is False

    def test_fixup_broken_file(self, fs):
        """A file that cannot be decoded has nothing to fixup"""
        fs.create_file('/images/broken.png', contents=b'broken')
        assert image_fixup('/images/broken.png') is False

    def test_fixup_unsupported_image(self, fs, monkeypatch):
        """An image that image_decode does not support has nothing to fixup"""
        fs.create_file('/images/a.png', contents=b'not empty')

        def fail(*args, **kwargs):
            raise ImageNotSupported('shape=(2, 2, 2)')

        monkeypatch.setattr(imfile, 'image_decode', fail)
        assert image_fixup('/images/a.png') is False

    @pytest.mark.parametrize("ext", ['bmp', 'gif'], ids=["bmp", "gif"])
    def test_fixup_unsupported_extension(self, fs, ext):
        """An image that image_encode cannot write has nothing to fixup"""
        path = f'/images/a.{ext}'
        content = encode_raw(make_image(3)[:, :, ::-1])
        fs.create_file(path, contents=content)
        assert image_fixup(path) is False
        assert atomic_read_bytes(path) == content

    def test_fixup_need_crop_skips_full_image(self, fs):
        """need_crop=True skips an image whose content touches every edge"""
        image = make_image(3)
        path = '/images/full.png'
        content = write_encoded(fs, path, image[:, :, ::-1], encode=[cv2.IMWRITE_PNG_COMPRESSION, 0])
        assert image_fixup(path, need_crop=True) is False
        assert atomic_read_bytes(path) == content

    def test_fixup_need_crop_fixes_cropped_image(self, fs):
        """need_crop=True fixes an image whose content does not touch every edge"""
        image = np.zeros((IMAGE_H, IMAGE_W, 3), dtype=np.uint8)
        image[2:10, 3:15] = 255
        path = '/images/cropped.png'
        content = write_encoded(fs, path, image[:, :, ::-1], encode=[cv2.IMWRITE_PNG_COMPRESSION, 0])
        assert image_fixup(path, need_crop=True) is True
        assert len(atomic_read_bytes(path)) < len(content)
        np.testing.assert_array_equal(image_load(path), image)

    def test_fixup_need_crop_skips_black_image(self, fs):
        """need_crop=True skips an image without content"""
        image = np.zeros((IMAGE_H, IMAGE_W, 3), dtype=np.uint8)
        path = '/images/black.png'
        content = write_encoded(fs, path, image[:, :, ::-1], encode=[cv2.IMWRITE_PNG_COMPRESSION, 0])
        assert image_fixup(path, need_crop=True) is False
        assert atomic_read_bytes(path) == content


class TestImageFixupAny:
    """Tests for image_fixup_any()"""

    def test_single_png(self, fs):
        """A single png path is fixed up"""
        path = '/images/a.png'
        write_encoded(fs, path, make_image(3)[:, :, ::-1], encode=[cv2.IMWRITE_PNG_COMPRESSION, 0])
        assert image_fixup_any(path) == 1
        assert image_fixup_any(path) == 0

    def test_directory_recursive(self, fs):
        """A directory is searched recursively for png files"""
        write_encoded(
            fs, '/assets/a.png', make_image(3)[:, :, ::-1], encode=[cv2.IMWRITE_PNG_COMPRESSION, 0]
        )
        write_encoded(
            fs, '/assets/sub/b.png', make_image(3)[:, :, ::-1], encode=[cv2.IMWRITE_PNG_COMPRESSION, 0]
        )
        fs.create_file('/assets/sub/note.txt', contents=b'not an image')
        fs.create_file('/assets/photo.jpg', contents=b'not an image')
        assert image_fixup_any('/assets') == 2
        assert image_fixup_any('/assets') == 0

    def test_directory_without_png(self, fs):
        """A directory without png files has nothing to fixup"""
        fs.create_dir('/assets')
        fs.create_file('/assets/note.txt', contents=b'not an image')
        assert image_fixup_any('/assets') == 0

    def test_directory_named_png(self, fs):
        """A directory is detected before its .png suffix is considered"""
        path = '/assets.png'
        write_encoded(
            fs, f'{path}/a.png', make_image(3)[:, :, ::-1], encode=[cv2.IMWRITE_PNG_COMPRESSION, 0]
        )
        assert image_fixup_any(path) == 1

    def test_directory_uppercase_png(self, fs):
        """An uppercase png extension is found in a directory too"""
        write_encoded(
            fs, '/assets/A.PNG', make_image(3)[:, :, ::-1], encode=[cv2.IMWRITE_PNG_COMPRESSION, 0]
        )
        assert image_fixup_any('/assets') == 1

    def test_non_png_file(self, fs):
        """A non-png image path is fixed up anyway"""
        path = '/images/a.tiff'
        write_encoded(
            fs,
            path,
            make_image(3)[:, :, ::-1],
            ext='tiff',
            encode=[cv2.IMWRITE_TIFF_COMPRESSION, 1],
        )
        assert image_fixup_any(path) == 1
        assert image_fixup_any(path) == 0

    def test_missing_path(self, fs):
        """A missing path has nothing to fixup"""
        assert image_fixup_any('/images/missing.png') == 0
        assert image_fixup_any('/images/missing') == 0


class TestGifOpenError:
    """
    Tests for the classification of a gif that cv2.VideoCapture failed to open.

    VideoCapture answers isOpened() False both for a path that is not there and
    for a path that is not a decodable gif, so the class of the error comes
    from the disk. A real gif file never reaches the two error branches, so the
    answers of os.stat are given here directly.
    """

    @pytest.mark.parametrize(
        "error",
        [
            FileNotFoundError(2, 'No such file or directory'),
            NotADirectoryError(20, 'Not a directory'),
        ],
        ids=["missing", "not_a_directory"],
    )
    def test_path_not_there(self, monkeypatch, error):
        """A path that is not there is a FileNotFoundError"""

        def stat(file):
            raise error

        with monkeypatch.context() as patch:
            patch.setattr(os, 'stat', stat)
            caught = imfile._gif_open_error('/images/a.gif')

        assert isinstance(caught, FileNotFoundError)
        assert str(caught) == 'No such GIF file: /images/a.gif'

    @pytest.mark.parametrize(
        "error, expected",
        [
            (PermissionError(13, 'Permission denied'),
             '[Errno 13] Cannot read GIF file: /images/a.gif, Permission denied'),
            (OSError(36, 'File name too long'),
             '[Errno 36] Cannot read GIF file: /images/a.gif, File name too long'),
        ],
        ids=["permission", "too_long"],
    )
    def test_path_not_readable(self, monkeypatch, error, expected):
        """A path the disk refuses to report is an OSError with a text of its own"""

        def stat(file):
            raise error

        with monkeypatch.context() as patch:
            patch.setattr(os, 'stat', stat)
            caught = imfile._gif_open_error('/images/a.gif')

        assert not isinstance(caught, FileNotFoundError)
        # OSError builds the subclass of the errno, so a permission stays one
        assert type(caught) is type(error)
        assert caught.errno == error.errno
        assert str(caught) == expected

    def test_decode_failure(self, monkeypatch):
        """A path that is on the disk was refused by the decoder"""

        def stat(file):
            return None

        with monkeypatch.context() as patch:
            patch.setattr(os, 'stat', stat)
            caught = imfile._gif_open_error('/images/a.gif')

        assert type(caught) is ImageBroken
        assert str(caught) == 'Failed to decode GIF file: /images/a.gif'

    def test_raised_by_the_loader(self, monkeypatch):
        """The loader raises the error of the classification"""

        class NotOpened:
            def isOpened(self):
                return False

            def release(self):
                pass

        monkeypatch.setattr(cv2, 'VideoCapture', lambda file: NotOpened())
        with pytest.raises(FileNotFoundError, match='No such GIF file: /images/missing.gif'):
            imfile.gif_load_fframe('/images/missing.gif')
        with pytest.raises(FileNotFoundError, match='No such GIF file: /images/missing.gif'):
            imfile.gif_load('/images/missing.gif')


class TestOpenCvPathNotUsed:
    """
    OpenCV resolves an image path with the encoding of its build, so cv2.imread
    and cv2.imwrite fail on a non ascii path of Windows. The module never uses
    them: it reads the file content into numpy and decodes it with
    cv2.imdecode, and it encodes with cv2.imencode then writes the bytes with
    the file io of the framework. The platform itself is covered by
    test_imfile_real.py, this test holds the rule on every run.
    """

    def test_cv2_path_functions_not_used(self, fs, monkeypatch):
        """The module never passes a path to cv2.imread or cv2.imwrite"""

        def fail(*args, **kwargs):
            raise AssertionError('an image path must not be given to opencv')

        monkeypatch.setattr(cv2, 'imread', fail)
        monkeypatch.setattr(cv2, 'imwrite', fail)
        path = '/素材/图标.png'
        image = make_image(3)
        assert image_save(path, image) is True
        np.testing.assert_array_equal(image_load(path), image)
        # the file was written with the default encode params, nothing to fixup
        assert image_fixup(path) is False
