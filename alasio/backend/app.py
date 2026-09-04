import argparse
import contextlib
import functools
import os
import platform
import socket

import trio
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute

from alasio.backend.auth import auth
from alasio.backend.dev.assets import ImageStaticFiles, SPANoCacheStaticFiles
from alasio.backend.lifespan import announce_started, get_shutdown_trigger
from alasio.backend.middleware.gate import DeploymentGateMiddleware
from alasio.backend.reactive.source import BaseSource
from alasio.backend.topic._worker import BACKEND_WORKER_MANAGER
from alasio.backend.topic.scan import ConfigScanSource
from alasio.backend.ws import renew as ws_renew
from alasio.backend.ws.context import GLOBAL_CONTEXT, GlobalContext
from alasio.backend.ws.renew import renewal_manager
from alasio.backend.ws.topic import PreviewServer, WebsocketServer
from alasio.backport.patch import patch_mimetype
from alasio.config.entry.model import MOD_JSON_CACHE
from alasio.db.conn import SQLITE_POOL
from alasio.deploy.config.model import DeployConfig
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.ext.path.calc import joinnormpath
from alasio.ext.starapi.router import APIRouter, StarAPI
from alasio.logger import logger

patch_mimetype()

# stored context object
WorkerContext_obj = None


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
    # idle sources (one-shot / viewport / DevAssets sources): remove
    # instances that have been idle (no subscriber) for IDLE_TTL seconds
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
    # MOD_LOADER is instantiated at loader.py module level and bound to
    # env.PROJECT_ROOT at that moment: it must be imported after
    # set_project_root() ran (create_config), so this import stays local.
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

    # Mount static files

    # for frontend local builds
    root = PathStr(__file__).uppath(3).joinpath('frontend/build')
    SPANoCacheStaticFiles.mount(app, '/', directory=root, name='static')
    # since static files mounted at "/", any route after it won't work

    return app


def apply_hypercorn_exclusivity_patch():
    """
    Apply cross-platform port exclusivity patch to Hypercorn Config class
    """
    from hypercorn import Config

    original_create_sockets = Config._create_sockets
    system_platform = platform.system()

    def patched_create_sockets(self, binds, type_=socket.SOCK_STREAM):
        original_setsockopt = socket.socket.setsockopt

        def mocked_setsockopt(sock_self, level, optname, value):
            # --- Windows special handling ---
            if system_platform == "Windows":
                # Intercept REUSEADDR setting
                if level == socket.SOL_SOCKET and optname == socket.SO_REUSEADDR:
                    # On Windows, we replace REUSEADDR with EXCLUSIVEADDRUSE
                    # This prevents port preemption, and if the port is already in use, bind() will raise an error
                    exclusive_opt = getattr(socket, "SO_EXCLUSIVEADDRUSE", -5)
                    return original_setsockopt(sock_self, level, exclusive_opt, 1)

            # --- Unix handling ---
            # On Linux/macOS, Hypercorn's default SO_REUSEADDR setting is correct,
            # as long as workers=1, it won't set SO_REUSEPORT, thus ensuring bind() conflicts.

            return original_setsockopt(sock_self, level, optname, value)

        # monkeypatch socket.setsockopt
        socket.socket.setsockopt = mocked_setsockopt
        try:
            # Call original logic, which triggers our mocked_setsockopt and eventually executes bind()
            return original_create_sockets(self, binds, type_)
        except OSError as e:
            logger.critical(f'Failed to bind {binds}: {e}')
            raise
        finally:
            # rollback
            socket.socket.setsockopt = original_setsockopt

    # override _create_sockets
    Config._create_sockets = patched_create_sockets


def apply_started_announce_patch():
    """
    Patch Config.create_sockets to announce pipe readiness after binding.

    The backend announces b'command:started' over the supervisor pipe once
    the listeners are bound (see lifespan.announce_started): the
    supervisor's recv_loop ends its startup window on the first backend
    message, and the stdin listener (command:stop channel) only starts
    after that confirmation. Without the announce the confirmation would
    wait out the whole startup_timeout (5s), so a close right after the
    webapp opened would sit unread in the stdin pipe until Electron
    force-kills the tree.

    The wrapper must run only after the sockets bound successfully: a bind
    failure (port in use) raises inside create_sockets, no announce is
    sent, and the supervisor still treats the crash as a startup failure
    instead of restart-looping. create_sockets (public) is wrapped rather
    than _create_sockets because SSL mode binds several socket lists in
    one create_sockets call.
    """
    from hypercorn import Config

    original_create_sockets = Config.create_sockets

    def patched_create_sockets(self):
        sockets = original_create_sockets(self)
        # listeners are bound and about to serve: tell the supervisor
        # (no-op when running without a supervisor pipe)
        announce_started()
        return sockets

    Config.create_sockets = patched_create_sockets


def create_config(args=None):
    """
    Args:
        args (list[str] | None): Commandline args from supervisor level
            Use this `args` input instead of `sys.args`, as backend is a sub-process
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=str, default='')
    parser.add_argument('--host', type=str, default='')
    parser.add_argument('--port', type=int, default=0)
    parsed_args, _ = parser.parse_known_args(args)

    # set project root, so we have the right path to save ./config
    if parsed_args.root:
        env.set_project_root(parsed_args.root)
        os.chdir(parsed_args.root)
    else:
        env.set_project_root(os.getcwd())
    logger.info(f'[PROJECT_ROOT] {env.PROJECT_ROOT}')
    apply_hypercorn_exclusivity_patch()
    apply_started_announce_patch()
    deploy = DeployConfig().config.data

    # build host port
    if parsed_args.host:
        host = parsed_args.host
    elif deploy.Backend.Host:
        host = deploy.Backend.Host
    else:
        host = '0:0:0:0'
    if parsed_args.port:
        port = parsed_args.port
    elif deploy.Backend.Port:
        port = deploy.Backend.Port
    else:
        port = 8000

    # build hypercorn config
    from hypercorn import Config
    config = Config()
    config.bind = [f'{host}:{port}']

    # SSL wiring: when both key and cert are configured the deployment
    # auto-enters public mode (DeploymentGateMiddleware mode detection)
    # and https is served. Note the field names: hypercorn Config uses
    # `keyfile` / `certfile` (not the uvicorn-style `ssl_keyfile` /
    # `ssl_certfile`), assigning the wrong names would silently create
    # plain instance attributes and leave the port plaintext.
    if deploy.Backend.WebuiSSLKey and deploy.Backend.WebuiSSLCert:
        config.keyfile = deploy.Backend.WebuiSSLKey
        config.certfile = deploy.Backend.WebuiSSLCert

    # To enable assess log
    # config.accesslog = '-'

    return config


async def serve_app(args=None):
    from hypercorn.trio import serve

    config = create_config(args)
    app = create_app()

    shutdown_trigger = get_shutdown_trigger()

    patch_context_cls()
    await serve(app, config, shutdown_trigger=shutdown_trigger)


def run(args=None):
    """
    Backend entry point
    """
    logger.info('Backend entry point')
    trio.run(functools.partial(serve_app, args=args))
