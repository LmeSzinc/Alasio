"""
An in-memory static file server for the tests: MockStaticFile.

The files are registered in memory and served by the ASGI app of the instance,
with the semantics a client of a static file server depends on: the whole file
(200), the bytes of a single Range request (206, with the content-range
header), a range outside the file (416), a path nothing was registered for
(404), the headers of a HEAD answer without a body, content-length and
accept-ranges on every answer. No filesystem is involved: a test that already
runs under the in-memory filesystem of the project
(alasio.testing.filesystem) does not have to materialize the files of the
server anywhere, the two layers stay apart.

The app is an ASGI callable (the instance itself), used with httpx2 or with
starlette's TestClient, on its own or mounted (Mount) under a path prefix:

    server = MockStaticFile()
    server.register_file('1.0/full.pack', b'...')
    client = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=server))

The mock is deliberately small: the multiple ranges (multipart/byteranges),
the conditional requests (If-Range / If-None-Match) and the content types of a
full static file server are not implemented, the clients of the project send a
single unconditional range of the bytes unit only.
"""
import re

from starlette.responses import PlainTextResponse, Response
from starlette.routing import get_route_path


class MockRangeNotSatisfiable(Exception):
    """
    Raised when a Range request asks for bytes outside the file: the 416 of a
    static file server.
    """

    def __init__(self, size):
        """
        Args:
            size (int): Size of the file the range was asked for
        """
        super().__init__(f'Range not satisfiable: {size} bytes')
        self.size = size


class MockStaticFile:
    """
    An in-memory static file server: register_file() the files, the app of the
    instance serves them.

    Attributes:
        files (dict[str, bytes]): {'/path': content} of the registered files,
            the paths are '/'-separated and absolute
    """

    # bytes of one body message of an answer
    CHUNK_SIZE = 65536

    # the single range forms the clients of the project send: 'bytes=a-b',
    # 'bytes=a-' and 'bytes=-n'
    REGEX_RANGE = re.compile(r'bytes=(\d*)-(\d*)')

    def __init__(self, files=None):
        """
        Args:
            files (dict[str, bytes], optional): Files to register, see
                register_file(). Defaults to None, no file
        """
        # {'/path': content}
        self.files = {}
        if files:
            self.register_files(files)

    def register_file(self, path, content):
        """
        Register a file, the app serves it at its path.

        Args:
            path (str): Path of the file below the root, '/'-separated, e.g.
                'latest.pack' or '1.0/full.pack'
            content (bytes): Content of the file
        """
        self.files[f'/{path.strip("/")}'] = content

    def register_files(self, files):
        """
        Register several files, a path registered before is replaced.

        Args:
            files (dict[str, bytes]): {path: content}, see register_file()
        """
        for path, content in files.items():
            self.register_file(path, content)

    @classmethod
    def parse_range(cls, http_range, size):
        """
        The range of a Range header.

        Args:
            http_range (str): Value of the Range header, e.g. 'bytes=2-5'
            size (int): Size of the file the range is asked for

        Returns:
            tuple[int, int]: (start, end), the inclusive range to serve

        Raises:
            ValueError: If the header is not a single range of the bytes unit
                (the form the mock does not implement)
            MockRangeNotSatisfiable: If the range is outside the file
        """
        match = cls.REGEX_RANGE.fullmatch(http_range.strip())
        if match is None:
            raise ValueError(f'Not a single range of bytes: {http_range!r}')
        first, last = match.groups()
        if not first and not last:
            raise ValueError(f'Not a range of bytes: {http_range!r}')
        if not first:
            # the last n bytes
            start, end = max(size - int(last), 0), size - 1
        else:
            start = int(first)
            end = size - 1 if not last else min(int(last), size - 1)
        if start >= size or start > end:
            raise MockRangeNotSatisfiable(size)
        return start, end

    async def __call__(self, scope, receive, send):
        """
        Serve one request: the whole file, the range of it, or a 404.

        Args:
            scope (dict): ASGI scope of the request
            receive (Callable): ASGI receive
            send (Callable): ASGI send
        """
        if scope['type'] != 'http':
            # a static file server serves no websocket and has no lifespan
            return
        # the app is mountable: the file is looked up below the mount point
        # (the root path of the scope), like the static files of starlette
        path = get_route_path(scope)
        content = self.files.get(path)
        if content is None:
            await PlainTextResponse('Not Found', status_code=404)(scope, receive, send)
            return
        size = len(content)
        http_range = dict(scope['headers']).get(b'range', b'').decode('latin-1')
        if not http_range:
            body, status = content, 200
            headers = {'accept-ranges': 'bytes', 'content-length': str(size)}
        else:
            try:
                start, end = self.parse_range(http_range, size)
            except MockRangeNotSatisfiable as e:
                await Response(
                    b'', status_code=416,
                    headers={'content-range': f'bytes */{e.size}'})(scope, receive, send)
                return
            body = content[start:end + 1]
            status = 206
            headers = {
                'accept-ranges': 'bytes',
                'content-range': f'bytes {start}-{end}/{size}',
                'content-length': str(len(body)),
            }
        if scope['method'] == 'HEAD':
            # the answer of a HEAD carries the headers only, the content-length
            # is the one of the body that would be served
            await Response(b'', status_code=status, headers=headers)(scope, receive, send)
            return
        await Response(body, status_code=status, headers=headers)(scope, receive, send)
