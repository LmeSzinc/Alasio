def create_app():
    from alasio.backend.auth import auth
    from alasio.backend.starapi.router import StarAPI

    app = StarAPI()

    # All APIs should under /api
    # Builtin APIs
    app.add_router('/api', auth.router)

    # Mod APIs
    pass

    return app


if __name__ == '__main__':
    import trio
    from hypercorn import Config
    from hypercorn.trio import serve

    config = Config()
    config.bind = '127.0.0.1:8000'

    # To enable assess log
    # config.accesslog = '-'

    trio.run(serve, create_app(), config)
