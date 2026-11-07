"""
The COS store against the fake COS server of the tests.

Every request of these tests goes over a real socket to fake_cos.FakeCos, which
verifies the signature with its own implementation, checks Content-MD5 before
it writes an object and answers the xml documents of COS. The two sides share
the protocol, not the code: a signature the client gets wrong is refused here,
and a value the server reports wrong (size, ETag) is caught by the assertions.

The file of a PUT lives in the in-memory filesystem of the project, the server
never touches a file: it receives the bytes over the socket, as the real one
does.

Every test is async (pytest-trio): the store sends its requests through
AsyncHttpClient and reads the body of an upload through read_chunks(), so the
tests drive it the way the uploader will. The backoff of a retry runs on the
autojump clock where a test asserts it, so no test waits the seconds of the
real schedule.
"""

import calendar
import hashlib
import time
from datetime import datetime

# httpx2 resolves the version of its transport with importlib.metadata at import
# time, and the in-memory filesystem of these tests cannot answer that call:
# the transport is imported here, while the disk of the process is still real
import httpcore2  # noqa: F401
import httpx2
import pytest
import trio

from alasio.deploy.httpclient.client_async import AsyncHttpClient
from alasio.deploy_dev.store import ObjectNotFound, read_chunks
from alasio.deploy_dev.store.cos import CosError, CosStore
from alasio.deploy_dev.store.cos.store import (
    normalize_etag, normalize_prefix, parse_endpoint, parse_http_date, parse_iso8601, validate_relative
)
from alasio.testing.filesystem import fs  # noqa: F401

# the file drives an in-process starlette app over ASGITransport and keeps its
# files in the fake filesystem of the process: no port, no shared directory, no
# state outside this process, so it is safe beside any other marked file (the
# group of the plugin keeps its own items in one worker, i.e. serial within it)
pytestmark = [
    pytest.mark.parallel,
]

BUCKET = 'alasio-test-1250000000'
REGION = 'ap-shanghai'
PREFIX = 'pack/Author_Repo_master/'

# the endpoint of the fake service: no socket is bound, the host is only what
# the request url and the Host header say. The port is not the default one of
# the scheme, so the authority of the store keeps its port (see TestUrl)
ENDPOINT = 'http://fake-cos.local:9000'


@pytest.fixture
async def store(fake_cos):
    """
    A store bound to the prefix of the test workspace.

    Yields:
        CosStore: The store, closed when the test ends
    """
    store = make_store(fake_cos)
    try:
        yield store
    finally:
        await store.aclose()


def make_store(fake_cos, prefix=PREFIX, **kwargs):
    """
    Build a store of the fake service.

    Args:
        fake_cos (FakeCos): The service the store talks to
        prefix (str): Prefix of the store. Defaults to PREFIX
        **kwargs: Overrides of the constructor arguments (client, secret_key, ...)

    Returns:
        CosStore: The store
    """
    arguments = dict(
        bucket=BUCKET,
        region=REGION,
        secret_id=fake_cos.secret_id,
        secret_key=fake_cos.secret_key,
        endpoint=ENDPOINT,
        prefix=prefix,
        client=AsyncHttpClient(fake_cos.client()),
    )
    arguments.update(kwargs)
    return CosStore(**arguments)


class Uploader:
    """
    Writes local files into the fake filesystem and uploads them.

    Attributes:
        count (int): Number of files written so far
    """

    def __init__(self):
        self.count = 0

    def file(self, fs, data):
        """
        Create a local file of the fake filesystem.

        Args:
            fs: The in-memory filesystem of the test
            data (bytes): Content of the file

        Returns:
            str: Path of the file
        """
        self.count += 1
        path = '/upload-%d.bin' % self.count
        fs.create_file(path, contents=data)
        return path

    async def put(self, fs, store, relative, data, **kwargs):
        """
        Upload a content under a key of the store.

        Args:
            fs: The in-memory filesystem of the test
            store (CosStore): The store to upload to
            relative (str): Relative key
            data (bytes): Content of the object
            **kwargs: Extra arguments of CosStore.put() (content_type, ...)

        Returns:
            ObjectInfo: What the server reports about the object
        """
        path = self.file(fs, data)
        return await store.put(relative, path, len(data), hashlib.md5(data).hexdigest(), **kwargs)


@pytest.fixture
def uploader():
    """
    Returns:
        Uploader: The helper that writes and uploads a content
    """
    return Uploader()


class TestPutGet:
    """A written object reads back with the bytes that were sent."""

    @pytest.mark.trio
    async def test_put_get_roundtrip(self, fs, store, uploader, fake_cos):
        """The bytes of the file, the ETag of its MD5 and the size come back."""
        data = b'\x00\x01hello world' * 100
        info = await uploader.put(fs, store, 'v1/data.bin', data)
        assert info.size == len(data)
        assert info.etag == hashlib.md5(data).hexdigest()
        assert await store.get('v1/data.bin') == data
        assert fake_cos.get(PREFIX + 'v1/data.bin').data == data

    @pytest.mark.trio
    async def test_nested_key(self, fs, store):
        """A key may be any depth, the path is escaped segment by segment."""
        data = b'nested'
        uploader = Uploader()
        await uploader.put(fs, store, 'v1/deep/folder/data.bin', data)
        assert await store.get('v1/deep/folder/data.bin') == data

    @pytest.mark.trio
    async def test_unicode_key(self, fs, store):
        """A non-ascii key is escaped on the wire and comes back unchanged."""
        data = b'unicode'
        uploader = Uploader()
        await uploader.put(fs, store, 'v1/中文 目录/data.bin', data)
        assert await store.get('v1/中文 目录/data.bin') == data
        assert sorted((await store.list()).objects) == ['v1/中文 目录/data.bin']

    @pytest.mark.trio
    @pytest.mark.parametrize('segment', [
        'a b',        # a space: escaped as %20 in the url
        'a+b',        # a plus: escaped as %2B, not taken for a space
        'a%b',        # a percent: escaped as %25, decoded back to one
        'a#b',        # a fragment marker: escaped, never a fragment
        'a?b',        # a query marker: escaped, never a query
        'a&b',        # a separator of the canonical string itself
        '中文',        # a non-ascii value: utf-8 of the text, percent-encoded
    ])
    async def test_key_needing_an_escape(self, fs, store, fake_cos, segment):
        """A key that needs percent-encoding is written and read back.

        The url carries the escaped path, the signature covers the decoded key:
        COS decodes the path of a request before it rebuilds the canonical
        string. A client that signs the escaped path passes every ascii key and
        fails every one of these.
        """
        data = ('content of ' + segment).encode('utf-8')
        relative = 'v1/%s/data.bin' % segment
        await Uploader().put(fs, store, relative, data)
        assert await store.get(relative) == data
        assert sorted((await store.list()).objects) == [relative]
        assert fake_cos.get(PREFIX + relative) is not None

    @pytest.mark.trio
    async def test_empty_object(self, fs, store, uploader):
        """A zero byte object is a valid object."""
        info = await uploader.put(fs, store, 'v1/empty.bin', b'')
        assert info.size == 0
        assert await store.get('v1/empty.bin') == b''

    @pytest.mark.trio
    async def test_content_type_and_cache_control(self, fs, store, uploader, fake_cos):
        """The two headers of the object reach the server."""
        await uploader.put(
            fs, store, 'v1/manifest.json', b'{}',
            content_type='application/json', cache_control='no-cache')
        written = fake_cos.get(PREFIX + 'v1/manifest.json')
        assert written.content_type == 'application/json'
        assert written.cache_control == 'no-cache'

    @pytest.mark.trio
    async def test_default_content_type(self, fs, store, uploader, fake_cos):
        """An object without a declared type is an octet stream."""
        await uploader.put(fs, store, 'v1/data.pack', b'pack')
        assert fake_cos.get(PREFIX + 'v1/data.pack').content_type == 'application/octet-stream'


class TestContentMd5:
    """Content-MD5 is what proves an upload to the server."""

    @pytest.mark.trio
    async def test_mismatch_is_refused_and_nothing_is_written(self, fs, store, fake_cos):
        """A wrong MD5 answers 400 BadDigest and the object does not exist."""
        data = b'the bytes that are sent'
        path = '/wrong-md5.bin'
        fs.create_file(path, contents=data)
        with pytest.raises(CosError) as caught:
            await store.put('v1/wrong.bin', path, len(data), '0' * 32)
        assert caught.value.status == 400
        assert caught.value.code == 'BadDigest'
        assert fake_cos.get(PREFIX + 'v1/wrong.bin') is None
        with pytest.raises(ObjectNotFound):
            await store.get('v1/wrong.bin')

    @pytest.mark.trio
    async def test_md5_must_be_hex(self, fs, store):
        """A MD5 that is not a hex string is a caller error, not a request."""
        fs.create_file('/data.bin', contents=b'data')
        with pytest.raises(ValueError):
            await store.put('v1/data.bin', '/data.bin', 4, 'not-a-md5')

    @pytest.mark.trio
    async def test_size_mismatch_is_a_caller_error(self, fs, store, fake_cos):
        """A declared size that is not the size of the file is refused up front.

        Left to the http stack the mismatch is a protocol error (a longer body
        than Content-Length is refused by h11, a shorter one stalls the peer),
        which reads as a network failure and is retried eight times. The check
        reads the size of the open file, so it costs no second lookup of the
        path.
        """
        data = b'x' * 100
        path = '/short.bin'
        fs.create_file(path, contents=data)
        with pytest.raises(ValueError) as caught:
            await store.put('v1/short.bin', path, 10, hashlib.md5(data).hexdigest())
        assert 'declared 10 bytes' in str(caught.value)
        assert fake_cos.requests == []


class TestOverwrite:
    """A key written twice holds the second content."""

    @pytest.mark.trio
    async def test_content_and_etag_change(self, fs, store, uploader):
        """The second content and the ETag of the second content win."""
        first = await uploader.put(fs, store, 'v1/data.bin', b'first')
        second = await uploader.put(fs, store, 'v1/data.bin', b'second')
        assert first.etag != second.etag
        assert await store.get('v1/data.bin') == b'second'
        assert second.etag == hashlib.md5(b'second').hexdigest()

    @pytest.mark.trio
    async def test_last_modified_advances(self, fs, store):
        """A rewrite moves LastModified forward, the ETag of a same content does not."""
        uploader = Uploader()
        path = uploader.file(fs, b'same bytes')
        md5 = hashlib.md5(b'same bytes').hexdigest()
        first = await store.put('v1/data.bin', path, 10, md5)
        # the server stamps whole seconds: the rewrite has to land in another one
        await trio.sleep(1.1)
        second = await store.put('v1/data.bin', path, 10, md5)
        assert second.etag == first.etag
        assert second.last_modified >= first.last_modified + 1


class TestList:
    """The listing of the prefix of the store."""

    @pytest.mark.trio
    async def test_objects_of_the_prefix(self, fs, store, uploader):
        """Every key of the prefix is listed with its size and ETag."""
        await uploader.put(fs, store, 'v1/a.bin', b'aaa')
        await uploader.put(fs, store, 'v1/b.bin', b'bb')
        listing = await store.list()
        assert sorted(listing.objects) == ['v1/a.bin', 'v1/b.bin']
        assert listing.objects['v1/a.bin'].size == 3
        assert listing.objects['v1/a.bin'].etag == hashlib.md5(b'aaa').hexdigest()
        assert abs(listing.server_date - time.time()) < 60

    @pytest.mark.trio
    async def test_pagination(self, fs, store, uploader, fake_cos):
        """A listing larger than a page is followed with the continuation token."""
        store.PAGE_SIZE = 2
        expected = []
        for index in range(5):
            await uploader.put(fs, store, 'v1/f%d.bin' % index, b'x' * (index + 1))
            expected.append('v1/f%d.bin' % index)
        listing = await store.list()
        assert sorted(listing.objects) == expected
        # five objects, two per page: three requests
        assert len([request for request in fake_cos.requests if request[0] == 'GET']) == 3

    @pytest.mark.trio
    async def test_prefix_isolation(self, fs, store, fake_cos):
        """A store never sees the objects of a sibling prefix.

        The prefix 'pack/Author_Repo_master/' and a workspace whose name starts
        with the same text ('pack/Author_Repo_master2/') share their first 25
        characters: without the trailing '/' of the prefix the second would
        leak into the first.
        """
        other = make_store(fake_cos, prefix='pack/Author_Repo_master2/')
        try:
            uploader = Uploader()
            await uploader.put(fs, store, 'v1/mine.bin', b'mine')
            await uploader.put(fs, other, 'v1/theirs.bin', b'theirs')
            assert sorted((await store.list()).objects) == ['v1/mine.bin']
            assert sorted((await other.list()).objects) == ['v1/theirs.bin']
        finally:
            await other.aclose()

    @pytest.mark.trio
    async def test_folder_placeholder_is_skipped(self, fake_cos, store):
        """A key that ends with '/' is a folder placeholder, not an object."""
        fake_cos.store(PREFIX + 'v1/', b'', 'application/octet-stream', None)
        fake_cos.store(PREFIX + 'v1/data.bin', b'data', 'application/octet-stream', None)
        assert sorted((await store.list()).objects) == ['v1/data.bin']

    @pytest.mark.trio
    async def test_empty_prefix(self, store):
        """An empty bucket lists nothing, not an error."""
        assert (await store.list()).objects == {}


class TestStat:
    """The state of a known set of keys."""

    @pytest.mark.trio
    async def test_only_the_requested_keys(self, fs, store, uploader):
        """A key that does not exist is absent, not an error."""
        await uploader.put(fs, store, 'v1/a.bin', b'aaa')
        await uploader.put(fs, store, 'v1/b.bin', b'bb')
        infos = await store.stat(['v1/a.bin', 'v1/missing.bin'])
        assert sorted(infos) == ['v1/a.bin']
        assert infos['v1/a.bin'].size == 3

    @pytest.mark.trio
    async def test_stat_of_a_store_with_sibling_keys(self, fs, fake_cos, store):
        """stat() never returns a key of another prefix."""
        other = make_store(fake_cos, prefix='other/')
        try:
            uploader = Uploader()
            await uploader.put(fs, other, 'v1/a.bin', b'other')
            assert await store.stat(['v1/a.bin']) == {}
        finally:
            await other.aclose()


class TestHead:
    """A single object without listing the prefix."""

    @pytest.mark.trio
    async def test_head(self, fs, store, uploader, fake_cos):
        """Size, ETag and LastModified of one object."""
        data = b'head me'
        info = await uploader.put(fs, store, 'v1/data.bin', data)
        head = await store.head('v1/data.bin')
        assert head.size == len(data)
        assert head.etag == info.etag
        assert abs(head.last_modified - time.time()) < 60
        assert fake_cos.get(PREFIX + 'v1/data.bin').content_type == 'application/octet-stream'

    @pytest.mark.trio
    async def test_head_missing(self, store):
        """A missing object raises ObjectNotFound."""
        with pytest.raises(ObjectNotFound):
            await store.head('v1/missing.bin')


class TestDelete:
    """Deleting an object, twice or not at all."""

    @pytest.mark.trio
    async def test_delete(self, fs, store, uploader, fake_cos):
        """The object is gone after the delete."""
        await uploader.put(fs, store, 'v1/data.bin', b'data')
        await store.delete('v1/data.bin')
        assert fake_cos.get(PREFIX + 'v1/data.bin') is None

    @pytest.mark.trio
    async def test_delete_is_idempotent(self, fs, store, uploader):
        """A second delete of the same key is a success."""
        await uploader.put(fs, store, 'v1/data.bin', b'data')
        await store.delete('v1/data.bin')
        await store.delete('v1/data.bin')

    @pytest.mark.trio
    async def test_delete_of_a_missing_key(self, store):
        """A key that never existed is a success as well."""
        await store.delete('v1/never.bin')

    @pytest.mark.trio
    async def test_delete_does_not_touch_the_others(self, fs, store, uploader):
        """Only the requested key is removed."""
        await uploader.put(fs, store, 'v1/a.bin', b'a')
        await uploader.put(fs, store, 'v1/b.bin', b'b')
        await store.delete('v1/a.bin')
        assert sorted((await store.list()).objects) == ['v1/b.bin']


class TestConcurrency:
    """The store is shared by the tasks of an upload."""

    @pytest.mark.trio
    async def test_concurrent_puts(self, fs, store):
        """Eight tasks write eight keys at once, all of them arrive.

        The uploader runs its transfers in a nursery, not in a thread pool: the
        store, its signer and its client are shared by every task, and one
        connection pool serves them all.
        """
        contents = {}
        for index in range(8):
            data = b'concurrent %d' % index
            path = '/concurrent-%d.bin' % index
            fs.create_file(path, contents=data)
            contents[index] = (path, data)

        async def upload(index):
            path, data = contents[index]
            await store.put(
                'v1/c%d.bin' % index, path, len(data), hashlib.md5(data).hexdigest())

        async with trio.open_nursery() as nursery:
            for index in range(8):
                nursery.start_soon(upload, index)
        assert sorted((await store.list()).objects) == [
            'v1/c%d.bin' % index for index in range(8)]


class TestErrors:
    """The failures of the server become the errors of the store."""

    @pytest.mark.trio
    async def test_bad_secret_is_a_signature_error(self, fake_cos):
        """A wrong SecretKey answers 403 SignatureDoesNotMatch."""
        store = make_store(fake_cos, secret_key='WRONG-SECRET')
        try:
            with pytest.raises(CosError) as caught:
                await store.list()
        finally:
            await store.aclose()
        assert caught.value.status == 403
        assert caught.value.code == 'SignatureDoesNotMatch'
        # the message names the check, never the credential
        assert 'WRONG-SECRET' not in str(caught.value)

    @pytest.mark.trio
    async def test_bad_secret_id_is_a_signature_error(self, fake_cos):
        """A SecretId the service does not know is refused with InvalidAccessKeyId.

        The real service answers this code, not SignatureDoesNotMatch: the fake
        mirrors it so a mapping of the store is exercised the same way offline
        and against the bucket.
        """
        store = make_store(fake_cos, secret_id='AKIDOTHER')
        try:
            with pytest.raises(CosError) as caught:
                await store.list()
        finally:
            await store.aclose()
        assert caught.value.status == 403
        assert caught.value.code == 'InvalidAccessKeyId'

    @pytest.mark.trio
    async def test_get_missing(self, store):
        """A missing key on a read is ObjectNotFound."""
        with pytest.raises(ObjectNotFound):
            await store.get('v1/missing.bin')

    @pytest.mark.trio
    async def test_server_error(self, store, fake_cos):
        """A 500 is a CosError with the status of the answer."""
        fake_cos.fail_next_requests = 1
        with pytest.raises(CosError) as caught:
            await store.list()
        assert caught.value.status == 500
        assert caught.value.code == 'InternalError'

    @pytest.mark.trio
    async def test_put_beyond_the_limit(self, fs, store):
        """An object larger than a single PUT is refused before the request."""
        store.MAX_PUT_SIZE = 10
        fs.create_file('/big.bin', contents=b'x' * 100)
        with pytest.raises(CosError):
            await store.put('v1/big.bin', '/big.bin', 100, '0' * 32)


class TestRetry:
    """A failed transfer is retried, with a fresh body every time."""

    @pytest.mark.trio
    async def test_put_retries_after_a_dropped_connection(self, fs, store, uploader, fake_cos):
        """The server drops the first PUT, the second one writes the full body."""
        fake_cos.fail_next_puts = 1
        data = b'retried body' * 1000
        info = await uploader.put(fs, store, 'v1/data.bin', data)
        assert info.size == len(data)
        assert await store.get('v1/data.bin') == data
        assert len([request for request in fake_cos.requests if request[0] == 'PUT']) == 2

    @pytest.mark.trio
    async def test_the_client_retry_would_send_an_empty_body(self, fs, autojump_clock):
        """The retry of AsyncHttpClient reuses the body, and the second attempt is empty.

        This is the hazard the store passes attempts=1 for: the client keeps the
        request it was given, so its retry iterates a body that is already
        drained. The test states the hazard instead of trusting the comment on
        it; the test above states that the store does not have it.
        """
        data = b'x' * 5000
        fs.create_file('/client-retry.bin', contents=data)
        bodies = []

        def handler(request):
            bodies.append(len(request.read()))
            if len(bodies) == 1:
                raise httpx2.ReadTimeout('read timed out')
            return httpx2.Response(200)

        client = AsyncHttpClient(httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
        try:
            with open('/client-retry.bin', 'rb') as f:
                await client.request(
                    'PUT', 'http://h.example/k', content=read_chunks(f),
                    headers={'Content-Length': str(len(data))}, attempts=2)
        finally:
            await client.aclose()
        assert bodies == [len(data), 0]

    @pytest.mark.trio
    async def test_the_retry_sends_the_whole_file(self, fs, autojump_clock):
        """The body of every attempt is the whole file.

        A body is consumed once: a retry that reuses the same file object or the
        same async iterable sends zero bytes while it still declares the
        original Content-Length. The handler records the size of every body it
        receives, and the sizes must be equal.
        """
        data = b'x' * 5000
        fs.create_file('/retry.bin', contents=data)
        bodies = []

        def handler(request):
            bodies.append(len(request.read()))
            if len(bodies) == 1:
                raise httpx2.ReadTimeout('read timed out')
            return httpx2.Response(
                200, headers={'ETag': '"%s"' % hashlib.md5(data).hexdigest()})

        client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        store = CosStore(
            bucket=BUCKET, region=REGION, secret_id='AKIDEXAMPLE', secret_key='SECRETEXAMPLE',
            prefix=PREFIX, client=client)
        try:
            info = await store.put(
                'v1/data.bin', '/retry.bin', len(data), hashlib.md5(data).hexdigest())
        finally:
            await store.aclose()
        assert bodies == [len(data), len(data)]
        assert info.size == len(data)

    @pytest.mark.trio
    async def test_a_status_error_is_not_retried(self, fake_cos):
        """A refused response is the answer of the server, not a blip to retry.

        No autojump clock here: the server is a real socket, and an autojumped
        clock expires the read timeout of the request before the thread of the
        server answers it.
        """
        store = make_store(fake_cos)
        try:
            store.ATTEMPTS = 3
            fake_cos.fail_next_requests = 2
            with pytest.raises(CosError) as caught:
                await store.list()
        finally:
            await store.aclose()
        assert caught.value.status == 500
        assert len(fake_cos.requests) == 1

    @pytest.mark.trio
    async def test_backoff_schedule(self, autojump_clock, fake_cos):
        """The waits are 0.5, 1, 2, then 4 s for every later attempt."""
        client = AsyncHttpClient(httpx2.AsyncClient(transport=httpx2.MockTransport(_refuse)))
        store = make_store(fake_cos, client=client)
        try:
            store.ATTEMPTS = 6
            start = trio.current_time()
            with pytest.raises(CosError):
                await store.list()
            elapsed = trio.current_time() - start
        finally:
            await store.aclose()
        # 0.5 + 1 + 2 + 4 + 4 = 11.5 s of waiting for six attempts
        assert elapsed == pytest.approx(11.5, abs=0.01)

    @pytest.mark.trio
    async def test_attempts_are_bounded(self, autojump_clock, fake_cos):
        """No more than ATTEMPTS requests of one call are sent."""
        sent = []

        def handler(request):
            sent.append(request.method)
            raise httpx2.ConnectError('refused', request=request)

        client = AsyncHttpClient(httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
        store = make_store(fake_cos, client=client)
        try:
            store.ATTEMPTS = 4
            with pytest.raises(CosError):
                await store.list()
        finally:
            await store.aclose()
        assert len(sent) == 4


def _refuse(request):
    """A transport handler that refuses every request."""
    raise httpx2.ConnectError('refused', request=request)


class TestUrl:
    """The url of a request, as the server receives it."""

    @pytest.mark.trio
    async def test_path_is_escaped(self, fs, store, uploader, fake_cos):
        """Every segment of a key is escaped down to the unreserved set.

        yarl keeps '+' (and the other sub-delimiters) in a path, and COS reads a
        literal '+' of a path as a space, so the escape cannot be left to the
        urler builder: the object would be stored under another key.
        """
        await uploader.put(fs, store, 'v1/a+b c/中文.txt', b'x')
        method, path, headers, _body = fake_cos.requests[0]
        assert method == 'PUT'
        assert path == '/pack/Author_Repo_master/v1/a%2Bb%20c/%E4%B8%AD%E6%96%87.txt'

    @pytest.mark.trio
    async def test_query_is_sorted(self, store, fake_cos):
        """The parameters of a request are sent in name order.

        The canonical string of the signature covers them sorted, so the url has
        to carry the same order.
        """
        await store.list()
        method, path, _headers, _body = fake_cos.requests[0]
        assert method == 'GET'
        assert path == '/?list-type=2&max-keys=1000&prefix=pack/Author_Repo_master/'

    @pytest.mark.trio
    async def test_root_path_is_a_slash(self, store, fake_cos):
        """A request of the bucket itself carries '/', not an empty path."""
        await store.list()
        assert fake_cos.requests[0][1].startswith('/?')

    @pytest.mark.trio
    async def test_host_header_carries_a_nonstandard_port(self, store):
        """The port of the endpoint is part of the Host header and the url."""
        assert store.authority == 'fake-cos.local:9000'
        url, headers = store._prepare('GET', 'v1/data.bin')
        assert url == 'http://fake-cos.local:9000/pack/Author_Repo_master/v1/data.bin'
        assert headers['Host'] == 'fake-cos.local:9000'

    @pytest.mark.trio
    async def test_the_host_header_reaches_the_service(self, store, fake_cos):
        """The Host of the request is the authority the store signed."""
        await store.list()
        assert fake_cos.requests[0][2]['host'] == 'fake-cos.local:9000'

    @pytest.mark.trio
    async def test_the_standard_port_is_dropped(self):
        """yarl drops the default port of the scheme from the url and the Host."""
        for scheme, port in (('https', 443), ('http', 80)):
            store = CosStore(
                bucket=BUCKET, region=REGION, secret_id='AKID', secret_key='SECRET',
                endpoint=f'{scheme}://h.example:{port}', prefix=PREFIX)
            try:
                assert store.authority == 'h.example'
                url, headers = store._prepare('GET', 'v1/data.bin')
                assert url == f'{scheme}://h.example/pack/Author_Repo_master/v1/data.bin'
                assert headers['Host'] == 'h.example'
            finally:
                await store.aclose()

    @pytest.mark.trio
    async def test_ipv6_host(self):
        """An ipv6 endpoint keeps its brackets in the authority."""
        store = CosStore(
            bucket=BUCKET, region=REGION, secret_id='AKID', secret_key='SECRET',
            endpoint='http://[::1]:9000', prefix=PREFIX)
        try:
            assert store.authority == '[::1]:9000'
            url, _headers = store._prepare('GET', 'v1/data.bin')
            assert url == 'http://[::1]:9000/pack/Author_Repo_master/v1/data.bin'
        finally:
            await store.aclose()


class TestHelpers:
    """The small pure functions of the module."""

    @pytest.mark.parametrize('prefix, expected', [
        ('', ''),
        ('pack', 'pack/'),
        ('pack/', 'pack/'),
        ('/pack/ch/', 'pack/ch/'),
    ])
    def test_normalize_prefix(self, prefix, expected):
        """A non-empty prefix always ends with '/', the empty one stays empty."""
        assert normalize_prefix(prefix) == expected

    @pytest.mark.parametrize('prefix', ['a//b', './a', 'a/../b'])
    def test_normalize_bad_prefix(self, prefix):
        """An empty, '.' or '..' segment is refused."""
        with pytest.raises(ValueError):
            normalize_prefix(prefix)

    @pytest.mark.parametrize('relative', ['', '/a', 'a//b', 'a/./b', 'a/../b'])
    def test_validate_relative_rejects(self, relative):
        """An empty, absolute or dot-segment key is refused."""
        with pytest.raises(ValueError):
            validate_relative(relative)

    @pytest.mark.parametrize('relative', ['a', 'a/b', 'a/中文.pack', 'a.b/c-d_e'])
    def test_validate_relative_accepts(self, relative):
        """A plain relative key is accepted."""
        validate_relative(relative)

    def test_parse_endpoint_default(self):
        """Without an endpoint the myqcloud host of the bucket is used."""
        assert parse_endpoint(None, 'alasio-1250000000', 'ap-shanghai') == (
            'https', 'alasio-1250000000.cos.ap-shanghai.myqcloud.com', None)

    @pytest.mark.parametrize('endpoint, expected', [
        ('http://127.0.0.1:53124', ('http', '127.0.0.1', 53124)),
        ('https://bucket.cos.example.com', ('https', 'bucket.cos.example.com', None)),
        ('127.0.0.1:9', ('https', '127.0.0.1', 9)),
    ])
    def test_parse_endpoint(self, endpoint, expected):
        """An endpoint may carry a scheme and a port."""
        assert parse_endpoint(endpoint, 'bucket', 'region') == expected

    @pytest.mark.parametrize('endpoint', [
        'ftp://host', 'https://host/path', 'https://', 'https://host:notaport'])
    def test_parse_endpoint_rejects(self, endpoint):
        """A scheme, a path, a missing host or a broken port is a configuration error."""
        with pytest.raises(ValueError):
            parse_endpoint(endpoint, 'bucket', 'region')

    def test_normalize_etag(self):
        """The quotes are dropped and the hex is lowercased."""
        assert normalize_etag('"ABCDEF0123"') == 'abcdef0123'
        assert normalize_etag('abcdef0123') == 'abcdef0123'
        assert normalize_etag(None) is None

    def test_parse_iso8601(self):
        """The LastModified of a listing is read as utc."""
        assert parse_iso8601('2026-11-06T19:39:59.000Z') == calendar.timegm(
            datetime(2026, 11, 6, 19, 39, 59).timetuple())
        assert parse_iso8601('2026-11-07T03:39:59.123+08:00') == parse_iso8601(
            '2026-11-06T19:39:59.000Z')
        assert parse_iso8601('2026-11-07T03:39:59.123Z') == calendar.timegm(
            datetime(2026, 11, 7, 3, 39, 59).timetuple())

    def test_parse_iso8601_rejects(self):
        """A value of another shape is an error, not a guess."""
        with pytest.raises(ValueError):
            parse_iso8601('yesterday')

    def test_parse_http_date(self):
        """The Date header of an answer is read as unix seconds."""
        assert parse_http_date('Thu, 06 Nov 2026 19:39:59 GMT') == calendar.timegm(
            datetime(2026, 11, 6, 19, 39, 59).timetuple())
        assert parse_http_date(None) is None
        assert parse_http_date('not a date') is None
