"""
Fixtures shared by the tests of alasio.base.image.color.

A worker process builds the exhaustive images once and every module it runs
reads them, instead of a copy per module. The images are read-only in practice:
a test that needs to write to one takes a copy first.
"""
import pytest

from tests.base.image.color_helper import create_rgb565, create_rgb888


@pytest.fixture(scope='session')
def rgb888_image():
    """
    An image holding every RGB888 color exactly once.

    Returns:
        np.ndarray: uint8 image of shape (4096, 4096, 3)
    """
    return create_rgb888()


@pytest.fixture(scope='session')
def rgb565_image():
    """
    An image holding every RGB565 value exactly once.

    Returns:
        np.ndarray: uint16 image of shape (256, 256)
    """
    return create_rgb565()
