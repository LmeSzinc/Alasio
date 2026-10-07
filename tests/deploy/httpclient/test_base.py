"""
Tests for HttpClientBase: the shared design of the retrying http clients
(the retry policy, the default timeout and the ownership of the httpx2
client), used by the sync HttpClient and the async AsyncHttpClient.
"""
import pytest

from alasio.deploy.httpclient.async_client import AsyncHttpClient
from alasio.deploy.httpclient.sync_client import HttpClient


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
