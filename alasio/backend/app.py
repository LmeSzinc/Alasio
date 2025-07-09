from starlette.middleware.gzip import GZipResponder
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.staticfiles import StaticFiles


class NoCacheStaticFiles(StaticFiles):
    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        # No cache for static files
        # We've seen too many styling issues in ALAS. We use electron as client and chromium caches static files on
        # user's disk. Those files may get broke for unknown reason, causing the styling issues.
        # To fix that, we tell the browsers don't cache any. Bandwidth increase should be acceptable on local service.
        resp.headers.setdefault('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        resp.headers.setdefault('Expires', '0')
        resp.headers.setdefault('Pragma', 'no-cache')

        # GZipMiddleware
        resp = GZipResponder(resp, minimum_size=500, compresslevel=9)

        return resp


def create_app():
    from alasio.ext.starapi.router import StarAPI
    app = StarAPI()

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
    root = PathStr(__file__).uppath(2).joinpath('ext')
    static_app = NoCacheStaticFiles(directory=root, html=True)
    app.mount('/', static_app, name='static')

    # Mount mod APIs
    pass

    # Mount mod static files
    pass

    return app


if __name__ == '__main__':
    import trio
    from hypercorn import Config
    from hypercorn.trio import serve

    config = Config()
    config.bind = '0.0.0.0:8000'
    # config.bind = ['0.0.0.0:8000', '[::]:8000']
    # To enable assess log
    config.accesslog = '-'

    trio.run(serve, create_app(), config)
