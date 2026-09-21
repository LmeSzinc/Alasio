import contextlib

import trio
from starlette import status
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute

from alasio.backend.auth import auth
from alasio.backend.dev.assets import ImageStaticFiles
from alasio.backend.frontend import SITE
from alasio.backend.middleware.gate import DeploymentGateMiddleware
from alasio.backend.reactive.source import BaseSource
from alasio.backend.restart import resume_after_restart
from alasio.backend.topic._worker import BACKEND_WORKER_MANAGER
from alasio.backend.topic.scan import ConfigScanSource
from alasio.backend.ws import renew as ws_renew
from alasio.backend.ws.context import GLOBAL_CONTEXT, GlobalContext
from alasio.backend.ws.renew import renewal_manager
from alasio.backend.ws.topic import PreviewServer, WebsocketServer
from alasio.backport.patch import patch_mimetype
from alasio.config.entry.model import MOD_JSON_CACHE
from alasio.db.conn import SQLITE_POOL
from alasio.ext.path.calc import joinnormpath
from alasio.ext.starapi.param import HTTPExceptionJson
from alasio.ext.starapi.router import APIRouter, StarAPI
from alasio.logger import logger

patch_mimetype()

# stored context object
WorkerContext_obj = None

# The catch-all route of the API namespace answers every method: a function
# route with methods=None answers GET only, and a non-GET request would be a
# partial match (a 405 candidate) that lets the frontend mount win.
API_NOT_FOUND_METHODS = ['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE']


async def api_not_found(request):
    """
    Answer an unmatched path of the API namespace.

    The frontend page server is mounted at "/" as the last route, so an
    unknown path under /api would be answered with the SPA page and status 200
    without this catch-all: a typo in an endpoint URL reads as a successful
    call and hides the mistake (the frontend only logs "Invalid JSON
    response"). The route is added after every API route and before the
    frontend mount, so only the paths no route matched reach it.

    Args:
        request (Request): The request of the unmatched path

    Raises:
        HTTPExceptionJson: 404 with the unmatched path
    """
    raise HTTPExceptionJson(
        status.HTTP_404_NOT_FOUND, err='API_NOT_FOUND', data={'path': request.url.path})


def patch_context_cls():
    """
    Patch should before hypercorn.trio.serve() runs
    """
    # local import
    from hypercorn.trio import run, worker_context

    class WorkerContextTracking(worker_context.WorkerContext):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            # store self so we can access context in lifespan
            global WorkerContext_obj
            WorkerContext_obj = self

    # monkey patch WorkerContext
    run.WorkerContext = WorkerContextTracking


def restore_context_cls():
    """
    Restore should before lifespan starts
    It should be fine without restore, as WorkerContext only created once,
    but for safety we restore it asap.
    """
    # local import
    from hypercorn.trio import run, worker_context
    run.WorkerContext = worker_context.WorkerContext


async def on_shutdown():
    """
    Do things if requested a shutdown
    """
    await WebsocketServer.close_all_connections()


async def task_listen_shutdown():
    """
    Coroutine task that listens shutdown request.

    This is a monkey patch magic to read from hypercorn
    which relays on:
        # hypercorn/tri/run.py worker_serve()
        try:
            async with trio.open_nursery(strict_exception_groups=True) as nursery:
                ...
        finally:
            await context.terminated.set()
            server_nursery.cancel_scope.deadline = trio.current_time() + config.graceful_timeout

    If application receives CTRL+C, nursery is cancelled and context.terminated is set.
    The idea is to monkey patch WorkerContext to capture the local variable `context = WorkerContext(max_requests)`
    in function worker_serve(), so we can wait for the signal.

    We have a 3s window time to gracefully shutdown before the outer `server_nursery` cancelled
    (which will trigger force shutdown)
    """
    if WorkerContext_obj is None:
        logger.error(f'Empty WorkerContext_obj, cannot listen to shutdown')
        return

    try:
        # wait until hypercorn shutdown TCP connections but not yet shutdown server_nursery
        await WorkerContext_obj.terminated.wait()
        # we have 3s by default to gracefully shutdown our websocket connections
        await on_shutdown()
    except Exception as e:
        logger.error(f'task_listen_shutdown error: {e}')
        logger.exception(e)


def sync_task_gc(wait=8):
    """
    Synchronous task that do garbage collect periodically at background
    """
    logger.check_rotate()
    SQLITE_POOL.gc(wait)
    MOD_JSON_CACHE.gc(wait)
    # frontend: the decoded bodies of the clients without gzip that stopped
    # asking for them (the gzip bodies are the payload, they stay resident)
    SITE.identity_cache.gc(wait)
    # data-expiry gc of topic sources: instances whose data TTL expired
    # (membership is static, GC=True classes only; NoCachePush removes
    # itself on the last unsubscribe and never appears here)
    BaseSource.gc_idle()
    # renewal codes: expiry scan, the main cleanup hook
    renewal_manager.gc()


async def task_gc(wait=8):
    """
    Coroutine task that do garbage collect periodically at background

    wait=8 is a magic number. Trio working thread exits after 10s of idle,
    so wait=8 would ensure gc thread won't start/stop everytime, and we have a free thread when gc is not running
    """
    while 1:
        # sleep first, no need to do gc at startup
        await trio.sleep(wait)

        try:
            await trio.to_thread.run_sync(sync_task_gc)
        except trio.Cancelled:
            # We've got a CTRL+C during GC
            raise
        except Exception as e:
            logger.error(f'task_gc error: {e}')
            logger.exception(e)


@contextlib.asynccontextmanager
async def lifespan(app):
    """
    A global starlette lifespan
    """
    restore_context_cls()
    logger.info('Lifespan start')
    async with trio.open_nursery() as nursery:
        # inject global context
        GLOBAL_CONTEXT.global_nursery = nursery
        GLOBAL_CONTEXT.trio_token = trio.lowlevel.current_trio_token()
        # start listening shutdown
        nursery.start_soon(task_listen_shutdown)
        # start gc task
        nursery.start_soon(task_gc)
        # warmups
        nursery.start_soon(ConfigScanSource.create_default_config)
        # the frontend: one worker thread reads the manifest and every file in
        # the background, the 5s startup window of the supervisor must not be
        # spent on the whole tree (requests prepare their file on demand)
        nursery.start_soon(SITE.warm_up)
        # auto-resume of the workers recorded before a graceful restart: a
        # no-op without the one-shot credential (normal cold start). The stale
        # resume file cleanup is part of this task: it must run after the read
        nursery.start_soon(resume_after_restart)

        # actual backend runs here
        yield
        # cancel nursery to stop task_gc()
        nursery.cancel_scope.cancel()

    # cleanup before exit
    # Terminate all workers
    BACKEND_WORKER_MANAGER.close()
    # release db connections
    SQLITE_POOL.release_all()
    # clear global context
    GlobalContext.singleton_clear()

    logger.info('Lifespan end')


def create_app():
    app = StarAPI(lifespan=lifespan)

    # Global admission + login middleware: DeploymentGateMiddleware
    # (rules A/B first: 403 / 4001, then the JWT login layer: 401).
    # The two layers are merged into one middleware with a fixed order
    # and must never be split or reordered (the login check relies on
    # rule A having run first). starlette wraps the app with
    # middlewares in reverse order of add_middleware, so the gate is
    # the outermost layer.
    app.add_middleware(DeploymentGateMiddleware)

    # All APIs should under /api
    # Builtin APIs
    app.add_router('/api', auth.router)

    # Renewal code endpoint: POST /api/ws/renew (require_login +
    # require_electron), mounted after the auth router
    app.add_router('/api', ws_renew.router)

    # Global websocket
    app.routes.append(WebSocketRoute('/api/ws', WebsocketServer.endpoint))
    app.routes.append(WebSocketRoute('/api/preview', PreviewServer.endpoint))

    # Alasio should be a local service and should not be exposed on public network
    # We serve in-memory robots.txt to deny all spiders
    # this router should be added before mounting static files
    async def robots_txt(request):
        return PlainTextResponse(content='User-agent: *\nDisallow: /', media_type='text/plain')

    app.routes.append(Route('/robots.txt', robots_txt))

    # Mound dev files
    # The local import is kept as the explicit marker of the import contract:
    # MOD_LOADER is instantiated at loader.py module level and bound to
    # env.PROJECT_ROOT at that moment, so the backend may only import this
    # module after set_project_root() ran (create_config) -- asgi.py imports
    # the app lazily for exactly that reason.
    from alasio.config.entry.loader import MOD_LOADER

    # Mount all mod assets
    assets_router = APIRouter('/dev_assets')
    for mod in MOD_LOADER.dict_mod.values():
        path = f'/{mod.name}/{mod.entry.path_assets}'
        ImageStaticFiles.mount(
            assets_router, path, directory=joinnormpath(mod.entry.root, mod.entry.path_assets), check_dir=False)
    app.add_router('/api', assets_router)

    # Mount mod APIs
    pass

    # Mount mod static files
    pass

    # Unknown paths of the API namespace: keep the API error format instead of
    # falling through to the frontend page server below (see api_not_found)
    app.routes.append(Route('/api', api_not_found, methods=API_NOT_FOUND_METHODS))
    app.routes.append(Route('/api/{path:path}', api_not_found, methods=API_NOT_FOUND_METHODS))

    # Mount static files

    # Mount the frontend page server: the files listed in the delivered
    # frontend-manifest.pack are served from memory (alasio.backend.frontend).
    # Nothing is read here, the warmup task of the lifespan loads the manifest
    # and the files; a deployment always has them, a checkout does not (vite
    # serves the frontend there) and the site answers 404.
    app.mount('/', SITE, name='static')
    # since static files mounted at "/", any route after it won't work

    return app
