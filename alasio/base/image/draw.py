import numbers

import cv2
import numpy as np

from alasio.base.image.imfile import ImageNotSupported, image_channel, image_copy, image_size


def show_as_pillow(image):
    """
    Show image in Pillow, for debugging purpose
    """
    channel = image_channel(image)
    from PIL import Image
    if channel == 3:
        Image.fromarray(image, mode='RGB').show()
    elif channel == 1:
        Image.fromarray(image, mode='L').show()
    elif channel == 4:
        Image.fromarray(image, mode='RGBA').show()
    else:
        raise ImageNotSupported(f'shape={image.shape}')


def resize(image, size):
    """
    Resize image like pillow image.resize(), but implement in opencv.
    Nearest neighbor interpolation is used.

    Args:
        image (np.ndarray):
        size: (x, y)

    Returns:
        np.ndarray:
    """
    return cv2.resize(image, size, interpolation=cv2.INTER_NEAREST)


def resize_maxpx(image, maxpx):
    """
    Resize image to maximum width or height

    Args:
        image (np.ndarray):
        maxpx: Maximum width or height

    Returns:
        np.ndarray:
    """
    w, h = image_size(image)
    if w <= maxpx and h <= maxpx:
        # no need to resize
        return image

    if w > h:
        h = round(maxpx / w * h)
        # Keep at least 1 pixel, otherwise an extreme aspect ratio rounds to 0
        if h < 1:
            h = 1
        w = maxpx
    else:
        w = round(maxpx / h * w)
        # Keep at least 1 pixel, otherwise an extreme aspect ratio rounds to 0
        if w < 1:
            w = 1
        h = maxpx

    return cv2.resize(image, (w, h), interpolation=cv2.INTER_NEAREST)


def image_paste(image, background, origin):
    """
    Paste an image on background.
    This method does not return a value, but instead updates the array "background".

    Args:
        image (np.ndarray):
        background:
        origin: Upper-left corner, (x, y)
    """
    if not image.flags.writeable:
        image = image_copy(image)

    x, y = origin
    w, h = image_size(image)
    background[y:y + h, x:x + w] = image


def image_paint(image, area, color):
    """
    Paint the area of image to given color.
    The color is adapted to the channels of the image:
    a single number is painted as gray (opaque on an RGBA image), RGB is
    converted to luma on a grayscale image, alpha is dropped on an RGB image,
    and an opaque alpha is added on an RGBA image.

    Args:
        image (np.ndarray):
        area:
        color (int | float | tuple | list): (r, g, b), (r, g, b, a), or a single value.
            A float value is truncated to an integer

    Returns:
        np.ndarray: The painted image, which is a copy if the input is read-only

    Raises:
        ValueError: if the color can't be adapted to the image channels,
            or doesn't fit into the dtype of the image
    """
    channel = image_channel(image)
    # Numpy wraps out of range values silently on assignment, which would paint
    # an unintended color, so the color is checked before painting.
    # None means the dtype has no range to check, like a float image.
    if np.issubdtype(image.dtype, np.integer):
        info = np.iinfo(image.dtype)
        color_min, color_max = info.min, info.max
    else:
        color_min, color_max = None, None

    if isinstance(color, numbers.Real):
        # A single number is a gray color, written to every channel explicitly
        # instead of relying on the numpy broadcast.
        # An RGBA image is painted opaque.
        # nan fails the range check, and can't be painted either
        if color_min is not None and not color_min <= color <= color_max:
            raise ValueError(f'Cannot paint color={color} on an image of {image.dtype}')
        value = int(color)
        if channel == 4:
            color = (value, value, value, 255)
        else:
            color = (value,) * channel
    else:
        length = len(color)
        if length != channel:
            if channel == 1 and length in (3, 4):
                # Grayscale: drop alpha and convert RGB to luma,
                # using the formula of cv2.COLOR_RGB2GRAY
                r, g, b = color[:3]
                color = ((r * 4899 + g * 9617 + b * 1868 + 8192) >> 14,)
            elif channel == 3 and length == 4:
                # RGB: drop alpha
                color = color[:3]
            elif channel == 4 and length == 3:
                # RGBA: add an opaque alpha
                color = (*color, 255)
            else:
                raise ValueError(f'Cannot paint color={color} on an image of {channel} channels')
        if color_min is not None:
            if any(not color_min <= channel_value <= color_max for channel_value in color):
                raise ValueError(f'Cannot paint color={color} on an image of {image.dtype}')

    if not image.flags.writeable:
        image = image_copy(image)

    x1, y1, x2, y2 = area
    image[y1:y2, x1:x2] = color
    return image


def _image_grayscale(image):
    """
    Convert an image to a 2D grayscale image, the luminance of RGB and RGBA.

    Args:
        image (np.ndarray):

    Returns:
        np.ndarray: shape (height, width)

    Raises:
        ImageNotSupported: if the image has an unsupported channel count
    """
    channel = image_channel(image)
    if channel == 3:
        # RGB
        return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    elif channel == 4:
        # RGBA
        return cv2.cvtColor(image, cv2.COLOR_RGBA2GRAY)
    elif channel == 1:
        if image.ndim == 3:
            # grayscale with an explicit channel axis, shape (height, width, 1)
            return image[:, :, 0]
        return image
    else:
        raise ImageNotSupported(f'shape={image.shape}')


def _row_flags(flat, threshold, foreground):
    """
    Get the per row content flags of a 2D image view.

    Args:
        flat (np.ndarray): 2D view of the image, shape (height, width * channel)
        threshold (int):
        foreground (bool): True to test color > threshold, False for color < threshold

    Returns:
        np.ndarray: bool array of shape (height,)
    """
    if not foreground:
        return flat.min(axis=1) < threshold
    if threshold == 0 and flat.dtype == np.uint8 and flat.shape[1] % 8 == 0:
        # A 64 bit word is non zero exactly when one of its bytes is, which is
        # the same test as max > 0, but reducing 8 bytes per step vectorizes
        # better than reducing single bytes on a 1280 wide screenshot.
        return np.bitwise_or.reduce(flat.view(np.uint64), axis=1) != 0
    return flat.max(axis=1) > threshold


def _bbox_of_gray(gray, threshold, foreground, error_message):
    """
    Get the bounding box of the content pixels of a grayscale image.

    A row belongs to the bbox when any of its pixels is content, and the same
    for a column, so reducing the rows and the columns of the bbox is enough.
    The reduction is done on the grayscale image instead of a boolean mask,
    which saves a full size allocation on every call.

    Args:
        gray (np.ndarray): 2D grayscale image
        threshold (int):
        foreground (bool): True if the content is the pixels > threshold,
            False if the content is the pixels < threshold
        error_message (str): Message of the error raised without any content

    Returns:
        tuple[int, int, int, int]: area, (x1, y1, x2, y2)

    Raises:
        ImageNotSupported: if the image has no content pixel
    """
    height, width = gray.shape
    row_content = _row_flags(gray, threshold, foreground)
    # argmax() answers 0 on an all False array, so the row has to be checked
    y1 = int(row_content.argmax())
    if not row_content[y1]:
        raise ImageNotSupported(error_message)
    y2 = int(height - row_content[::-1].argmax())
    # every content pixel is inside the rows of the bbox, so the columns of
    # that band are enough and the column scan is shorter than the whole image
    band = gray[y1:y2]
    if foreground:
        col_content = band.max(axis=0) > threshold
    else:
        col_content = band.min(axis=0) < threshold
    x1 = int(col_content.argmax())
    x2 = int(width - col_content[::-1].argmax())
    return x1, y1, x2, y2


def get_bbox(image, threshold=0):
    """
    Get outbound box of the content in image
    A opencv implementation of the getbbox() in pillow

    Args:
        image (np.ndarray):
        threshold (int):
            color > threshold will be considered as content
            color <= threshold will be considered background

    Returns:
        tuple[int, int, int, int]: area

    Raises:
        ImageNotSupported: if failed to get bbox
    """
    gray = _image_grayscale(image)
    return _bbox_of_gray(gray, threshold, True, 'Cannot get bbox from a pure black image')


def get_bbox_reversed(image, threshold=255):
    """
    Get outbound box of the content in image
    A opencv implementation, the reversed version of get_bbox()

    Args:
        image (np.ndarray):
        threshold (int):
            color < threshold will be considered as content
            color >= threshold will be considered background

    Returns:
        tuple[int, int, int, int]: area

    Raises:
        ImageNotSupported: if failed to get bbox
    """
    gray = _image_grayscale(image)
    return _bbox_of_gray(gray, threshold, False, 'Cannot get bbox from an image without content')
