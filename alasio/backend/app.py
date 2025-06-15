def app():
    from alasio.backend.auth import auth
    from alasio.backend.starapi.router import StarAPI
    app = StarAPI()
    app.add_router('/', auth.router)
    return app


if __name__ == '__main__':
    import trio
    from hypercorn import Config
    from hypercorn.trio import serve

    config = Config()
    config.bind = '127.0.0.1'
    config.port = 8000

    trio.run(serve, app(), config)
