"""
Tests for HttpClient: the request core (the timeout and the transport
error retry), the streamed transfer and the verb wrappers.

The requests are served by an httpx2.MockTransport handler, the tests
never touch the network. The retry backoff of the sync client is a real
time.sleep: the fake_sleep fixture records the waits instead of
sleeping them.
"""
import gc
import weakref

import httpx2
import pytest

from alasio.deploy.httpclient import sync_client as sync_client_module
from alasio.deploy.httpclient.sync_client import HttpClient
from alasio.logger import logger


def make_sclient(handler):
    """A httpx2.Client with a MockTransport handler."""
    return httpx2.Client(transport=httpx2.MockTransport(handler))


class FakeSleep:
    """
    Stand-in of the time module of sync_client: the sleep of the retry
    backoff is recorded instead of slept.

    Attributes:
        seconds (list[float]): Seconds of every sleep() call, in call order
    """

    def __init__(self):
        self.seconds = []

    def sleep(self, seconds):
        """
        Args:
            seconds (float): Seconds of the sleep, recorded
        """
        self.seconds.append(seconds)


@pytest.fixture
def fake_sleep(monkeypatch):
    """
    Replace the time module of sync_client with a recorder.

    The retry backoff of the sync client is a real time.sleep: the tests
    observe the waits instead of sleeping them.

    Returns:
        FakeSleep: The recorder, its seconds list holds the sleeps
    """
    fake = FakeSleep()
    monkeypatch.setattr(sync_client_module, 'time', fake)
    return fake


class TestSyncRequest:
    """The request core of HttpClient: the verb, the headers, the
    timeout and the redirects."""

    def test_get_sends_get(self):
        """get() sends the GET method with the headers of the call."""
        requests = []

        def handler(request):
            requests.append(request)
            return httpx2.Response(200, content=b'ok')

        client = HttpClient(make_sclient(handler))
        response = client.get('http://test/a', headers={'Range': 'bytes=0-1'})
        assert response.content == b'ok'
        assert requests[0].method == 'GET'
        assert str(requests[0].url) == 'http://test/a'
        assert requests[0].headers['Range'] == 'bytes=0-1'

    def test_request_sends_the_method(self):
        """request() sends the method of the call: the core of the
        future verbs (post, ...)."""
        requests = []

        def handler(request):
            requests.append(request)
            return httpx2.Response(200, content=b'ok')

        client = HttpClient(make_sclient(handler))
        response = client.request('HEAD', 'http://test/a')
        assert response.status_code == 200
        assert [request.method for request in requests] == ['HEAD']

    def test_timeout_of_the_call(self, fake_sleep):
        """The timeout of the call replaces the default timeout of the
        client and is applied to every attempt, the retried ones
        included."""
        timeouts = []

        def handler(request):
            timeouts.append(request.extensions['timeout'])
            if len(timeouts) == 1:
                raise httpx2.ReadTimeout('read timed out')
            return httpx2.Response(200, content=b'ok')

        client = HttpClient(make_sclient(handler))
        with logger.mock_capture_writer():
            response = client.get('http://test/a', timeout=2.5)
        assert response.content == b'ok'
        assert timeouts == [{'connect': 2.5, 'read': 2.5, 'write': 2.5, 'pool': 2.5}] * 2
        assert fake_sleep.seconds == [HttpClient.RETRY_BACKOFF]

    def test_default_timeout_of_the_client(self):
        """Without a timeout of the call, the default timeout of
        HttpClient applies: the connect/pool budget stays short, the
        read/write budget is longer. The default timeout of the injected
        raw client does not apply."""
        timeouts = []

        def handler(request):
            timeouts.append(request.extensions['timeout'])
            return httpx2.Response(200, content=b'ok')

        raw = httpx2.Client(transport=httpx2.MockTransport(handler), timeout=0.25)
        client = HttpClient(raw)
        assert client.get('http://test/a').content == b'ok'
        default = HttpClient.DEFAULT_TIMEOUT
        assert timeouts == [{
            'connect': HttpClient.PROBE_TIMEOUT,
            'read': default.read,
            'write': default.write,
            'pool': HttpClient.PROBE_TIMEOUT,
        }]
        assert default.read == default.write
        assert default.read > HttpClient.PROBE_TIMEOUT

    def test_follow_redirects(self):
        """follow_redirects=True is handed to httpx: the redirect of the
        mirror is followed, the default of the client applies without
        it."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            if request.url.path == '/httpx/':
                return httpx2.Response(302, headers={'Location': '/httpx/index/'})
            return httpx2.Response(200, content=b'ok')

        client = HttpClient(make_sclient(handler))
        assert client.get('http://mirror/httpx/', follow_redirects=True).content == b'ok'
        assert requests == ['http://mirror/httpx/', 'http://mirror/httpx/index/']
        requests.clear()
        # without follow_redirects the redirect is not followed and the
        # 3xx is raised by the wrapper like any other status error
        with pytest.raises(httpx2.HTTPStatusError, match='302'):
            client.get('http://mirror/httpx/')
        assert requests == ['http://mirror/httpx/']


class TestSyncRetry:
    """A transport error is retried up to the attempts of the request,
    every failed attempt is logged; an http status error is never
    retried. The wait between the attempts is a time.sleep, observed
    with the fake_sleep fixture."""

    def test_transport_error_is_retried(self, fake_sleep):
        """The first attempt raises a transport error, the retry waits
        RETRY_BACKOFF and works; the absorbed failure is still logged."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            if len(requests) == 1:
                raise httpx2.ReadTimeout('read timed out')
            return httpx2.Response(200, content=b'ok')

        client = HttpClient(make_sclient(handler))
        with logger.mock_capture_writer() as capture:
            response = client.get('http://test/a')
        assert response.content == b'ok'
        assert requests == ['http://test/a', 'http://test/a']
        assert fake_sleep.seconds == [HttpClient.RETRY_BACKOFF]
        warnings = [log['m'] for log in capture.backend.logs if log['l'] == 'WARNING']
        assert warnings == [
            'Request to "http://test/a" failed (attempt 1/3): ReadTimeout: read timed out',
        ]

    def test_attempts_are_three(self, fake_sleep):
        """Two transport errors are retried: the third attempt is the
        last chance and succeeds after two waits."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            if len(requests) <= 2:
                raise httpx2.ReadTimeout('read timed out')
            return httpx2.Response(200, content=b'ok')

        client = HttpClient(make_sclient(handler))
        with logger.mock_capture_writer():
            response = client.get('http://test/a')
        assert response.content == b'ok'
        assert requests == ['http://test/a'] * 3
        assert fake_sleep.seconds == [HttpClient.RETRY_BACKOFF] * 2

    def test_retry_is_limited(self, fake_sleep):
        """A persistent transport error is raised after the attempts,
        every wait happened before."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            raise httpx2.ReadTimeout('read timed out')

        client = HttpClient(make_sclient(handler))
        with logger.mock_capture_writer():
            with pytest.raises(httpx2.ReadTimeout):
                client.get('http://test/a')
        assert requests == ['http://test/a'] * 3
        assert fake_sleep.seconds == [HttpClient.RETRY_BACKOFF] * 2

    def test_attempts_one_is_single_shot(self, fake_sleep):
        """attempts=1 makes the request single-shot without a wait."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            raise httpx2.ReadTimeout('read timed out')

        client = HttpClient(make_sclient(handler))
        with logger.mock_capture_writer():
            with pytest.raises(httpx2.ReadTimeout):
                client.get('http://test/a', attempts=1)
        assert requests == ['http://test/a']
        assert fake_sleep.seconds == []

    def test_status_error_is_not_retried(self, fake_sleep):
        """An http status error is the answer of the server: it is
        raised as-is, without a retry."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return httpx2.Response(500)

        client = HttpClient(make_sclient(handler))
        with logger.mock_capture_writer():
            with pytest.raises(httpx2.HTTPStatusError):
                client.get('http://test/a')
        assert requests == ['http://test/a']
        assert fake_sleep.seconds == []

    def test_every_exhausted_failure_is_logged(self, fake_sleep):
        """Exhausted retries read as the sequence of attempts, one
        warning per failed attempt."""

        def handler(request):
            raise httpx2.ReadTimeout('read timed out')

        client = HttpClient(make_sclient(handler))
        with logger.mock_capture_writer() as capture:
            with pytest.raises(httpx2.ReadTimeout):
                client.get('http://test/a')
        warnings = [log['m'] for log in capture.backend.logs if log['l'] == 'WARNING']
        assert warnings == [
            'Request to "http://test/a" failed (attempt 1/3): ReadTimeout: read timed out',
            'Request to "http://test/a" failed (attempt 2/3): ReadTimeout: read timed out',
            'Request to "http://test/a" failed (attempt 3/3): ReadTimeout: read timed out',
        ]


class TestSyncStream:
    """The streamed transfer: the consumer is called once per attempt, a
    transport error of the transfer (opening the response or reading it)
    is retried as a whole with a fresh request."""

    def test_consume(self, fake_sleep):
        """The consumer reads the streamed response; a transport error
        while opening it is retried."""
        attempts = []

        def handler(request):
            attempts.append(str(request.url))
            if len(attempts) == 1:
                raise httpx2.ReadTimeout('read timed out')
            return httpx2.Response(200, content=b'payload')

        client = HttpClient(make_sclient(handler))
        chunks = []
        with logger.mock_capture_writer():
            client.stream('http://test/a', lambda response: chunks.extend(response.iter_bytes()))
        assert chunks == [b'payload']
        assert attempts == ['http://test/a', 'http://test/a']
        assert fake_sleep.seconds == [HttpClient.RETRY_BACKOFF]

    def test_mid_stream_error_restarts_the_transfer(self, fake_sleep):
        """A transport error while the consumer reads the response
        discards the attempt: the transfer starts over with a fresh
        request."""
        class BrokenStream(httpx2.SyncByteStream):
            """A stream that yields one chunk, then fails like a
            connection that dropped mid-transfer."""

            def __iter__(self):
                yield b'partial '
                raise httpx2.ReadError('connection lost')

        attempts = []

        def handler(request):
            attempts.append(str(request.url))
            if len(attempts) == 1:
                return httpx2.Response(200, stream=BrokenStream())
            return httpx2.Response(200, content=b'payload')

        client = HttpClient(make_sclient(handler))
        chunks = []
        with logger.mock_capture_writer() as capture:
            client.stream('http://test/a', lambda response: chunks.extend(response.iter_bytes()))
        # the chunk of the failed attempt was handed over, then the
        # whole transfer was started over
        assert chunks == [b'partial ', b'payload']
        assert attempts == ['http://test/a', 'http://test/a']
        assert fake_sleep.seconds == [HttpClient.RETRY_BACKOFF]
        warnings = [log['m'] for log in capture.backend.logs if log['l'] == 'WARNING']
        assert warnings == [
            'Request to "http://test/a" failed (attempt 1/3): ReadError: connection lost',
        ]

    def test_consumer_failure_is_not_retried(self, fake_sleep):
        """A failure of the consumer (a hash check, ...) is raised as-is
        and is never retried: it is not a transport error."""
        attempts = []

        def handler(request):
            attempts.append(str(request.url))
            return httpx2.Response(200, content=b'payload')

        def consume(response):
            raise ValueError('digest mismatch')

        client = HttpClient(make_sclient(handler))
        with logger.mock_capture_writer() as capture:
            with pytest.raises(ValueError, match='digest mismatch'):
                client.stream('http://test/a', consume)
        assert attempts == ['http://test/a']
        assert fake_sleep.seconds == []
        warnings = [log['m'] for log in capture.backend.logs if log['l'] == 'WARNING']
        assert warnings == [
            'Request to "http://test/a" failed (attempt 1/3): ValueError: digest mismatch',
        ]

    def test_attempts_one_is_single_shot(self, fake_sleep):
        """attempts=1 makes the transfer single-shot without a wait."""
        attempts = []

        def handler(request):
            attempts.append(str(request.url))
            raise httpx2.ReadTimeout('read timed out')

        client = HttpClient(make_sclient(handler))
        with logger.mock_capture_writer():
            with pytest.raises(httpx2.ReadTimeout):
                client.stream('http://test/a', lambda response: None, attempts=1)
        assert attempts == ['http://test/a']
        assert fake_sleep.seconds == []


class TestSyncLifecycle:
    """The client of the instance: created on the first request and
    reused by every later one, an injected or taken client belongs to its
    owner."""

    def test_created_client_is_reused_and_closed(self, monkeypatch):
        """Without an injected client, the first request creates the
        client of the instance and every later request reuses it;
        close() closes it, closing again is a no-op."""
        requests = []
        created = []

        def handler(request):
            requests.append(str(request.url))
            return httpx2.Response(200, content=b'ok')

        class TrackingClient(httpx2.Client):
            def __init__(self, **kwargs):
                super().__init__(transport=httpx2.MockTransport(handler))
                created.append(self)

        monkeypatch.setattr(httpx2, 'Client', TrackingClient)
        client = HttpClient()
        # nothing was created yet, close is a no-op
        client.close()
        assert created == []
        assert client.get('http://test/a').content == b'ok'
        assert client.get('http://test/a').content == b'ok'
        assert len(created) == 1 and requests == ['http://test/a', 'http://test/a']
        assert not created[0].is_closed
        client.close()
        assert created[0].is_closed
        client.close()
        assert created[0].is_closed

    def test_injected_client_is_used_and_never_closed(self):
        """An injected client serves the requests and is left alone by
        close(): its lifetime belongs to the caller."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return httpx2.Response(200, content=b'ok')

        raw = make_sclient(handler)
        client = HttpClient(raw)
        assert client.get('http://test/a').content == b'ok'
        client.close()
        assert not raw.is_closed
        # the injected client is still usable
        assert client.get('http://test/a').content == b'ok'
        assert requests == ['http://test/a', 'http://test/a']

    def test_client_of_another_wrapper_is_taken(self, monkeypatch):
        """An HttpClient input takes the client of the other wrapper:
        the two share it and no second client is created, the taker
        never closes it."""
        created = []

        def handler(request):
            return httpx2.Response(200, content=b'ok')

        class TrackingClient(httpx2.Client):
            def __init__(self, **kwargs):
                super().__init__(transport=httpx2.MockTransport(handler))
                created.append(self)

        monkeypatch.setattr(httpx2, 'Client', TrackingClient)
        source = HttpClient()
        assert source.get('http://test/a').content == b'ok'
        # the source created its client on its first request, the taker
        # takes that client
        shared = HttpClient(source)
        assert shared.client is created[0]
        assert shared.get('http://test/b').content == b'ok'
        assert len(created) == 1
        # the client belongs to the source, the taker never closes it
        shared.close()
        assert not created[0].is_closed
        assert source.get('http://test/c').content == b'ok'
        # the source owns the client and closes it
        source.close()
        assert created[0].is_closed

    def test_input_wrapper_is_released(self):
        """Only the client of the input wrapper is kept, not the wrapper
        itself: the source can be released right after construction."""

        def handler(request):
            return httpx2.Response(200, content=b'ok')

        source = HttpClient(make_sclient(handler))
        shared = HttpClient(source)
        source_ref = weakref.ref(source)
        del source
        gc.collect()
        assert source_ref() is None
        # the taken client is still usable
        assert shared.get('http://test/a').content == b'ok'

    def test_unused_wrapper_contributes_no_client(self, monkeypatch):
        """A wrapper that has not created its client yet has none to
        take at construction: the taker creates its own client on the
        first request and owns it (wrap a real client to share one)."""
        created = []

        def handler(request):
            return httpx2.Response(200, content=b'ok')

        class TrackingClient(httpx2.Client):
            def __init__(self, **kwargs):
                super().__init__(transport=httpx2.MockTransport(handler))
                created.append(self)

        monkeypatch.setattr(httpx2, 'Client', TrackingClient)
        source = HttpClient()
        shared = HttpClient(source)
        assert shared.client is None
        assert shared.get('http://test/a').content == b'ok'
        assert len(created) == 1 and shared.client is created[0]
        # the source was never used, it has no client of its own
        assert source.client is None
        # the taker created the client itself: it owns and closes it
        shared.close()
        assert created[0].is_closed
