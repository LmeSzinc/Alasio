import trio

SHUTDOWN_EVENT = trio.Event()


def mpipe_recv_loop(conn, trio_token):
    """
    Args:
        conn (PipeConnection):
        trio_token:
    """
    from alasio.logger import logger
    while 1:
        try:
            msg = conn.recv_bytes()
        except (EOFError, OSError):
            logger.info('Backend disconnected to supervisor, shutting down backend')
            trio.from_thread.run_sync(SHUTDOWN_EVENT.set, trio_token=trio_token)
            break

        if msg == b'stop':
            logger.info('Backend received stop request from supervisor, shutting down backend')
            trio.from_thread.run_sync(SHUTDOWN_EVENT.set, trio_token=trio_token)
            break
        else:
            logger.warning(f'Backend received unknown msg from supervisor: {msg}')


async def lifespan_restart():
    """
    Restart the entire backend
    """
    import builtins
    conn = getattr(builtins, '__mpipe_conn__', None)
    if conn is None:
        raise PermissionError(f'Cannot restart backend running without supervisor')

    # log
    from alasio.logger import logger
    logger.info('Backend received restart request from RPC, shutting down backend')

    # Send b'restart' to supervisor
    await trio.to_thread.run_sync(conn.send_bytes, b'restart')

    # stop backend
    SHUTDOWN_EVENT.set()


async def lifespan_stop():
    """
    Stop the entire backend
    """
    import builtins
    conn = getattr(builtins, '__mpipe_conn__', None)
    if conn is None:
        raise PermissionError(f'Cannot stop backend running without supervisor')

    # log
    from alasio.logger import logger
    logger.info('Backend received stop request from RPC, shutting down backend')

    # Send b'restart' to supervisor
    await trio.to_thread.run_sync(conn.send_bytes, b'stop')

    # stop backend
    SHUTDOWN_EVENT.set()


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

    async def shutdown_trigger():
        # if shutdown event is set, shutdown_trigger() will stop hypercorn
        await SHUTDOWN_EVENT.wait()

    from threading import Thread
    thread = Thread(target=mpipe_recv_loop, args=(conn, trio_token),
                    name='mpipe_child_recv', daemon=True)
    thread.start()
    return shutdown_trigger
