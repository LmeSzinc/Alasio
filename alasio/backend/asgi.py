import argparse
import functools
import os
import platform
import socket

import trio
from hypercorn import Config

from alasio.backend.lifespan import announce_started, get_shutdown_trigger
from alasio.deploy.config.model import DeployConfig
from alasio.ext import env
from alasio.logger import logger


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


class BackendConfig(Config):
    """
    Hypercorn Config subclass that announces pipe readiness after binding.

    The backend announces b'command:started' over the supervisor pipe once
    the listeners are bound (see lifespan.announce_started): the
    supervisor's recv_loop ends its startup window on the first backend
    message, and the stdin listener (command:stop channel) only starts
    after that confirmation. Without the announce the confirmation would
    wait out the whole startup_timeout (5s), so a close right after the
    webapp opened would sit unread in the stdin pipe until Electron
    force-kills the tree.

    The announce lives in create_sockets() rather than the starlette
    lifespan because hypercorn (trio) runs the lifespan startup before it
    binds the sockets: announcing there would end the supervisor's startup
    window while the port is still unbound, and a bind failure (port in
    use) would then crash after the window ended -- the supervisor would
    restart-loop instead of treating it as a startup failure. create_sockets
    (public) is overridden rather than _create_sockets because SSL mode
    binds several socket lists in one create_sockets call, so the announce
    fires exactly once, after every list bound successfully.
    """

    def create_sockets(self):
        """
        Bind the listeners, then announce pipe readiness to the supervisor.

        A bind failure (port already in use) raises inside the super call
        before the announce, so it stays a startup failure and the
        supervisor does not restart-loop on it.

        Returns:
            Sockets: hypercorn Sockets dataclass of the bound listeners
        """
        sockets = super().create_sockets()
        # listeners are bound and about to serve: tell the supervisor
        # (no-op when running without a supervisor pipe)
        announce_started()
        return sockets


def create_config(args=None):
    """
    Args:
        args (list[str] | None): Commandline args from supervisor level
            Use this `args` input instead of `sys.args`, as backend is a sub-process
    """
    logger.hr('Start', level=0)

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
    logger.attr('PROJECT_ROOT', env.PROJECT_ROOT)
    logger.attr('ELECTRON', bool(env.ELECTRON))
    DeployConfig().config.show()
    DeployConfig().config.write()

    apply_hypercorn_exclusivity_patch()
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
    config = BackendConfig()
    config.bind = [f'{host}:{port}']
    logger.attr('Bind', config.bind)

    # SSL wiring: when both key and cert are configured the deployment
    # auto-enters public mode (DeploymentGateMiddleware mode detection)
    # and https is served. Note the field names: hypercorn Config uses
    # `keyfile` / `certfile` (not the uvicorn-style `ssl_keyfile` /
    # `ssl_certfile`), assigning the wrong names would silently create
    # plain instance attributes and leave the port plaintext.
    if deploy.Backend.WebuiSSLKey and deploy.Backend.WebuiSSLCert:
        config.keyfile = deploy.Backend.WebuiSSLKey
        config.certfile = deploy.Backend.WebuiSSLCert
        logger.attr('SSL', True)
    else:
        logger.attr('SSL', False)

    # To enable assess log
    # config.accesslog = '-'

    return config


async def serve_app(args=None):
    from hypercorn.trio import serve

    config = create_config(args)

    # local import, after create_config(): the app chain must only be
    # imported once PROJECT_ROOT is set (see the module header). The
    # hypercorn WorkerContext patch stays in app.py next to the lifespan
    # that consumes it.
    from alasio.backend.app import create_app, patch_context_cls

    app = create_app()

    shutdown_trigger = get_shutdown_trigger()

    patch_context_cls()
    await serve(app, config, shutdown_trigger=shutdown_trigger)


def run(args=None):
    """
    Backend entry point
    """
    trio.run(functools.partial(serve_app, args=args))
