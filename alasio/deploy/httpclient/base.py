"""
Shared design of the retrying http clients: the retry policy, the default
timeout and the ownership of the httpx2 client (HttpClientBase).

The concrete clients implement the requests of their flavor over it: the
synchronous sync_client.HttpClient, whose requests block the calling
thread, and the asynchronous async_client.AsyncHttpClient, whose requests
run on the event loop of the caller (trio).

The core of both clients is request(): it sends one request with the
client of the instance, applies the timeout of the call and retries a
transport error. get() and the future verbs (post, ...) are thin
wrappers that only fill in the method, mirroring the
request()/get()/post() structure of httpx2 itself; the clients also
stream a response into a consumer and retry the whole transfer
(HttpClient.stream() / AsyncHttpClient.stream()).

Only a transport error is retried, i.e. a failure without a status
code: dns lookup failure, connect refusal, ssl handshake failure,
timeout, read/write error. A one-off network blip of the user network
is absorbed instead of failing the caller. An http status error is
never retried: it is the answer of the server, and whether another
strategy (e.g. another mirror) makes sense is decided by the caller,
which knows the content semantics. Every failed attempt is logged
with a warning, whatever the failure is: an absorbed blip stays
visible in the log, and exhausted retries read as the sequence of
attempts.

Every request carries a timeout: the timeout of the call, or the default
timeout of the class (DEFAULT_TIMEOUT) when the call gives none. The
default keeps the connection budget short (PROBE_TIMEOUT: a peer that
cannot be connected within the budget is dropped quickly) and gives the
transfer a longer read/write budget (a data range may be large, a slow
transfer must not be taken for a stall). A call overrides the default
with its own timeout; httpx always receives an explicit timeout, the
default timeout of the injected raw client never applies.

The wait before a retry is a time.sleep in the sync client (it blocks
the calling thread) and a trio sleep in the async one, so the
cancellation of the enclosing task interrupts the async wait, like it
interrupts the in-flight request itself.

httpx2 is imported lazily in the places that use it: the package
resolves its own version with importlib.metadata at import time, which
the in-memory filesystem of the tests cannot answer.
"""
from alasio.ext.cache import cached_class_property
from alasio.logger import logger


class HttpClientBase:
    """
    Shared design of the retrying http clients: the retry policy, the
    default timeout and the ownership of the httpx2 client.

    The client of an instance is the injected httpx2 client, the client
    of another wrapper of the same flavor (the client is taken, so the
    wrappers share one client and one connection pool), or one created
    on the first request: it is reused by every later request, and
    client is the accessor. An injected or taken client belongs to its
    owner and is left alone; close() / aclose() closes a client the
    instance created.

    The concrete classes implement the requests of their flavor and
    unwrap the wrapper they take the client from, see HttpClient and
    AsyncHttpClient.
    """

    # seconds of the short budget: the connect/pool budget of
    # DEFAULT_TIMEOUT, and the budget of a request that passes it as
    # its timeout (a probe candidate and the latest.pack of a flow,
    # see ServerFile). A peer that cannot be connected within the
    # budget is dropped quickly (e.g. a mirror that is blocked in the
    # user network); the default timeout of httpx2 clients is the same
    PROBE_TIMEOUT = 5.0

    # default timeout of a request whose caller does not give one: the
    # connection budget stays short (a blocked peer is dropped
    # quickly), the read/write budget is longer (the range of one pack
    # file may be large, a slow transfer must not be taken for a
    # stall). A call overrides it with its own timeout. Built on the
    # first access, so httpx2 stays lazily imported, see the module
    # note
    @cached_class_property
    def DEFAULT_TIMEOUT(cls):
        """
        Returns:
            httpx2.Timeout: The default timeout of a request
        """
        import httpx2
        return httpx2.Timeout(connect=cls.PROBE_TIMEOUT, read=30.0, write=30.0, pool=cls.PROBE_TIMEOUT)

    # attempts of a request whose caller does not set them: a transport
    # error is retried, up to three attempts (a one-off network blip
    # should not fail a data request)
    REQUEST_ATTEMPTS = 3
    # seconds to wait before the retry of a request
    RETRY_BACKOFF = 0.5

    def __init__(self, client=None):
        """
        Args:
            client (httpx2.Client | httpx2.AsyncClient, optional): The
                client of the flavor of this class, already unwrapped by
                the concrete class when a wrapper was given, see
                HttpClient.__init__ and AsyncHttpClient.__init__. Its
                lifetime belongs to the caller (this class never closes
                an injected client). Defaults to None: the client of
                this instance is created on the first request and
                reused by every later one, close() / aclose() closes it
        """
        if isinstance(client, HttpClientBase):
            # the concrete class unwraps the wrapper of its own flavor
            # before calling here: a wrapper of the other flavor is a
            # caller error (an async client never works in the sync
            # client and the other way around)
            raise TypeError(
                f'{type(self).__name__} cannot take the client of {type(client).__name__}')
        self._client = client
        # whether the client belongs to this instance: a created one is
        # closed by close() / aclose(), an injected or taken one is left
        # alone
        self._own_client = client is None

    @property
    def client(self):
        """
        The httpx2 client of this instance: the injected one, the one
        taken from another wrapper at construction, or the one created
        on the first request. None until a request created it - an
        instance does not create its client before its first request
        (see _get_client() / _get_aclient()).

        Returns:
            httpx2.Client | httpx2.AsyncClient: The client to send the
                requests with, None when it has not been created yet
        """
        return self._client

    def _get_timeout(self, timeout):
        """
        The timeout of a request: the timeout of the call, or the
        default timeout of the class when the call gives none.

        Args:
            timeout (float | httpx2.Timeout | None): Timeout of the call

        Returns:
            httpx2.Timeout: The timeout to send with the request
        """
        return self.DEFAULT_TIMEOUT if timeout is None else timeout

    def _log_attempt_failure(self, url, attempt, attempts, e):
        """
        Log one failed attempt of a request.

        Every attempt is logged, whatever the failure is: an absorbed
        blip stays visible in the log and exhausted retries read as the
        sequence of attempts.

        Args:
            url (str): URL of the request
            attempt (int): Zero based index of the failed attempt
            attempts (int): Attempts of the request
            e (Exception): The failure of the attempt
        """
        logger.warning(
            f'Request to "{url}" failed (attempt {attempt + 1}/{attempts}): {type(e).__name__}: {e}')
