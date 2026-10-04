"""
Tests for the mirror probe of ServerFile: the group racing, the
priority inside a group, the reuse of the probe payload, the gui.db
record and the probe-once rule.

Every mirror is an in-memory httpx2.MockTransport handler, the tests
never touch the network. The gui.db record is the FakeMirrorTable of
conftest, an in-memory table injected by make_server_url().
"""
import threading

import httpx2
import pytest

from alasio.deploy.httpclient.probe import AllMirrorsFailedError
from alasio.deploy.pack.server_file import ServerFile
from alasio.logger import logger
from tests.deploy_dev.pack.conftest import FakeMirrorTable, make_server_url


def latest_content(version='v1'):
    """
    Content of a latest.pack: the version and 20 bytes index checksum.

    Args:
        version (str): Version to serve

    Returns:
        bytes: latest.pack content
    """
    return version.encode() + b'\x00' * 20


def make_client(handler):
    """A httpx2.Client with a MockTransport handler."""
    return httpx2.Client(transport=httpx2.MockTransport(handler))


class TestSingleMirror:
    """A single mirror set has nothing to select."""

    def test_direct_request(self):
        """The request goes straight to the only mirror, no probe."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return httpx2.Response(200, content=latest_content('v1'))

        server = ServerFile('http://only', client=make_client(handler))
        with logger.mock_capture_writer():
            info = server.get_latest_info()
        assert info.version == 'v1'
        assert requests == ['http://only/latest.pack']


class TestProbeSelection:
    """The two-level selection: groups race, members fall back in order."""

    def test_group_members_in_order(self):
        """The members of a group are tried in order, the first usable wins."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            if request.url.host == 'down':
                return httpx2.Response(500)
            return httpx2.Response(200, content=latest_content('tencent'))

        table = FakeMirrorTable()
        server = ServerFile(
            make_server_url({'cn': {'down': 'http://down', 'up': 'http://up'}}, table=table),
            client=make_client(handler))
        with logger.mock_capture_writer():
            name, info = server.probe
        assert name == 'up'
        assert info.version == 'tencent'
        # the members were tried in the declared order
        assert requests == ['http://down/latest.pack', 'http://up/latest.pack']
        # the winner is recorded
        assert table.select_one(scope='').name == 'up'

    def test_preferred_member_wins(self):
        """A working preferred member is not bypassed by the fallback."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return httpx2.Response(200, content=latest_content('v1'))

        server = ServerFile(
            make_server_url({'cn': {'preferred': 'http://a', 'fallback': 'http://b'}}, table=FakeMirrorTable()),
            client=make_client(handler))
        with logger.mock_capture_writer():
            name, _ = server.probe
        assert name == 'preferred'
        assert requests == ['http://a/latest.pack']

    def test_groups_race(self, monkeypatch):
        """The fastest group wins, a slow group is not awaited."""
        release = threading.Event()
        entered = threading.Event()
        worker_done = {'slow': threading.Event(), 'fast': threading.Event()}

        def handler(request):
            if request.url.host == 'slow':
                entered.set()
                # the losing group is abandoned by the probe, it ends
                # when the test releases it
                release.wait(timeout=3)
            return httpx2.Response(200, content=latest_content(request.url.host))

        server = ServerFile(
            make_server_url({'slow': 'http://slow', 'fast': 'http://fast'}, table=FakeMirrorTable()),
            client=make_client(handler))
        # observe the completion of the group workers: the abandoned
        # worker logs its result after the probe returned, the test
        # waits for it before leaving the capture
        original_probe_group = server._probe_group

        def probe_group(members, results):
            try:
                original_probe_group(members, results)
            finally:
                worker_done[next(iter(members))].set()

        monkeypatch.setattr(server, '_probe_group', probe_group)
        with logger.mock_capture_writer():
            try:
                name, info = server.probe
                assert name == 'fast'
                assert info.version == 'fast'
                assert entered.wait(3)
                # the probe returned while the slow group is still running
                assert not worker_done['slow'].is_set()
            finally:
                release.set()
            assert worker_done['slow'].wait(3)

    def test_all_mirrors_failed(self):
        """Every candidate fails, the error carries the reasons."""
        def handler(request):
            return httpx2.Response(503)

        table = FakeMirrorTable()
        server = ServerFile(
            make_server_url({'a': 'http://a', 'b': 'http://x'}, table=table),
            client=make_client(handler))
        with logger.mock_capture_writer() as capture:
            with pytest.raises(AllMirrorsFailedError) as e:
                server.get_latest_info()
        message = str(e.value)
        assert 'a' in message
        assert 'b' in message
        assert capture.backend.any_contains('is not usable')
        # nothing was recorded
        assert table.select_one(scope='') is None


class TestSelectionReuse:
    """The resolved mirror of an instance: the payload, the record, the reuse."""

    def test_probe_payload_is_reused(self, monkeypatch):
        """The probe request is the prefetch: latest.pack is fetched
        once, and a later call fetches again (nothing is cached)."""
        release = threading.Event()
        requests = []
        worker_done = {'a': threading.Event(), 'b': threading.Event()}

        def handler(request):
            if request.url.host == 'b':
                # recorded after the release, the assertions below run
                # while this losing group is still in flight
                release.wait(timeout=3)
                requests.append(str(request.url))
                return httpx2.Response(200, content=latest_content('b'))
            requests.append(str(request.url))
            return httpx2.Response(200, content=latest_content('a'))

        table = FakeMirrorTable()
        server = ServerFile(
            make_server_url({'a': 'http://a', 'b': 'http://b'}, table=table),
            client=make_client(handler))
        # wait for the abandoned worker before leaving the capture
        original_probe_group = server._probe_group

        def probe_group(members, results):
            try:
                original_probe_group(members, results)
            finally:
                worker_done[next(iter(members))].set()

        monkeypatch.setattr(server, '_probe_group', probe_group)
        with logger.mock_capture_writer():
            try:
                info = server.get_latest_info()
                assert info.version == 'a'
                assert table.select_one(scope='').name == 'a'
                # the probe request is the only request made for the winner
                assert requests == ['http://a/latest.pack']
                info = server.get_latest_info()
                assert info.version == 'a'
                # the payload was not cached, the later call fetches again
                assert requests == ['http://a/latest.pack', 'http://a/latest.pack']
            finally:
                release.set()
            assert worker_done['b'].wait(3)

    def test_recorded_mirror_is_used_directly(self):
        """A recorded mirror is used without a probe."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return httpx2.Response(200, content=latest_content('v1'))

        table = FakeMirrorTable()
        server_url = make_server_url({'a': 'http://a', 'b': 'http://b'}, table=table)
        table.seed(set_key=server_url.set_key, name='b')
        server = ServerFile(server_url, client=make_client(handler))
        with logger.mock_capture_writer():
            info = server.get_latest_info()
        assert info.version == 'v1'
        assert requests == ['http://b/latest.pack']

    def test_pack_request_resolves_first(self):
        """A flow whose first request is a pack file resolves (probes)
        first, the probe payload is dropped then."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return httpx2.Response(200, content=latest_content('v2'))

        server = ServerFile(
            make_server_url({'g': {'a': 'http://a', 'b': 'http://b'}}, table=FakeMirrorTable()),
            client=make_client(handler))
        with logger.mock_capture_writer():
            content = server.get_file_content('v2', 0, 4)
        # the same group: the first member is probed, then the file of
        # the winner is fetched
        assert requests == ['http://a/latest.pack', 'http://a/v2/full.pack']
        assert content == latest_content('v2')[:4]


class TestFailureReselection:
    """A failing mirror is dropped and the probe selects a new one.

    The mirror set of the class is one group of two members, so the
    requests of the probe are sequential and the tests can assert the
    exact request list.
    """

    @staticmethod
    def recorded_server(handler):
        """
        A server of one group (g/b then g/a) with g/b recorded.

        Returns:
            tuple[FakeMirrorTable, ServerFile]: The table and the server
        """
        table = FakeMirrorTable()
        server_url = make_server_url({'g': {'b': 'http://b', 'a': 'http://a'}}, table=table)
        table.seed(set_key=server_url.set_key, name='b')
        return table, ServerFile(server_url, client=make_client(handler))

    @pytest.mark.parametrize('status', [500, 404])
    def test_latest_failure_reselects(self, status):
        """A failed latest.pack of the recorded mirror (5xx or 4xx)
        triggers a probe, the failed mirror is a candidate again."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            if request.url.host == 'b':
                return httpx2.Response(status)
            return httpx2.Response(200, content=latest_content('a'))

        table, server = self.recorded_server(handler)
        with logger.mock_capture_writer() as capture:
            info = server.get_latest_info()
        assert info.version == 'a'
        # the record is replaced by the winner
        assert table.select_one(scope='').name == 'a'
        # the record fetch, then the probe of the failed mirror and of
        # the fallback member
        assert requests == ['http://b/latest.pack', 'http://b/latest.pack', 'http://a/latest.pack']
        assert capture.backend.any_contains('probing again')

    def test_latest_timeout_reselects(self):
        """A timeout of the recorded mirror triggers a probe: the user
        network environment may have changed."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            if request.url.host == 'b':
                raise httpx2.ReadTimeout('read timed out')
            return httpx2.Response(200, content=latest_content('a'))

        table, server = self.recorded_server(handler)
        with logger.mock_capture_writer():
            info = server.get_latest_info()
        assert info.version == 'a'
        assert table.select_one(scope='').name == 'a'
        assert requests == ['http://b/latest.pack', 'http://b/latest.pack', 'http://a/latest.pack']

    def test_pack_request_reselects_and_retries(self):
        """A pack file failure reselects the mirror and retries the
        request once on the new winner."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            if request.url.host == 'b':
                return httpx2.Response(500)
            if request.url.path.endswith('/latest.pack'):
                return httpx2.Response(200, content=latest_content('v1'))
            return httpx2.Response(200, content=b'update pack data')

        table, server = self.recorded_server(handler)
        with logger.mock_capture_writer():
            assert server.get_update_pack('old', 'new') == b'update pack data'
        assert table.select_one(scope='').name == 'a'
        assert requests == [
            'http://b/new/from_old.pack',
            'http://b/latest.pack',
            'http://a/latest.pack',
            'http://a/new/from_old.pack',
        ]

    def test_retry_failure_is_raised(self):
        """The request is retried exactly once, a failure of the retry
        is raised."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            if request.url.host == 'b':
                return httpx2.Response(500)
            if request.url.path.endswith('/latest.pack'):
                return httpx2.Response(200, content=latest_content('v1'))
            return httpx2.Response(500)

        table, server = self.recorded_server(handler)
        with logger.mock_capture_writer():
            with pytest.raises(httpx2.HTTPStatusError):
                server.get_update_pack('old', 'new')
        assert requests == [
            'http://b/new/from_old.pack',
            'http://b/latest.pack',
            'http://a/latest.pack',
            'http://a/new/from_old.pack',
        ]

    def test_404_is_not_reselected(self):
        """A 4xx of a pack file is the answer of the server, the mirror
        is kept and the request is not retried."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return httpx2.Response(404)

        table, server = self.recorded_server(handler)
        with logger.mock_capture_writer():
            with pytest.raises(httpx2.HTTPStatusError):
                server.get_update_pack('old', 'new')
        assert requests == ['http://b/new/from_old.pack']
        assert table.select_one(scope='').name == 'b'

    @pytest.mark.parametrize('failure', [
        # no status code at all: dns lookup failure and ssl handshake
        # failure both raise httpx2.ConnectError, a timeout raises a
        # timeout exception; a blocked mirror never answers a status
        # code either way
        httpx2.ConnectError,
        httpx2.ConnectTimeout,
        httpx2.ReadTimeout,
    ])
    def test_transport_failure_reselects(self, failure):
        """A failure without a status code means the mirror is not
        usable: the request is reselected and retried."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            if request.url.host == 'b':
                raise failure('request failed')
            if request.url.path.endswith('/latest.pack'):
                return httpx2.Response(200, content=latest_content('v1'))
            return httpx2.Response(200, content=b'update pack data')

        table, server = self.recorded_server(handler)
        with logger.mock_capture_writer():
            assert server.get_update_pack('old', 'new') == b'update pack data'
        assert table.select_one(scope='').name == 'a'
        assert requests == [
            'http://b/new/from_old.pack',
            'http://b/latest.pack',
            'http://a/latest.pack',
            'http://a/new/from_old.pack',
        ]

    def test_total_failure_keeps_the_record(self):
        """Every mirror fails: the error is raised and the record keeps
        the old name, a total failure usually means the machine is
        offline."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            if request.url.host == 'b':
                raise httpx2.ReadTimeout('read timed out')
            return httpx2.Response(500)

        table, server = self.recorded_server(handler)
        with logger.mock_capture_writer():
            with pytest.raises(AllMirrorsFailedError):
                server.get_latest_info()
        assert requests == ['http://b/latest.pack', 'http://b/latest.pack', 'http://a/latest.pack']
        # nothing was saved, the old hint stays
        assert table.select_one(scope='').name == 'b'
        # the failed probe is recorded: a later attempt raises it again
        # instead of racing
        with logger.mock_capture_writer():
            with pytest.raises(AllMirrorsFailedError):
                server.get_latest_info()
        assert requests == [
            'http://b/latest.pack',
            'http://b/latest.pack',
            'http://a/latest.pack',
            'http://b/latest.pack',
        ]


class TestProbeOnce:
    """The probe of an instance runs at most once.

    A later access reuses the cached result, a recorded failure is
    re-raised and a failure of the selected mirror is raised as-is:
    probing again in the same flow is meaningless, the network
    environment does not change between its requests.
    """

    def test_probe_result_is_cached(self):
        """A second access reuses the cached probe, no request again."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return httpx2.Response(200, content=latest_content('v1'))

        server = ServerFile(
            make_server_url({'g': {'a': 'http://a', 'b': 'http://b'}}, table=FakeMirrorTable()),
            client=make_client(handler))
        with logger.mock_capture_writer():
            first = server.probe
            second = server.probe
        assert first is second
        assert first[0] == 'a'
        assert requests == ['http://a/latest.pack']

    def test_probe_failure_is_recorded(self):
        """A failed probe is recorded: a later access re-raises it
        without racing again."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return httpx2.Response(503)

        server = ServerFile(
            make_server_url({'g': {'a': 'http://a', 'b': 'http://b'}}, table=FakeMirrorTable()),
            client=make_client(handler))
        with logger.mock_capture_writer():
            with pytest.raises(AllMirrorsFailedError):
                server.probe
            tried = list(requests)
            with pytest.raises(AllMirrorsFailedError):
                server.probe
        assert tried == ['http://a/latest.pack', 'http://b/latest.pack']
        assert requests == tried

    def test_failure_after_the_resolve_probe_is_raised(self):
        """A mirror selected by the probe is not reselected: probing
        again would select the same mirror, the failure is raised."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            if request.url.path.endswith('/latest.pack'):
                return httpx2.Response(200, content=latest_content('v1'))
            return httpx2.Response(500)

        server = ServerFile(
            make_server_url({'g': {'a': 'http://a', 'b': 'http://b'}}, table=FakeMirrorTable()),
            client=make_client(handler))
        with logger.mock_capture_writer():
            with pytest.raises(httpx2.HTTPStatusError):
                server.get_file_content('v1', 0, 4)
        # the resolve probe selected a, the failed file request did not
        # probe again
        assert requests == ['http://a/latest.pack', 'http://a/v1/full.pack']

    def test_late_failure_does_not_reselect(self):
        """A failure after the reselect probe is raised as-is: the
        probe of the instance already ran."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            if request.url.host == 'b':
                return httpx2.Response(500)
            if request.url.path.endswith('/latest.pack'):
                return httpx2.Response(200, content=latest_content('v1'))
            return httpx2.Response(500)

        table = FakeMirrorTable()
        server_url = make_server_url({'g': {'b': 'http://b', 'a': 'http://a'}}, table=table)
        table.seed(set_key=server_url.set_key, name='b')
        server = ServerFile(server_url, client=make_client(handler))
        with logger.mock_capture_writer():
            # the first failure reselects: the probe selects a, the
            # retry of the request fails
            with pytest.raises(httpx2.HTTPStatusError):
                server.get_update_pack('old', 'new')
            tried = list(requests)
            # the probe already ran, the second failure is raised as-is
            with pytest.raises(httpx2.HTTPStatusError):
                server.get_update_pack('old', 'new')
        assert tried == [
            'http://b/new/from_old.pack',
            'http://b/latest.pack',
            'http://a/latest.pack',
            'http://a/new/from_old.pack',
        ]
        assert requests == tried + ['http://a/new/from_old.pack']
        # the winner of the probe replaced the record
        assert table.select_one(scope='').name == 'a'
