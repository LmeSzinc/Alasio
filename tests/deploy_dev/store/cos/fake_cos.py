"""
An equivalent COS service of the tests: a starlette app in the memory.

The store under test speaks to this app through httpx2.ASGITransport, the
standard in-process ASGI flow of the project (see
tests/deploy/pack/test_server_asgi.py): no socket is bound, the app runs in the
event loop of the test, and the client sends the bytes it would send to Tencent
- the escaped path, the async file body, the query in name order - and parses
the xml it would parse there. A fake that only answers a MockTransport proves
that the client sends what the test expects, and the test and the client would
share the same misunderstanding.

It is also the reason there is no port: the tests of a file run in parallel
workers, and a bound port is the one resource a worker cannot share.

The signature is implemented here a second time, from the protocol, and never
imported from the client: the app reads q-header-list and q-url-param-list out
of the Authorization header, rebuilds the canonical string from the request it
actually received, and compares. The two implementations share the spec, not
the code, so a mistake has to be made twice in the same place to go unnoticed.
The real bucket tests are the external judge that neither can fool.

The path of the canonical string is the decoded one: COS decodes the path of a
request before it rebuilds the string, which is why a client that signs the
escaped path passes every ascii key and fails every key with a space, a '+' or
a non-ascii character.

What is faithful:

- Content-MD5 is verified before the object is written, a mismatch answers
  400 BadDigest and stores nothing;
- a missing object answers 404 with an xml NoSuchKey for GET and HEAD, and 404
  for DELETE (stricter than COS, to exercise the idempotent delete of the
  client);
- a bad signature answers 403 SignatureDoesNotMatch with the xml error body;
- ListObjectsV2 supports prefix, max-keys and continuation-token, where the
  token is the last key of the previous page;
- every response carries a Date header, LastModified is ISO 8601 in UTC.

What is not: ACL, storage classes, multipart, encryption, versioning. The store
never asks for them.
"""

import base64
import hashlib
import hmac
import time
import xml.sax.saxutils as saxutils
from datetime import datetime, timezone
from email.utils import formatdate
from urllib.parse import quote

import httpx2
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Route

# the characters the canonical string leaves unescaped, the unreserved set
SAFE_CHARS = '-_.~'

# the request headers COS signs: the fixed list and the reserved prefixes. A
# second copy of the client rule, written from the protocol on purpose
SIGN_HEADERS = frozenset((
    'cache-control', 'content-disposition', 'content-encoding', 'content-language',
    'content-length', 'content-md5', 'content-type', 'expires', 'host', 'origin',
    'range',
))
SIGN_PREFIXES = ('x-cos-', 'x-ci-')

# seconds of tolerance around the sign time of a request
CLOCK_SKEW = 300


def encode_value(value):
    """
    Percent-encode a value of the canonical string.

    Args:
        value (str): Value to encode

    Returns:
        str: Encoded value
    """
    return quote(str(value), safe=SAFE_CHARS)


def is_sign_header(name):
    """
    Whether a header takes part in a signature.

    Args:
        name (str): Header name, any case

    Returns:
        bool: True for a whitelisted header or a reserved prefix
    """
    name = name.lower()
    return name in SIGN_HEADERS or name.startswith(SIGN_PREFIXES)


def signature_of(secret_key, key_time, http_string):
    """
    Compute the q-signature of a canonical string.

    Args:
        secret_key (str): SecretKey of the credential
        key_time (str): Validity window of the signature
        http_string (str): Canonical string of the request

    Returns:
        str: Hex signature
    """
    sign_key = hmac.new(
        secret_key.encode('utf-8'), key_time.encode('utf-8'), hashlib.sha1).hexdigest()
    to_sign = 'sha1\n{}\n{}\n'.format(
        key_time, hashlib.sha1(http_string.encode('utf-8')).hexdigest())
    return hmac.new(
        sign_key.encode('utf-8'), to_sign.encode('utf-8'), hashlib.sha1).hexdigest()


class FakeObject:
    """
    One object of the fake bucket.

    Attributes:
        data (bytes): Content of the object
        last_modified (float): Unix time of the write
        content_type (str): Content-Type of the write
        cache_control (str): Cache-Control of the write, None when unset
    """

    __slots__ = ('data', 'last_modified', 'content_type', 'cache_control')

    def __init__(self, data, content_type, cache_control, last_modified=None):
        """
        Args:
            data (bytes): Content of the object
            content_type (str): Content-Type of the write
            cache_control (str): Cache-Control of the write
            last_modified (float): Unix time of the write, now when None
        """
        self.data = data
        self.last_modified = time.time() if last_modified is None else last_modified
        self.content_type = content_type
        self.cache_control = cache_control

    @property
    def etag(self):
        """
        Returns:
            str: The ETag a single PUT of this content gets, quoted
        """
        return '"{}"'.format(hashlib.md5(self.data).hexdigest())

    @property
    def checksum(self):
        """
        Returns:
            str: The value of x-cos-hash-crc64ecma of this content

        The real service answers the CRC-64/XZ of the object, a digest this
        fake does not compute (the flow never does either: it compares the value
        of a PUT with the value of a HEAD). Any content-dependent token pins
        that comparison, and the value of the real service is pinned by the
        bucket test of test_real.py.
        """
        digest = hashlib.md5(self.data).digest()
        return str(int.from_bytes(digest[:8], 'big'))


class FakeCos:
    """
    The state and the ASGI app of the fake COS service.

    The app is a Starlette application with one catch-all route: every request
    of the store is one call of handle(), in the event loop of the test.

    Attributes:
        secret_id (str): SecretId the service accepts
        secret_key (str): SecretKey the service signs with
        objects (dict): Key -> FakeObject
        requests (list): (method, raw path with query, headers, body) of every
            request served, in order
        fail_next_puts (int): Number of PUTs to answer by raising a transport
            error after the body was received, for the retry test
        fail_next_gets (int): Number of GETs to answer by raising a transport
            error before the response is open, for the retry of a readback
        fail_next_requests (int): Number of requests to answer with 500
    """

    def __init__(self, secret_id='AKIDFAKE', secret_key='FAKESECRET'):
        """
        Args:
            secret_id (str): SecretId the service accepts
            secret_key (str): SecretKey the service signs with
        """
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.objects = {}
        self.requests = []
        self.fail_next_puts = 0
        self.fail_next_gets = 0
        self.fail_next_requests = 0
        self._clients = []
        self.app = Starlette(routes=[
            Route('/{key:path}', self.handle, methods=['GET', 'HEAD', 'PUT', 'DELETE']),
        ])

    # ------------------------------------------------------------------
    # client and lifecycle
    # ------------------------------------------------------------------

    def client(self):
        """
        A client of this app, over the in-process ASGI transport.

        The client belongs to the service and is closed by aclose(): a store
        takes it as an injected client and never closes it.

        Returns:
            httpx2.AsyncClient: The client to hand to a store
        """
        client = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=self.app))
        self._clients.append(client)
        return client

    async def aclose(self):
        """
        Close every client handed out by client().
        """
        for client in self._clients:
            await client.aclose()
        self._clients.clear()

    # ------------------------------------------------------------------
    # helpers used by a test
    # ------------------------------------------------------------------

    def store(self, key, data, content_type, cache_control, last_modified=None):
        """
        Write an object, as a PUT would.

        Args:
            key (str): Full key of the object
            data (bytes): Content
            content_type (str): Content-Type
            cache_control (str): Cache-Control, None when unset
            last_modified (float): Unix time of the write, now when None
        """
        self.objects[key] = FakeObject(data, content_type, cache_control, last_modified)

    def remove(self, key):
        """
        Remove an object.

        Args:
            key (str): Full key of the object

        Returns:
            bool: True when the key existed
        """
        return self.objects.pop(key, None) is not None

    def get(self, key):
        """
        Read an object.

        Args:
            key (str): Full key of the object

        Returns:
            FakeObject: The object, None when it does not exist
        """
        return self.objects.get(key)

    def keys(self, prefix=''):
        """
        The keys of the bucket, sorted.

        Args:
            prefix (str): Only the keys that start with it

        Returns:
            list[str]: Sorted keys
        """
        return sorted(key for key in self.objects if key.startswith(prefix))

    # ------------------------------------------------------------------
    # the app
    # ------------------------------------------------------------------

    async def handle(self, request):
        """
        Serve one request, whatever its method.

        Args:
            request (starlette.requests.Request): The request

        Returns:
            starlette.responses.Response: The answer
        """
        method = request.method
        body = await request.body()
        scope = request.scope
        # the escaped path and the raw query, for the record of the test: the
        # two are what went over the wire, the decoded scope['path'] is what
        # the signature covers
        raw_path = scope.get('raw_path', b'').decode('latin-1') or '/'
        raw_query = scope.get('query_string', b'').decode('latin-1')
        self.requests.append((
            method,
            raw_path + ('?' + raw_query if raw_query else ''),
            dict(request.headers),
            body,
        ))

        if self.fail_next_requests:
            self.fail_next_requests -= 1
            return self._error(500, 'InternalError', 'injected failure')

        if method == 'PUT' and self.fail_next_puts:
            self.fail_next_puts -= 1
            # the body above was read first: the service dies after it received
            # the upload, like a peer that drops the connection mid-answer, so
            # the client sees a transport error and has to send the body again
            raise httpx2.ReadTimeout('the fake service dropped the connection')

        if (method == 'GET' and self.fail_next_gets
                and request.query_params.get('list-type') != '2'):
            self.fail_next_gets -= 1
            # a readback of one object: the connection is dropped before an
            # answer is opened, so the retry has to start the digest over
            raise httpx2.ReadTimeout('the fake service dropped the connection')

        problem = self._check_authorization(request, scope['path'])
        if problem is not None:
            code, message = problem
            status = 403 if code in ('SignatureDoesNotMatch', 'InvalidAccessKeyId') else 400
            return self._error(status, code, message)

        key = scope['path'][1:]
        if method == 'PUT':
            return self._put(request, key, body)
        if method == 'GET':
            if request.query_params.get('list-type') == '2':
                return self._list(request)
            return self._get(key)
        if method == 'HEAD':
            return self._head(key)
        return self._delete(key)

    # ------------------------------------------------------------------
    # verbs
    # ------------------------------------------------------------------

    def _put(self, request, key, body):
        """
        Write an object, a wrong Content-MD5 writes nothing.

        Args:
            request (starlette.requests.Request): The PUT request
            key (str): Full key of the object
            body (bytes): Received body

        Returns:
            starlette.responses.Response: The answer
        """
        if not key:
            return self._error(400, 'InvalidArgument', 'a PUT needs an object key')
        declared = request.headers.get('content-length')
        if declared is not None and declared.isdigit() and int(declared) != len(body):
            # the real service reads exactly Content-Length bytes; a body of
            # another length never reaches the write
            return self._error(
                400, 'IncompleteBody', 'The request body is not of Content-Length')
        content_md5 = request.headers.get('content-md5')
        if content_md5:
            expected = hashlib.md5(body).digest()
            try:
                given = base64.b64decode(content_md5)
            except (ValueError, TypeError):
                given = None
            if given != expected:
                # nothing is written: the object of a rejected PUT does not
                # exist, which is the property the uploader relies on
                return self._error(400, 'BadDigest', 'The Content-MD5 you specified is not match.')
        self.store(
            key, body,
            request.headers.get('content-type', 'application/octet-stream'),
            request.headers.get('cache-control'),
        )
        return self._answer(200, b'', {
            'ETag': self.objects[key].etag,
            'x-cos-hash-crc64ecma': self.objects[key].checksum,
        })

    def _get(self, key):
        """
        Read one object.

        Args:
            key (str): Full key of the object

        Returns:
            starlette.responses.Response: The answer
        """
        obj = self.objects.get(key)
        if obj is None:
            return self._error(404, 'NoSuchKey', 'The specified key does not exist.')
        return self._answer(200, obj.data, self._object_headers(obj))

    def _head(self, key):
        """
        Read the metadata of one object.

        Args:
            key (str): Full key of the object

        Returns:
            starlette.responses.Response: The answer, without a body
        """
        obj = self.objects.get(key)
        if obj is None:
            return self._error(404, 'NoSuchKey', 'The specified key does not exist.')
        headers = self._object_headers(obj)
        headers['Content-Length'] = str(len(obj.data))
        return self._answer(200, b'', headers)

    def _delete(self, key):
        """
        Delete one object.

        Args:
            key (str): Full key of the object

        Returns:
            starlette.responses.Response: The answer
        """
        if not self.remove(key):
            return self._error(404, 'NoSuchKey', 'The specified key does not exist.')
        return self._answer(204, b'')

    def _list(self, request):
        """
        One page of a ListObjectsV2 answer.

        Args:
            request (starlette.requests.Request): The LIST request

        Returns:
            starlette.responses.Response: The answer
        """
        query = request.query_params
        prefix = query.get('prefix', '')
        try:
            max_keys = min(int(query.get('max-keys', '1000')), 1000)
        except ValueError:
            return self._error(400, 'InvalidArgument', 'max-keys is not a number')
        token = query.get('continuation-token')
        keys = [key for key in self.keys(prefix) if token is None or key > token]
        page = keys[:max_keys]
        truncated = len(keys) > len(page)
        next_token = page[-1] if truncated and page else None
        body = self._listing_xml(prefix, page, truncated, next_token)
        return self._answer(200, body, {'Content-Type': 'application/xml'})

    # ------------------------------------------------------------------
    # signature
    # ------------------------------------------------------------------

    def _check_authorization(self, request, path):
        """
        Rebuild the canonical string of the request and compare the signature.

        Args:
            request (starlette.requests.Request): The request
            path (str): Decoded path of the request, the one COS signs

        Returns:
            tuple[str, str]: (code, message) of the refusal, None when the
                signature is good
        """
        authorization = request.headers.get('authorization')
        if not authorization:
            return 'AccessDenied', 'The request has no Authorization header'
        fields = {}
        for part in authorization.split('&'):
            name, sep, value = part.partition('=')
            if sep:
                fields[name] = value
        if fields.get('q-sign-algorithm') != 'sha1':
            return 'SignatureDoesNotMatch', 'The signature algorithm is not sha1'
        if fields.get('q-ak') != self.secret_id:
            # the code the real service answers for an unknown SecretId, not the
            # one of a wrong signature: the two are checked apart in the tests
            return 'InvalidAccessKeyId', 'The SecretId is unknown'
        key_time = fields.get('q-sign-time') or ''
        start, _sep, end = key_time.partition(';')
        try:
            start, end = int(start), int(end)
        except ValueError:
            return 'SignatureDoesNotMatch', 'The sign time is not a window'
        now = time.time()
        if now < start - CLOCK_SKEW or now > end + CLOCK_SKEW:
            return 'SignatureDoesNotMatch', 'The signature has expired'

        headers = request.headers
        names = [name for name in (fields.get('q-header-list') or '').split(';') if name]
        header_list = '&'.join(
            f'{name.lower()}={encode_value(headers[name.lower()])}'
            for name in sorted(names)
            if headers.get(name.lower()) is not None)
        params = request.query_params
        param_names = [name for name in (fields.get('q-url-param-list') or '').split(';') if name]
        param_list = '&'.join(
            f'{encode_value(name)}={encode_value(params.get(name, ""))}'
            for name in sorted(param_names))
        http_string = f'{request.method.lower()}\n{path}\n{param_list}\n{header_list}\n'
        expected = signature_of(self.secret_key, key_time, http_string)
        if not hmac.compare_digest(expected, fields.get('q-signature') or ''):
            return 'SignatureDoesNotMatch', 'The Signature you specified is invalid.'
        return None

    # ------------------------------------------------------------------
    # answers
    # ------------------------------------------------------------------

    def _object_headers(self, obj):
        """
        The headers of an object.

        Args:
            obj (FakeObject): The object

        Returns:
            dict: ETag, Last-Modified and Content-Type of the object
        """
        headers = {
            'ETag': obj.etag,
            'Last-Modified': self._http_date(obj.last_modified),
            'Content-Type': obj.content_type,
            'x-cos-hash-crc64ecma': obj.checksum,
        }
        if obj.cache_control:
            headers['Cache-Control'] = obj.cache_control
        return headers

    @staticmethod
    def _http_date(timestamp):
        """
        Format a unix time as an http date.

        Args:
            timestamp (float): Unix time

        Returns:
            str: E.g. 'Thu, 06 Nov 2026 19:39:59 GMT'
        """
        return formatdate(timestamp, usegmt=True)

    def _listing_xml(self, prefix, keys, truncated, next_token):
        """
        Build the ListBucketResult of one page.

        Args:
            prefix (str): Prefix of the listing
            keys (list[str]): Keys of the page
            truncated (bool): Whether another page follows
            next_token (str): Continuation token of the next page

        Returns:
            bytes: The xml document
        """
        parts = ["<?xml version='1.0' encoding='utf-8' ?>", '<ListBucketResult>']
        parts.append('<Name>fake</Name>')
        parts.append(f'<Prefix>{saxutils.escape(prefix)}</Prefix>')
        parts.append(f'<KeyCount>{len(keys)}</KeyCount>')
        parts.append('<MaxKeys>1000</MaxKeys>')
        parts.append(f'<IsTruncated>{"true" if truncated else "false"}</IsTruncated>')
        if next_token:
            parts.append(
                f'<NextContinuationToken>{saxutils.escape(next_token)}</NextContinuationToken>')
        for key in keys:
            obj = self.objects[key]
            last_modified = datetime.fromtimestamp(obj.last_modified, timezone.utc)
            parts.append(
                '<Contents>'
                f'<Key>{saxutils.escape(key)}</Key>'
                f'<Size>{len(obj.data)}</Size>'
                f'<ETag>{obj.etag}</ETag>'
                f'<LastModified>{last_modified.strftime("%Y-%m-%dT%H:%M:%S")}.000Z'
                '</LastModified>'
                '</Contents>')
        parts.append('</ListBucketResult>')
        return ''.join(parts).encode('utf-8')

    def _error(self, status, code, message):
        """
        Build the xml error document of a refusal.

        Args:
            status (int): Http status
            code (str): Error code
            message (str): Message of the error

        Returns:
            starlette.responses.Response: The answer
        """
        body = (
            "<?xml version='1.0' encoding='utf-8' ?>\n"
            f'<Error><Code>{code}</Code><Message>{saxutils.escape(message)}</Message>'
            '</Error>').encode('utf-8')
        return self._answer(status, body, {'Content-Type': 'application/xml'})

    @staticmethod
    def _answer(status, body, headers=None):
        """
        Build a response.

        Args:
            status (int): Http status
            body (bytes): Body of the answer
            headers (dict): Headers of the answer. Defaults to None

        Returns:
            starlette.responses.Response: The answer
        """
        return Response(body, status_code=status, headers=headers)
