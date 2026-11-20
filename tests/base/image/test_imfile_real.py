"""
Tests of the image file functions on the real disk, run on CI.

The rest of the suite runs on the in-memory fake filesystem, and cv2.imdecode /
cv2.imencode are pure functions of a buffer, so everything but the two things
below is covered there:

- cv2.VideoCapture takes a path and decodes it with the codec backend built into
  opencv, it reads the disk itself and the in-memory filesystem cannot serve it.
  Which backend a wheel carries, and which gif it refuses, is a property of the
  platform, so the answer is only meaningful on the runner;
- a non-ascii path is resolved by opencv with the encoding of its build, and the
  answer differs between Windows, Linux and macOS. The module never hands a path
  to cv2.imread / cv2.imwrite for that reason (it decodes a buffer and writes the
  encoded bytes with the file io of the framework), and these tests are the ones
  that prove it on the platform that has the problem.

The file is skipped outside CI on purpose: the rest of the suite keeps running
on the fake filesystem there, and a developer machine (a codec that another
opencv wheel carries, a locale that strips the non-ascii characters of a path)
would change the answer of the operating system rather than exercise the code
under test.

Both halves run on CI: the fake filesystem always, this file when CI is set.
"""
import os
import shutil
import stat
import tempfile

import cv2
import numpy as np
import pytest

from alasio.base.image.imfile import (
    ImageBroken, gif_load, gif_load_fframe, image_fixup, image_fixup_any, image_load, image_save
)
from alasio.ext import env
from alasio.ext.path.atomic import atomic_write
from alasio.ext.path.calc import joinpath

pytestmark = [
    pytest.mark.skipif(
        not env.CI, reason='the real disk is covered by the CI matrix, the local runs use the fake filesystem'),
    pytest.mark.parallel,
]

# Colors of the test gif, a pixel of a decoded frame is GIF_PALETTE[index]
GIF_PALETTE = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]


@pytest.fixture
def folder():
    """
    A folder of the real disk, removed after the test.

    Yields:
        str: Absolute path of an empty folder, '/'-separated
    """
    path = tempfile.mkdtemp(prefix='alasio_imfile_').replace(os.sep, '/')
    try:
        yield path
    finally:
        _rmtree(path)


def _rmtree(path):
    """
    Remove a folder of the real disk, read-only entries included.

    Args:
        path (str): Folder to remove
    """
    def clear_and_retry(func, target, _):
        try:
            os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
            func(target)
        except OSError:
            pass

    try:
        shutil.rmtree(path, onerror=clear_and_retry)
    except OSError:
        pass


def gif_lzw_pack(codes):
    """
    Pack LZW codes of a 2 bit palette.

    Every pixel is preceded by a clear code, which resets the code table, so
    the code width stays at 3 bits and no code dictionary is needed.

    Args:
        codes (list[int]): Codes to pack

    Returns:
        bytes: Packed code stream
    """
    packed = bytearray()
    buffer = 0
    bits = 0
    for code in codes:
        buffer |= code << bits
        bits += 3
        while bits >= 8:
            packed.append(buffer & 0xFF)
            buffer >>= 8
            bits -= 8
    if bits:
        packed.append(buffer & 0xFF)
    return bytes(packed)


def gif_sub_blocks(data):
    """
    Split data into gif sub blocks of at most 255 bytes.

    Args:
        data (bytes): Data to split

    Returns:
        bytes: Sub blocks, ended by a zero length block
    """
    blocks = bytearray()
    for start in range(0, len(data), 255):
        chunk = data[start:start + 255]
        blocks.append(len(chunk))
        blocks += chunk
    blocks.append(0)
    return bytes(blocks)


def gif_bytes(frames, width, height, palette=GIF_PALETTE):
    """
    Build an animated gif, every frame is a full size image of palette indices.

    Args:
        frames (list[list[int]]): Palette index of every pixel, row by row
        width (int): Image width
        height (int): Image height
        palette (list[tuple[int, int, int]]): RGB colors, of a power of two size

    Returns:
        bytes: Gif content
    """
    # The size of the global color table is a power of two, its log2 is 1 based
    size_code = (len(palette) - 1).bit_length() - 1
    gif = bytearray(b'GIF89a')
    gif += width.to_bytes(2, 'little')
    gif += height.to_bytes(2, 'little')
    # Global color table present, color resolution of 8 bits, table size
    gif.append(0x80 | 0x70 | size_code)
    gif += b'\x00\x00'
    for red, green, blue in palette:
        gif += bytes((red, green, blue))
    for pixels in frames:
        # Graphic control extension, 100ms delay, no transparency
        gif += b'\x21\xf9\x04\x00\x0a\x00\x00\x00'
        # Image descriptor of the whole image, no local color table, no interlace
        gif += b'\x2c' + b'\x00' * 4
        gif += width.to_bytes(2, 'little')
        gif += height.to_bytes(2, 'little')
        gif.append(0x00)
        # A clear code before every pixel keeps the code width at 3 bits
        gif.append(2)
        codes = [code for pixel in pixels for code in (4, pixel)]
        codes.append(5)
        gif += gif_sub_blocks(gif_lzw_pack(codes))
    gif.append(0x3b)
    return bytes(gif)


def write_gif(folder, frames, width, height, name='a.gif'):
    """
    Write a test gif into a folder of the real disk.

    Args:
        folder (str): Target folder, '/'-separated
        frames (list[list[int]]): Palette index of every pixel, row by row
        width (int): Image width
        height (int): Image height
        name (str): File name

    Returns:
        str: Path of the written gif
    """
    path = joinpath(folder, name)
    atomic_write(path, gif_bytes(frames, width, height))
    return path


def make_image(channel, height=12, width=20):
    """
    Make a deterministic image of a gradient, every channel holding the value.

    Args:
        channel (int): 1 for grayscale, otherwise the channel count
        height (int): Image height
        width (int): Image width

    Returns:
        np.ndarray: uint8 image of shape (height, width) or (height, width, channel)
    """
    base = np.arange(height * width, dtype=np.uint8).reshape(height, width)
    if channel == 1:
        return base
    return np.repeat(base[:, :, None], channel, axis=2)


def write_png(folder, name, image, compress=9):
    """
    Write an image as png, in the channel order of opencv.

    Args:
        folder (str): Target folder, '/'-separated
        name (str): File name below the folder
        image (np.ndarray): RGB, RGBA or grayscale image
        compress (int): PNG compression level, 0 to 9

    Returns:
        str: Path of the written png
    """
    path = joinpath(folder, name)
    if image.ndim == 2:
        raw = image
    else:
        raw = image[:, :, ::-1]
    ok, buf = cv2.imencode('.png', raw, [cv2.IMWRITE_PNG_COMPRESSION, compress])
    assert ok
    atomic_write(path, buf.tobytes())
    return path


class FakeVideoCapture:
    """A cv2.VideoCapture replacement whose read() fails"""

    def __init__(self, file):
        self.file = file
        self.released = False

    def isOpened(self):
        return True

    def read(self):
        raise RuntimeError('read failed')

    def release(self):
        self.released = True


def mock_video_capture_read_error(monkeypatch):
    """
    Replace cv2.VideoCapture with a capture whose read() raises.

    A real gif file can not make read() raise, so this one path is mocked.

    Args:
        monkeypatch (MonkeyPatch): Pytest monkeypatch fixture

    Returns:
        list[FakeVideoCapture]: Captures created by the function under test
    """
    captures = []

    def factory(file):
        capture = FakeVideoCapture(file)
        captures.append(capture)
        return capture

    monkeypatch.setattr(cv2, 'VideoCapture', factory)
    return captures


class TestGifLoadFrame:
    """Tests for gif_load_fframe() on a real gif file"""

    def test_first_frame(self, folder):
        """The first frame is returned in RGB, the frames after it are ignored"""
        path = write_gif(folder, [[0, 1, 2, 3], [3, 2, 1, 0]], 2, 2)
        image = gif_load_fframe(path)
        assert image.shape == (2, 2, 3)
        assert image.dtype == np.uint8
        np.testing.assert_array_equal(image.reshape(-1, 3), np.array(GIF_PALETTE, dtype=np.uint8))

    def test_area(self, folder):
        """The area is cropped from the frame"""
        # 3x2 image, row 0 is red green blue, row 1 is yellow red green
        path = write_gif(folder, [[0, 1, 2, 3, 0, 1]], 3, 2)
        image = gif_load_fframe(path, area=(1, 1, 3, 2))
        # row 1, column 1 and 2 are the palette index 0 and 1
        np.testing.assert_array_equal(image, np.array([[(255, 0, 0), (0, 255, 0)]], dtype=np.uint8))

    def test_area_outside(self, folder):
        """An area outside the frame is padded black"""
        path = write_gif(folder, [[0, 1, 2, 3]], 2, 2)
        image = gif_load_fframe(path, area=(-1, -1, 1, 1))
        expected = np.zeros((2, 2, 3), dtype=np.uint8)
        expected[1, 1] = GIF_PALETTE[0]
        np.testing.assert_array_equal(image, expected)

    def test_missing_file(self, folder):
        """A missing file raises FileNotFoundError"""
        with pytest.raises(FileNotFoundError):
            gif_load_fframe(joinpath(folder, 'missing.gif'))

    def test_empty_file(self, folder):
        """An empty file raises ImageBroken, the file exists but is not a gif"""
        path = joinpath(folder, 'empty.gif')
        atomic_write(path, b'')
        with pytest.raises(ImageBroken):
            gif_load_fframe(path)

    def test_broken_file(self, folder):
        """A file that is not a gif raises ImageBroken"""
        path = joinpath(folder, 'broken.gif')
        atomic_write(path, b'broken')
        with pytest.raises(ImageBroken):
            gif_load_fframe(path)

    def test_read_raises(self, monkeypatch):
        """An error while reading is propagated and the capture is released"""
        captures = mock_video_capture_read_error(monkeypatch)
        with pytest.raises(RuntimeError, match='read failed'):
            gif_load_fframe('/images/broken.gif')
        assert captures[0].released is True


class TestGifLoad:
    """Tests for gif_load() on a real gif file"""

    def test_all_frames(self, folder):
        """All frames are returned in RGB"""
        path = write_gif(folder, [[0, 1, 2, 3], [3, 3, 3, 3]], 2, 2)
        frames = gif_load(path)
        assert len(frames) == 2
        np.testing.assert_array_equal(frames[0].reshape(-1, 3), np.array(GIF_PALETTE, dtype=np.uint8))
        np.testing.assert_array_equal(frames[1].reshape(-1, 3), np.repeat([[255, 255, 0]], 4, axis=0))

    def test_all_frames_area(self, folder):
        """The area is cropped from every frame"""
        pixels = [0, 1, 2, 3, 0, 1]
        path = write_gif(folder, [pixels, pixels], 3, 2)
        frames = gif_load(path, area=(1, 0, 3, 1))
        expected = np.array([[(0, 255, 0), (0, 0, 255)]], dtype=np.uint8)
        assert len(frames) == 2
        for frame in frames:
            np.testing.assert_array_equal(frame, expected)

    def test_missing_file(self, folder):
        """A missing file raises FileNotFoundError"""
        with pytest.raises(FileNotFoundError):
            gif_load(joinpath(folder, 'missing.gif'))

    def test_empty_file(self, folder):
        """An empty file raises ImageBroken, the file exists but is not a gif"""
        path = joinpath(folder, 'empty.gif')
        atomic_write(path, b'')
        with pytest.raises(ImageBroken):
            gif_load(path)

    def test_broken_file(self, folder):
        """A file that is not a gif raises ImageBroken"""
        path = joinpath(folder, 'broken.gif')
        atomic_write(path, b'broken')
        with pytest.raises(ImageBroken):
            gif_load(path)

    def test_read_raises(self, monkeypatch):
        """An error while reading is propagated and the capture is released"""
        captures = mock_video_capture_read_error(monkeypatch)
        with pytest.raises(RuntimeError, match='read failed'):
            gif_load('/images/broken.gif')
        assert captures[0].released is True


class TestNonAsciiPath:
    """
    An image in a folder with a non ascii name, written and read on the real
    disk. opencv resolves a path with the encoding of its build, which is why
    the module hands cv2 a buffer instead: this is the platform check of that
    rule.
    """

    @pytest.mark.parametrize('channel', [1, 3, 4], ids=['grayscale', 'rgb', 'rgba'])
    def test_save_and_load_png(self, folder, channel):
        """A png is saved and loaded in a non ascii folder with a non ascii name"""
        path = joinpath(folder, '测试目录/图片.png')
        image = make_image(channel)
        assert image_save(path, image) is True
        loaded = image_load(path)
        if channel == 4:
            # image_load drops alpha
            np.testing.assert_array_equal(loaded, image[:, :, :3])
        else:
            np.testing.assert_array_equal(loaded, image)

    def test_save_creates_non_ascii_folder(self, folder):
        """A missing non ascii folder is created by the save"""
        path = joinpath(folder, '素材/界面/战斗/按钮.png')
        image = make_image(3)
        assert image_save(path, image) is True
        np.testing.assert_array_equal(image_load(path), image)

    def test_load_area(self, folder):
        """An area is cropped from an image of a non ascii path"""
        path = joinpath(folder, '素材/图标.png')
        image = make_image(3)
        assert image_save(path, image) is True
        np.testing.assert_array_equal(image_load(path, area=(3, 2, 15, 10)), image[2:10, 3:15])

    def test_fixup_png(self, folder):
        """A png in a non ascii folder is fixed up"""
        image = make_image(3)
        path = write_png(folder, '素材/图标.png', image, compress=0)
        assert image_fixup(path) is True
        np.testing.assert_array_equal(image_load(path), image)
        assert image_fixup(path) is False

    def test_fixup_any_non_ascii_folder(self, folder):
        """A non ascii folder is searched recursively for png files"""
        write_png(folder, '素材/图标.png', make_image(3), compress=0)
        write_png(folder, '素材/界面/按钮.png', make_image(3), compress=0)
        assert image_fixup_any(joinpath(folder, '素材')) == 2
        assert image_fixup_any(joinpath(folder, '素材')) == 0

    def test_load_gif(self, folder):
        """A gif is loaded from a non ascii folder with a non ascii name"""
        path = write_gif(folder, [[0, 1, 2, 3]], 2, 2, name='动画目录/动画.gif')
        image = image_load(path)
        np.testing.assert_array_equal(image.reshape(-1, 3), np.array(GIF_PALETTE, dtype=np.uint8))

    def test_all_frames_of_a_non_ascii_gif(self, folder):
        """Every frame of a gif in a non ascii folder is loaded"""
        path = write_gif(folder, [[0, 0, 0, 0], [2, 2, 2, 2]], 2, 2, name='素材/图标.gif')
        frames = gif_load(path)
        assert len(frames) == 2
        np.testing.assert_array_equal(frames[0].reshape(-1, 3), np.repeat([[255, 0, 0]], 4, axis=0))
        np.testing.assert_array_equal(frames[1].reshape(-1, 3), np.repeat([[0, 0, 255]], 4, axis=0))

    @pytest.mark.skipif(not env.WINDOWS, reason='opencv resolves a utf-8 path on posix')
    def test_cv2_path_functions_fail_on_windows(self, folder):
        """
        The contrast that makes the tests above meaningful: on Windows the two
        path based functions of opencv cannot open a non ascii path at all, so a
        module that used them would lose every image of a translated asset tree.
        """
        path = joinpath(folder, '测试目录/图片.png')
        image = make_image(3)
        assert image_save(path, image) is True
        # the file is on the disk and the module reads it, opencv cannot
        assert cv2.imread(path) is None
        assert cv2.imwrite(path, image) is False
        np.testing.assert_array_equal(image_load(path), image)
