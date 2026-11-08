"""
The screenshots of the golden test review page.

The page receives the sources and the image anchors of a test file over the
websocket topics of alasio.backend.topic.golden_test; the screenshots themselves
are streamed by this route: an `<img>` cannot read a websocket message, and
keeping the binary out of the topic payload keeps the pushes small.

The route serves the `collection` folder of a mod, the folder the `image`
decorator of alasio.testing.screenshot reads a test screenshot from. The only
paths it accepts are the relative paths of that decorator, and the resolution
(the topic module) keeps every request inside the folder.
"""
import os
import stat

from starlette import status
from starlette.responses import FileResponse
from typing_extensions import Annotated

from alasio.backend.auth.deps import require_electron
from alasio.backend.topic.golden_test import GoldenTestPathError, resolve_collection_image
from alasio.ext.starapi.param import Depends, HTTPExceptionJson
from alasio.ext.starapi.router import APIRouter

router = APIRouter('/golden_test')

# The screenshots are pictures and a mod folder must never serve executable
# content, so svg is absent here like in the asset server (svg can embed
# scripts). The media types are spelled out because the backend pins the
# mimetype table to the builtin one (alasio.backport.patch.patch_mimetype),
# which does not know every extension of the whitelist
MEDIA_TYPES = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
    '.bmp': 'image/bmp',
}

# A screenshot edited by hand is shown by the next reload: the same no-cache
# rule as the asset server, the browser cache of the client never hides it
NO_CACHE_HEADERS = {
    'Cache-Control': 'no-cache, no-store, private, must-revalidate, max-age=0',
    'Expires': '0',
    'Pragma': 'no-cache',
}


@router.get('/image')
async def golden_test_image(
        _electron: Annotated[None, Depends(require_electron)],
        mod: str,
        image_path: str,
):
    """
    Stream a screenshot of the collection folder of a mod

    Args:
        _electron (None): Electron-only gate, a remote browser has no token
        mod (str): Name of the mod, a query parameter
        image_path (str): Path relative to `<mod root>/collection`, a query
            parameter, like the path of the `image` decorator

    Returns:
        FileResponse: The screenshot, never cached by the client

    Raises:
        HTTPExceptionJson: 403 when the request is not from the desktop client,
            when the path is invalid or when the file is not an image;
            404 when the screenshot does not exist
    """
    try:
        file = resolve_collection_image(mod, image_path)
    except GoldenTestPathError as e:
        raise HTTPExceptionJson(
            status.HTTP_403_FORBIDDEN,
            err='GOLDEN_TEST_IMAGE_INVALID_PATH',
            data={'path': image_path, 'reason': e.message},
        ) from None

    if os.path.splitext(file)[1].lower() not in MEDIA_TYPES:
        raise HTTPExceptionJson(
            status.HTTP_403_FORBIDDEN,
            err='GOLDEN_TEST_IMAGE_ONLY',
            data={'path': image_path},
        )
    media_type = MEDIA_TYPES[os.path.splitext(file)[1].lower()]

    # The stat is handed to the response, so the file is read with the size and
    # the mtime that were just checked instead of stat-ing the path again
    try:
        st = os.stat(file)
    except OSError:
        raise HTTPExceptionJson(
            status.HTTP_404_NOT_FOUND,
            err='GOLDEN_TEST_IMAGE_NOT_FOUND',
            data={'path': image_path},
        ) from None
    if not stat.S_ISREG(st.st_mode):
        raise HTTPExceptionJson(
            status.HTTP_404_NOT_FOUND,
            err='GOLDEN_TEST_IMAGE_NOT_FOUND',
            data={'path': image_path},
        )

    return FileResponse(
        file,
        headers=NO_CACHE_HEADERS,
        media_type=media_type,
        stat_result=st,
    )
