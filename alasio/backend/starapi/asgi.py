import functools
import inspect
import types
import typing
from typing import Union

import msgspec
from msgspec.json import decode, encode
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send
from starlette.websockets import WebSocket
from typing_extensions import Annotated, get_args, get_origin

from alasio.backend.starapi.param import Body, Cookie, Form, Json, Path, Query, RequestInject, RequestModel, \
    ResponseInject, SetCookie, ValidationError

SIG_EMPTY = inspect.Signature.empty

# Pre-fetch UnionType if available (Python 3.10+) to avoid repeated hasattr checks
UNION_TYPE = getattr(types, "UnionType", object())
NONE_TYPE = type(None)
DI_INSTANCE = (
    # starapi dependency injecct
    RequestInject,
    ResponseInject,
)
DI_CLASS = (
    # starlette request
    Request,
    WebSocket,
    # request model
    msgspec.Struct
)


def isdi(obj):
    if isinstance(obj, DI_INSTANCE):
        return True
    if inspect.isclass(obj) and issubclass(obj, (DI_INSTANCE, DI_CLASS)):
        return True
    return False


def iscoroutinefunction_or_partial(obj) -> bool:
    """
    Correctly determines if an object is a coroutine function,
    including those wrapped in functools.partial objects.
    """
    while isinstance(obj, functools.partial):
        obj = obj.func
    return inspect.iscoroutinefunction(obj)


class MsgspecResponse(JSONResponse):
    """
    JSONResponse but encodes with msgspec
    """

    def render(self, content: typing.Any) -> bytes:
        return encode(content)


class MsgspecRequest(Request):
    """
    starlette Request but parse json using msgspec
    """

    async def json(self) -> typing.Any:
        if not hasattr(self, "_json"):
            body = await self.body()
            try:
                self._json = decode(body)
            except msgspec.DecodeError:
                raise ValidationError('Not a valid json', loc=['body'])
        return self._json


class RequestASGI:
    def __init__(self, endpoint, request_di, response_di):
        self.endpoint = endpoint
        # Key: arg name
        # Value: DependencyInject object
        self.request_di: "dict[str, RequestInject]" = request_di
        self.response_di: "dict[str, ResponseInject]" = response_di

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """
        Act like ASGI class
        """
        request = MsgspecRequest(scope, receive=receive, send=send)

        # prepare request inject
        dict_dep = {}
        try:
            for name, dep in self.request_di.items():
                dict_dep[name] = await dep.from_request(request)
            for name, dep in self.response_di.items():
                dict_dep[name] = dep
        except ValidationError as e:
            response = MsgspecResponse(e.to_error_response(), status_code=422)
            await response(scope, receive, send)
            return

        # run endpoint
        response = await self.endpoint(**dict_dep)
        # convert endpoint result to response
        if not isinstance(response, Response):
            response = MsgspecResponse(response)

        # prepare response inject
        for name, dep in self.response_di.items():
            dep.from_response(response)

        await response(scope, receive, send)

    @classmethod
    def from_route(cls, route: Route):
        request_di = {}
        response_di = {}
        path_params = route.param_convertors

        assert iscoroutinefunction_or_partial(route.endpoint), 'Endpoint must be an async function'
        sig = inspect.signature(route.endpoint)
        for name, sig_param in sig.parameters.items():
            # name, format, param, default
            annotation = sig_param.annotation
            default = sig_param.default

            # No annotation, just "path" or "name='unknown'"
            # treat as Path or Query
            if annotation is SIG_EMPTY:
                param = Path(name, default=default) if name in path_params else Query(name, default=default)
                request_di[name] = param
                continue

            # check NoneType
            assert annotation is not NONE_TYPE, f'Annotation cannot be NoneType, name={name}, typehint={annotation}'
            # having annotation
            param = SIG_EMPTY
            origin = get_origin(annotation)
            if origin is Annotated:
                # Annotated
                anno_args = get_args(annotation)
                annotation = anno_args[0]
                # update origin
                origin = get_origin(annotation)
                # The first starapi param in Annotated
                for arg in anno_args[1:]:
                    if isdi(arg):
                        param = arg
                        break
                # no dependency inject, probably name:Annotated[str, Doc(...)]
                # treat as Path or Query
                if param is SIG_EMPTY:
                    param = Path(name, default=default) if name in path_params else Query(name, default=default)
                    param.set_formatter(annotation)
                    request_di[name] = param
                    continue

            # extract first from union
            if origin is Union or origin is UNION_TYPE:
                args = [anno for anno in get_args(annotation) if anno is not NONE_TYPE]
                args_count = len(args)
                assert args_count <= 1, f'Union is a NoneType, name={name}, typehint={annotation}'
                assert args_count == 1, f'Cannot extract type from union, name={name}, typehint={annotation}'
                annotation = args[0]

            # ensure param is set
            if param is SIG_EMPTY:
                if isdi(annotation):
                    param = annotation
                    annotation = SIG_EMPTY
                else:
                    # no dependency inject, probably name:str, name:Optional[str]
                    # treat as Path or Query
                    if name in path_params:
                        param = Path(name, default=default)
                    else:
                        param = Query(name, default=default)
                    param.set_formatter(annotation)
                    request_di[name] = param
                    continue

            # format param
            is_class = inspect.isclass(param)
            if is_class and issubclass(param, (Path, Query, Cookie)):
                param = param()
                # convert to object, no elif
            if isinstance(param, (Path, Query, Cookie)):
                # If key is not set, use arg name
                if not param.key:
                    param.key = name
                if param.default is SIG_EMPTY:
                    param.default = default
                # Path key must pair with path_params
                if not isinstance(param, (Query, Cookie)):
                    assert param.key in path_params, f'Path("{param.key}") is not in path_params, path={route.path}'
                param.set_formatter(annotation)
            # request model
            elif is_class and issubclass(param, msgspec.Struct):
                param = RequestModel(param)
            # starlette.Request methods
            elif isinstance(param, (Body, Json, Form)):
                pass
            elif is_class and issubclass(param, (Body, Json, Form)):
                param = param()
            # starlette objects
            elif is_class and issubclass(param, (Request, WebSocket)):
                param = RequestInject()
            # response inject
            elif isinstance(param, SetCookie):
                response_di[name] = param
                continue
            elif is_class and issubclass(param, SetCookie):
                response_di[name] = param()
                continue
            # unknown
            else:
                raise AssertionError(f'Unknown annotation, name={name}, typehint={annotation}')

            request_di[name] = param

        return cls(
            endpoint=route.endpoint,
            request_di=request_di,
            response_di=response_di,
        )
