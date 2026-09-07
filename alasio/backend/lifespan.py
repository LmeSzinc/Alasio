from threading import Thread

import trio

from alasio.backend.mpipe.mpipe_backend import mpipe_backend
from alasio.backend.mpipe.token_backend import token_table
from alasio.backend.prefs import handle_stdin_set_dpi_scaling, handle_stdin_set_lang, handle_stdin_set_theme
from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.ws.ws_server import WebsocketTopicServer
from alasio.logger import logger

SHUTDOWN_EVENT = trio.Event()

# Per-connection timeout for the rotation notification (send / close):
# a stuck connection must not stall the others. notify_rotation runs
# the connections concurrently, so the whole pass is bounded by the
# slowest single connection (worst case one timeout).
ROTATION_NOTIFY_TIMEOUT = 2.0


def mpipe_recv_loop(conn, trio_token):
    """
    Args:
        conn (PipeConnection):
        trio_token:
    """

    def request_shutdown(reason):
        """
        Request the backend to shut itself down.

        The shutdown event is always set, even if logging fails: when the
        supervisor or the host process (electron) died, the stdout pipe may
        be broken and the logger could raise. The backend must never be left
        running as an orphan just because the log message could not be
        written.

        Args:
            reason (str): Log message for the shutdown
        """
        try:
            trio.from_thread.run_sync(SHUTDOWN_EVENT.set, trio_token=trio_token)
        except Exception:
            # trio loop is already gone, nothing left to signal
            pass
        try:
            logger.info(reason)
        except Exception:
            # stdout pipe broken (host process died), drop the message
            pass

    while 1:
        try:
            msg = conn.recv_bytes()
        except (EOFError, OSError):
            request_shutdown('Backend disconnected to supervisor, shutting down backend')
            break

        if msg == b'command:stop':
            request_shutdown('Backend received stop request from supervisor, shutting down backend')
            break
        elif msg.startswith(b'token:'):
            # New token from the supervisor rotation: add it to the table
            # (handle_token acknowledges back through MPipeBackend) and
            # notify the ws connections (Phase 5 rotation check).
            token = msg[6:].decode()
            token_table.handle_token(token)
            _notify_rotation(trio_token)
        elif msg.startswith(b'command:set_lang:'):
            # Host-level webapp language from the stdin contract. Parse,
            # validate and persist; keep looping (do not break).
            lang = msg.split(b':', 2)[2].decode()
            handle_stdin_set_lang(lang)
        elif msg.startswith(b'command:set_theme:'):
            # Host-level webapp theme from the stdin contract.
            theme = msg.split(b':', 2)[2].decode()
            handle_stdin_set_theme(theme)
        elif msg.startswith(b'command:set_dpi_scaling:'):
            # Host-level webapp dpi scaling from the stdin contract. A
            # single value ('true'/'false'), no config/display split.
            dpi_scaling = msg.split(b':', 2)[2].decode()
            handle_stdin_set_dpi_scaling(dpi_scaling)
        else:
            logger.warning(f'Backend received unknown msg from supervisor: {msg}')


async def _notify_one(server):
    """
    Notify one restricted-subscription connection after a token rotation,
    with a per-connection timeout: a stuck or broken connection is
    skipped without affecting the other connections.

    Args:
        server (WebsocketTopicServer): The connection to notify
    """
    try:
        with trio.fail_after(ROTATION_NOTIFY_TIMEOUT):
            if token_table.verify(server.auth_token):
                # token still in the window: ask the frontend to renew
                await server.send(ResponseEvent(t='auth', o='full', v='renew'))
            else:
                # token already evicted: standard reconnect will carry a
                # fresh token on the next handshake
                await server.close(4002, 'token rotated')
    except trio.TooSlowError:
        # send/close stuck (e.g. send buffer back-pressure), skip this
        # connection; the next rotation re-checks it
        logger.warning(f'Rotation notify timeout for {server}')
    except Exception as e:
        # a single broken connection must not abort the notification
        # of the other connections
        logger.warning(f'Rotation notify failed for {server}: {e}')


async def notify_rotation():
    """
    Notify ws connections after a token rotation: connections subscribed
    to an electron-restricted topic either receive the "renew" control
    message (token still in window) or close(4002) (token already
    evicted). Connections are notified concurrently inside a nursery,
    each with its own timeout, so one stuck connection cannot stall the
    others. Runs on the trio loop (mpipe_recv_loop calls it through
    _notify_rotation).
    """
    targets = []
    for server in list(WebsocketTopicServer.active.values()):
        # the restricted set comes from the connection's own class
        # (subclasses register their own ALL_TOPIC_CLASS)
        restricted = {
            name for name, cls in type(server).ALL_TOPIC_CLASS.items()
            if cls.REQUIRE_ELECTRON
        }
        if not restricted.intersection(server.subscribed):
            # ordinary connections without restricted subscriptions
            # never need to renew
            continue
        targets.append(server)

    if not targets:
        return
    async with trio.open_nursery() as nursery:
        for server in targets:
            nursery.start_soon(_notify_one, server)


def _notify_rotation(trio_token):
    """
    Bridge notify_rotation into the trio loop from the mpipe recv thread.

    Args:
        trio_token:
    """
    try:
        trio.from_thread.run(notify_rotation, trio_token=trio_token)
    except Exception:
        # trio loop already gone, nothing to notify
        pass


async def lifespan_restart():
    """
    Restart the entire backend
    """
    if not mpipe_backend:
        raise PermissionError(f'Cannot restart backend running without supervisor')

    # log
    logger.info('Backend received restart request from RPC, shutting down backend')

    # Send b'command:restart' to supervisor (strict: must fail loudly
    # when the pipe is unavailable)
    await trio.to_thread.run_sync(mpipe_backend.send, b'command:restart', True)

    # stop backend
    SHUTDOWN_EVENT.set()


async def lifespan_stop():
    """
    Stop the entire backend
    """
    if not mpipe_backend:
        raise PermissionError(f'Cannot stop backend running without supervisor')

    # log
    logger.info('Backend received stop request from RPC, shutting down backend')

    # Send b'command:stop' to supervisor (strict: must fail loudly
    # when the pipe is unavailable)
    await trio.to_thread.run_sync(mpipe_backend.send, b'command:stop', True)

    # stop backend
    SHUTDOWN_EVENT.set()


def announce_started():
    """
    Announce pipe readiness to the supervisor once the listeners are bound.

    recv_loop treats the first backend message as the startup confirmation
    and only then starts the stdin listener (the listener must stay stopped
    while a backend spawns, see the Windows constraint in supervisor.py).
    Without this message the confirmation falls back to the startup_timeout
    (5s), during which a command:stop written to stdin (e.g. closing the
    webapp right after it opened) would sit unread until the timeout ends,
    longer than the Electron close flow waits before force-killing the tree.

    Called by BackendConfig.create_sockets in app.py right after the
    sockets are bound, before any request can arrive. A bind failure (port
    already in use) raises before this runs, so it stays a startup failure
    and the supervisor does not restart-loop on it.
    """
    mpipe_backend.send(b'command:started')


def get_shutdown_trigger():
    """
    Get shutdown_trigger function, or None if no daemon by supervisor.
    When shutdown_trigger() runs ended, hypercorn will stop serving connections.
    """
    if not mpipe_backend:
        # no supervisor, cannot restart
        logger.info('Backend running without supervisor')
        return None

    logger.info('Backend running with supervisor')
    trio_token = trio.lowlevel.current_trio_token()

    async def shutdown_trigger():
        # if shutdown event is set, shutdown_trigger() will stop hypercorn
        await SHUTDOWN_EVENT.wait()

    thread = Thread(target=mpipe_recv_loop, args=(mpipe_backend.conn, trio_token),
                    name='mpipe_child_recv', daemon=True)
    thread.start()
    return shutdown_trigger
