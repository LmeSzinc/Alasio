"""
Tests for the mirror probe of ServerFile: the group racing, the
priority inside a group, the reuse of the probe payload, the gui.db
record, the probe-once rule and the request retry.

Every mirror is an in-memory httpx2.MockTransport handler, the tests
never touch the network. The gui.db record is the FakeMirrorTable of
conftest, an in-memory table injected by make_server_url(). Every test
performing a request is async (pytest-trio); a slow candidate is a
slow async handler, the losing groups are cancelled by the probe
instead of running to their timeouts.
"""
from time import time

import httpx2
import pytest
import trio

from alasio.deploy.httpclient.httpclient import AsyncHttpClient
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


def make_aclient(handler):
    """A httpx2.AsyncClient with a MockTransport handler."""
    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


class TestSingleMirror:
    """A single mirror set has nothing to select."""

    @pytest.mark.trio
    async def test_direct_request(self):
        """The request goes straight to the only mirror, no probe."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return httpx2.Response(200, content=latest_content('v1'))

        server = ServerFile('http://only', client=make_aclient(handler))
        with logger.mock_capture_writer():
            info = await server.get_latest_info()
        assert info.version == 'v1'
        assert requests == ['http://only/latest.pack']


class TestProbeSelection:
    """The two-level selection: groups race, members fall back in order."""

    @pytest.mark.trio
    async def test_group_members_in_order(self):
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
            client=make_aclient(handler))
        with logger.mock_capture_writer():
            name, info = await server.probe()
        assert name == 'up'
        assert info.version == 'tencent'
        # the members were tried in the declared order
        assert requests == ['http://down/latest.pack', 'http://up/latest.pack']
        # the winner is recorded
        assert table.select_one(scope='').name == 'up'

    @pytest.mark.trio
    async def test_preferred_member_wins(self):
        """A working preferred member is not bypassed by the fallback."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return httpx2.Response(200, content=latest_content('v1'))

        server = ServerFile(
            make_server_url({'cn': {'preferred': 'http://a', 'fallback': 'http://b'}}, table=FakeMirrorTable()),
            client=make_aclient(handler))
        with logger.mock_capture_writer():
            name, _ = await server.probe()
        assert name == 'preferred'
        assert requests == ['http://a/latest.pack']

    @pytest.mark.trio
    async def test_groups_race(self):
        """The fastest group wins, the losing group is cancelled."""
        entered = trio.Event()

        async def handler(request):
            if request.url.host == 'slow':
                entered.set()
                # the losing group is cancelled by the probe instead of
                # running to its timeout, this sleep never finishes
                await trio.sleep_forever()
            return httpx2.Response(200, content=latest_content(request.url.host))

        server = ServerFile(
            make_server_url({'slow': 'http://slow', 'fast': 'http://fast'}, table=FakeMirrorTable()),
            client=make_aclient(handler))
        with logger.mock_capture_writer():
            name, info = await server.probe()
        assert name == 'fast'
        assert info.version == 'fast'
        # the slow group started its request and was cancelled
        assert entered.is_set()

    @pytest.mark.trio
    async def test_all_mirrors_failed(self):
        """Every candidate fails, the error carries the reasons."""
        def handler(request):
            return httpx2.Response(503)

        table = FakeMirrorTable()
        server = ServerFile(
            make_server_url({'a': 'http://a', 'b': 'http://x'}, table=table),
            client=make_aclient(handler))
        with logger.mock_capture_writer() as capture:
            with pytest.raises(AllMirrorsFailedError) as e:
                await server.get_latest_info()
        message = str(e.value)
        assert 'a' in message
        assert 'b' in message
        assert capture.backend.any_contains('is not usable')
        # nothing was recorded
        assert table.select_one(scope='') is None


class TestSelectionReuse:
    """The resolved mirror of an instance: the payload, the record, the reuse."""

    @pytest.mark.trio
    async def test_probe_payload_is_reused(self):
        """The probe request is the prefetch: latest.pack is fetched
        once, and a later call fetches again (nothing is cached)."""
        requests = []

        async def handler(request):
            if request.url.host == 'b':
                # the losing group is cancelled mid-flight, its request
                # never records a response
                await trio.sleep_forever()
            requests.append(str(request.url))
            return httpx2.Response(200, content=latest_content('a'))

        table = FakeMirrorTable()
        server = ServerFile(
            make_server_url({'a': 'http://a', 'b': 'http://b'}, table=table),
            client=make_aclient(handler))
        with logger.mock_capture_writer():
            info = await server.get_latest_info()
            assert info.version == 'a'
            assert table.select_one(scope='').name == 'a'
            # the probe request is the only request made for the winner
            assert requests == ['http://a/latest.pack']
            info = await server.get_latest_info()
            assert info.version == 'a'
            # the payload was not cached, the later call fetches again
            assert requests == ['http://a/latest.pack', 'http://a/latest.pack']

    @pytest.mark.trio
    async def test_recorded_mirror_is_used_directly(self):
        """A recorded mirror is used without a probe."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return httpx2.Response(200, content=latest_content('v1'))

        table = FakeMirrorTable()
        server_url = make_server_url({'a': 'http://a', 'b': 'http://b'}, table=table)
        table.seed(set_key=server_url.set_key, name='b')
        server = ServerFile(server_url, client=make_aclient(handler))
        with logger.mock_capture_writer():
            info = await server.get_latest_info()
        assert info.version == 'v1'
        assert requests == ['http://b/latest.pack']

    @pytest.mark.trio
    async def test_pack_request_resolves_first(self):
        """A flow whose first request is a pack file resolves (probes)
        first, the probe payload is dropped then."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return httpx2.Response(200, content=latest_content('v2'))

        server = ServerFile(
            make_server_url({'g': {'a': 'http://a', 'b': 'http://b'}}, table=FakeMirrorTable()),
            client=make_aclient(handler))
        with logger.mock_capture_writer():
            content = await server.get_file_content('v2', 0, 4)
        # the same group: the first member is probed, then the file of
        # the winner is fetched
        assert requests == ['http://a/latest.pack', 'http://a/v2/full.pack']
        assert content == latest_content('v2')[:4]


class TestRecordExpire:
    """The expiration of the gui.db record: an expired record is not
    a hint, the flow probes again and rewrites it.

    The record is what keeps a normal flow probe-free; its expiration
    makes the selection follow the network environment: a mirror that
    came back becomes the selection again (an upgrade from the
    fallback), a better one is picked up, about once a day per
    machine.
    """

    MIRRORS = {'g': {'preferred': 'http://preferred', 'fallback': 'http://fallback'}}

    @pytest.mark.trio
    async def test_expired_record_selects_the_preferred_mirror_again(self):
        """The record of a failed-over mirror expires: the probe tries
        the preferred member first again and upgrades to it."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return httpx2.Response(200, content=latest_content('v1'))

        table = FakeMirrorTable()
        server_url = make_server_url(self.MIRRORS, table=table)
        table.seed(set_key=server_url.set_key, name='fallback', expire=int(time()) - 1)
        server = ServerFile(server_url, client=make_aclient(handler))
        with logger.mock_capture_writer() as capture:
            info = await server.get_latest_info()
        assert info.version == 'v1'
        assert capture.backend.any_contains('is expired')
        # the expired record was ignored: the probe selected the
        # preferred member, its payload is the answer, no extra request
        assert requests == ['http://preferred/latest.pack']
        # the record was rewritten with the winner and a new expiration
        assert table.upserts == 1
        row = table.select_one(scope='')
        assert row.name == 'preferred'
        assert row.expire > int(time())

    @pytest.mark.trio
    async def test_expired_record_is_rewritten_when_the_same_mirror_wins(self):
        """The same mirror wins the probe again: the expired record is
        rewritten with a fresh expiration (it was read as '' and
        cached as ''), the same-name skip does not apply."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            if request.url.host == 'preferred':
                return httpx2.Response(500)
            return httpx2.Response(200, content=latest_content('v1'))

        table = FakeMirrorTable()
        server_url = make_server_url(self.MIRRORS, table=table)
        table.seed(set_key=server_url.set_key, name='fallback', expire=int(time()) - 1)
        server = ServerFile(server_url, client=make_aclient(handler))
        with logger.mock_capture_writer():
            info = await server.get_latest_info()
        assert info.version == 'v1'
        assert requests == ['http://preferred/latest.pack', 'http://fallback/latest.pack']
        assert table.upserts == 1
        row = table.select_one(scope='')
        assert row.name == 'fallback'
        assert row.expire > int(time())

    @pytest.mark.trio
    async def test_valid_record_keeps_the_flow_probe_free(self):
        """A record inside its lifetime is the hint: the flow goes
        straight to the recorded mirror, no probe request, no write."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return httpx2.Response(200, content=latest_content('v1'))

        table = FakeMirrorTable()
        server_url = make_server_url(self.MIRRORS, table=table)
        table.seed(set_key=server_url.set_key, name='fallback')
        server = ServerFile(server_url, client=make_aclient(handler))
        with logger.mock_capture_writer():
            info = await server.get_latest_info()
        assert info.version == 'v1'
        assert requests == ['http://fallback/latest.pack']
        assert table.upserts == 0


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
        return table, ServerFile(server_url, client=make_aclient(handler))

    @pytest.mark.trio
    @pytest.mark.parametrize('status', [500, 404])
    async def test_latest_failure_reselects(self, status):
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
            info = await server.get_latest_info()
        assert info.version == 'a'
        # the record is replaced by the winner
        assert table.select_one(scope='').name == 'a'
        # the record fetch, then the probe of the failed mirror and of
        # the fallback member
        assert requests == ['http://b/latest.pack', 'http://b/latest.pack', 'http://a/latest.pack']
        assert capture.backend.any_contains('probing again')

    @pytest.mark.trio
    async def test_latest_timeout_reselects(self):
        """A timeout of the recorded mirror triggers a probe: the user
        network environment may have changed. The data request is
        retried first (transport error, up to 3 attempts), the probe
        candidates stay single-shot."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            if request.url.host == 'b':
                raise httpx2.ReadTimeout('read timed out')
            return httpx2.Response(200, content=latest_content('a'))

        table, server = self.recorded_server(handler)
        with logger.mock_capture_writer():
            info = await server.get_latest_info()
        assert info.version == 'a'
        assert table.select_one(scope='').name == 'a'
        # the data request is attempted 3 times on b, then the probe
        # retries b once (single-shot) and falls to a
        assert requests == [
            'http://b/latest.pack',
            'http://b/latest.pack',
            'http://b/latest.pack',
            'http://b/latest.pack',
            'http://a/latest.pack',
        ]

    @pytest.mark.trio
    async def test_pack_request_reselects_and_retries(self):
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
            assert await server.get_update_pack('old', 'new') == b'update pack data'
        assert table.select_one(scope='').name == 'a'
        assert requests == [
            'http://b/new/from_old.pack',
            'http://b/latest.pack',
            'http://a/latest.pack',
            'http://a/new/from_old.pack',
        ]

    @pytest.mark.trio
    async def test_retry_failure_is_raised(self):
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
                await server.get_update_pack('old', 'new')
        assert requests == [
            'http://b/new/from_old.pack',
            'http://b/latest.pack',
            'http://a/latest.pack',
            'http://a/new/from_old.pack',
        ]

    @pytest.mark.trio
    async def test_404_is_not_reselected(self):
        """A 4xx of a pack file is the answer of the server, the mirror
        is kept and the request is not retried."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return httpx2.Response(404)

        table, server = self.recorded_server(handler)
        with logger.mock_capture_writer():
            with pytest.raises(httpx2.HTTPStatusError):
                await server.get_update_pack('old', 'new')
        assert requests == ['http://b/new/from_old.pack']
        assert table.select_one(scope='').name == 'b'

    @pytest.mark.trio
    @pytest.mark.parametrize('failure', [
        # no status code at all: dns lookup failure and ssl handshake
        # failure both raise httpx2.ConnectError, a timeout raises a
        # timeout exception; a blocked mirror never answers a status
        # code either way
        httpx2.ConnectError,
        httpx2.ConnectTimeout,
        httpx2.ReadTimeout,
    ])
    async def test_transport_failure_reselects(self, failure):
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
            assert await server.get_update_pack('old', 'new') == b'update pack data'
        assert table.select_one(scope='').name == 'a'
        # the data request is attempted 3 times on b, then the probe
        # retries b once (single-shot) and the request is retried on a
        assert requests == [
            'http://b/new/from_old.pack',
            'http://b/new/from_old.pack',
            'http://b/new/from_old.pack',
            'http://b/latest.pack',
            'http://a/latest.pack',
            'http://a/new/from_old.pack',
        ]

    @pytest.mark.trio
    async def test_total_failure_keeps_the_record(self):
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
                await server.get_latest_info()
        # the data request is attempted 3 times on b, then the probe
        # retries b once (single-shot) and a is not usable either
        assert requests == [
            'http://b/latest.pack',
            'http://b/latest.pack',
            'http://b/latest.pack',
            'http://b/latest.pack',
            'http://a/latest.pack',
        ]
        # nothing was saved, the old hint stays
        assert table.select_one(scope='').name == 'b'
        # the failed probe is recorded: a later attempt raises it again
        # instead of racing (the data request is retried again)
        with logger.mock_capture_writer():
            with pytest.raises(AllMirrorsFailedError):
                await server.get_latest_info()
        assert requests == [
            'http://b/latest.pack',
            'http://b/latest.pack',
            'http://b/latest.pack',
            'http://b/latest.pack',
            'http://a/latest.pack',
            'http://b/latest.pack',
            'http://b/latest.pack',
            'http://b/latest.pack',
        ]


class TestProbeOnce:
    """The probe of an instance runs at most once.

    A later access reuses the cached result, a recorded failure is
    re-raised and a failure of the selected mirror is raised as-is:
    probing again in the same flow is meaningless, the network
    environment does not change between its requests.
    """

    @pytest.mark.trio
    async def test_probe_result_is_cached(self):
        """A second access reuses the cached probe, no request again."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return httpx2.Response(200, content=latest_content('v1'))

        server = ServerFile(
            make_server_url({'g': {'a': 'http://a', 'b': 'http://b'}}, table=FakeMirrorTable()),
            client=make_aclient(handler))
        with logger.mock_capture_writer():
            first = await server.probe()
            second = await server.probe()
        assert first is second
        assert first[0] == 'a'
        assert requests == ['http://a/latest.pack']

    @pytest.mark.trio
    async def test_probe_failure_is_recorded(self):
        """A failed probe is recorded: a later access re-raises it
        without racing again."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return httpx2.Response(503)

        server = ServerFile(
            make_server_url({'g': {'a': 'http://a', 'b': 'http://b'}}, table=FakeMirrorTable()),
            client=make_aclient(handler))
        with logger.mock_capture_writer():
            with pytest.raises(AllMirrorsFailedError):
                await server.probe()
            tried = list(requests)
            with pytest.raises(AllMirrorsFailedError):
                await server.probe()
        assert tried == ['http://a/latest.pack', 'http://b/latest.pack']
        assert requests == tried

    @pytest.mark.trio
    async def test_failure_after_the_resolve_probe_is_raised(self):
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
            client=make_aclient(handler))
        with logger.mock_capture_writer():
            with pytest.raises(httpx2.HTTPStatusError):
                await server.get_file_content('v1', 0, 4)
        # the resolve probe selected a, the failed file request did not
        # probe again
        assert requests == ['http://a/latest.pack', 'http://a/v1/full.pack']

    @pytest.mark.trio
    async def test_late_failure_does_not_reselect(self):
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
        server = ServerFile(server_url, client=make_aclient(handler))
        with logger.mock_capture_writer():
            # the first failure reselects: the probe selects a, the
            # retry of the request fails
            with pytest.raises(httpx2.HTTPStatusError):
                await server.get_update_pack('old', 'new')
            tried = list(requests)
            # the probe already ran, the second failure is raised as-is
            with pytest.raises(httpx2.HTTPStatusError):
                await server.get_update_pack('old', 'new')
        assert tried == [
            'http://b/new/from_old.pack',
            'http://b/latest.pack',
            'http://a/latest.pack',
            'http://a/new/from_old.pack',
        ]
        assert requests == tried + ['http://a/new/from_old.pack']
        # the winner of the probe replaced the record
        assert table.select_one(scope='').name == 'a'


class TestRequestRetry:
    """A data request is retried on the same mirror: up to 3 attempts,
    only for transport errors, the probe candidates stay single-shot.
    Every failed attempt is logged.
    """

    @pytest.mark.trio
    async def test_transport_error_is_retried(self):
        """The first attempt raises a transport error, the retry works."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            if len(requests) == 1:
                raise httpx2.ReadTimeout('read timed out')
            return httpx2.Response(200, content=latest_content('v1'))

        server = ServerFile('http://only', client=make_aclient(handler))
        with logger.mock_capture_writer():
            info = await server.get_latest_info()
        assert info.version == 'v1'
        assert requests == ['http://only/latest.pack', 'http://only/latest.pack']

    @pytest.mark.trio
    async def test_attempts_are_three(self):
        """Two transport errors are retried: the third attempt is the
        last chance and succeeds."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            if len(requests) <= 2:
                raise httpx2.ReadTimeout('read timed out')
            return httpx2.Response(200, content=latest_content('v1'))

        server = ServerFile('http://only', client=make_aclient(handler))
        with logger.mock_capture_writer():
            info = await server.get_latest_info()
        assert info.version == 'v1'
        assert requests == ['http://only/latest.pack'] * 3

    @pytest.mark.trio
    async def test_retry_is_limited(self):
        """A persistent transport error is raised after 3 attempts."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            raise httpx2.ReadTimeout('read timed out')

        server = ServerFile('http://only', client=make_aclient(handler))
        with logger.mock_capture_writer():
            with pytest.raises(httpx2.ReadTimeout):
                await server.get_latest_info()
        assert requests == ['http://only/latest.pack'] * 3

    @pytest.mark.trio
    async def test_status_error_is_not_retried(self):
        """An http status error is the answer of the server, not a
        transport error: it is raised without a retry, and logged."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return httpx2.Response(500)

        server = ServerFile('http://only', client=make_aclient(handler))
        with logger.mock_capture_writer() as capture:
            with pytest.raises(httpx2.HTTPStatusError):
                await server.get_latest_info()
        assert requests == ['http://only/latest.pack']
        warnings = [log['m'] for log in capture.backend.logs if log['l'] == 'WARNING']
        assert len(warnings) == 1
        assert warnings[0].startswith(
            'Request to "http://only/latest.pack" failed (attempt 1/3): HTTPStatusError')

    @pytest.mark.trio
    async def test_probe_candidate_is_not_retried(self):
        """A probe candidate stays single-shot: a transport error makes
        it unusable without a retry."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            if request.url.host == 'a':
                raise httpx2.ReadTimeout('read timed out')
            return httpx2.Response(200, content=latest_content('v1'))

        server = ServerFile(
            make_server_url({'g': {'a': 'http://a', 'b': 'http://b'}}, table=FakeMirrorTable()),
            client=make_aclient(handler))
        with logger.mock_capture_writer():
            name, info = await server.probe()
        assert name == 'b'
        assert info.version == 'v1'
        assert requests == ['http://a/latest.pack', 'http://b/latest.pack']

    @pytest.mark.trio
    async def test_every_attempt_failure_is_logged(self):
        """Every failed attempt logs one warning, exhausted or not."""
        def handler(request):
            raise httpx2.ReadTimeout('read timed out')

        server = ServerFile('http://only', client=make_aclient(handler))
        with logger.mock_capture_writer() as capture:
            with pytest.raises(httpx2.ReadTimeout):
                await server.get_latest_info()
        warnings = [log['m'] for log in capture.backend.logs if log['l'] == 'WARNING']
        assert warnings == [
            'Request to "http://only/latest.pack" failed (attempt 1/3): ReadTimeout: read timed out',
            'Request to "http://only/latest.pack" failed (attempt 2/3): ReadTimeout: read timed out',
            'Request to "http://only/latest.pack" failed (attempt 3/3): ReadTimeout: read timed out',
        ]

    @pytest.mark.trio
    async def test_retried_failure_is_logged(self):
        """A transport error absorbed by the retry is still logged."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            if len(requests) == 1:
                raise httpx2.ReadTimeout('read timed out')
            return httpx2.Response(200, content=latest_content('v1'))

        server = ServerFile('http://only', client=make_aclient(handler))
        with logger.mock_capture_writer() as capture:
            info = await server.get_latest_info()
        assert info.version == 'v1'
        warnings = [log['m'] for log in capture.backend.logs if log['l'] == 'WARNING']
        assert warnings == [
            'Request to "http://only/latest.pack" failed (attempt 1/3): ReadTimeout: read timed out',
        ]

    @pytest.mark.trio
    async def test_probe_candidate_failure_is_logged(self):
        """A failed probe candidate logs its request failure, then the
        engine logs the rejection."""
        def handler(request):
            if request.url.host == 'a':
                raise httpx2.ReadTimeout('read timed out')
            return httpx2.Response(200, content=latest_content('v1'))

        server = ServerFile(
            make_server_url({'g': {'a': 'http://a', 'b': 'http://b'}}, table=FakeMirrorTable()),
            client=make_aclient(handler))
        with logger.mock_capture_writer() as capture:
            name, _ = await server.probe()
        assert name == 'b'
        warnings = [log['m'] for log in capture.backend.logs if log['l'] == 'WARNING']
        assert warnings == [
            'Request to "http://a/latest.pack" failed (attempt 1/1): ReadTimeout: read timed out',
            'Mirror "a" is not usable: ReadTimeout: read timed out',
        ]


class TestRequestTimeout:
    """latest.pack overrides the timeout of the http client with the
    short budget, the pack downloads use its default timeout."""

    @pytest.mark.trio
    async def test_latest_pack_timeout(self):
        """A latest.pack request uses PROBE_TIMEOUT on every phase."""
        timeouts = []

        def handler(request):
            timeouts.append(request.extensions['timeout'])
            return httpx2.Response(200, content=latest_content('v1'))

        server = ServerFile('http://only', client=make_aclient(handler))
        with logger.mock_capture_writer():
            info = await server.get_latest_info()
        assert info.version == 'v1'
        assert timeouts == [{
            'connect': AsyncHttpClient.PROBE_TIMEOUT,
            'read': AsyncHttpClient.PROBE_TIMEOUT,
            'write': AsyncHttpClient.PROBE_TIMEOUT,
            'pool': AsyncHttpClient.PROBE_TIMEOUT,
        }]

    @pytest.mark.trio
    async def test_download_timeout(self):
        """A pack download uses the default timeout of the http client:
        the connection budget stays short, the read/write budget is
        longer than latest.pack's (the range of one file may be large,
        a slow transfer must not be taken for a stall)."""
        timeouts = []

        def handler(request):
            timeouts.append(request.extensions['timeout'])
            return httpx2.Response(206, content=b'pack data')

        server = ServerFile('http://only', client=make_aclient(handler))
        with logger.mock_capture_writer():
            assert await server.get_file_content('v1', 0, 4) == b'pack data'
        assert timeouts == [{
            'connect': AsyncHttpClient.PROBE_TIMEOUT,
            'read': AsyncHttpClient.DEFAULT_TIMEOUT.read,
            'write': AsyncHttpClient.DEFAULT_TIMEOUT.write,
            'pool': AsyncHttpClient.PROBE_TIMEOUT,
        }]
        assert AsyncHttpClient.DEFAULT_TIMEOUT.read > AsyncHttpClient.PROBE_TIMEOUT
