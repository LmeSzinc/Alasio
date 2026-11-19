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
    channel = image_channel(image)
    # convert to grayscale
    if channel == 3:
        # RGB
        mask = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        cv2.threshold(mask, threshold, 255, cv2.THRESH_BINARY, dst=mask)
    elif channel == 1:
        # grayscale
        _, mask = cv2.threshold(image, threshold, 255, cv2.THRESH_BINARY)
    elif channel == 4:
        # RGBA
        mask = cv2.cvtColor(image, cv2.COLOR_RGBA2GRAY)
        cv2.threshold(mask, threshold, 255, cv2.THRESH_BINARY, dst=mask)
    else:
        raise ImageNotSupported(f'shape={image.shape}')

    # find bbox
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_y, min_x = mask.shape
    max_x = 0
    max_y = 0
    # all black
    if not contours:
        raise ImageNotSupported(f'Cannot get bbox from a pure black image')
    for contour in contours:
        # x, y, w, h
        x1, y1, x2, y2 = cv2.boundingRect(contour)
        x2 += x1
        y2 += y1
        if x1 < min_x:
            min_x = x1
        if y1 < min_y:
            min_y = y1
        if x2 > max_x:
            max_x = x2
        if y2 > max_y:
            max_y = y2
    if min_x < max_x and min_y < max_y:
        return min_x, min_y, max_x, max_y
    else:
        # This shouldn't happen
        raise ImageNotSupported(f'Empty bbox {(min_x, min_y, max_x, max_y)}')


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
    channel = image_channel(image)
    # convert to grayscale
    if channel == 3:
        # RGB
        mask = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        # threshold - 1 makes color == threshold background, as documented
        cv2.threshold(mask, threshold - 1, 255, cv2.THRESH_BINARY_INV, dst=mask)
    elif channel == 1:
        # grayscale
        _, mask = cv2.threshold(image, threshold - 1, 255, cv2.THRESH_BINARY_INV)
    elif channel == 4:
        # RGBA
        mask = cv2.cvtColor(image, cv2.COLOR_RGBA2GRAY)
        cv2.threshold(mask, threshold - 1, 255, cv2.THRESH_BINARY_INV, dst=mask)
    else:
        raise ImageNotSupported(f'shape={image.shape}')

    # find bbox
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_y, min_x = mask.shape
    max_x = 0
    max_y = 0
    # no content
    if not contours:
        raise ImageNotSupported(f'Cannot get bbox from an image without content')
    for contour in contours:
        # x, y, w, h
        x1, y1, x2, y2 = cv2.boundingRect(contour)
        x2 += x1
        y2 += y1
        if x1 < min_x:
            min_x = x1
        if y1 < min_y:
            min_y = y1
        if x2 > max_x:
            max_x = x2
        if y2 > max_y:
            max_y = y2
    if min_x < max_x and min_y < max_y:
        return min_x, min_y, max_x, max_y
    else:
        # This shouldn't happen
        raise ImageNotSupported(f'Empty bbox {(min_x, min_y, max_x, max_y)}')
