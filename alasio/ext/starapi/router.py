import typing
from typing import Callable

from starlette import routing
from starlette.applications import Starlette

from alasio.ext.starapi.asgi import RequestASGI


class APIRoute(routing.Route):
    def __init__(
            self,
            path: str,
            endpoint: typing.Callable,
            *,
            methods: typing.List[str] = None,
            tags: "list[str]" = None,
            dependencies: "list[Callable]" = None
    ):
        super().__init__(
            path,
            endpoint,
            methods=methods,
        )
        self.tags: "list[str]" = tags or []
        self.dependencies = list(dependencies) if dependencies is not None else []

    def rebuild_from_prefix(self, prefix: str):
        """
        Args:
            prefix:

        Returns:
            APIRoute:
        """
        assert prefix.startswith('/'), "A path prefix must start with '/'"
        prefix = prefix.rstrip('/')
        if not prefix:
            return self

        return APIRoute(
            path=prefix + self.path,
            endpoint=self.endpoint,
            methods=self.methods,
            tags=self.tags,
            dependencies=self.dependencies,
        )


class APIMount(routing.Mount):
    def rebuild_from_prefix(self, prefix: str):
        """
        Args:
            prefix:

        Returns:
            APIRoute:
        """
        assert prefix.startswith('/'), "A path prefix must start with '/'"
        prefix = prefix.rstrip('/')
        if not prefix:
            return self

        return APIMount(
            path=prefix + self.path,
            app=self.app,
            routes=self.routes,
            name=self.name,
            # middlewares are applied to app when creating Mount object,
            # no need to pass through during re-creation
            # middleware=self.middleware,
        )


class APIRouter(routing.Router):
    def __init__(
            self,
            prefix: str = '',
            tags: "list[str]" = None,
            dependencies: "list[Callable]" = None
    ):
        self.prefix = prefix.rstrip('/')
        self.tags: "list[str]" = tags or []
        self.dependencies = list(dependencies) if dependencies is not None else []
        super().__init__()

    def mount(self, path, app, name = None) -> None:
        path = self.prefix + path
        route = APIMount(path=path, app=app, name=name)
        self.routes.append(route)

    def route(
            self,
            path: str,
            methods: typing.List[str] = None,
    ):
        path = self.prefix + path

        def decorator(func: typing.Callable):
            # convert route to ASGI app
            asgi = RequestASGI.from_route(path, func, self.dependencies)
            route = APIRoute(
                path,
                asgi,
                methods=methods,
                tags=self.tags,
                dependencies=self.dependencies,
            )
            self.routes.append(route)

        return decorator

    def get(self, path: str):
        return self.route(path, methods=['GET'])

    def put(self, path: str):
        return self.route(path, methods=['PUT'])

    def post(self, path: str):
        return self.route(path, methods=['POST'])

    def delete(self, path: str):
        return self.route(path, methods=['DELETE'])

    def options(self, path: str):
        return self.route(path, methods=['OPTIONS'])

    def head(self, path: str):
        return self.route(path, methods=['HEAD'])

    def patch(self, path: str):
        return self.route(path, methods=['PATCH'])

    def trace(self, path: str):
        return self.route(path, methods=['TRACE'])


class StarAPI(Starlette):
    """
    StarAPI is a simple FastAPI imitation using starlette + msgspec instead of starlette + pydantic
    """
    def add_router(self, prefix: str, router: APIRouter):
        routers = self.router.routes
        routers += [r.rebuild_from_prefix(prefix) for r in router.routes]
