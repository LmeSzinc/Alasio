import inspect
from uuid import UUID

import msgspec
from msgspec.json import decode
from starlette.requests import Request
from starlette.responses import Response

SIG_EMPTY = inspect.Signature.empty


class ValidationError(Exception):
    """
    ValidationError like fastapi
    """

    def __init__(self, msg, loc=None):
        self.msg = msg
        self.loc = loc if loc is not None else []

    def __str__(self):
        return f'{self.__class__.__name__}(loc={self.loc}, msg={self.msg})'

    def to_error_response(self):
        """
        Get a fastapi-like error response
        """
        return {
            'detail': {
                'loc': self.loc,
                'msg': self.msg
            }
        }


class RequestInject:
    """
    Base class for dependency inject.
    Subclasses should implement from_request, receiving a Request and return Any
    """

    async def from_request(self, request: Request):
        return request


class ResponseInject:
    """
    Base class for dependency inject to modify response
    Subclasses should implement from_response, receiving a Response and return None
    """

    async def from_response(self, response: Response):
        pass


class Body(RequestInject):
    def __init__(self):
        """
        Get the request body in bytes

        Examples:
            @router.get('/item')
            async def get_item(body: Annotated[bytes, Body()]):
                # `body` is the request body in bytes
                print(body)
        """
        pass

    async def from_request(self, request: Request):
        return await request.body()


class Json(RequestInject):
    def __init__(self):
        """
        Get the data from json request

        Examples:
            @router.get('/item')
            async def get_item(data: Json()):
                # `data` is the request data decoded from json
                print(data)
        """
        pass

    async def from_request(self, request: Request):
        return await request.json()


class Form(RequestInject):
    def __init__(self):
        """
        Get the data from form request
        Note that you need to install `python-multipart` to parse form

        Examples:
            @router.get('/login')
            async def get_item(form: Annotated[FormData, Form()]):
                # `form` is the request form
                print(form)
        """
        pass

    async def from_request(self, request: Request):
        return await request.form()


class Path(RequestInject):
    loc = 'path'

    def __init__(self, key: str = '', default=SIG_EMPTY):
        """
        Get path parameter from request

        Args:
            key (str): Usually to be empty string to use the param name in endpoint,
                or a specific name to read
            default:

        Examples:
            @router.get('/{path}')
            async def login_auth(path):
                # `path` is the {path} in url
                print(path)

            @router.get('/item/{id}')
            async def login_auth(item_id: Annotated[int, Path('id')]):
                # `item_id` is the {id} in url
                # and it's an integer
                print(item_id)
        """
        self.key = key
        self.default = default
        self.formatter = str

    async def from_request(self, request: Request):
        data = request.path_params
        default = self.default
        if default is SIG_EMPTY:
            try:
                value = data[self.key]
            except KeyError:
                raise ValidationError(f'Missing "{self.key}" in {self.loc}', loc=[self.loc, self.key]) from None
        else:
            value = data.get(self.key, default)
        return self.formatter(value)

    def set_formatter(self, annotation):
        if annotation is SIG_EMPTY:
            pass
        elif annotation is str:
            pass
        elif annotation is int:
            self.formatter = self.convert_int
        elif annotation is float:
            self.formatter = self.convert_float
        elif annotation is UUID:
            self.formatter = self.convert_uuid
        else:
            raise AssertionError(f'No formatter for annotation {annotation}')

    def convert_int(self, value):
        try:
            return int(value)
        except ValueError:
            raise ValidationError(f'Not an integer', loc=[self.loc, self.key]) from None

    def convert_float(self, value):
        try:
            return float(value)
        except ValueError:
            raise ValidationError(f'Not a float', loc=[self.loc, self.key]) from None

    def convert_uuid(self, value):
        try:
            return UUID(value)
        except ValueError:
            raise ValidationError(f'Not a valid uuid', loc=[self.loc, self.key]) from None


class Query(Path):
    loc = 'query'

    def __init__(self, key: str = '', default=SIG_EMPTY):
        """
        Get query parameter from request

        Args:
            key (str): Usually to be empty string to use the param name in endpoint,
                or a specific name to read
            default:

        Examples:
            @router.get('/item')
            async def get_item(name):
                # `name` is the {name} in url /item?name=...
                print(name)

            @router.get('/item')
            async def get_item(item_id: Annotated[int, Query('id')]):
                # `item_id` is the {id} in url /item?id=...
                # and it's an integer
                print(item_id)
        """
        super().__init__(key, default=default)

    async def from_request(self, request: Request):
        data = request.query_params
        default = self.default
        if default is SIG_EMPTY:
            try:
                value = data[self.key]
            except KeyError:
                raise ValidationError(f'Missing "{self.key}" in {self.loc}', loc=[self.loc, self.key]) from None
        else:
            value = data.get(self.key, default)
        return self.formatter(value)


class Cookie(Path):
    loc = 'cookie'

    def __init__(self, key: str = '', default=SIG_EMPTY):
        """
        Get cookie from request

        Args:
            key (str): Usually to be empty string to use the param name in endpoint,
                or a specific name to read
            default:

        Examples:
            @router.get('/item')
            async def get_item(token: Cookie()):
                # `token` is the {token} in cookies
                print(name)
        """
        super().__init__(key, default=default)

    async def from_request(self, request: Request):
        data = request.cookies
        default = self.default
        if default is SIG_EMPTY:
            try:
                value = data[self.key]
            except KeyError:
                raise ValidationError(f'Missing "{self.key}" in {self.loc}', loc=[self.loc, self.key]) from None
        else:
            value = data.get(self.key, default)
        return self.formatter(value)


class RequestModel(RequestInject):
    def __init__(self, model):
        """
        Get request data in msgspec model

        Args:
            model (Type[msgspec.Struct]):

        Examples:
            class RequestItem(msgspec.Struct):
                shop_id: int
                item_id: int

            @router.post('/item')
            async def get_item(request: RequestItem):
                # `request` is the post data
                print(request)
        """
        self.model = model

    async def from_request(self, request: Request):
        data = await request.body()
        try:
            value = decode(data, type=self.model)
        except msgspec.DecodeError as e:
            raise ValidationError(str(e), loc=['body']) from None
        return value


class Depends(RequestInject):
    def __init__(self, dependency=None):
        """
        Args:
            dependency (Callable):
        """
        self.dependency = dependency

    def __repr__(self) -> str:
        attr = getattr(self.dependency, '__name__', type(self.dependency).__name__)
        return f'{self.__class__.__name__}({attr})'

    async def from_request(self, request: Request):
        return self.dependency()


class SetCookie(ResponseInject):
    def __init__(self):
        """
        Set or delete cookies in response
        """
        self.call = []

    def from_response(self, response: Response):
        for call, key, kwargs in self.call:
            if call == 'set_cookie':
                response.set_cookie(key, **kwargs)
            if call == 'delete_cookie':
                response.delete_cookie(key, **kwargs)

    def set_cookie(
            self,
            key: str,
            value: str = SIG_EMPTY,
            max_age: int = SIG_EMPTY,
            expires: int = SIG_EMPTY,
            path: str = SIG_EMPTY,
            domain: str = SIG_EMPTY,
            secure: bool = SIG_EMPTY,
            httponly: bool = SIG_EMPTY,
            samesite: str = SIG_EMPTY,
    ):
        """
        Args:
            key:
            value:
            max_age:
            expires:
            path:
            domain:
            secure:
            httponly:
            samesite: must be either 'strict', 'lax' or 'none'
        """
        kwargs = {}
        if value is not SIG_EMPTY:
            kwargs['value'] = value
        if max_age is not SIG_EMPTY:
            kwargs['max_age'] = max_age
        if expires is not SIG_EMPTY:
            kwargs['expires'] = expires
        if path is not SIG_EMPTY:
            kwargs['path'] = path
        if domain is not SIG_EMPTY:
            kwargs['domain'] = domain
        if secure is not SIG_EMPTY:
            kwargs['secure'] = secure
        if httponly is not SIG_EMPTY:
            kwargs['httponly'] = httponly
        if samesite is not SIG_EMPTY:
            kwargs['samesite'] = samesite
        self.call.append(['set_cookie', key, kwargs])

    def delete_cookie(
            self,
            key: str,
            path: str = SIG_EMPTY,
            domain: str = SIG_EMPTY,
    ):
        kwargs = {}
        if path is not SIG_EMPTY:
            kwargs['path'] = path
        if domain is not SIG_EMPTY:
            kwargs['domain'] = domain
        self.call.append(['delete_cookie', key, kwargs])
