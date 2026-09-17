"""
Binary asar fixtures used by the tests, base64 encoded so that the
repository does not need to store binaries.

Sources, both pinned to a reference implementation version because the
byte level comparison below is only meaningful for a known version:

- ``TINY_341``: packed by @electron/asar **3.4.1** (the version installed in
  ``webapp/node_modules``, the same one electron-builder uses), from a
  source tree of ``hello.txt`` (10 bytes) and ``sub/bin.dat`` (7 bytes), with
  an explicit file list ``['hello.txt', 'sub', 'sub/bin.dat']``.
- ``*_430``: taken from the @electron/asar **4.3.0** repository tarball
  (``codeload.github.com/electron/asar/tar.gz/refs/tags/v4.3.0``), files from
  ``test/expected/`` and ``test/input/``.

The only fixture that is not stored here is the release archive of the desktop
client: electron-builder builds it into ``webapp/release/app.asar``, it is read
at import time, see ``release_archive_bytes()``.
"""
import base64
import os

import msgspec

from alasio.codegen.asar.format import calc_header_size, pack_header_pickle, pack_size_pickle

# tiny.asar, 549 bytes
TINY_341 = (
    'BAAAAAwCAAAIAgAAAwIAAHsiZmlsZXMiOnsiaGVsbG8udHh0Ijp7InNpemUiOjEwLCJvZmZzZXQiOiIwIiwiaW50ZWdyaXR5'
    'Ijp7ImFsZ29yaXRobSI6IlNIQTI1NiIsImhhc2giOiJhMDNlNzgwNTVmMzI1OTE4MDBiZjc5NzYxOTg5YWIwOGY2OWVkNTdj'
    'N2E0ODBjNzY5YzY5N2VlYTA0MGFiZGQ3IiwiYmxvY2tTaXplIjo0MTk0MzA0LCJibG9ja3MiOlsiYTAzZTc4MDU1ZjMyNTkx'
    'ODAwYmY3OTc2MTk4OWFiMDhmNjllZDU3YzdhNDgwYzc2OWM2OTdlZWEwNDBhYmRkNyJdfX0sInN1YiI6eyJmaWxlcyI6eyJi'
    'aW4uZGF0Ijp7InNpemUiOjcsIm9mZnNldCI6IjEwIiwiaW50ZWdyaXR5Ijp7ImFsZ29yaXRobSI6IlNIQTI1NiIsImhhc2gi'
    'OiJlYTM2ZTRkYTQwMTcwMDAwMjhkYjc3OTRkOTQ2YjE1MjU0MGQ3YzY4YmJkYjZjNjBlOTk5ZjFkY2UxOWE0MDliIiwiYmxv'
    'Y2tTaXplIjo0MTk0MzA0LCJibG9ja3MiOlsiZWEzNmU0ZGE0MDE3MDAwMDI4ZGI3Nzk0ZDk0NmIxNTI1NDBkN2M2OGJiZGI2'
    'YzYwZTk5OWYxZGNlMTlhNDA5YiJdfX19fX19AGhlbGxvIGFzYXJQQVlMT0FE'
)

# packthis.asar, 1774 bytes
PACKTHIS_430 = (
    'BAAAAAQGAAAABgAA+QUAAHsiZmlsZXMiOnsiLmhpZGRlbmZpbGUudHh0Ijp7InNpemUiOjE5LCJvZmZzZXQiOiIwIiwiaW50'
    'ZWdyaXR5Ijp7ImFsZ29yaXRobSI6IlNIQTI1NiIsImhhc2giOiJkNDAwZDlhNzRmNjdlNzI0YTQ3OTMxNzNkNzNlMTQwM2Jk'
    'NmI3MzQ5MzA2MzJmNGVlZTUwYTNhNWExZTVhNDc4IiwiYmxvY2tTaXplIjo0MTk0MzA0LCJibG9ja3MiOlsiZDQwMGQ5YTc0'
    'ZjY3ZTcyNGE0NzkzMTczZDczZTE0MDNiZDZiNzM0OTMwNjMyZjRlZWU1MGEzYTVhMWU1YTQ3OCJdfX0sImRpcjEiOnsiZmls'
    'ZXMiOnsiZmlsZTEudHh0Ijp7InNpemUiOjksIm9mZnNldCI6IjE5IiwiaW50ZWdyaXR5Ijp7ImFsZ29yaXRobSI6IlNIQTI1'
    'NiIsImhhc2giOiI0MjAxNDlkM2Y4NTI4OTRiYTdmMzJlOWQzZWM3ZDg5MTllZTI0NTE3MjRiZjI1ODBiMDE4NmNkMzczYmQ2'
    'ZDgyIiwiYmxvY2tTaXplIjo0MTk0MzA0LCJibG9ja3MiOlsiNDIwMTQ5ZDNmODUyODk0YmE3ZjMyZTlkM2VjN2Q4OTE5ZWUy'
    'NDUxNzI0YmYyNTgwYjAxODZjZDM3M2JkNmQ4MiJdfX19fSwiZGlyMiI6eyJmaWxlcyI6eyJmaWxlMi5wbmciOnsic2l6ZSI6'
    'MTgyLCJvZmZzZXQiOiIyOCIsImludGVncml0eSI6eyJhbGdvcml0aG0iOiJTSEEyNTYiLCJoYXNoIjoiY2M0MDJiNzk2ZGM5'
    'MmIyYjFmM2E2ZDA5NTE1MDAzZDg0MDBlNjNkOGFjYWZmYzk2N2U0OWMwY2YwMTVmY2ZmZSIsImJsb2NrU2l6ZSI6NDE5NDMw'
    'NCwiYmxvY2tzIjpbImNjNDAyYjc5NmRjOTJiMmIxZjNhNmQwOTUxNTAwM2Q4NDAwZTYzZDhhY2FmZmM5NjdlNDljMGNmMDE1'
    'ZmNmZmUiXX19LCJmaWxlMy50eHQiOnsic2l6ZSI6Mywib2Zmc2V0IjoiMjEwIiwiaW50ZWdyaXR5Ijp7ImFsZ29yaXRobSI6'
    'IlNIQTI1NiIsImhhc2giOiJhNjY1YTQ1OTIwNDIyZjlkNDE3ZTQ4NjdlZmRjNGZiOGEwNGExZjNmZmYxZmEwN2U5OThlODZm'
    'N2Y3YTI3YWUzIiwiYmxvY2tTaXplIjo0MTk0MzA0LCJibG9ja3MiOlsiYTY2NWE0NTkyMDQyMmY5ZDQxN2U0ODY3ZWZkYzRm'
    'YjhhMDRhMWYzZmZmMWZhMDdlOTk4ZTg2ZjdmN2EyN2FlMyJdfX19fSwiZW1wdHlmaWxlLnR4dCI6eyJzaXplIjowLCJvZmZz'
    'ZXQiOiIyMTMiLCJpbnRlZ3JpdHkiOnsiYWxnb3JpdGhtIjoiU0hBMjU2IiwiaGFzaCI6ImUzYjBjNDQyOThmYzFjMTQ5YWZi'
    'ZjRjODk5NmZiOTI0MjdhZTQxZTQ2NDliOTM0Y2E0OTU5OTFiNzg1MmI4NTUiLCJibG9ja1NpemUiOjQxOTQzMDQsImJsb2Nr'
    'cyI6WyJlM2IwYzQ0Mjk4ZmMxYzE0OWFmYmY0Yzg5OTZmYjkyNDI3YWU0MWU0NjQ5YjkzNGNhNDk1OTkxYjc4NTJiODU1Il19'
    'fSwiZmlsZTAudHh0Ijp7InNpemUiOjEzLCJvZmZzZXQiOiIyMTMiLCJpbnRlZ3JpdHkiOnsiYWxnb3JpdGhtIjoiU0hBMjU2'
    'IiwiaGFzaCI6IjQxYTk3OGJlODhmZjg3YTU3MzA4YmYyMzQxMDZhNTdlYmY5YWEwOTcxZTIxMDFlNzUxMTM1NDdhODE1YmU3'
    'MmIiLCJibG9ja1NpemUiOjQxOTQzMDQsImJsb2NrcyI6WyI0MWE5NzhiZTg4ZmY4N2E1NzMwOGJmMjM0MTA2YTU3ZWJmOWFh'
    'MDk3MWUyMTAxZTc1MTEzNTQ3YTgxNWJlNzJiIl19fX19AAAAVGhpcyBmaWxlIGlzIGhpZGRlbmZpbGUgb25lLolQTkcNChoK'
    'AAAADUlIRFIAAAAIAAAACAgCAAAAS20p3AAAAAFzUkdCAK7OHOkAAAAEZ0FNQQAAsY8L/GEFAAAACXBIWXMAAA7DAAAOwwHH'
    'b6hkAAAAGHRFWHRTb2Z0d2FyZQBwYWludC5uZXQgNC4wLjOM5pdQAAAAJ0lEQVQYV2PAB/5jAIQElAUG6BIglTAGWJigBByg'
    'SKABqAQWwMAAAAZMR7nTt9O0AAAAAElFTkSuQmCCMTIzZmlsZTAgY29udGVudA=='
)

# packthis-unpack.asar, 1317 bytes
PACKTHIS_UNPACK_430 = (
    'BAAAAAQFAAAABQAA/AQAAHsiZmlsZXMiOnsiZGlyMSI6eyJmaWxlcyI6eyJmaWxlMS50eHQiOnsic2l6ZSI6OSwib2Zmc2V0'
    'IjoiMCIsImludGVncml0eSI6eyJhbGdvcml0aG0iOiJTSEEyNTYiLCJoYXNoIjoiNDIwMTQ5ZDNmODUyODk0YmE3ZjMyZTlk'
    'M2VjN2Q4OTE5ZWUyNDUxNzI0YmYyNTgwYjAxODZjZDM3M2JkNmQ4MiIsImJsb2NrU2l6ZSI6NDE5NDMwNCwiYmxvY2tzIjpb'
    'IjQyMDE0OWQzZjg1Mjg5NGJhN2YzMmU5ZDNlYzdkODkxOWVlMjQ1MTcyNGJmMjU4MGIwMTg2Y2QzNzNiZDZkODIiXX19fX0s'
    'ImRpcjIiOnsiZmlsZXMiOnsiZmlsZTIucG5nIjp7InNpemUiOjE4MiwidW5wYWNrZWQiOnRydWUsImludGVncml0eSI6eyJh'
    'bGdvcml0aG0iOiJTSEEyNTYiLCJoYXNoIjoiY2M0MDJiNzk2ZGM5MmIyYjFmM2E2ZDA5NTE1MDAzZDg0MDBlNjNkOGFjYWZm'
    'Yzk2N2U0OWMwY2YwMTVmY2ZmZSIsImJsb2NrU2l6ZSI6NDE5NDMwNCwiYmxvY2tzIjpbImNjNDAyYjc5NmRjOTJiMmIxZjNh'
    'NmQwOTUxNTAwM2Q4NDAwZTYzZDhhY2FmZmM5NjdlNDljMGNmMDE1ZmNmZmUiXX19LCJmaWxlMy50eHQiOnsic2l6ZSI6Mywi'
    'b2Zmc2V0IjoiOSIsImludGVncml0eSI6eyJhbGdvcml0aG0iOiJTSEEyNTYiLCJoYXNoIjoiYTY2NWE0NTkyMDQyMmY5ZDQx'
    'N2U0ODY3ZWZkYzRmYjhhMDRhMWYzZmZmMWZhMDdlOTk4ZTg2ZjdmN2EyN2FlMyIsImJsb2NrU2l6ZSI6NDE5NDMwNCwiYmxv'
    'Y2tzIjpbImE2NjVhNDU5MjA0MjJmOWQ0MTdlNDg2N2VmZGM0ZmI4YTA0YTFmM2ZmZjFmYTA3ZTk5OGU4NmY3ZjdhMjdhZTMi'
    'XX19fX0sImVtcHR5ZmlsZS50eHQiOnsic2l6ZSI6MCwib2Zmc2V0IjoiMTIiLCJpbnRlZ3JpdHkiOnsiYWxnb3JpdGhtIjoi'
    'U0hBMjU2IiwiaGFzaCI6ImUzYjBjNDQyOThmYzFjMTQ5YWZiZjRjODk5NmZiOTI0MjdhZTQxZTQ2NDliOTM0Y2E0OTU5OTFi'
    'Nzg1MmI4NTUiLCJibG9ja1NpemUiOjQxOTQzMDQsImJsb2NrcyI6WyJlM2IwYzQ0Mjk4ZmMxYzE0OWFmYmY0Yzg5OTZmYjky'
    'NDI3YWU0MWU0NjQ5YjkzNGNhNDk1OTkxYjc4NTJiODU1Il19fSwiZmlsZTAudHh0Ijp7InNpemUiOjEzLCJvZmZzZXQiOiIx'
    'MiIsImludGVncml0eSI6eyJhbGdvcml0aG0iOiJTSEEyNTYiLCJoYXNoIjoiNDFhOTc4YmU4OGZmODdhNTczMDhiZjIzNDEw'
    'NmE1N2ViZjlhYTA5NzFlMjEwMWU3NTExMzU0N2E4MTViZTcyYiIsImJsb2NrU2l6ZSI6NDE5NDMwNCwiYmxvY2tzIjpbIjQx'
    'YTk3OGJlODhmZjg3YTU3MzA4YmYyMzQxMDZhNTdlYmY5YWEwOTcxZTIxMDFlNzUxMTM1NDdhODE1YmU3MmIiXX19fX1maWxl'
    'IG9uZS4xMjNmaWxlMCBjb250ZW50'
)

# packthis-read-stream-symlink.asar, 717 bytes
PACKTHIS_SYMLINK_430 = (
    'BAAAAJwCAACYAgAAkgIAAHsiZmlsZXMiOnsiQSI6eyJmaWxlcyI6eyJyZWFsLnR4dCI6eyJzaXplIjoxOSwib2Zmc2V0Ijoi'
    'MCIsImludGVncml0eSI6eyJhbGdvcml0aG0iOiJTSEEyNTYiLCJoYXNoIjoiOWVmMjYwOTA0YzM4YTAxNzMxMzdiYTVkOWU5'
    'NWQxNzM5YjhkY2FjMjA1ODQ3ZjIwMmY2Y2ZmNDE4YTk4OWJiZSIsImJsb2NrU2l6ZSI6NDE5NDMwNCwiYmxvY2tzIjpbIjll'
    'ZjI2MDkwNGMzOGEwMTczMTM3YmE1ZDllOTVkMTczOWI4ZGNhYzIwNTg0N2YyMDJmNmNmZjQxOGE5ODliYmUiXX19LCJyZXZl'
    'cnNlLXN5bWxpbmsudHh0Ijp7ImxpbmsiOiJCL3JldmVyc2Utc3ltbGluay50eHQifX19LCJCIjp7ImZpbGVzIjp7InJldmVy'
    'c2Utc3ltbGluay50eHQiOnsic2l6ZSI6MjIsIm9mZnNldCI6IjE5IiwiaW50ZWdyaXR5Ijp7ImFsZ29yaXRobSI6IlNIQTI1'
    'NiIsImhhc2giOiI3NjY2NTNmN2E3YTNlOThhNDk4ODg4ZDViNzc4NGJmODA4YzUzMDViZjVhMDE5NTNjYTg2NmY3ZjM2YTll'
    'MTExIiwiYmxvY2tTaXplIjo0MTk0MzA0LCJibG9ja3MiOlsiNzY2NjUzZjdhN2EzZTk4YTQ5ODg4OGQ1Yjc3ODRiZjgwOGM1'
    'MzA1YmY1YTAxOTUzY2E4NjZmN2YzNmE5ZTExMSJdfX19fSwiQ3VycmVudCI6eyJsaW5rIjoiQSJ9LCJyZWFsLnR4dCI6eyJs'
    'aW5rIjoiQ3VycmVudC9yZWFsLnR4dCJ9fX0AAEkgQU0gUkVBTCBUWFQgRklMRQpJIFNZTUxJTksgVE8gU1VQRVIgRElS'
)

# extractthis.asar, 467 bytes
EXTRACTTHIS_430 = (
    'BAAAAPwAAAD4AAAA8gAAAHsiZmlsZXMiOnsiZGlyMSI6eyJmaWxlcyI6eyJmaWxlMS50eHQiOnsic2l6ZSI6OSwib2Zmc2V0'
    'IjoiMCJ9fX0sImRpcjIiOnsiZmlsZXMiOnsiZmlsZTIucG5nIjp7InNpemUiOjE4Miwib2Zmc2V0IjoiOSJ9LCJmaWxlMy50'
    'eHQiOnsic2l6ZSI6Mywib2Zmc2V0IjoiMTkxIn19fSwiZW1wdHlmaWxlLnR4dCI6eyJzaXplIjowLCJvZmZzZXQiOiIxOTQi'
    'fSwiZmlsZTAudHh0Ijp7InNpemUiOjEzLCJvZmZzZXQiOiIxOTQifX19AABmaWxlIG9uZS6JUE5HDQoaCgAAAA1JSERSAAAA'
    'CAAAAAgIAgAAAEttKdwAAAABc1JHQgCuzhzpAAAABGdBTUEAALGPC/xhBQAAAAlwSFlzAAAOwwAADsMBx2+oZAAAABh0RVh0'
    'U29mdHdhcmUAcGFpbnQubmV0IDQuMC4zjOaXUAAAACdJREFUGFdjwAf+YwCEBJQFBugSIJUwBliYoAQcoEigAagEFsDAAAAG'
    'TEe507fTtAAAAABJRU5ErkJggjEyM2ZpbGUwIGNvbnRlbnQ='
)

# extractthis-unpack.asar, 285 bytes
EXTRACTTHIS_UNPACK_430 = (
    'BAAAAPwAAAD4AAAA8QAAAHsiZmlsZXMiOnsiZGlyMSI6eyJmaWxlcyI6eyJmaWxlMS50eHQiOnsic2l6ZSI6OSwib2Zmc2V0'
    'IjoiMCJ9fX0sImRpcjIiOnsiZmlsZXMiOnsiZmlsZTIucG5nIjp7InNpemUiOjE4MiwidW5wYWNrZWQiOnRydWV9LCJmaWxl'
    'My50eHQiOnsic2l6ZSI6Mywib2Zmc2V0IjoiOSJ9fX0sImVtcHR5ZmlsZS50eHQiOnsic2l6ZSI6MCwib2Zmc2V0IjoiMTIi'
    'fSwiZmlsZTAudHh0Ijp7InNpemUiOjEzLCJvZmZzZXQiOiIxMiJ9fX0AAABmaWxlIG9uZS4xMjNmaWxlMCBjb250ZW50'
)


# Content of ``packthis-unpack.asar.unpacked/``, the files that are not stored
# in the archive body of ``PACKTHIS_UNPACK_430``
UNPACKED_430_FILES = {
    'dir2/file2.png': (
    'iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAJcEhZcwAA'
    'DsMAAA7DAcdvqGQAAAAYdEVYdFNvZnR3YXJlAHBhaW50Lm5ldCA0LjAuM4zml1AAAAAnSURBVBhXY8AH/mMAhASUBQboEiCV'
    'MAZYmKAEHKBIoAGoBBbAwAAABkxHudO307QAAAAASUVORK5CYII='
    ),
}


def _decode(value):
    """
    Decode a base64 fixture.

    Args:
        value (str): Base64 encoded archive

    Returns:
        bytes: Archive bytes
    """
    return base64.b64decode(value)


# The archive electron-builder built for the desktop client, the one fixture
# that is not stored in this file (it needs a release build of webapp/).
RELEASE_ARCHIVE = os.path.join('webapp', 'release', 'app.asar')


def _read_release_archive():
    """
    Read the release archive, at import time.

    The tests unpack it under the in-memory filesystem, which serves every
    path from memory and never touches the real disk: the bytes have to be in
    hand before a test starts (the same reason why fixture.py is imported at
    module level).

    Returns:
        bytes | None: Archive bytes, None when the release build is not present
    """
    try:
        with open(RELEASE_ARCHIVE, 'rb') as f:
            return f.read()
    except FileNotFoundError:
        return None


RELEASE_ARCHIVE_BYTES = _read_release_archive()


def release_archive_bytes():
    """
    Get the bytes of the release archive, read once at import time.

    Returns:
        bytes | None: Archive bytes, None when the release build is not present
    """
    return RELEASE_ARCHIVE_BYTES


def tiny_341():
    """
    Get the archive bytes.

    Returns:
        bytes: Archive bytes
    """
    return _decode(TINY_341)


def packthis_430():
    """
    Get the archive bytes.

    Returns:
        bytes: Archive bytes
    """
    return _decode(PACKTHIS_430)


def packthis_unpack_430():
    """
    Get the archive bytes.

    Returns:
        bytes: Archive bytes
    """
    return _decode(PACKTHIS_UNPACK_430)


def packthis_symlink_430():
    """
    Get the archive bytes.

    Returns:
        bytes: Archive bytes
    """
    return _decode(PACKTHIS_SYMLINK_430)


def extractthis_430():
    """
    Get the archive bytes.

    Returns:
        bytes: Archive bytes
    """
    return _decode(EXTRACTTHIS_430)


def extractthis_unpack_430():
    """
    Get the archive bytes.

    Returns:
        bytes: Archive bytes
    """
    return _decode(EXTRACTTHIS_UNPACK_430)


def unpacked_430_files():
    """
    Get the unpacked files of ``PACKTHIS_UNPACK_430``.

    Returns:
        dict: {archive path: content}
    """
    return {name: _decode(value) for name, value in UNPACKED_430_FILES.items()}


def make_archive(header, data=b'', header_size=None):
    """
    Build an archive around a header, for the hand made cases of the tests.

    Args:
        header (dict): Header JSON, as a plain object
        data (bytes): Content of the data area
        header_size (int): Value written in the frame, defaults to the real
            header length, an override builds a deliberately broken archive

    Returns:
        bytes: Archive bytes
    """
    json_bytes = msgspec.json.encode(header)
    if header_size is None:
        header_size = calc_header_size(len(json_bytes))
    return pack_size_pickle(header_size) + pack_header_pickle(json_bytes) + data
