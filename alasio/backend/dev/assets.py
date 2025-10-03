from starlette.middleware.gzip import GZipResponder
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from alasio.config.entry.loader import MOD_LOADER
from alasio.ext.starapi.router import APIRouter


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


router = APIRouter('/dev_assets')

# Mount all mod assets
for mod in MOD_LOADER.dict_mod.values():
    app = NoCacheStaticFiles(directory=mod.path_assets, check_dir=False)
    path = f'/{mod.name}/{mod.entry.path_assets}'
    router.mount(path, app)
