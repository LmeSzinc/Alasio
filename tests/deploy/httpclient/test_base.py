"""
Tests for HttpClientBase: the shared design of the retrying http clients
(the retry policy, the default timeout, the ownership of the httpx2
client and the ignoring of the environment of the user), used by the
sync HttpClient and the async AsyncHttpClient.
"""
import httpx2
import pytest

from alasio.deploy.httpclient.client_async import AsyncHttpClient
from alasio.deploy.httpclient.client_base import RetryPolicyBase
from alasio.deploy.httpclient.client_sync import HttpClient
from alasio.git.fetch.transport_http import GitHttpClient
from alasio.git.file.exception import PackBroken, PktLineBroken


class TestClientFlavor:
    """A wrapper only takes the client of a wrapper of its own flavor:
    an async client never works in the sync wrapper and the other way
    around."""

    def test_sync_client_rejects_an_async_wrapper(self):
        """An AsyncHttpClient is not a client of the sync flavor."""
        with pytest.raises(TypeError, match='cannot take the client of'):
            HttpClient(AsyncHttpClient())

    def test_async_client_rejects_a_sync_wrapper(self):
        """An HttpClient is not a client of the async flavor."""
        with pytest.raises(TypeError, match='cannot take the client of'):
            AsyncHttpClient(HttpClient())


class TestRetryErrors:
    """The failures a transfer retries are declared by the client itself, so a
    transport adds the errors of its protocol without knowing the others."""

    def test_the_policy_retries_nothing_by_default(self):
        """The policy owns no transport of its own, so it declares no failure."""
        assert RetryPolicyBase.retry_errors() == ()

    def test_the_http_clients_retry_a_transport_error(self):
        """A failure without a status code is the shared set of the flavor."""
        assert httpx2.TransportError in AsyncHttpClient.retry_errors()
        assert httpx2.TransportError in HttpClient.retry_errors()

    def test_a_client_adds_the_errors_of_its_protocol(self):
        """The git client over http retries a broken pack too, and keeps the
        transport errors of its parent."""
        assert PackBroken in GitHttpClient.retry_errors()
        assert PktLineBroken in GitHttpClient.retry_errors()
        assert httpx2.TransportError in GitHttpClient.retry_errors()
