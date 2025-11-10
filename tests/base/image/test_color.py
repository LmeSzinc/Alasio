import numpy as np
import pytest

from alasio.base.image.color import rgb2luma
from alasio.base.image.testing import create_rgb888


class TestColor:
    """Test suite for rgb2luma functions"""

    @pytest.fixture(scope="class")
    def rgb888_image(self):
        """
        Fixture to create the RGB888 test image once for all tests in this class.
        This contains all possible RGB888 colors in a 4096x4096 image.
        """
        return create_rgb888()

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
