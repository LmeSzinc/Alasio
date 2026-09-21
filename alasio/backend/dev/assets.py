import os

from starlette import status
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from alasio.ext.starapi.param import HTTPExceptionJson
from alasio.logger import logger


class NoCacheStaticFiles(StaticFiles):
    """
    Static file server with no-cache headers.

    Subclasses:
    - ImageStaticFiles: mod dev assets, image-only
    """

    def file_response(self, full_path, stat_result, scope, status_code=status.HTTP_200_OK):
        resp = super().file_response(full_path, stat_result, scope, status_code)
        if not isinstance(resp, FileResponse):
            # return NotModifiedResponse directly
            return resp

        # No cache for static files
        # We've seen too many styling issues in ALAS. We use electron as client and chromium caches static files on
        # user's disk. Those files may get broke for unknown reason, causing the styling issues.
        # To fix that, we tell the browsers don't cache any. Bandwidth increase should be acceptable on local service.
        resp.headers.setdefault('Cache-Control', 'no-cache, no-store, private, must-revalidate, max-age=0')
        resp.headers.setdefault('Expires', '0')
        resp.headers.setdefault('Pragma', 'no-cache')

        # No compression: the files of this server are images, already
        # compressed, and wrapping the response in GZipResponder set
        # Content-Encoding without checking Accept-Encoding (RFC 9110: an
        # encoding the client did not ask for must never be sent) while
        # burning CPU on every response.

        return resp

    @classmethod
    def mount(
            cls,
            router,
            path,
            name: "str | None" = None,
            directory: "PathLike | None " = None,
            packages: "list[str | tuple[str, str]] | None" = None,
            html: bool = False,
            check_dir: bool = True,
            follow_symlink: bool = False,
    ):
        """
        Safely mount a directory to router or app
        """
        try:
            app = cls(directory=directory, packages=packages, html=html,
                      check_dir=check_dir, follow_symlink=follow_symlink)
        except RuntimeError as e:
            logger.error(f'Mount static files failed: {e}')
            return
        router.mount(path, app, name=name)


class ImageStaticFiles(NoCacheStaticFiles):
    """
    Static file server for mod dev assets: image-only.

    Only image files are served; any other content is rejected with 403
    (a mod asset directory must never serve executable content, e.g. an
    html file that would run without a CSP). No CSP is attached here:
    images do not need one.
    """

    # image whitelist; svg is deliberately absent (svg can embed scripts)
    IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}

    async def get_response(self, path, scope):
        """
        Reject non-image content outright.

        Args:
            path (str): The request path
            scope (Scope):

        Raises:
            HTTPExceptionJson: 403 when the file is not an image
        """
        suffix = os.path.splitext(path)[1].lower()
        if suffix not in self.IMAGE_EXTENSIONS:
            raise HTTPExceptionJson(status.HTTP_403_FORBIDDEN, err='ASSETS_IMAGE_ONLY')
        return await super().get_response(path, scope)
