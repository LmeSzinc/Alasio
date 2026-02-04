import contextlib
import os

import trio
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute

from alasio.ext.patch import patch_mimetype

patch_mimetype()

# stored context object
WorkerContext_obj = None


def patch_context_cls():
    """
    Patch should before hypercorn.trio.serve() runs
    """
    # local import
    from hypercorn.trio import worker_context, run

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
    from hypercorn.trio import worker_context, run
    run.WorkerContext = worker_context.WorkerContext


async def on_shutdown():
    """
    Do things if requested a shutdown
    """
    from alasio.backend.ws.topic import WebsocketServer
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
    from alasio.logger import logger
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
    from alasio.db.conn import SQLITE_POOL
    SQLITE_POOL.gc(wait)
    from alasio.config.entry.mod import MOD_JSON_CACHE
    MOD_JSON_CACHE.gc(wait)


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
    from alasio.logger import logger
    logger.info('Lifespan start')
    async with trio.open_nursery() as nursery:
        # start listening shutdown
        nursery.start_soon(task_listen_shutdown)
        # start gc task
        nursery.start_soon(task_gc)
        # start message bus task
        from alasio.backend.ws.topic import WebsocketServer
        nursery.start_soon(WebsocketServer.task_msgbus_global)
        nursery.start_soon(WebsocketServer.task_msgbus_config)
        # warmups
        from alasio.backend.topic.scan import ConfigScanSource
        nursery.start_soon(ConfigScanSource.create_default_config)

        # actual backend runs here
        yield
        # cancel nursery to stop task_gc()
        nursery.cancel_scope.cancel()

    # cleanup before exit
    # release db connections
    from alasio.db.conn import SQLITE_POOL
    SQLITE_POOL.release_all()
    # Terminate all workers
    from alasio.backend.worker.manager import WorkerManager
    manager: WorkerManager = WorkerManager.singleton_instance()
    if manager is not None:
        manager.close()

    logger.info('Lifespan end')


def mpipe_recv_loop(conn, trio_token, shutdown_event):
    """
    Args:
        conn (PipeConnection):
        trio_token:
        shutdown_event (trio.Event):
    """
    from alasio.logger import logger
    while 1:
        try:
            msg = conn.recv_bytes()
        except (EOFError, OSError):
            logger.info('Backend disconnected to supervisor, shutting down backend')
            trio.from_thread.run_sync(shutdown_event.set, trio_token=trio_token)
            break

        if msg == b'stop':
            logger.info('Backend received stop request from supervisor, shutting down backend')
            trio.from_thread.run_sync(shutdown_event.set, trio_token=trio_token)
            break
        else:
            logger.warning(f'Backend received unknown msg from supervisor: {msg}')


def get_shutdown_trigger():
    """
    Get shutdown_trigger function, or None if no daemon by supervisor.
    When shutdown_trigger() runs ended, hypercorn will stop serving connections.
    """
    import builtins
    from alasio.logger import logger
    conn = getattr(builtins, '__mpipe_conn__', None)
    if conn is None:
        # no supervisor, cannot restart
        logger.info('Backend running without supervisor')
        return None

    logger.info('Backend running with supervisor')
    trio_token = trio.lowlevel.current_trio_token()
    shutdown_event = trio.Event()

    async def shutdown_trigger():
        # if shutdown event is set, shutdown_trigger() will stop hypercorn
        await shutdown_event.wait()

    from threading import Thread
    thread = Thread(target=mpipe_recv_loop, args=(conn, trio_token, shutdown_event),
                    name='mpipe_child_recv', daemon=True)
    thread.start()
    return shutdown_trigger


def create_app():
    from alasio.ext.starapi.router import StarAPI
    app = StarAPI(lifespan=lifespan)

    # All APIs should under /api
    # Builtin APIs
    from alasio.backend.auth import auth
    app.add_router('/api', auth.router)

    # Global websocket
    from alasio.backend.ws.topic import WebsocketServer
    app.routes.append(WebSocketRoute('/api/ws', WebsocketServer.endpoint))

    # Alasio should be a local service and should not be exposed on public network
    # We serve in-memory robots.txt to deny all spiders
    # this router should be added before mounting static files
    async def robots_txt(request):
        return PlainTextResponse(content='User-agent: *\nDisallow: /', media_type='text/plain')

    app.routes.append(Route('/robots.txt', robots_txt))

    # Mound dev files
    from alasio.backend.dev.assets import NoCacheStaticFiles, SPANoCacheStaticFiles
    from alasio.config.entry.loader import MOD_LOADER
    from alasio.ext.starapi.router import APIRouter

    # Mount all mod assets
    assets_router = APIRouter('/dev_assets')
    for mod in MOD_LOADER.dict_mod.values():
        path = f'/{mod.name}/{mod.entry.path_assets}'
        NoCacheStaticFiles.mount(assets_router, path, directory=dict, check_dir=False)
    app.add_router('/api', assets_router)

    # Mount mod APIs
    pass

    # Mount mod static files
    pass

    # Mount static files
    from alasio.ext.path import PathStr
    # for frontend local builds
    root = PathStr(__file__).uppath(3).joinpath('frontend/build')
    SPANoCacheStaticFiles.mount(app, '/', directory=root, name='static')
    # since static files mounted at "/", any route after it won't work

    return app


def create_config(args=None):
    """
    Args:
        args (list[str] | None): Commandline args from supervisor level
            Use this `args` input instead of `sys.args`, as backend is a sub-process
    """
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=str, default='')
    parser.add_argument('--host', type=str, default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8000)
    parsed_args, _ = parser.parse_known_args(args)

    # set project root, so we have the right path to save ./config
    from alasio.ext import env
    if parsed_args.root:
        env.set_project_root(parsed_args.root)
    else:
        env.set_project_root(os.getcwd())
    from alasio.logger import logger
    logger.info(f'[PROJECT_ROOT] {env.PROJECT_ROOT}')

    from hypercorn import Config
    config = Config()

    # Bind address
    config.bind = [f'{parsed_args.host}:{parsed_args.port}']

    # To enable assess log
    config.accesslog = '-'

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
    import functools
    trio.run(functools.partial(serve_app, args=args))
