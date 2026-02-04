from starlette.exceptions import HTTPException
from starlette.middleware.gzip import GZipResponder
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from alasio.logger import logger


class NoCacheStaticFiles(StaticFiles):
    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
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

        # GZipMiddleware
        resp = GZipResponder(resp, minimum_size=500, compresslevel=9)

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


class SPAStaticFiles(StaticFiles):
    """
    Subclass StaticFiles to serve index.html for any path that doesn't match a file.
    This is the key to letting a client-side router handle routes.
    """

    async def get_response(self, path, scope):
        try:
            # Try to get the file from the parent class
            return await super().get_response(path, scope)
        except HTTPException as e:
            # If the file is not found (404), serve index.html
            if e.status_code == 404:
                # Important: we need to serve index.html from the root path
                return await super().get_response('index.html', scope)
            # Re-raise any other exceptions
            raise e


class SPANoCacheStaticFiles(SPAStaticFiles, NoCacheStaticFiles):
    pass
