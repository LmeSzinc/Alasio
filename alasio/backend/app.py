def create_app():
    from alasio.backend.starapi.router import StarAPI

    app = StarAPI()

    # All APIs should under /api
    # Builtin APIs
    from alasio.backend.auth import auth
    app.add_router('/api', auth.router)

    # Global websocket
    from starlette.routing import WebSocketRoute
    from alasio.backend.ws.ws import WebsocketServer
    app.routes.append(WebSocketRoute('/api/ws', WebsocketServer.endpoint))

    # Mod APIs
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
    # config.accesslog = '-'

    trio.run(serve, create_app(), config)
