"""
Retrying async http client: the shared request core (timeout and
transport error retry) and the verb wrappers on top of it.

The core is request(): it sends one request with the client of the
instance, applies the timeout of the call and retries a transport
error. get() and the future verbs (post, ...) are thin wrappers that
only fill in the method, mirroring the request()/get()/post()
structure of httpx2 itself.

Every request carries a timeout: the timeout of the call, or the
default timeout of the class (DEFAULT_TIMEOUT) when the call gives
none. The default keeps the connection budget short (PROBE_TIMEOUT:
a peer that cannot be connected within the budget is dropped
quickly) and gives the transfer a longer read/write budget (a data
range may be large, a slow transfer must not be taken for a stall).
A call overrides the default with its own timeout, e.g. a
latest.pack request uses the short budget for every phase (see
ServerFile).

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

The wait before a retry is a trio sleep, so the cancellation of the
enclosing task interrupts it, like it interrupts the in-flight request
itself.

The first user is ServerFile, the pack update client of the deploy
domain (alasio/deploy/pack/server_file.py).

httpx2 is imported lazily in the places that use it: the package
resolves its own version with importlib.metadata at import time, which
the in-memory filesystem of the tests cannot answer.
"""
import trio

from alasio.ext.cache import cached_class_property
from alasio.logger import logger


class AsyncHttpClient:
    """
    Retrying async http client over a httpx2.AsyncClient.

    The client of the instance is the injected httpx2.AsyncClient,
    the client of another AsyncHttpClient (the client is taken, so the
    wrappers share one client and one connection pool), or one created
    on the first request: it is reused by every later request, so the
    keep-alive connections of a flow are reused instead of a new
    connection pool per request. aclose() closes a client the instance
    created; an injected or taken client belongs to its owner (the
    caller, or the wrapper that created it) and is left alone.

    The retry policy is REQUEST_ATTEMPTS and RETRY_BACKOFF: a request
    whose caller does not set the attempts retries a transport error
    up to that many attempts, waiting that long between them. The
    callers that need a single-shot request (e.g. a probe candidate
    racing other candidates) pass attempts=1.

    The timeout of a request is the timeout of the call, or the
    default timeout of the class (DEFAULT_TIMEOUT) when the call gives
    none: the connect and pool budget is PROBE_TIMEOUT, the read and
    write budget is longer. The default timeout of an injected raw
    client does not apply, the timeout is always explicit for httpx.

    See the module docstring for the retry semantics, request() for
    the timeout of a call.
    """

    # seconds of the short budget: the connect/pool budget of
    # DEFAULT_TIMEOUT, and the budget of a request that passes it as
    # its timeout (a probe candidate and the latest.pack of a flow,
    # see ServerFile). A peer that cannot be connected within the
    # budget is dropped quickly (e.g. a mirror that is blocked in the
    # user network); the default timeout of httpx2.AsyncClient is the
    # same
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
            client (httpx2.AsyncClient | AsyncHttpClient, optional):
                Client to send the requests with, or another
                AsyncHttpClient whose client is taken: the two
                wrappers share one client and one connection pool, and
                the wrapper that created the client keeps owning it
                (see client). Only the client of the input wrapper is
                kept, not the wrapper itself, so the source can be
                released right after construction. The lifetime of an
                injected or taken client belongs to its owner (this
                class never closes it). Defaults to None: the client
                of this instance is created on the first request and
                reused by every later one, aclose() closes it
        """
        if isinstance(client, AsyncHttpClient):
            # take the client of the other wrapper: the two wrappers
            # share one client and one connection pool. Only the http
            # client is kept, the wrapper itself is not referenced, so
            # the source can be released after construction
            client = client.client
        # the http client of this instance: the injected or taken one,
        # or None until the first request creates the client of the
        # instance (see _get_aclient())
        self._client = client
        # whether the client belongs to this instance: a created one is
        # closed by aclose(), an injected or taken one is left alone
        self._own_client = client is None

    @property
    def client(self):
        """
        The httpx2.AsyncClient of this instance: the injected one, the
        one taken from another wrapper at construction, or the one
        created on the first request. None until a request created it -
        an instance does not create its client before its first
        request (see _get_aclient()).

        Returns:
            httpx2.AsyncClient: The client to send the requests with,
                None when it has not been created yet
        """
        return self._client

    def _get_aclient(self):
        """
        The async http client of this instance: the injected one, the
        one taken from another wrapper, or the client of the instance
        created on the first request.

        The client is reused by every request of the instance, so the
        keep-alive connections of a flow are reused instead of a new
        connection pool per request. Only a client created here is
        closed by aclose(); an injected or taken one belongs to its
        owner. No lock guards the lazy creation: every task of a flow
        runs on the same thread (the single thread invariant of async
        code), only await points switch tasks and the creation below
        has no await, so nothing races on it - the concurrent probe
        tasks of the first request all observe the same client.

        Returns:
            httpx2.AsyncClient: The client to send the requests with
        """
        if self._client is None:
            import httpx2
            self._client = httpx2.AsyncClient()
        return self._client

    async def aclose(self):
        """
        Close the http client of this instance when it created one.

        An injected client is never closed: its lifetime belongs to
        the caller, see __init__. The client created here is closed
        with its keep-alive connections; a closed instance must not
        send another request (the next request raises). Closing is
        idempotent, the owner of the instance usually closes it on
        exit. Do not close while a request is in flight.
        """
        client = self._client
        if self._own_client and client is not None:
            await client.aclose()

    async def request(self, method, url, headers=None, timeout=None, attempts=REQUEST_ATTEMPTS):
        """
        Send one http request and return its response, retrying a
        transport error up to `attempts` attempts.

        The core of every verb of the class: get() and the future
        verbs (post, ...) only fill in the method, the timeout and the
        retry are handled here.

        A transport error (dns lookup failure, connect refusal, ssl
        handshake failure, timeout, read/write error - there is no
        status code at all) is retried while an attempt is left: a
        one-off network blip should not fail the caller. An http
        status error is never retried here, it is the answer of the
        server and is classified by the caller (e.g. a mirror may be
        reselected for a 5xx, see ServerFile._mirror_failed). Every
        failed attempt is logged with a warning, whatever the failure
        is: an absorbed blip stays visible in the log and exhausted
        retries read as the sequence of attempts. The wait before a
        retry is a trio sleep, so the cancellation of the enclosing
        task interrupts it (like it interrupts the in-flight request
        itself).

        The timeout is the timeout of the call, or the default timeout
        of the class when the call gives none: httpx always receives
        it explicitly, the default timeout of the raw client never
        applies (see DEFAULT_TIMEOUT).

        Args:
            method (str): Http method, e.g. 'GET'
            url (str): URL to request
            headers (dict, optional): Request headers
            timeout (float | httpx2.Timeout, optional): Request
                timeout, the default timeout of the class
                (DEFAULT_TIMEOUT) when not given: the connect/pool
                budget is PROBE_TIMEOUT, the read/write budget is
                longer. A given timeout replaces the default
            attempts (int): Attempts of the request, the retry waits
                RETRY_BACKOFF seconds. Defaults to REQUEST_ATTEMPTS, a
                single-shot request passes 1

        Returns:
            httpx2.Response: The response

        Raises:
            httpx2.HTTPError: If the request fails: the transport
                error of the last attempt, or the status error of the
                server
        """
        import httpx2

        # the timeout of the call, or the default timeout of the
        # class: httpx always receives an explicit timeout
        if timeout is None:
            timeout = self.DEFAULT_TIMEOUT
        client = self._get_aclient()
        for attempt in range(attempts):
            if attempt:
                # a transport error is often a one-off blip of the
                # network: wait a moment, then retry the same request
                await trio.sleep(self.RETRY_BACKOFF)
            try:
                response = await client.request(method, url, headers=headers, timeout=timeout)
                response.raise_for_status()
                return response
            except httpx2.TransportError as e:
                # a transport error is logged, and retried while an
                # attempt is left: a one-off network blip should not
                # fail the request
                logger.warning(
                    f'Request to "{url}" failed (attempt {attempt + 1}/{attempts}): {type(e).__name__}: {e}')
                if attempt + 1 >= attempts:
                    raise
            except Exception as e:
                # every other failure (an http status error, ...) is
                # logged and raised as-is, it is never retried
                logger.warning(
                    f'Request to "{url}" failed (attempt {attempt + 1}/{attempts}): {type(e).__name__}: {e}')
                raise

    async def get(self, url, headers=None, timeout=None, attempts=REQUEST_ATTEMPTS):
        """
        Get a url: the GET wrapper of request(), see it for the retry,
        the timeout and the raised errors.

        Args:
            url (str): URL to get
            headers (dict, optional): Request headers
            timeout (float | httpx2.Timeout, optional): Request
                timeout, the default timeout of the class
                (DEFAULT_TIMEOUT) when not given, see request()
            attempts (int): Attempts of the request. Defaults to
                REQUEST_ATTEMPTS, a single-shot request passes 1

        Returns:
            httpx2.Response: The response

        Raises:
            httpx2.HTTPError: If the request fails
        """
        return await self.request('GET', url, headers=headers, timeout=timeout, attempts=attempts)
