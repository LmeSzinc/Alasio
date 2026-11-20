"""
Helpers shared by the tests of alasio.base.image.color.

Every 2D function of the module branches on the pixel count of its image
(30000 pixels), and what the function promises is a statement about every
RGB888 color. The image built here holds each of the 16.7M colors exactly
once, so one image proves one branch over the whole input space, and
color_blocks() cuts it into pieces that take the small branch.
"""
import cv2
import numpy as np

# Side of the image holding every RGB888 color: 4096 * 4096 == 256 ** 3
RGB888_SIZE = 4096
# Side of the image holding every RGB565 value: 256 * 256 == 65536
RGB565_SIZE = 256
# Pixel count where the 2D functions switch to the per-channel path
BRANCH_PIXELS = 30000
# Side of a block of the small branch, 173 * 173 == 29929 < 30000
BLOCK_SIZE = 173


def create_rgb888():
    """
    Create an image holding every RGB888 color exactly once.

    Returns:
        np.ndarray: uint8 image of shape (4096, 4096, 3)
    """
    size = RGB888_SIZE
    channel_0 = np.tile(np.arange(256, dtype=np.uint8), 65536).reshape(size, size)
    channel_1 = np.tile(np.repeat(np.arange(256, dtype=np.uint8), 256), 256).reshape(size, size)
    channel_2 = np.repeat(np.arange(256, dtype=np.uint8), 65536).reshape(size, size)
    return cv2.merge([channel_0, channel_1, channel_2])


def create_rgb565():
    """
    Create an image holding every RGB565 value exactly once.

    Returns:
        np.ndarray: uint16 image of shape (256, 256)
    """
    return np.arange(65536, dtype=np.uint16).reshape(RGB565_SIZE, RGB565_SIZE)


def color_blocks(image, size=BLOCK_SIZE):
    """
    Yield non-overlapping size x size blocks covering the whole image.

    Args:
        image (np.ndarray): Source image
        size (int): Block side, 173 by default, so a block holds 29929 pixels
            and takes the small branch of the functions under test

    Yields:
        np.ndarray: Image block
    """
    for y in range(0, image.shape[0], size):
        for x in range(0, image.shape[1], size):
            yield image[y:y + size, x:x + size]


def diff_ladder(rows=1):
    """
    Build an image whose pixel i has the tolerance i to black.

    Pixel (i, i, i) differs from (0, 0, 0) by i and from (255, 255, 255) by
    255 - i, so one row holds the tolerance of every difference from 0 to 255.

    Args:
        rows (int): Row count. 1 takes the small branch (256 pixels), 118 takes
            the per-channel branch (30208 pixels)

    Returns:
        np.ndarray: uint8 image of shape (rows, 256, 3)
    """
    values = np.arange(256, dtype=np.uint8)
    row = np.repeat(values[:, None], 3, axis=1)
    return np.repeat(row[None, :, :], rows, axis=0)
