"""
Helpers shared by the asset extractor tests.

The tests never touch the real disk, they build a small project on the fake
filesystem with 40x20 PNG images instead of the real 1280x720 assets, image
decoding is the slowest part of reading assets.
"""
import cv2
import numpy as np

from alasio.assets_dev.parse import AssetData, AssetImage, AssetModule, AssetMultilang
from alasio.base.op import Slist
from alasio.ext.path import PathStr

# Resolution of the assets of the tests
RESOLUTION = (40, 20)
# Area of the content of a test image, a test image is black except this area
ASSET_AREA = (5, 4, 15, 12)


def project_root(fs):
    """
    Root of the fake project of a test

    Args:
        fs (FakeFilesystem): Fake filesystem of the test

    Returns:
        PathStr: Absolute path of the project root
    """
    return PathStr.new(fs.root_dir.path).joinpath('project')


def png_bytes(width=RESOLUTION[0], height=RESOLUTION[1], area=ASSET_AREA, rgb=(255, 255, 255), compress=9):
    """
    Make a png of a black image with a filled rectangle

    Args:
        width (int): Image width
        height (int): Image height
        area (tuple[int, int, int, int]): Filled rectangle, (x1, y1, x2, y2)
        rgb (tuple[int, int, int]): Color of the rectangle
        compress (int): PNG compression level, 0 to 9

    Returns:
        bytes: PNG content
    """
    image = np.zeros((height, width, 3), dtype=np.uint8)
    x1, y1, x2, y2 = area
    # opencv encodes in BGR order, image_load() decodes it back to RGB
    image[y1:y2, x1:x2] = rgb[::-1]
    ok, buf = cv2.imencode('.png', image, [cv2.IMWRITE_PNG_COMPRESSION, compress])
    assert ok
    return buf.tobytes()


def write_png(fs, file, **kwargs):
    """
    Write a test png, see png_bytes()

    Args:
        fs (FakeFilesystem): Fake filesystem of the test
        file (str): Absolute filepath
        **kwargs: Arguments of png_bytes()

    Returns:
        bytes: Content written
    """
    content = png_bytes(**kwargs)
    fs.create_file(file, contents=content)
    return content


def parse_image(project, relative, lang='share'):
    """
    Create an AssetImage of a file of the fake project

    AssetImage takes the repository root and the language root from class
    attributes, this points them to the fake project before parsing.

    Args:
        project (PathStr): Root of the fake project
        relative (str): Filepath relative to the project root
        lang (str): Language folder of the file. Defaults to 'share'.

    Returns:
        AssetImage: Parsed image, not loaded yet
    """
    AssetImage.REPO_ROOT = project
    AssetImage.LANG_ROOT = project.joinpath(f'assets/{lang}')
    return AssetImage(project.joinpath(relative))


def build_images(project, files):
    """
    Parse a list of files of the fake project, like AssetsExtractor.assets does

    The language folder is not part of the filename, the extractor sets the
    language of the image it read the folder of.

    Args:
        project (PathStr): Root of the fake project
        files (list[tuple[str, str]]): (language, filepath) pairs

    Returns:
        Slist[AssetImage]:
    """
    images = []
    for lang, relative in files:
        image = parse_image(project, relative, lang=lang)
        image.lang = lang
        images.append(image)
    return Slist(images)


def build_data(project, files):
    """
    Build an AssetData like AssetMultilang does

    Args:
        project (PathStr): Root of the fake project
        files (list[tuple[str, str]]): (language, filepath) pairs

    Returns:
        AssetData | None: None if the files have no base image
    """
    return AssetData.product(build_images(project, files))


def build_multilang(project, files):
    """
    Build an AssetMultilang like AssetModule does

    Args:
        project (PathStr): Root of the fake project
        files (list[tuple[str, str]]): (language, filepath) pairs

    Returns:
        AssetMultilang:
    """
    return AssetMultilang(build_images(project, files))


def build_module(project, files):
    """
    Build an AssetModule like AssetAll does

    Args:
        project (PathStr): Root of the fake project
        files (list[tuple[str, str]]): (language, filepath) pairs

    Returns:
        AssetModule:
    """
    return AssetModule(build_images(project, files))
