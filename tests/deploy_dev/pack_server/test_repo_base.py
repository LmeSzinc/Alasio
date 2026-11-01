"""
Tests for the state the steps of a repo share: repo_base.RepoBase.

The proxy of the config (PackRepoModel.Env) routes the downloads of the pack
server: the wheels come from the index a pin declares and never from a mirror
of the client, so a machine that cannot reach those indexes downloads them
through the proxy the operator configured, see RepoBase.fetch_client. One client
serves every fetcher of a step, with or without the proxy.
"""
import trio

from alasio.deploy_dev.pack_server import repo_base
from alasio.deploy_dev.pack_server.repo_base import RepoBase
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack_server.test_repo import PROXY, make_config, make_proxy_config, make_repo


class FakeClient:
    """
    A client of the tests: it sends nothing and records the close of the step.
    """

    def __init__(self):
        self.closed = False

    async def aclose(self):
        """Close the client, the end of the step that owned it."""
        self.closed = True


class TestFetchClient:
    """The client the wheel fetches of one step are sent with."""

    def test_direct_client_of_the_step(self, fs, run_dir, monkeypatch):
        """Without a proxy the step still gets one client of its own, closed at the end."""
        created = []

        def create_client(proxy):
            client = FakeClient()
            created.append((proxy, client))
            return client

        monkeypatch.setattr(repo_base, 'create_client', create_client)
        base = RepoBase(make_repo(), make_config())

        async def open():
            async with base.fetch_client() as client:
                assert client is created[0][1]
                assert client.closed is False
                return client

        with logger.mock_capture_writer() as capture:
            client = trio.run(open)

        # the step creates the client once, without a proxy, and closes it
        assert created == [('', client)]
        assert client.closed is True
        assert capture.concat() == ''

    def test_proxy(self, fs, run_dir, monkeypatch):
        """The proxy of the config builds the client of the step, closed with the step."""
        created = []

        def create_client(proxy):
            client = FakeClient()
            created.append((proxy, client))
            return client

        monkeypatch.setattr(repo_base, 'create_client', create_client)
        base = RepoBase(make_repo(), make_proxy_config())

        async def open():
            async with base.fetch_client() as client:
                # the client is the one of the proxy, open for the whole step
                assert client is created[0][1]
                assert client.closed is False
                return client

        with logger.mock_capture_writer() as capture:
            client = trio.run(open)

        assert created == [(PROXY, client)]
        assert client.closed is True
        assert capture.concat() == f"""\
INFO | Downloading the wheels through the proxy "{PROXY}"
"""

    def test_injected_client(self, fs, run_dir, monkeypatch):
        """An injected client is used as it is, the proxy of the config is not applied."""
        def create_client(proxy):
            raise AssertionError('The proxy of the config must not be used with an injected client')

        monkeypatch.setattr(repo_base, 'create_client', create_client)
        client = FakeClient()
        base = RepoBase(make_repo(), make_proxy_config(), client)

        async def open():
            async with base.fetch_client() as held:
                return held

        with logger.mock_capture_writer() as capture:
            assert trio.run(open) is client

        # the client of the caller is not closed by the step, the route of it is
        # the one the caller set up
        assert client.closed is False
        assert capture.concat() == ''
