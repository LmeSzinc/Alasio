import cv2
import numpy as np

from alasio.base.path.atomic import atomic_read_bytes, atomic_write


class ImageTruncated(Exception):
    pass


def image_copy(src):
    """
    Equivalent to image.copy() but a little bit faster

    Time cost to copy a 1280*720*3 image:
        image.copy()      0.743ms
        image_copy(image) 0.639ms
    """
    dst = np.empty_like(src)
    cv2.copyTo(src, None, dst)
    return dst


def crop(image, area, copy=True):
    """
    Crop image like pillow, when using opencv / numpy.
    Provides a black background if cropping outside of image.

    Args:
        image (np.ndarray):
        area:
        copy (bool):

    Returns:
        np.ndarray:
    """
    # map(round, area)
    x1, y1, x2, y2 = area
    x1 = round(x1)
    y1 = round(y1)
    x2 = round(x2)
    y2 = round(y2)
    # h, w = image.shape[:2]
    shape = image.shape
    h = shape[0]
    w = shape[1]
    # top, bottom, left, right
    # border = np.maximum((0 - y1, y2 - h, 0 - x1, x2 - w), 0)
    overflow = False
    if y1 >= 0:
        top = 0
        if y1 >= h:
            overflow = True
    else:
        top = -y1
    if y2 > h:
        bottom = y2 - h
    else:
        bottom = 0
        if y2 <= 0:
            overflow = True
    if x1 >= 0:
        left = 0
        if x1 >= w:
            overflow = True
    else:
        left = -x1
    if x2 > w:
        right = x2 - w
    else:
        right = 0
        if x2 <= 0:
            overflow = True
    # If overflowed, return empty image
    if overflow:
        if len(shape) == 2:
            size = (y2 - y1, x2 - x1)
        else:
            size = (y2 - y1, x2 - x1, shape[2])
        return np.zeros(size, dtype=image.dtype)
    # x1, y1, x2, y2 = np.maximum((x1, y1, x2, y2), 0)
    if x1 < 0:
        x1 = 0
    if y1 < 0:
        y1 = 0
    if x2 < 0:
        x2 = 0
    if y2 < 0:
        y2 = 0
    # crop image
    image = image[y1:y2, x1:x2]
    # if border
    if top or bottom or left or right:
        if len(shape) == 2:
            value = 0
        else:
            value = tuple(0 for _ in range(image.shape[2]))
        return cv2.copyMakeBorder(image, top, bottom, left, right, borderType=cv2.BORDER_CONSTANT, value=value)
    elif copy:
        return image_copy(image)
    else:
        return image


def image_decode(data, area=None):
    """
    Args:
        data (np.ndarray):
        area (tuple):

    Returns:
        np.ndarray

    Raises:
        ImageTruncated:
    """
    # Decode image
    image = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ImageTruncated('Empty image after cv2.imdecode')
    shape = image.shape
    if not shape:
        raise ImageTruncated('Empty image after cv2.imdecode')
    if len(shape) == 2:
        channel = 0
    else:
        channel = shape[2]

    if area:
        # If image get cropped, return image should be copied to re-order array,
        # making later usages faster
        if channel == 3:
            image = crop(image, area, copy=False)
            return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        elif channel == 0:
            return crop(image, area, copy=True)
        elif channel == 4:
            image = crop(image, area, copy=False)
            return cv2.cvtColor(image, cv2.COLOR_BGRA2RGB)
        else:
            # proceed as RGB
            image = crop(image, area, copy=False)
            return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    else:
        if channel == 3:
            cv2.cvtColor(image, cv2.COLOR_BGR2RGB, dst=image)
            return image
        elif channel == 0:
            return image
        elif channel == 4:
            return cv2.cvtColor(image, cv2.COLOR_BGRA2RGB)
        else:
            # proceed as RGB
            cv2.cvtColor(image, cv2.COLOR_BGR2RGB, dst=image)
            return image


def image_load(file, area=None):
    """
    Load an image like pillow and drop alpha channel.

    Args:
        file (str):
        area (tuple):

    Returns:
        np.ndarray:

    Raises:
        FileNotFoundError:
        ImageTruncated:
    """
    # cv2.imread can't handle non-ascii filepath and PIL.Image.open is slow
    # Here we read with numpy first
    content = atomic_read_bytes(file)
    data = np.frombuffer(content, dtype=np.uint8)
    if not data.size:
        raise ImageTruncated('Empty file')
    return image_decode(data, area=area)


def image_encode(image, ext='png', encode=None):
    """
    Encode image

    Args:
        image (np.ndarray):
        ext:
        encode (list): Extra encode options

    Returns:
        np.ndarray:
    """
    shape = image.shape
    if len(shape) == 3:
        channel = shape[2]
        if channel == 3:
            image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        elif channel == 4:
            image = cv2.cvtColor(image, cv2.COLOR_RGBA2BGRA)
        else:
            # proceed as RGB
            image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    # Keep grayscale unchanged

    # Prepare encode params
    ext = ext.lower()
    if encode is None:
        if ext == 'png':
            # Best compression, 0~9
            encode = [cv2.IMWRITE_PNG_COMPRESSION, 9]
        elif ext == 'jpg' or ext == 'jpeg':
            # Best quality
            encode = [cv2.IMWRITE_JPEG_QUALITY, 100]
        elif ext.lower() == '.webp':
            # Best quality
            encode = [cv2.IMWRITE_WEBP_QUALITY, 100]
        elif ext == 'tiff' or ext == 'tif':
            # LZW compression in TIFF
            encode = [cv2.IMWRITE_TIFF_COMPRESSION, 5]
        else:
            encode = []

    # Encode
    ret, buf = cv2.imencode(f'.{ext}', image, encode)
    if not ret:
        raise ImageTruncated('cv2.imencode failed')

    return buf


def image_save(image, file, encode=None):
    """
    Save an image like pillow.

    Args:
        image (np.ndarray):
        file (str):
        encode: (list[str]): Encode params
    """
    _, _, ext = file.rpartition('.')
    data = image_encode(image, ext=ext, encode=encode)
    atomic_write(file, data)


def image_save2(image, file, encode=None):
    """
    Save an image like pillow.

    Args:
        image (np.ndarray):
        file (str):
        encode: (list[str]): Encode params
    """
    _, _, ext = file.rpartition('.')
    data = image_encode(image, ext=ext, encode=encode)
    data = data.tobytes()
    atomic_write(file, data)


def image_fixup(file: str):
    """
    Save image using opencv again, making it smaller and shutting up libpng
    libpng warning: iCCP: known incorrect sRGB profile

    Args:
        file (str):

    Returns:
        bool: If file changed
    """
    # fixup png only
    _, _, ext = file.rpartition('.')
    if ext != 'png':
        return False

    # image_load
    content = atomic_read_bytes(file)
    data = np.frombuffer(content, dtype=np.uint8)
    if not data.size:
        return False
    try:
        image = image_decode(data)
    except ImageTruncated:
        # Ignore error because truncated image don't need fixup
        return False

    # image_save
    try:
        data = image_encode(image, ext=ext)
    except ImageTruncated:
        # Ignore error because truncated image don't need fixup
        return False

    # Writing numpy array directly is faster
    # but here we want to compare before and after
    new_content = data.tobytes()
    if content == new_content:
        # Same as before, no need to write
        return False
    else:
        atomic_write(file, data)
        return True


def resize(image, size):
    """
    Resize image like pillow image.resize(), but implement in opencv.
    Pillow uses PIL.Image.NEAREST by default.

    Args:
        image (np.ndarray):
        size: (x, y)

    Returns:
        np.ndarray:
    """
    return cv2.resize(image, size, interpolation=cv2.INTER_NEAREST)


def image_channel(image) -> int:
    """
    Args:
        image (np.ndarray):

    Returns:
        int: 0 for grayscale, 3 for RGB, 4 for RGBA
    """
    shape = image.shape
    if len(shape) == 2:
        return 0
    else:
        return shape[2]


def image_size(image):
    """
    Args:
        image (np.ndarray):

    Returns:
        int, int: width, height
    """
    shape = image.shape
    return shape[1], shape[0]


def image_paste(image, background, origin):
    """
    Paste an image on background.
    This method does not return a value, but instead updates the array "background".

    Args:
        image:
        background:
        origin: Upper-left corner, (x, y)
    """
    x, y = origin
    w, h = image_size(image)
    background[y:y + h, x:x + w] = image


def show_as_pillow(image):
    """
    Show image in Pillow, for debugging purpose
    """
    channel = image_channel(image)
    from PIL import Image
    if channel == 3:
        Image.fromarray(image, mode='RGB').show()
    elif channel == 0:
        Image.fromarray(image, mode='L').show()
    elif channel == 4:
        Image.fromarray(image, mode='RGBA').show()
    else:
        # proceed as RGB
        Image.fromarray(image, mode='RGB').show()
