"""
Asynchronous retrying http client: AsyncHttpClient, over a
httpx2.AsyncClient.

The client of the asynchronous callers of the deploy domain, e.g.
ServerFile, the pack update client
(alasio/deploy/pack/server_file.py). The retry policy, the default
timeout and the ownership of the client are the shared design of
alasio/deploy/httpclient/base.py, see HttpClientBase.

request() runs on the event loop of the caller (trio): the wait before a
retry is a trio sleep, so the cancellation of the enclosing task
interrupts it, like it interrupts the in-flight request itself. stream()
is the streamed transfer, the async twin of HttpClient.stream().

httpx2 is imported lazily in the places that use it: the package
resolves its own version with importlib.metadata at import time, which
the in-memory filesystem of the tests cannot answer.
"""
import trio

from alasio.deploy.httpclient.base import HttpClientBase


class AsyncHttpClient(HttpClientBase):
    """
    Retrying async http client over a httpx2.AsyncClient.

    The async twin of HttpClient: the same retry policy, default timeout
    and ownership of the client (the shared design of base.py), for the
    callers that run on the event loop (trio).

    See request() for the timeout of a call and stream() for the retried
    transfer.
    """

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
        super().__init__(client)

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

    async def request(self, method, url, headers=None, timeout=None, attempts=HttpClientBase.REQUEST_ATTEMPTS):
        """
        Send one http request and return its response, retrying a
        transport error up to `attempts` attempts.

        The core of every verb of the class: get() and the future
        verbs (post, ...) only fill in the method, the timeout and the
        retry are handled here.

        The retry semantics are the shared design of base.py: a
        transport error (dns lookup failure, connect refusal, ssl
        handshake failure, timeout, read/write error - there is no
        status code at all) is retried while an attempt is left, an
        http status error is never retried (it is the answer of the
        server, classified by the caller, e.g. a mirror may be
        reselected for a 5xx, see ServerFile._mirror_failed), and every
        failed attempt is logged with a warning. The wait before a
        retry is a trio sleep, so the cancellation of the enclosing
        task interrupts it (like it interrupts the in-flight request
        itself).

        The timeout is the timeout of the call, or the default timeout
        of the class when the call gives none: httpx always receives
        it explicitly, the default timeout of the raw client never
        applies (see HttpClientBase).

        Args:
            method (str): Http method, e.g. 'GET'
            url (str): URL to request
            headers (dict, optional): Request headers
            timeout (float | httpx2.Timeout, optional): Request
                timeout, the default timeout of the class when not
                given: the connect/pool budget is PROBE_TIMEOUT, the
                read/write budget is longer. A given timeout replaces
                the default
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

        timeout = self._get_timeout(timeout)
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
                self._log_attempt_failure(url, attempt, attempts, e)
                if attempt + 1 >= attempts:
                    raise
            except Exception as e:
                # every other failure (an http status error, ...) is
                # logged and raised as-is, it is never retried
                self._log_attempt_failure(url, attempt, attempts, e)
                raise

    async def get(self, url, headers=None, timeout=None, attempts=HttpClientBase.REQUEST_ATTEMPTS):
        """
        Get a url: the GET wrapper of request(), see it for the retry,
        the timeout and the raised errors.

        Args:
            url (str): URL to get
            headers (dict, optional): Request headers
            timeout (float | httpx2.Timeout, optional): Request
                timeout, the default timeout of the class when not
                given, see request()
            attempts (int): Attempts of the request. Defaults to
                REQUEST_ATTEMPTS, a single-shot request passes 1

        Returns:
            httpx2.Response: The response

        Raises:
            httpx2.HTTPError: If the request fails
        """
        return await self.request('GET', url, headers=headers, timeout=timeout, attempts=attempts)

    async def stream(self, url, consume, headers=None, timeout=None, attempts=HttpClientBase.REQUEST_ATTEMPTS):
        """
        Get a url and hand the streamed response to an async consumer,
        retrying the whole transfer.

        The async twin of HttpClient.stream(), see it for the contract:
        the response body is not read into memory, the consumer reads it
        (e.g. response.aiter_bytes() into an async file write). A
        transport error while opening the response and a transport
        error while the consumer reads it (e.g. a read timeout in the
        middle of a download) discard the attempt and the transfer is
        started over with a fresh request: the consumer is awaited once
        per attempt and must leave nothing of a failed attempt behind.
        Any other failure of the consumer (e.g. a hash check of the
        bytes) is logged and raised as-is, it is never retried. The
        wait before a retry is a trio sleep, so the cancellation of the
        enclosing task interrupts it (like it interrupts the in-flight
        request itself).

        Args:
            url (str): URL to get
            consume (Callable[[httpx2.Response], Awaitable]): Async
                consumer of one attempt, awaited with the open response
                of a fresh request; every byte it does not read is
                discarded with the attempt
            headers (dict, optional): Request headers
            timeout (float | httpx2.Timeout, optional): Request
                timeout, the default timeout of the class when not
                given, see request()
            attempts (int): Attempts of the transfer. Defaults to
                REQUEST_ATTEMPTS, a single-shot transfer passes 1

        Raises:
            httpx2.HTTPError: If the transfer fails on every attempt:
                the transport error of the last one, or the status
                error of the server
            Exception: The failure of the consumer, raised as-is
        """
        import httpx2

        timeout = self._get_timeout(timeout)
        client = self._get_aclient()
        for attempt in range(attempts):
            if attempt:
                # a transport error is often a one-off blip of the
                # network: wait a moment, then retry the same transfer
                await trio.sleep(self.RETRY_BACKOFF)
            try:
                async with client.stream('GET', url, headers=headers, timeout=timeout) as response:
                    response.raise_for_status()
                    await consume(response)
                return
            except httpx2.TransportError as e:
                # a transport error of the transfer (opening the
                # response or reading it) is logged, and retried while
                # an attempt is left
                self._log_attempt_failure(url, attempt, attempts, e)
                if attempt + 1 >= attempts:
                    raise
            except Exception as e:
                # every other failure (a status error, the check of the
                # consumer, ...) is logged and raised as-is, it is
                # never retried
                self._log_attempt_failure(url, attempt, attempts, e)
                raise
