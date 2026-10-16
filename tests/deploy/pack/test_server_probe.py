"""
Tests for the mirror selection of ServerFile: the record, the probe of the
first request, the optimistic reuse and the reselection of a failed mirror
(probe_base documents the flow, probe_async implements it).

Every mirror is an in-memory httpx2.MockTransport handler, the tests never
touch the network; the selection record is the FakeMirrorTable of conftest
(an in-memory table injected through ServerFile.RECORD_TABLE). Every test
performing a request is async (pytest-trio).
"""
import httpx2
import pytest
import trio

from alasio.deploy.httpclient import probe_async
from alasio.deploy.httpclient.probe_mirrors import AllMirrorsFailedError
from alasio.logger import logger
from tests.deploy_dev.pack.conftest import FakeMirrorTable, make_server_file


def make_aclient(handler):
    """A httpx2.AsyncClient with a MockTransport handler."""
    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


def latest_response(version='v1'):
    """A 200 response of latest.pack of a version."""
    return httpx2.Response(200, content=version.encode() + b'\x00' * 20)


@pytest.fixture
def fake_clock(monkeypatch):
    """
    Freeze the clock the probes measure their candidates with.

    A probe logs the measured time of the winning candidate ('Mirror "a"
    responded in 0ms'): the time of the machine differs on every run and
    is not assertable, the frozen one is always 0ms.

    Args:
        monkeypatch (MonkeyPatch): The monkeypatch fixture of pytest
    """
    monkeypatch.setattr(probe_async, 'perf_counter', lambda: 0.0)


class TestRecord:
    """A usable record serves the flow, no probe runs."""

    @pytest.mark.trio
    async def test_record_hit_serves_the_request(self):
        """The recorded mirror is requested directly, zero probe requests."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return latest_response()

        table = FakeMirrorTable()
        server = make_server_file({'g': {'a': 'http://a', 'b': 'http://b'}}, table=table,
                                  client=make_aclient(handler))
        table.seed(scene='repo', name='', digest=server.mirrors.digest, result='b')
        with logger.mock_capture_writer() as capture:
            info = await server.get_latest_info()
        assert info.version == 'v1'
        # the record serves the flow: no probe, no log
        assert capture.concat() == ''
        assert requests == ['http://b/latest.pack']
        assert table.upserts == 0

    @pytest.mark.trio
    async def test_single_mirror_never_touches_the_record(self):
        """A single mirror set has nothing to select: no read, no write, no
        probe."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return latest_response()

        table = FakeMirrorTable()
        server = make_server_file('http://only', table=table, client=make_aclient(handler))
        with logger.mock_capture_writer() as capture:
            assert (await server.get_latest_info()).version == 'v1'
        # a single mirror set has nothing to select and nothing to probe: no log
        assert capture.concat() == ''
        assert requests == ['http://only/latest.pack']
        assert (table.selects, table.upserts) == (0, 0)


class TestProbe:
    """Without a record the first request probes, then everything is
    reused (optimistic reuse)."""

    @pytest.mark.trio
    async def test_latest_pack_probe_is_the_request(self, fake_clock):
        """latest.pack is a small path: the probe is the request itself,
        the payload is reused, the winner is recorded."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return latest_response()

        table = FakeMirrorTable()
        server = make_server_file({'g': {'a': 'http://a', 'b': 'http://b'}}, table=table,
                                  client=make_aclient(handler))
        with logger.mock_capture_writer() as capture:
            info = await server.get_latest_info()
        assert info.version == 'v1'
        # one request: the probe of 'a' (the first member of the group)
        assert requests == ['http://a/latest.pack']
        assert capture.concat() == """\
INFO | Probing `repo` for "/latest.pack": a, b
INFO | Mirror "a" responded in 0ms
INFO | [Mirror `repo`] a
"""
        assert table.select_one(scene='repo', name='').result == 'a'

    @pytest.mark.trio
    async def test_requests_are_reused_after_the_probe(self, fake_clock):
        """After the first request the mirror is resolved: every later request
        goes to it, no probe again."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            if request.url.path.endswith('/full.pack'):
                return httpx2.Response(206, content=b'\x81')
            return latest_response()

        server = make_server_file({'g': {'a': 'http://a', 'b': 'http://b'}},
                                  table=FakeMirrorTable(), client=make_aclient(handler))
        with logger.mock_capture_writer() as capture:
            await server.get_latest_info()
            # the index pack request: HEAD (the probe of the path) + the GET
            with pytest.raises(Exception):
                await server.get_index_pack('v1')
        # the probe of latest.pack happened once and resolved the mirror a,
        # which served the HEAD probe and the GET of the full pack target; the
        # broken content of a moved the flow to b for the retry
        assert capture.concat() == """\
INFO | Probing `repo` for "/latest.pack": a, b
INFO | Mirror "a" responded in 0ms
INFO | [Mirror `repo`] a
INFO | Probing `repo` for "/v1/full.pack": a, b
INFO | Mirror "b" responded in 0ms
INFO | [Mirror `repo`] b
"""
        assert requests.count('http://a/latest.pack') == 1
        assert requests[1] == 'http://a/v1/full.pack'
        assert 'http://b/v1/full.pack' in requests

    @pytest.mark.trio
    async def test_full_pack_target_is_head_probed(self, fake_clock):
        """A whole target is probed with a HEAD before the real request."""
        requests = []

        def handler(request):
            requests.append((request.method, str(request.url)))
            if request.method == 'HEAD':
                return httpx2.Response(200)
            return httpx2.Response(200, content=b'update pack data')

        server = make_server_file({'g': {'a': 'http://a', 'b': 'http://b'}},
                                  table=FakeMirrorTable(), client=make_aclient(handler))
        with logger.mock_capture_writer() as capture:
            with pytest.raises(Exception):
                # the update pack of the test is not a real pack: the point is
                # the request sequence (HEAD probe, then the GET)
                await server.get_update_pack('old', 'new')
        assert requests == [
            ('HEAD', 'http://a/new/from_old.pack'),
            ('GET', 'http://a/new/from_old.pack'),
        ]
        assert capture.concat() == """\
INFO | Probing `repo` for "/new/from_old.pack": a, b
INFO | Mirror "a" responded in 0ms
INFO | [Mirror `repo`] a
"""

    @pytest.mark.trio
    async def test_head_unsupported_falls_back_to_a_range_get(self, fake_clock):
        """A server that does not support HEAD (405) is probed with a one-byte
        range GET: its status is the availability, the body is never read."""
        requests = []

        def handler(request):
            requests.append((request.method, request.url.path, request.headers.get('Range', '')))
            if request.method == 'HEAD':
                return httpx2.Response(405)
            return httpx2.Response(206, content=b'\x81')

        server = make_server_file({'g': {'a': 'http://a', 'b': 'http://b'}}, table=FakeMirrorTable(),
                                  client=make_aclient(handler))
        with logger.mock_capture_writer() as capture:
            with pytest.raises(Exception):
                # the update pack of the test is not a real pack: the point is
                # the request sequence (the HEAD, its fallback, the real request)
                await server.get_update_pack('old', 'new')
        assert requests == [
            ('HEAD', '/new/from_old.pack', ''),
            ('GET', '/new/from_old.pack', 'bytes=0-0'),
            ('GET', '/new/from_old.pack', ''),
        ]
        assert capture.concat() == """\
INFO | Probing `repo` for "/new/from_old.pack": a, b
WARNING | Request to "http://a/new/from_old.pack" failed (attempt 1/3): HTTPStatusError: Client error '405 Method Not Allowed' for url 'http://a/new/from_old.pack'
INFO | Mirror "a" responded in 0ms
INFO | [Mirror `repo`] a
"""

    @pytest.mark.trio
    async def test_head_unsupported_and_fallback_failed_moves_on(self, fake_clock):
        """A fallback probe that fails makes the candidate unusable: the race
        moves on to the next member."""
        requests = []

        def handler(request):
            requests.append((request.method, request.url.host))
            if request.url.host == 'a':
                # HEAD is not supported and the fallback GET fails: the
                # candidate cannot serve the target
                return httpx2.Response(405 if request.method == 'HEAD' else 404)
            if request.method == 'HEAD':
                return httpx2.Response(200)
            return httpx2.Response(200, content=b'update pack data')

        server = make_server_file({'g': {'a': 'http://a', 'b': 'http://b'}}, table=FakeMirrorTable(),
                                  client=make_aclient(handler))
        with logger.mock_capture_writer() as capture:
            with pytest.raises(Exception):
                await server.get_update_pack('old', 'new')
        assert requests == [
            ('HEAD', 'a'),
            ('GET', 'a'),
            ('HEAD', 'b'),
            ('GET', 'b'),
        ]
        assert capture.concat() == """\
INFO | Probing `repo` for "/new/from_old.pack": a, b
WARNING | Request to "http://a/new/from_old.pack" failed (attempt 1/3): HTTPStatusError: Client error '405 Method Not Allowed' for url 'http://a/new/from_old.pack'
WARNING | Request to "http://a/new/from_old.pack" failed (attempt 1/3): HTTPStatusError: Client error '404 Not Found' for url 'http://a/new/from_old.pack'
WARNING | Mirror "a" is not usable: HTTPStatusError: Client error '404 Not Found' for url 'http://a/new/from_old.pack'
INFO | Mirror "b" responded in 0ms
INFO | [Mirror `repo`] b
"""

    @pytest.mark.trio
    async def test_probe_reads_the_record_under_the_lock(self, fake_clock):
        """Two flows of the same (scene, name) probe once: the second one
        reuses the record written by the first (the probe lock)."""
        requests = []
        entered = trio.Event()

        async def handler(request):
            requests.append(str(request.url))
            if not entered.is_set():
                entered.set()
                await trio.sleep(0.05)
            return latest_response()

        table = FakeMirrorTable()
        mirrors = {'g': {'a': 'http://a', 'b': 'http://b'}}
        first = make_server_file(mirrors, table=table, client=make_aclient(handler))
        second = make_server_file(mirrors, table=table, client=make_aclient(handler))
        with logger.mock_capture_writer() as capture:
            async with trio.open_nursery() as nursery:
                nursery.start_soon(first.get_latest_info)
                await entered.wait()
                nursery.start_soon(second.get_latest_info)
        # one probe only (one record write): the second flow re-read the
        # record inside the lock and made its own request
        assert capture.concat() == """\
INFO | Probing `repo` for "/latest.pack": a, b
INFO | Mirror "a" responded in 0ms
INFO | [Mirror `repo`] a
"""
        assert table.upserts == 1
        assert requests == ['http://a/latest.pack', 'http://a/latest.pack']
        assert table.select_one(scene='repo', name='').result == 'a'


class TestReselection:
    """A failed request probes the target once and retries once on the
    winner; with no winner the original failure is raised (the caller keeps
    its own fallback)."""

    MIRRORS = {'g': {'a': 'http://a', 'b': 'http://b'}}

    @pytest.mark.trio
    async def test_transport_failure_reselects_and_retries(self, fake_clock):
        """A 500 of the recorded mirror: the target is probed again (the
        failed mirror stays a candidate) and the request is retried once."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            if request.url.host == 'a':
                return httpx2.Response(500)
            return latest_response('b')

        table = FakeMirrorTable()
        server = make_server_file(self.MIRRORS, table=table, client=make_aclient(handler))
        table.seed(scene='repo', name='', digest=server.mirrors.digest, result='a')
        with logger.mock_capture_writer() as capture:
            info = await server.get_latest_info()
        assert info.version == 'b'
        assert requests == [
            'http://a/latest.pack',  # the recorded mirror fails
            'http://a/latest.pack',  # the probe retries it (may be a blip)
            'http://b/latest.pack',  # the winner, its payload is the retry
        ]
        assert capture.concat() == """\
WARNING | Request to "http://a/latest.pack" failed (attempt 1/3): HTTPStatusError: Server error '500 Internal Server Error' for url 'http://a/latest.pack'
INFO | Probing `repo` for "/latest.pack": a, b
WARNING | Request to "http://a/latest.pack" failed (attempt 1/3): HTTPStatusError: Server error '500 Internal Server Error' for url 'http://a/latest.pack'
WARNING | Mirror "a" is not usable: HTTPStatusError: Server error '500 Internal Server Error' for url 'http://a/latest.pack'
INFO | Mirror "b" responded in 0ms
INFO | [Mirror `repo`] b
"""
        assert table.select_one(scene='repo', name='').result == 'b'

    @pytest.mark.trio
    async def test_exhausted_timeouts_reselect_and_retry(self, fake_clock, autojump_clock):
        """Every attempt of the request times out: the exhausted transport
        error makes the flow probe the target again, and the winner serves
        the retry."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            if request.url.host == 'a':
                raise httpx2.ReadTimeout('read timed out')
            return latest_response('b')

        table = FakeMirrorTable()
        server = make_server_file(self.MIRRORS, table=table, client=make_aclient(handler))
        table.seed(scene='repo', name='', digest=server.mirrors.digest, result='a')
        with logger.mock_capture_writer() as capture:
            info = await server.get_latest_info()
        assert info.version == 'b'
        assert requests == [
            'http://a/latest.pack',  # attempt 1 of the request
            'http://a/latest.pack',  # attempt 2
            'http://a/latest.pack',  # attempt 3: the request is exhausted
            'http://a/latest.pack',  # the probe of the path: a timeout may be a blip
            'http://a/latest.pack',  # attempt 2 of the probe
            'http://a/latest.pack',  # attempt 3: the candidate is unusable
            'http://b/latest.pack',  # the winner, its payload is the retry
        ]
        assert table.select_one(scene='repo', name='').result == 'b'
        assert capture.concat() == """\
WARNING | Request to "http://a/latest.pack" failed (attempt 1/3): ReadTimeout: read timed out
WARNING | Request to "http://a/latest.pack" failed (attempt 2/3): ReadTimeout: read timed out
WARNING | Request to "http://a/latest.pack" failed (attempt 3/3): ReadTimeout: read timed out
INFO | Probing `repo` for "/latest.pack": a, b
WARNING | Request to "http://a/latest.pack" failed (attempt 1/3): ReadTimeout: read timed out
WARNING | Request to "http://a/latest.pack" failed (attempt 2/3): ReadTimeout: read timed out
WARNING | Request to "http://a/latest.pack" failed (attempt 3/3): ReadTimeout: read timed out
WARNING | Mirror "a" is not usable: ReadTimeout: read timed out
INFO | Mirror "b" responded in 0ms
INFO | [Mirror `repo`] b
"""

    @pytest.mark.trio
    async def test_404_is_raised_when_no_mirror_serves_it(self):
        """A 404 everywhere is the answer of the server: the failure is
        raised as-is (DeployJob.update() falls back to a rebuild on it), the
        record keeps the name."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return httpx2.Response(404)

        table = FakeMirrorTable()
        server = make_server_file(self.MIRRORS, table=table, client=make_aclient(handler))
        table.seed(scene='repo', name='', digest=server.mirrors.digest, result='a')
        with logger.mock_capture_writer() as capture:
            with pytest.raises(httpx2.HTTPStatusError):
                await server.get_update_pack('old', 'new')
        assert requests == ['http://a/new/from_old.pack', 'http://a/new/from_old.pack', 'http://b/new/from_old.pack']
        assert capture.concat() == """\
WARNING | Request to "http://a/new/from_old.pack" failed (attempt 1/3): HTTPStatusError: Client error '404 Not Found' for url 'http://a/new/from_old.pack'
INFO | Probing `repo` for "/new/from_old.pack": a, b
WARNING | Request to "http://a/new/from_old.pack" failed (attempt 1/3): HTTPStatusError: Client error '404 Not Found' for url 'http://a/new/from_old.pack'
WARNING | Mirror "a" is not usable: HTTPStatusError: Client error '404 Not Found' for url 'http://a/new/from_old.pack'
WARNING | Request to "http://b/new/from_old.pack" failed (attempt 1/3): HTTPStatusError: Client error '404 Not Found' for url 'http://b/new/from_old.pack'
WARNING | Mirror "b" is not usable: HTTPStatusError: Client error '404 Not Found' for url 'http://b/new/from_old.pack'
ERROR | Failed to probe `repo` for "/new/from_old.pack": no mirror is usable
"""
        assert table.select_one(scene='repo', name='').result == 'a'

    @pytest.mark.trio
    async def test_range_unsupported_reselects(self, fake_clock):
        """A mirror that ignores a range request is not usable: the content
        failure excludes it from the re-probe, another mirror serves the
        index pack."""
        from tests.deploy_dev.pack.conftest import WEBSITE_FULL_PACK, WEBSITE_INDEX_PACK

        requests = []

        def handler(request):
            requests.append(str(request.url))
            if request.url.host == 'a':
                # the range is ignored: the whole pack as a 200
                return httpx2.Response(200, content=WEBSITE_FULL_PACK)
            range_header = request.headers.get('Range', '')
            if not range_header:
                # the HEAD probe of the path
                return httpx2.Response(200)
            start, _, end = range_header.partition('=')[2].partition('-')
            return httpx2.Response(206, content=WEBSITE_FULL_PACK[int(start):int(end) + 1])

        table = FakeMirrorTable()
        server = make_server_file(self.MIRRORS, table=table, client=make_aclient(handler))
        table.seed(scene='repo', name='', digest=server.mirrors.digest, result='a')
        with logger.mock_capture_writer() as capture:
            decoder = await server.get_index_pack('v1')
        assert decoder.data.tobytes() == WEBSITE_INDEX_PACK
        # a: the header range ignored; the probe of the path skips a (the
        # content failure) and takes b; the index pack is fetched from b
        assert requests[0] == 'http://a/v1/full.pack'
        assert 'http://b/v1/full.pack' in requests
        assert capture.concat() == """\
WARNING | Request to "http://a/v1/full.pack" failed (attempt 1/3): RangeUnsupportedError: The mirror does not support range requests: http://a/v1/full.pack
INFO | Probing `repo` for "/v1/full.pack": a, b
INFO | Mirror "b" responded in 0ms
INFO | [Mirror `repo`] b
"""
        assert table.select_one(scene='repo', name='').result == 'b'

    @pytest.mark.trio
    async def test_all_mirrors_failed(self):
        """No mirror of the set is usable for the first request: the
        aggregate error carries the reasons, nothing is recorded."""
        def handler(request):
            return httpx2.Response(503)

        table = FakeMirrorTable()
        server = make_server_file(self.MIRRORS, table=table, client=make_aclient(handler))
        with logger.mock_capture_writer() as capture:
            with pytest.raises(AllMirrorsFailedError) as e:
                await server.get_latest_info()
        message = str(e.value)
        assert 'a' in message
        assert 'b' in message
        assert capture.concat() == """\
INFO | Probing `repo` for "/latest.pack": a, b
WARNING | Request to "http://a/latest.pack" failed (attempt 1/3): HTTPStatusError: Server error '503 Service Unavailable' for url 'http://a/latest.pack'
WARNING | Mirror "a" is not usable: HTTPStatusError: Server error '503 Service Unavailable' for url 'http://a/latest.pack'
WARNING | Request to "http://b/latest.pack" failed (attempt 1/3): HTTPStatusError: Server error '503 Service Unavailable' for url 'http://b/latest.pack'
WARNING | Mirror "b" is not usable: HTTPStatusError: Server error '503 Service Unavailable' for url 'http://b/latest.pack'
ERROR | Failed to probe `repo` for "/latest.pack": no mirror is usable
"""
        # nothing was recorded: a total failure usually means the machine is
        # offline, not that a mirror is wrong
        assert table.select_one(scene='repo', name='') is None

    @pytest.mark.trio
    async def test_record_expiry_probes_again(self, fake_clock):
        """An expired record is no hint: the flow probes again and rewrites
        the record."""
        # the record is read twice (before the probe lock and under it), so
        # its expiration is warned about twice
        from time import time
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return latest_response()

        table = FakeMirrorTable()
        server = make_server_file(self.MIRRORS, table=table, client=make_aclient(handler))
        table.seed(scene='repo', name='', digest=server.mirrors.digest, result='b',
                   expire=int(time()) - 1)
        with logger.mock_capture_writer() as capture:
            info = await server.get_latest_info()
        assert info.version == 'v1'
        assert capture.concat() == """\
INFO | Mirror record of `repo` is expired, selecting again: b
INFO | Mirror record of `repo` is expired, selecting again: b
INFO | Probing `repo` for "/latest.pack": a, b
INFO | Mirror "a" responded in 0ms
INFO | [Mirror `repo`] a
"""
        assert requests == ['http://a/latest.pack']
        row = table.select_one(scene='repo', name='')
        assert row.result == 'a'
        assert row.expire > int(time())

    @pytest.mark.trio
    async def test_scene_isolates_the_mods(self, fake_clock):
        """The record of one scene/name is never read by another one."""
        table = FakeMirrorTable()
        mirrors = {'g': {'a': 'http://a', 'b': 'http://b'}}

        def handler(request):
            return latest_response(request.url.host)

        first = make_server_file(mirrors, name='a', table=table, client=make_aclient(handler))
        second = make_server_file(mirrors, name='b', table=table, client=make_aclient(handler))
        with logger.mock_capture_writer() as capture:
            assert (await first.get_latest_info()).version == 'a'
        assert capture.concat() == """\
INFO | Probing `repo/a` for "/latest.pack": a, b
INFO | Mirror "a" responded in 0ms
INFO | [Mirror `repo/a`] a
"""
        # the record of 'a' does not serve 'b': it probes its own target
        assert table.select_one(scene='repo', name='a').result == 'a'
        assert table.select_one(scene='repo', name='b') is None
        with logger.mock_capture_writer() as capture:
            assert (await second.get_latest_info()).version == 'a'
        assert capture.concat() == """\
INFO | Probing `repo/b` for "/latest.pack": a, b
INFO | Mirror "a" responded in 0ms
INFO | [Mirror `repo/b`] a
"""
        assert table.select_one(scene='repo', name='b').result == 'a'
