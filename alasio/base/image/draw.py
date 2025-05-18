import cv2

from alasio.base.image.imfile import image_channel, image_size


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


def image_paint(image, area, color):
    """
    Paint the area of image to given color

    Args:
        image (np.ndarray):
        area:
        color: (r, g, b) or value if grayscale
    """
    x1, y1, x2, y2 = area
    image[y1:y2, x1:x2] = color


def image_paint_white(image, area):
    """
    Paint the area of image to black

    Args:
        image (np.ndarray):
        area:
    """
    channel = image_channel(image)
    x1, y1, x2, y2 = area
    if channel == 3:
        # RGB
        image[y1:y2, x1:x2] = (255, 255, 255)
    elif channel == 0:
        # grayscale
        image[y1:y2, x1:x2] = 255
    elif channel == 4:
        # RGBA
        image[y1:y2, x1:x2] = (255, 255, 255, 255)
    else:
        # what is this?
        color = tuple([255] * channel)
        image[y1:y2, x1:x2] = color
    return image


def image_paint_black(image, area):
    """
    Paint the area of image to black

    Args:
        image (np.ndarray):
        area:
    """
    channel = image_channel(image)
    x1, y1, x2, y2 = area
    if channel == 3:
        # RGB
        image[y1:y2, x1:x2] = (0, 0, 0)
    elif channel == 0:
        # grayscale
        image[y1:y2, x1:x2] = 0
    elif channel == 4:
        # RGBA
        image[y1:y2, x1:x2] = (0, 0, 0, 255)
    else:
        # what is this?
        color = tuple([0] * channel)
        image[y1:y2, x1:x2] = color
    return image
