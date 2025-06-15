def app():
    from alasio.backend.auth import auth
    from alasio.backend.starapi.router import StarAPI
    app = StarAPI()
    app.add_router('/', auth.router)
    return app


if __name__ == '__main__':
    import uvicorn

    host = '127.0.0.1'
    port = 8000

    uvicorn.run(app, host=host, port=port, factory=True)
