"""
Tests for the blocking flavour of the probes: the thread pool race (SyncProbe)
and its HTTP twin (SyncHttpProbe), the counterparts of test_probe_async.py.

The engine is transport agnostic, the tests drive it with a stub subclass of
canned results and errors, no http is involved; the HTTP section serves the
requests with an httpx2.MockTransport handler. The selection record is the
FakeMirrorTable of the pack conftest (an in-memory table), injected through
ProbeBase.RECORD_TABLE. The flavour has no async test of its own: unlike the
async race, the losing probes of a group are not cancelled (a blocking request
cannot be interrupted from outside), they finish in their own time on their
pool thread and their results are dropped, so a slow candidate of a test is a
short sleep rather than a sleep forever.
"""
import threading
import time

import httpx2
import pytest

from alasio.deploy.httpclient.probe_mirrors import AllMirrorsFailedError, Mirrors, RangeUnsupportedError
from alasio.deploy.httpclient.probe_sync import SyncHttpProbe, SyncProbe, probe_lock
from alasio.logger import logger
from tests.deploy_dev.pack.conftest import FakeMirrorTable

# the path of the tests: the stub does not care what it means, the engine keys
# its bookkeeping by it
PATH = '/latest.pack'


def make_sclient(handler):
    """A httpx2.Client with a MockTransport handler."""
    return httpx2.Client(transport=httpx2.MockTransport(handler))


class StubProbe(SyncProbe):
    """
    A probe of canned candidates: probe_function() returns the payload
    registered for a name, or raises the registered exception; request() the
    same for the plain request of a flow.
    """

    def __init__(self, mirrors, name='', results=None, requests=None, table=None):
        """
        Args:
            mirrors (dict | Mirrors): The mirror structure
            name (str): Identity of the set. Defaults to ''
            results (dict[str, Any], optional): {name: payload | Exception} of
                probe_function(); None is a probe that fetched nothing (a HEAD
                probe of the HTTP flavours)
            requests (dict[str, Any], optional): {name: payload | Exception} of
                request()
            table (AlasioTable-like, optional): The selection record table, None
                for the real gui.db one (the tests inject FakeMirrorTable)
        """
        super().__init__(mirrors, name=name)
        if table is not None:
            self.RECORD_TABLE = table
        self.results = results or {}
        self.requests = requests or {}
        # (name, url, path) of every probe_function call, in call order
        self.probed = []
        # (name, url, path) of every request call, in call order
        self.called = []

    def probe_function(self, name, url, path):
        self.probed.append((name, url, path))
        result = self.results.get(name, None)
        if isinstance(result, Exception):
            raise result
        return result

    def request(self, name, url, path):
        self.called.append((name, url, path))
        result = self.requests.get(name, None)
        if isinstance(result, Exception):
            raise result
        return result


class TestRace:
    """The race: groups run in parallel on the pool, members fall back in
    order."""

    def test_member_order(self):
        """The members of a group are probed in the declared order, a
        failed member falls through to the next one."""
        probe = StubProbe(
            {'cn': {'down': 'http://down', 'up': 'http://up'}},
            results={'down': ValueError('boom'), 'up': 'UP'}, table=FakeMirrorTable())
        with logger.mock_capture_writer():
            name, payload = probe.run(PATH)
        assert (name, payload) == ('up', 'UP')
        assert probe.probed == [
            ('down', 'http://down', PATH), ('up', 'http://up', PATH)]

    def test_preferred_member_wins(self):
        """A usable preferred member is not bypassed by the fallback."""
        probe = StubProbe(
            {'cn': {'preferred': 'http://a', 'fallback': 'http://b'}},
            results={'preferred': 'P', 'fallback': 'F'}, table=FakeMirrorTable())
        with logger.mock_capture_writer():
            name, payload = probe.run(PATH)
        assert (name, payload) == ('preferred', 'P')
        assert probe.probed == [('preferred', 'http://a', PATH)]

    def test_groups_race(self):
        """Groups run in parallel, the fastest one wins. The losing group is
        not cancelled (a blocking request cannot be interrupted from outside):
        it finishes in its own time and its result is dropped."""
        class SlowProbe(StubProbe):
            def probe_function(self, name, url, path):
                if name == 'slow':
                    # the loser keeps running on its pool thread, the probe
                    # does not wait for it
                    time.sleep(0.3)
                return super().probe_function(name, url, path)

        probe = SlowProbe(
            {'slow': {'slow': 'http://slow'}, 'fast': {'fast': 'http://fast'}},
            results={'slow': 'S', 'fast': 'F'}, table=FakeMirrorTable())
        with logger.mock_capture_writer():
            start = time.monotonic()
            name, payload = probe.run(PATH)
            elapsed = time.monotonic() - start
        assert (name, payload) == ('fast', 'F')
        # the slow group (0.3s) did not hold the race
        assert elapsed < 0.25

    def test_all_failed(self):
        """Every candidate fails: the error carries the reasons."""
        probe = StubProbe(
            {'a': {'a': 'http://a'}, 'b': {'b': 'http://b'}},
            results={'a': ValueError('down'), 'b': KeyError('gone')}, table=FakeMirrorTable())
        with logger.mock_capture_writer() as capture:
            with pytest.raises(AllMirrorsFailedError) as e:
                probe.run(PATH)
        message = str(e.value)
        assert 'a: ValueError: down' in message
        assert "b: KeyError: 'gone'" in message
        assert capture.backend.any_contains('is not usable')

    def test_mirrors_input(self):
        """A Mirrors instance is accepted as the probe input."""
        mirrors = Mirrors.from_input({'g': {'a': 'http://a'}})
        probe = StubProbe(mirrors, results={'a': 'A'}, table=FakeMirrorTable())
        with logger.mock_capture_writer():
            name, payload = probe.run(PATH)
        assert (name, payload) == ('a', 'A')

    def test_the_payload_of_the_winner_is_returned(self):
        """The payload of the winner is returned as-is."""
        token = object()
        probe = StubProbe({'g': {'a': 'http://a'}}, results={'a': token}, table=FakeMirrorTable())
        with logger.mock_capture_writer():
            name, payload = probe.run(PATH)
        assert name == 'a'
        assert payload is token

    def test_probe_function_not_implemented(self):
        """The base probe_function makes every candidate unusable."""
        probe = SyncProbe({'g': {'a': 'http://a'}}, name='')
        probe.RECORD_TABLE = FakeMirrorTable()
        with logger.mock_capture_writer():
            with pytest.raises(AllMirrorsFailedError) as e:
                probe.run(PATH)
        assert 'a: NotImplementedError' in str(e.value)


class TestResolve:
    """The record, the probe of the first request, the payload."""

    MIRRORS = {'g': {'a': 'http://a', 'b': 'http://b'}}

    def test_single_mirror_skips_the_record_and_the_probe(self):
        """A single mirror set has nothing to select: no record read, no
        probe, the only candidate serves the request."""
        table = FakeMirrorTable()
        probe = StubProbe('http://only', requests={'default': 'PAY'}, table=table)
        with logger.mock_capture_writer():
            assert probe.fetch(PATH) == 'PAY'
        assert probe.probed == []
        # no read, no write: a single mirror set never touches the table
        assert (table.selects, table.upserts) == (0, 0)

    def test_record_hit_is_used_without_a_probe(self):
        """A usable record serves the requests, no probe runs."""
        table = FakeMirrorTable()
        probe = StubProbe(self.MIRRORS, requests={'b': 'PAY'}, table=table)
        table.seed(scene='probe', name='', digest=probe.mirrors.digest, result='b')
        with logger.mock_capture_writer():
            assert probe.fetch(PATH) == 'PAY'
        assert probe.probed == []
        assert probe.called == [('b', 'http://b', PATH)]
        # the record is an optimization: it is only read, never rewritten
        assert table.upserts == 0

    def test_probe_of_the_first_request_records_the_winner(self):
        """Without a record the first request probes: the winner is recorded
        and every later request of the instance reuses it (no probe again)."""
        table = FakeMirrorTable()
        probe = StubProbe(self.MIRRORS, results={'a': None}, requests={'a': 'PAY'}, table=table)
        with logger.mock_capture_writer():
            assert probe.fetch(PATH) == 'PAY'
            # optimistic reuse: a second request goes to the same mirror
            assert probe.fetch(PATH) == 'PAY'
        assert probe.probed == [('a', 'http://a', PATH)]
        assert probe.called == [('a', 'http://a', PATH), ('a', 'http://a', PATH)]
        row = table.select_one(scene='probe', name='')
        assert row.result == 'a'
        assert row.digest == probe.mirrors.digest

    def test_the_payload_of_the_probe_is_the_answer_of_the_flow(self):
        """A probe that was the request itself answers the flow: its payload is
        returned as-is and the work of the flow does not run."""
        table = FakeMirrorTable()
        probe = StubProbe(self.MIRRORS, results={'a': 'BODY'}, table=table)
        seen = []

        def work(name, url):
            seen.append(name)
            return 'WORK'

        with logger.mock_capture_writer():
            assert probe.fetch(PATH, work) == 'BODY'
        assert probe.probed == [('a', 'http://a', PATH)]
        assert probe.called == []
        assert seen == []

    def test_a_probe_that_fetched_nothing_runs_the_work(self):
        """A probe that only proved the availability (a HEAD) hands nothing
        over: the work of the flow runs and fetches the path itself."""
        table = FakeMirrorTable()
        probe = StubProbe(self.MIRRORS, results={'a': None}, table=table)
        seen = []

        def work(name, url):
            seen.append(name)
            return 'WORK'

        with logger.mock_capture_writer():
            assert probe.fetch(PATH, work) == 'WORK'
        assert probe.probed == [('a', 'http://a', PATH)]
        assert seen == ['a']

    def test_a_flow_without_a_work_takes_the_payload_as_its_answer(self):
        """A flow that passes no work of its own takes the payload of the probe
        as its answer: the plain request of the flavour does not repeat it."""
        table = FakeMirrorTable()
        probe = StubProbe(self.MIRRORS, results={'a': 'BODY'}, table=table)
        with logger.mock_capture_writer():
            assert probe.fetch(PATH) == 'BODY'
        assert probe.probed == [('a', 'http://a', PATH)]
        assert probe.called == []

    def test_probe_lock_returns_the_lock_of_the_key(self):
        """probe_lock returns the lock itself, not acquired: one lock per key,
        handed to the caller that holds it with `with`."""
        lock = probe_lock('probe', 'a')
        assert probe_lock('probe', 'a') is lock
        assert probe_lock('probe', 'b') is not lock
        assert not lock.locked()

    def test_probe_lock_is_per_key(self):
        """The lock of another (scene, name) does not block the probe."""
        with probe_lock('probe', 'a'):
            other = probe_lock('probe', 'b')
            assert other.acquire(timeout=0.1)
            other.release()

    def test_probe_lock_serializes_the_flows_of_one_key(self):
        """Two flows of the process with the same (scene, name) probe once: the
        second one waits for the lock and reuses the written record."""
        table = FakeMirrorTable()
        entered = threading.Event()

        class SlowProbe(StubProbe):
            def probe_function(self, name, url, path):
                entered.set()
                time.sleep(0.05)
                return super().probe_function(name, url, path)

        first = SlowProbe(self.MIRRORS, results={'a': 'A'}, requests={'a': 'PAY'}, table=table)
        second = SlowProbe(self.MIRRORS, results={'a': 'A'}, requests={'a': 'PAY'}, table=table)
        with logger.mock_capture_writer():
            thread = threading.Thread(target=first.fetch, args=(PATH,))
            thread.start()
            assert entered.wait(2)
            # the second flow of the same key waits for the lock of the first
            # and re-reads the record the first one wrote: its own request runs
            # on the recorded mirror
            assert second.fetch(PATH) == 'PAY'
            thread.join()
        # exactly one probe ran, the second flow re-read the record
        assert len(first.probed) + len(second.probed) == 1
        assert first.probed == [('a', 'http://a', PATH)]
        assert second.called == [('a', 'http://a', PATH)]


class TestFailure:
    """A failed request re-probes the path once and retries once."""

    MIRRORS = {'g': {'a': 'http://a', 'b': 'http://b'}}

    def test_content_failure_excludes_the_mirror(self):
        """A content failure (the domain's own: the bytes are not usable) does
        not get better by asking the same mirror again: the mirror that failed
        is excluded from the re-probe, another candidate wins and the retry
        succeeds."""
        table = FakeMirrorTable()
        probe = StubProbe(self.MIRRORS, table=table)
        table.seed(scene='probe', name='', digest=probe.mirrors.digest, result='a')
        probe.results = {'a': None, 'b': None}
        probe.requests = {'a': RangeUnsupportedError('no ranges'), 'b': 'PAY'}
        with logger.mock_capture_writer():
            assert probe.fetch(PATH) == 'PAY'
        # the failed mirror is not probed again, the winner is the other one
        assert probe.probed == [('b', 'http://b', PATH)]
        assert probe.called == [('a', 'http://a', PATH), ('b', 'http://b', PATH)]
        assert table.select_one(scene='probe', name='').result == 'b'

    def test_transport_failure_keeps_the_mirror_a_candidate(self):
        """A transport failure may be a blip: the mirror stays a candidate and
        can win the re-probe again (the retry on it is the new attempt)."""
        table = FakeMirrorTable()
        probe = StubProbe(self.MIRRORS, table=table)
        table.seed(scene='probe', name='', digest=probe.mirrors.digest, result='a')
        probe.results = {'a': None}
        probe.requests = {'a': httpx2.ReadTimeout('slow')}
        with logger.mock_capture_writer():
            with pytest.raises(httpx2.ReadTimeout):
                probe.fetch(PATH)
        # a is probed again and wins: the retry (its request) fails again
        assert probe.probed == [('a', 'http://a', PATH)]
        assert probe.called == [('a', 'http://a', PATH), ('a', 'http://a', PATH)]

    def test_the_payload_of_the_reprobe_serves_the_retry(self):
        """A re-probe that was the request itself serves the retry: the flow
        consumes its payload instead of repeating the request that failed."""
        table = FakeMirrorTable()
        probe = StubProbe(self.MIRRORS, table=table)
        table.seed(scene='probe', name='', digest=probe.mirrors.digest, result='a')
        probe.results = {'a': 'BODY'}
        probe.requests = {'a': httpx2.ReadTimeout('slow')}
        with logger.mock_capture_writer():
            assert probe.fetch(PATH) == 'BODY'
        assert probe.probed == [('a', 'http://a', PATH)]
        assert probe.called == [('a', 'http://a', PATH)]

    def test_retry_failure_is_raised(self):
        """The request is retried exactly once: a failure of the retry is
        raised as-is, no second probe."""
        table = FakeMirrorTable()
        probe = StubProbe(self.MIRRORS, table=table)
        table.seed(scene='probe', name='', digest=probe.mirrors.digest, result='a')
        probe.results = {'b': None}
        probe.requests = {
            'a': RangeUnsupportedError('boom'), 'b': RangeUnsupportedError('boom again')}
        with logger.mock_capture_writer():
            with pytest.raises(RangeUnsupportedError, match='boom again'):
                probe.fetch(PATH)
        assert probe.probed == [('b', 'http://b', PATH)]
        assert probe.called == [('a', 'http://a', PATH), ('b', 'http://b', PATH)]

    def test_a_probed_path_failure_is_raised(self):
        """A path of this instance was already probed (the first request): a
        later failure is the answer, probing again is meaningless."""
        table = FakeMirrorTable()
        probe = StubProbe(self.MIRRORS, results={'a': None}, requests={'a': 'PAY'}, table=table)
        with logger.mock_capture_writer():
            assert probe.fetch(PATH) == 'PAY'
            probe.requests['a'] = RangeUnsupportedError('no ranges')
            with pytest.raises(RangeUnsupportedError, match='no ranges'):
                probe.fetch(PATH)
        assert len(probe.probed) == 1

    def test_no_winner_raises_the_original_failure(self):
        """No candidate can serve the path: the original failure is raised
        as-is (the caller keeps its own fallback), the record keeps the name."""
        table = FakeMirrorTable()
        probe = StubProbe(self.MIRRORS, table=table)
        table.seed(scene='probe', name='', digest=probe.mirrors.digest, result='a')
        probe.requests = {'a': RangeUnsupportedError('boom')}
        probe.results = {'a': ValueError('down'), 'b': ValueError('gone')}
        with logger.mock_capture_writer():
            with pytest.raises(RangeUnsupportedError, match='boom'):
                probe.fetch(PATH)
        assert table.select_one(scene='probe', name='').result == 'a'

    def test_single_mirror_failure_is_raised(self):
        """A single mirror set has nothing to reselect: the failure is
        raised as-is."""
        probe = StubProbe('http://only', requests={'default': RuntimeError('boom')})
        with logger.mock_capture_writer():
            with pytest.raises(RuntimeError, match='boom'):
                probe.fetch(PATH)
        assert probe.probed == []


class TestHttpProbe:
    """SyncHttpProbe: the HEAD probe of a path, the 405 / 501 fallback and the
    range check of the transfer."""

    MIRRORS = {'g': {'a': 'http://a', 'b': 'http://b'}}

    def test_the_default_probe_is_a_head(self):
        """A path is probed with a HEAD before the work of the flow requests
        it."""
        requests = []

        def handler(request):
            requests.append((request.method, request.url.path))
            return httpx2.Response(200, content=b'body')

        probe = SyncHttpProbe(self.MIRRORS, client=make_sclient(handler))
        probe.RECORD_TABLE = FakeMirrorTable()

        def work(name, url):
            return probe.get_bytes(url, '/v/full.pack')

        with logger.mock_capture_writer():
            assert probe.fetch('/v/full.pack', work) == b'body'
        assert requests == [('HEAD', '/v/full.pack'), ('GET', '/v/full.pack')]

    def test_head_unsupported_falls_back_to_a_range_get(self):
        """A server that does not support HEAD (405) is probed with a one-byte
        range GET: its status is the availability, the body is never read."""
        requests = []

        def handler(request):
            requests.append((request.method, request.headers.get('Range', '')))
            if request.method == 'HEAD':
                return httpx2.Response(405)
            return httpx2.Response(206, content=b'body')

        probe = SyncHttpProbe(self.MIRRORS, client=make_sclient(handler))
        probe.RECORD_TABLE = FakeMirrorTable()

        def work(name, url):
            return probe.get_bytes(url, '/v/full.pack')

        with logger.mock_capture_writer():
            assert probe.fetch('/v/full.pack', work) == b'body'
        assert requests == [
            ('HEAD', ''),
            ('GET', 'bytes=0-0'),
            ('GET', ''),
        ]

    def test_a_failed_head_moves_to_the_next_member(self):
        """A candidate the HEAD does not serve falls through to the next
        member of the group."""
        requests = []

        def handler(request):
            requests.append((request.method, request.url.host))
            if request.url.host == 'a':
                return httpx2.Response(404)
            return httpx2.Response(200, content=b'body')

        probe = SyncHttpProbe(self.MIRRORS, client=make_sclient(handler))
        probe.RECORD_TABLE = FakeMirrorTable()

        def work(name, url):
            return probe.get_bytes(url, '/v/full.pack')

        with logger.mock_capture_writer():
            assert probe.fetch('/v/full.pack', work) == b'body'
        assert requests == [('HEAD', 'a'), ('HEAD', 'b'), ('GET', 'b')]

    def test_get_bytes_rejects_an_ignored_range(self):
        """A request that carries a Range header answered with the whole file
        (a 200) means the server ignored the range: the mirror is not usable
        (the body is never read)."""
        def handler(request):
            return httpx2.Response(200, content=b'whole file')

        probe = SyncHttpProbe('http://only', client=make_sclient(handler))
        with logger.mock_capture_writer():
            with pytest.raises(RangeUnsupportedError):
                probe.get_bytes('http://only', '/v/full.pack', headers={'Range': 'bytes=0-3'})

    def test_get_bytes_returns_a_range(self):
        """A range request answered with the range (a 206) is the body of the
        answer."""
        def handler(request):
            assert request.headers.get('Range') == 'bytes=0-3'
            return httpx2.Response(206, content=b'body')

        probe = SyncHttpProbe('http://only', client=make_sclient(handler))
        with logger.mock_capture_writer():
            assert probe.get_bytes('http://only', '/v/full.pack', headers={'Range': 'bytes=0-3'}) == b'body'

    def test_the_plain_request_gets_the_path(self):
        """The default request of a flow (no work) is a plain GET of the
        path."""
        requests = []

        def handler(request):
            requests.append(request.method)
            return httpx2.Response(200, content=b'body')

        probe = SyncHttpProbe('http://only', client=make_sclient(handler))
        with logger.mock_capture_writer():
            assert probe.request('only', 'http://only', '/v/full.pack') == b'body'
        assert requests == ['GET']
