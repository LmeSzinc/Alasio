import cv2
import numpy as np


def create_rgb888():
    """
    Create a 4096x4096 image containing every color in RGB888
    for testing purpose
    """
    size = 4096

    b = np.tile(np.arange(256, dtype=np.uint8), 65536)
    g = np.repeat(np.arange(256, dtype=np.uint8), 256)
    g = np.tile(g, 256)
    r = np.repeat(np.arange(256, dtype=np.uint8), 65536)

    b = b.reshape(size, size)
    g = g.reshape(size, size)
    r = r.reshape(size, size)

    img_array = cv2.merge([b, g, r])
    return img_array
