"""
Synchronous retrying http client: HttpClient, over a httpx2.Client.

The client of the synchronous callers of the deploy domain, e.g. the
wheel fetch of the pack server
(alasio/deploy_dev/pack_server/fetch_wheel.py). The retry policy, the
default timeout and the ownership of the client are the shared design of
alasio/deploy/httpclient/base.py, see HttpClientBase.

request() blocks the calling thread: the wait before a retry is a
time.sleep. stream() is the streamed transfer: the consumer reads the
response body (e.g. into an atomic file write) and the whole transfer is
retried on a transport error.

httpx2 is imported lazily in the places that use it: the package
resolves its own version with importlib.metadata at import time, which
the in-memory filesystem of the tests cannot answer.
"""
import time

from alasio.deploy.httpclient.base import HttpClientBase


class HttpClient(HttpClientBase):
    """
    Retrying synchronous http client over a httpx2.Client.

    The sync twin of AsyncHttpClient: the same retry policy, default
    timeout and ownership of the client (the shared design of base.py),
    with blocking requests for the synchronous callers.

    See request() for the timeout and stream() for the retried transfer.
    """

    def __init__(self, client=None):
        """
        Args:
            client (httpx2.Client | HttpClient, optional): Client to
                send the requests with, or another HttpClient whose
                client is taken: the two wrappers share one client and
                one connection pool, and the wrapper that created the
                client keeps owning it (see client). Only the client of
                the input wrapper is kept, not the wrapper itself, so
                the source can be released right after construction.
                The lifetime of an injected or taken client belongs to
                its owner (this class never closes it). Defaults to
                None: the client of this instance is created on the
                first request and reused by every later one, close()
                closes it
        """
        if isinstance(client, HttpClient):
            # take the client of the other wrapper: the two wrappers
            # share one client and one connection pool. Only the http
            # client is kept, the wrapper itself is not referenced, so
            # the source can be released after construction
            client = client.client
        super().__init__(client)

    def _get_client(self):
        """
        The http client of this instance: the injected one, the one
        taken from another wrapper, or the client of the instance
        created on the first request.

        The client is reused by every request of the instance, so the
        keep-alive connections of a flow are reused instead of a new
        connection pool per request. Only a client created here is
        closed by close(); an injected or taken one belongs to its
        owner. The creation itself opens nothing, the first request of
        the client opens the connection.

        Returns:
            httpx2.Client: The client to send the requests with
        """
        if self._client is None:
            import httpx2
            self._client = httpx2.Client()
        return self._client

    def close(self):
        """
        Close the http client of this instance when it created one.

        An injected client is never closed: its lifetime belongs to the
        caller, see __init__. The client created here is closed with its
        keep-alive connections; a closed instance must not send another
        request (the next request raises). Closing is idempotent, the
        owner of the instance usually closes it on exit. Do not close
        while a request is in flight.
        """
        client = self._client
        if self._own_client and client is not None:
            client.close()

    def request(self, method, url, headers=None, timeout=None, attempts=HttpClientBase.REQUEST_ATTEMPTS, follow_redirects=None):
        """
        Send one http request and return its response, retrying a
        transport error up to `attempts` attempts.

        The retry semantics are the shared design of base.py: only a
        transport error is retried, an http status error is the answer
        of the server and is classified by the caller, and every failed
        attempt is logged with a warning. The wait before a retry is a
        time.sleep, it blocks the calling thread.

        The timeout is the timeout of the call, or the default timeout
        of the class when the call gives none: httpx always receives it
        explicitly, the default timeout of the raw client never applies
        (see HttpClientBase).

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
            follow_redirects (bool, optional): Whether httpx follows the
                redirects of the response, the default of the client
                when not given

        Returns:
            httpx2.Response: The response

        Raises:
            httpx2.HTTPError: If the request fails: the transport error
                of the last attempt, or the status error of the server
        """
        import httpx2

        timeout = self._get_timeout(timeout)
        # follow_redirects is only passed when set: the default of the
        # client applies otherwise
        kwargs = {'follow_redirects': follow_redirects} if follow_redirects is not None else {}
        client = self._get_client()
        for attempt in range(attempts):
            if attempt:
                # a transport error is often a one-off blip of the
                # network: wait a moment, then retry the same request
                time.sleep(self.RETRY_BACKOFF)
            try:
                response = client.request(method, url, headers=headers, timeout=timeout, **kwargs)
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

    def get(self, url, headers=None, timeout=None, attempts=HttpClientBase.REQUEST_ATTEMPTS, follow_redirects=None):
        """
        Get a url: the GET wrapper of request(), see it for the retry,
        the timeout, the redirects and the raised errors.

        Args:
            url (str): URL to get
            headers (dict, optional): Request headers
            timeout (float | httpx2.Timeout, optional): Request
                timeout, the default timeout of the class when not
                given, see request()
            attempts (int): Attempts of the request. Defaults to
                REQUEST_ATTEMPTS, a single-shot request passes 1
            follow_redirects (bool, optional): Whether httpx follows the
                redirects of the response, the default of the client
                when not given

        Returns:
            httpx2.Response: The response

        Raises:
            httpx2.HTTPError: If the request fails
        """
        return self.request(
            'GET', url, headers=headers, timeout=timeout, attempts=attempts,
            follow_redirects=follow_redirects)

    def stream(self, url, consume, headers=None, timeout=None, attempts=HttpClientBase.REQUEST_ATTEMPTS, follow_redirects=None):
        """
        Get a url and hand the streamed response to a consumer, retrying
        the whole transfer.

        The core of a download: the response body is not read into
        memory, the consumer reads it (e.g. response.iter_bytes() into
        an atomic file write). A transport error while opening the
        response and a transport error while the consumer reads it
        (e.g. a read timeout in the middle of a download) discard the
        attempt and the transfer is started over with a fresh request:
        the consumer is called once per attempt and must leave nothing
        of a failed attempt behind (an atomic write discards its tmp
        file on a failure). Any other failure of the consumer (e.g. a
        hash check of the bytes) is logged and raised as-is, it is never
        retried.

        Args:
            url (str): URL to get
            consume (Callable[[httpx2.Response], Any]): Consumer of one
                attempt, called with the open response of a fresh
                request; every byte it does not read is discarded with
                the attempt
            headers (dict, optional): Request headers
            timeout (float | httpx2.Timeout, optional): Request
                timeout, the default timeout of the class when not
                given, see request()
            attempts (int): Attempts of the transfer. Defaults to
                REQUEST_ATTEMPTS, a single-shot transfer passes 1
            follow_redirects (bool, optional): Whether httpx follows the
                redirects of the response, the default of the client
                when not given

        Raises:
            httpx2.HTTPError: If the transfer fails on every attempt:
                the transport error of the last one, or the status
                error of the server
            Exception: The failure of the consumer, raised as-is
        """
        import httpx2

        timeout = self._get_timeout(timeout)
        kwargs = {'follow_redirects': follow_redirects} if follow_redirects is not None else {}
        client = self._get_client()
        for attempt in range(attempts):
            if attempt:
                # a transport error is often a one-off blip of the
                # network: wait a moment, then retry the same transfer
                time.sleep(self.RETRY_BACKOFF)
            try:
                with client.stream('GET', url, headers=headers, timeout=timeout, **kwargs) as response:
                    response.raise_for_status()
                    consume(response)
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
