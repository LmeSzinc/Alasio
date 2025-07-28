import contextlib

import trio
from starlette.middleware.gzip import GZipResponder
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.staticfiles import StaticFiles

from alasio.ext.env import patch_mimetype

patch_mimetype()


async def task_gc(wait=8):
    """
    Coroutine task that do garbage collect periodically at background

    wait=8 is a magic number. Trio working thread exits after 10s of idle,
    so wait=8 would ensure gc thread won't start/stop everytime, and we have a free thread when gc is not running
    """
    from alasio.logger import logger
    while 1:
        # sleep first, no need to do gc at startup
        await trio.sleep(wait)

        from alasio.db.conn import SQLITE_POOL
        try:
            await trio.to_thread.run_sync(SQLITE_POOL.gc, wait)
        except trio.Cancelled:
            # We've got a CTRL+C during GC
            raise
        except Exception as e:
            logger.exception(e)


@contextlib.asynccontextmanager
async def lifespan(app):
    """
    A global starlette lifespan
    """
    async with trio.open_nursery() as nursery:
        # startup
        nursery.start_soon(task_gc)

        # actual backend runs here
        yield
        # cancel nursery to stop task_gc()
        nursery.cancel_scope.cancel()

    # cleanup
    from alasio.db.conn import SQLITE_POOL
    SQLITE_POOL.release_all()


class NoCacheStaticFiles(StaticFiles):
    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        # No cache for static files
        # We've seen too many styling issues in ALAS. We use electron as client and chromium caches static files on
        # user's disk. Those files may get broke for unknown reason, causing the styling issues.
        # To fix that, we tell the browsers don't cache any. Bandwidth increase should be acceptable on local service.
        resp.headers.setdefault('Cache-Control', 'private, must-revalidate, max-age=0')
        resp.headers.setdefault('Expires', '0')
        resp.headers.setdefault('Pragma', 'no-cache')

        # GZipMiddleware
        resp = GZipResponder(resp, minimum_size=500, compresslevel=9)

        return resp


def create_app():
    from alasio.ext.starapi.router import StarAPI
    app = StarAPI(lifespan=lifespan)

    # All APIs should under /api
    # Builtin APIs
    from alasio.backend.auth import auth
    app.add_router('/api', auth.router)

    # Global websocket
    from alasio.backend.topic import WebsocketServer
    app.routes.append(WebSocketRoute('/api/ws', WebsocketServer.endpoint))

    # Alasio should be a local service and should not be exposed on public network
    # We serve in-memory robots.txt to deny all spiders
    # this router should be added before mounting static files
    async def robots_txt(request):
        return PlainTextResponse(content='User-agent: *\nDisallow: /', media_type='text/plain')

    app.routes.append(Route('/robots.txt', robots_txt))

    # Mount static files
    from alasio.ext.path import PathStr
    # for frontend local builds
    root = PathStr(__file__).uppath(3).joinpath('frontend/build')
    static_app = NoCacheStaticFiles(directory=root, html=True)
    app.mount('/', static_app, name='static')

    # Mount mod APIs
    pass

    # Mount mod static files
    pass

    return app


if __name__ == '__main__':
    from hypercorn import Config
    from hypercorn.trio import serve

    config = Config()
    # No poor wait, just exit directly on CTRL+C
    config.graceful_timeout = 0.

    # Bind address
    config.bind = '0.0.0.0:8000'
    # config.bind = ['0.0.0.0:8000', '[::]:8000']

    # To enable assess log
    config.accesslog = '-'

    trio.run(serve, create_app(), config)
