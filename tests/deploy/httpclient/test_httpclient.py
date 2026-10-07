"""
Tests for AsyncHttpClient: the request core (the timeout and the
transport error retry) and the verb wrappers.

The requests are served by an httpx2.MockTransport handler, the tests
never touch the network. Every test performing a request is async
(pytest-trio); the retry of a test runs on the virtual clock
(autojump_clock), so the backoff is asserted exactly instead of slept.
"""
import gc
import weakref

import httpx2
import pytest
import trio

from alasio.deploy.httpclient.httpclient import AsyncHttpClient
from alasio.logger import logger


def make_aclient(handler):
    """A httpx2.AsyncClient with a MockTransport handler."""
    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


class TestRequest:
    """The request core: the verb, the headers and the timeout."""

    @pytest.mark.trio
    async def test_get_sends_get(self):
        """get() sends the GET method with the headers of the call."""
        requests = []

        def handler(request):
            requests.append(request)
            return httpx2.Response(200, content=b'ok')

        client = AsyncHttpClient(make_aclient(handler))
        response = await client.get('http://test/a', headers={'Range': 'bytes=0-1'})
        assert response.content == b'ok'
        assert requests[0].method == 'GET'
        assert str(requests[0].url) == 'http://test/a'
        assert requests[0].headers['Range'] == 'bytes=0-1'

    @pytest.mark.trio
    async def test_request_sends_the_method(self):
        """request() sends the method of the call: the core of the
        future verbs (post, ...)."""
        requests = []

        def handler(request):
            requests.append(request)
            return httpx2.Response(200, content=b'ok')

        client = AsyncHttpClient(make_aclient(handler))
        response = await client.request('HEAD', 'http://test/a')
        assert response.status_code == 200
        assert [request.method for request in requests] == ['HEAD']

    @pytest.mark.trio
    async def test_timeout_of_the_call(self, autojump_clock):
        """The timeout of the call replaces the default timeout of the
        client and is applied to every attempt, the retried ones
        included."""
        timeouts = []

        def handler(request):
            timeouts.append(request.extensions['timeout'])
            if len(timeouts) == 1:
                raise httpx2.ReadTimeout('read timed out')
            return httpx2.Response(200, content=b'ok')

        client = AsyncHttpClient(make_aclient(handler))
        with logger.mock_capture_writer():
            response = await client.get('http://test/a', timeout=2.5)
        assert response.content == b'ok'
        assert timeouts == [{'connect': 2.5, 'read': 2.5, 'write': 2.5, 'pool': 2.5}] * 2

    @pytest.mark.trio
    async def test_default_timeout_of_the_client(self):
        """Without a timeout of the call, the default timeout of
        AsyncHttpClient applies: the connect/pool budget stays short,
        the read/write budget is longer. The default timeout of the
        injected raw client does not apply."""
        timeouts = []

        def handler(request):
            timeouts.append(request.extensions['timeout'])
            return httpx2.Response(200, content=b'ok')

        raw = httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=0.25)
        client = AsyncHttpClient(raw)
        assert (await client.get('http://test/a')).content == b'ok'
        default = AsyncHttpClient.DEFAULT_TIMEOUT
        assert timeouts == [{
            'connect': AsyncHttpClient.PROBE_TIMEOUT,
            'read': default.read,
            'write': default.write,
            'pool': AsyncHttpClient.PROBE_TIMEOUT,
        }]
        assert default.read == default.write
        assert default.read > AsyncHttpClient.PROBE_TIMEOUT


class TestRetry:
    """A transport error is retried up to the attempts of the request,
    every failed attempt is logged; an http status error is never
    retried."""

    @pytest.mark.trio
    async def test_transport_error_is_retried(self, autojump_clock):
        """The first attempt raises a transport error, the retry waits
        RETRY_BACKOFF and works; the absorbed failure is still logged."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            if len(requests) == 1:
                raise httpx2.ReadTimeout('read timed out')
            return httpx2.Response(200, content=b'ok')

        client = AsyncHttpClient(make_aclient(handler))
        start = trio.current_time()
        with logger.mock_capture_writer() as capture:
            response = await client.get('http://test/a')
        assert response.content == b'ok'
        assert requests == ['http://test/a', 'http://test/a']
        assert trio.current_time() - start == AsyncHttpClient.RETRY_BACKOFF
        warnings = [log['m'] for log in capture.backend.logs if log['l'] == 'WARNING']
        assert warnings == [
            'Request to "http://test/a" failed (attempt 1/3): ReadTimeout: read timed out',
        ]

    @pytest.mark.trio
    async def test_attempts_are_three(self, autojump_clock):
        """Two transport errors are retried: the third attempt is the
        last chance and succeeds after two waits."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            if len(requests) <= 2:
                raise httpx2.ReadTimeout('read timed out')
            return httpx2.Response(200, content=b'ok')

        client = AsyncHttpClient(make_aclient(handler))
        start = trio.current_time()
        with logger.mock_capture_writer():
            response = await client.get('http://test/a')
        assert response.content == b'ok'
        assert requests == ['http://test/a'] * 3
        assert trio.current_time() - start == 2 * AsyncHttpClient.RETRY_BACKOFF

    @pytest.mark.trio
    async def test_retry_is_limited(self, autojump_clock):
        """A persistent transport error is raised after the attempts,
        every wait happened before."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            raise httpx2.ReadTimeout('read timed out')

        client = AsyncHttpClient(make_aclient(handler))
        start = trio.current_time()
        with logger.mock_capture_writer():
            with pytest.raises(httpx2.ReadTimeout):
                await client.get('http://test/a')
        assert requests == ['http://test/a'] * 3
        assert trio.current_time() - start == 2 * AsyncHttpClient.RETRY_BACKOFF

    @pytest.mark.trio
    async def test_attempts_one_is_single_shot(self, autojump_clock):
        """attempts=1 makes the request single-shot without a wait: the
        probe candidates of a race pass it."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            raise httpx2.ReadTimeout('read timed out')

        client = AsyncHttpClient(make_aclient(handler))
        start = trio.current_time()
        with logger.mock_capture_writer():
            with pytest.raises(httpx2.ReadTimeout):
                await client.get('http://test/a', attempts=1)
        assert requests == ['http://test/a']
        assert trio.current_time() - start == 0

    @pytest.mark.trio
    async def test_status_error_is_not_retried(self, autojump_clock):
        """An http status error is the answer of the server: it is
        raised as-is, without a retry."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return httpx2.Response(500)

        client = AsyncHttpClient(make_aclient(handler))
        start = trio.current_time()
        with logger.mock_capture_writer():
            with pytest.raises(httpx2.HTTPStatusError):
                await client.get('http://test/a')
        assert requests == ['http://test/a']
        # the status error is not retried: the request was sent once
        assert trio.current_time() - start == 0

    @pytest.mark.trio
    async def test_every_exhausted_failure_is_logged(self, autojump_clock):
        """Exhausted retries read as the sequence of attempts, one
        warning per failed attempt."""

        def handler(request):
            raise httpx2.ReadTimeout('read timed out')

        client = AsyncHttpClient(make_aclient(handler))
        with logger.mock_capture_writer() as capture:
            with pytest.raises(httpx2.ReadTimeout):
                await client.get('http://test/a')
        warnings = [log['m'] for log in capture.backend.logs if log['l'] == 'WARNING']
        assert warnings == [
            'Request to "http://test/a" failed (attempt 1/3): ReadTimeout: read timed out',
            'Request to "http://test/a" failed (attempt 2/3): ReadTimeout: read timed out',
            'Request to "http://test/a" failed (attempt 3/3): ReadTimeout: read timed out',
        ]


class TestLifecycle:
    """The client of the instance: created on the first request and
    reused by every later one, an injected or taken client belongs to
    its owner."""

    @pytest.mark.trio
    async def test_created_client_is_reused_and_closed(self, monkeypatch):
        """Without an injected client, the first request creates the
        client of the instance and every later request reuses it;
        aclose() closes it, closing again is a no-op."""
        requests = []
        created = []

        def handler(request):
            requests.append(str(request.url))
            return httpx2.Response(200, content=b'ok')

        class TrackingClient(httpx2.AsyncClient):
            def __init__(self, **kwargs):
                super().__init__(transport=httpx2.MockTransport(handler))
                created.append(self)

        monkeypatch.setattr(httpx2, 'AsyncClient', TrackingClient)
        client = AsyncHttpClient()
        # nothing was created yet, aclose is a no-op
        await client.aclose()
        assert created == []
        assert (await client.get('http://test/a')).content == b'ok'
        assert (await client.get('http://test/a')).content == b'ok'
        assert len(created) == 1 and requests == ['http://test/a', 'http://test/a']
        assert not created[0].is_closed
        await client.aclose()
        assert created[0].is_closed
        await client.aclose()
        assert created[0].is_closed

    @pytest.mark.trio
    async def test_injected_client_is_used_and_never_closed(self):
        """An injected client serves the requests and is left alone by
        aclose(): its lifetime belongs to the caller."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return httpx2.Response(200, content=b'ok')

        raw = make_aclient(handler)
        client = AsyncHttpClient(raw)
        assert (await client.get('http://test/a')).content == b'ok'
        await client.aclose()
        assert not raw.is_closed
        # the injected client is still usable
        assert (await client.get('http://test/a')).content == b'ok'
        assert requests == ['http://test/a', 'http://test/a']

    @pytest.mark.trio
    async def test_client_of_another_wrapper_is_taken(self, monkeypatch):
        """An AsyncHttpClient input takes the client of the other
        wrapper: the two share it and no second client is created, the
        taker never closes it."""
        requests = []
        created = []

        def handler(request):
            requests.append(str(request.url))
            return httpx2.Response(200, content=b'ok')

        class TrackingClient(httpx2.AsyncClient):
            def __init__(self, **kwargs):
                super().__init__(transport=httpx2.MockTransport(handler))
                created.append(self)

        monkeypatch.setattr(httpx2, 'AsyncClient', TrackingClient)
        source = AsyncHttpClient()
        assert (await source.get('http://test/a')).content == b'ok'
        # the source created its client on its first request, the taker
        # takes that client
        shared = AsyncHttpClient(source)
        assert shared.client is created[0]
        assert (await shared.get('http://test/b')).content == b'ok'
        assert len(created) == 1
        # the client belongs to the source, the taker never closes it
        await shared.aclose()
        assert not created[0].is_closed
        assert (await source.get('http://test/c')).content == b'ok'
        assert requests == ['http://test/a', 'http://test/b', 'http://test/c']
        # the source owns the client and closes it
        await source.aclose()
        assert created[0].is_closed

    @pytest.mark.trio
    async def test_input_wrapper_is_released(self):
        """Only the client of the input wrapper is kept, not the wrapper
        itself: the source can be released right after construction."""

        def handler(request):
            return httpx2.Response(200, content=b'ok')

        source = AsyncHttpClient(make_aclient(handler))
        shared = AsyncHttpClient(source)
        source_ref = weakref.ref(source)
        del source
        gc.collect()
        assert source_ref() is None
        # the taken client is still usable
        assert (await shared.get('http://test/a')).content == b'ok'

    @pytest.mark.trio
    async def test_unused_wrapper_contributes_no_client(self, monkeypatch):
        """A wrapper that has not created its client yet has none to
        take at construction: the taker creates its own client on the
        first request and owns it (wrap a real client to share one)."""
        created = []

        def handler(request):
            return httpx2.Response(200, content=b'ok')

        class TrackingClient(httpx2.AsyncClient):
            def __init__(self, **kwargs):
                super().__init__(transport=httpx2.MockTransport(handler))
                created.append(self)

        monkeypatch.setattr(httpx2, 'AsyncClient', TrackingClient)
        source = AsyncHttpClient()
        shared = AsyncHttpClient(source)
        assert shared.client is None
        assert (await shared.get('http://test/a')).content == b'ok'
        assert len(created) == 1 and shared.client is created[0]
        # the source was never used, it has no client of its own
        assert source.client is None
        # the taker created the client itself: it owns and closes it
        await shared.aclose()
        assert created[0].is_closed
