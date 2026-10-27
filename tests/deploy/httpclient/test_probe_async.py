"""
Tests for the async flavour of the probes: the trio race (AsyncProbe.run) and
the resolution flow it implements (probe_base.ProbeBase documents the flow).

The engine is transport agnostic, the tests drive it with a stub subclass of
canned results and errors, no http is involved. The selection record is the
FakeMirrorTable of the pack conftest (an in-memory table), injected through
ProbeBase.RECORD_TABLE. Every test running the flow is async (pytest-trio); a
slow candidate is a slow async probe_function, the losing groups are cancelled
by the engine instead of running to their timeouts.
"""
import httpx2
import pytest
import trio

from alasio.deploy.httpclient import probe_async
from alasio.deploy.httpclient.probe_async import AsyncHttpProbe, AsyncProbe, probe_lock
from alasio.deploy.httpclient.probe_mirrors import AllMirrorsFailedError, Mirrors, RangeUnsupportedError
from alasio.logger import logger
from tests.deploy_dev.pack.conftest import FakeMirrorTable

# the path of the tests: the stub does not care what it means, the engine keys
# its bookkeeping by it
PATH = '/latest.pack'

# the mirror set of the blocked-network tests: the hosts a CN network blocks,
# as a temporary mirror set of a deployment may list them. The urls are labels
# of the real hosts: the tests serve their requests with a MockTransport
BLOCKED_MIRRORS = {
    'cn': {'google': 'https://www.google.com', 'youtube': 'https://www.youtube.com'},
}


def make_aclient(handler):
    """A httpx2.AsyncClient with a MockTransport handler."""
    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


class StubProbe(AsyncProbe):
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

    async def probe_function(self, name, url, path):
        self.probed.append((name, url, path))
        result = self.results.get(name, None)
        if isinstance(result, Exception):
            raise result
        return result

    async def request(self, name, url, path):
        self.called.append((name, url, path))
        result = self.requests.get(name, None)
        if isinstance(result, Exception):
            raise result
        return result


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


class TestRace:
    """The race: groups run in parallel, members fall back in order."""

    @pytest.mark.trio
    async def test_member_order(self, fake_clock):
        """The members of a group are probed in the declared order, a
        failed member falls through to the next one."""
        probe = StubProbe(
            {'cn': {'down': 'http://down', 'up': 'http://up'}},
            results={'down': ValueError('boom'), 'up': 'UP'}, table=FakeMirrorTable())
        with logger.mock_capture_writer() as capture:
            name, payload = await probe.run(PATH)
        assert (name, payload) == ('up', 'UP')
        assert probe.probed == [
            ('down', 'http://down', PATH), ('up', 'http://up', PATH)]
        assert capture.concat() == f"""\
INFO | Probing `probe` for "{PATH}": down, up
WARNING | Mirror "down" is not usable: ValueError: boom
INFO | Mirror "up" responded in 0ms
INFO | [Mirror `probe`] up
"""

    @pytest.mark.trio
    async def test_preferred_member_wins(self, fake_clock):
        """A usable preferred member is not bypassed by the fallback."""
        probe = StubProbe(
            {'cn': {'preferred': 'http://a', 'fallback': 'http://b'}},
            results={'preferred': 'P', 'fallback': 'F'}, table=FakeMirrorTable())
        with logger.mock_capture_writer() as capture:
            name, payload = await probe.run(PATH)
        assert (name, payload) == ('preferred', 'P')
        assert probe.probed == [('preferred', 'http://a', PATH)]
        assert capture.concat() == f"""\
INFO | Probing `probe` for "{PATH}": preferred, fallback
INFO | Mirror "preferred" responded in 0ms
INFO | [Mirror `probe`] preferred
"""

    @pytest.mark.trio
    async def test_groups_race(self, fake_clock):
        """Groups run in parallel, the fastest one wins, the losing
        group is cancelled instead of running to its timeout."""
        entered = trio.Event()

        class SlowProbe(StubProbe):
            async def probe_function(self, name, url, path):
                if name == 'slow':
                    entered.set()
                    # the losing group is cancelled by the probe, this
                    # sleep never finishes
                    await trio.sleep_forever()
                return await super().probe_function(name, url, path)

        probe = SlowProbe(
            {'slow': {'slow': 'http://slow'}, 'fast': {'fast': 'http://fast'}},
            results={'slow': 'S', 'fast': 'F'}, table=FakeMirrorTable())
        with logger.mock_capture_writer() as capture:
            name, payload = await probe.run(PATH)
        assert (name, payload) == ('fast', 'F')
        # the slow group started its probe and was cancelled
        assert entered.is_set()
        assert capture.concat() == f"""\
INFO | Probing `probe` for "{PATH}": slow, fast
INFO | Mirror "fast" responded in 0ms
INFO | [Mirror `probe`] fast
"""

    @pytest.mark.trio
    async def test_all_failed(self):
        """Every candidate fails: the error carries the reasons."""
        probe = StubProbe(
            {'a': {'a': 'http://a'}, 'b': {'b': 'http://b'}},
            results={'a': ValueError('down'), 'b': KeyError('gone')}, table=FakeMirrorTable())
        with logger.mock_capture_writer() as capture:
            with pytest.raises(AllMirrorsFailedError) as e:
                await probe.run(PATH)
        message = str(e.value)
        assert 'a: ValueError: down' in message
        assert "b: KeyError: 'gone'" in message
        # the groups race in parallel and trio may run the batch of the two
        # tasks in either order: the sequence of the two failures is not
        # defined
        assert sorted(capture.concat().splitlines()) == [
            'ERROR | Failed to probe `probe` for "/latest.pack": no mirror is usable',
            'INFO | Probing `probe` for "/latest.pack": a, b',
            'WARNING | Mirror "a" is not usable: ValueError: down',
            'WARNING | Mirror "b" is not usable: KeyError: \'gone\'',
        ]

    @pytest.mark.trio
    async def test_mirrors_input(self, fake_clock):
        """A Mirrors instance is accepted as the probe input."""
        mirrors = Mirrors.from_input({'g': {'a': 'http://a'}})
        probe = StubProbe(mirrors, results={'a': 'A'}, table=FakeMirrorTable())
        with logger.mock_capture_writer() as capture:
            name, payload = await probe.run(PATH)
        assert (name, payload) == ('a', 'A')
        assert capture.concat() == f"""\
INFO | Probing `probe` for "{PATH}": a
INFO | Mirror "a" responded in 0ms
INFO | [Mirror `probe`] a
"""

    @pytest.mark.trio
    async def test_the_payload_of_the_winner_is_returned(self, fake_clock):
        """The payload of the winner is returned as-is."""
        token = object()
        probe = StubProbe({'g': {'a': 'http://a'}}, results={'a': token}, table=FakeMirrorTable())
        with logger.mock_capture_writer() as capture:
            name, payload = await probe.run(PATH)
        assert name == 'a'
        assert payload is token
        assert capture.concat() == f"""\
INFO | Probing `probe` for "{PATH}": a
INFO | Mirror "a" responded in 0ms
INFO | [Mirror `probe`] a
"""

    @pytest.mark.trio
    async def test_probe_function_not_implemented(self):
        """The base probe_function makes every candidate unusable."""
        probe = AsyncProbe({'g': {'a': 'http://a'}}, name='')
        probe.RECORD_TABLE = FakeMirrorTable()
        with logger.mock_capture_writer() as capture:
            with pytest.raises(AllMirrorsFailedError) as e:
                await probe.run(PATH)
        assert 'a: NotImplementedError' in str(e.value)
        # the base probe_function raises without a message: the reason of the
        # log is the type alone (stringify_event)
        assert capture.concat() == f"""\
INFO | Probing `probe` for "{PATH}": a
WARNING | Mirror "a" is not usable: NotImplementedError
ERROR | Failed to probe `probe` for "{PATH}": no mirror is usable
"""


class TestResolve:
    """The record, the probe of the first request, the payload."""

    MIRRORS = {'g': {'a': 'http://a', 'b': 'http://b'}}

    @pytest.mark.trio
    async def test_single_mirror_skips_the_record_and_the_probe(self):
        """A single mirror set has nothing to select: no record read, no
        probe, the only candidate serves the request."""
        table = FakeMirrorTable()
        probe = StubProbe('http://only', requests={'default': 'PAY'}, table=table)
        with logger.mock_capture_writer() as capture:
            assert await probe.fetch(PATH) == 'PAY'
        # no probe, no log: the flow of a single mirror set is silent
        assert capture.concat() == ''
        assert probe.probed == []
        # no read, no write: a single mirror set never touches the table
        assert (table.selects, table.upserts) == (0, 0)

    @pytest.mark.trio
    async def test_record_hit_is_used_without_a_probe(self):
        """A usable record serves the requests, no probe runs."""
        table = FakeMirrorTable()
        probe = StubProbe(self.MIRRORS, requests={'b': 'PAY'}, table=table)
        table.seed(scene='probe', name='', digest=probe.mirrors.digest, result='b')
        with logger.mock_capture_writer() as capture:
            assert await probe.fetch(PATH) == 'PAY'
        # the record serves the flow: no probe, no log
        assert capture.concat() == ''
        assert probe.probed == []
        assert probe.called == [('b', 'http://b', PATH)]
        # the record is an optimization: it is only read, never rewritten
        assert table.upserts == 0

    @pytest.mark.trio
    async def test_probe_of_the_first_request_records_the_winner(self, fake_clock):
        """Without a record the first request probes: the winner is recorded
        and every later request of the instance reuses it (no probe again)."""
        table = FakeMirrorTable()
        probe = StubProbe(self.MIRRORS, results={'a': None}, requests={'a': 'PAY'}, table=table)
        with logger.mock_capture_writer() as capture:
            assert await probe.fetch(PATH) == 'PAY'
            # optimistic reuse: a second request goes to the same mirror
            assert await probe.fetch(PATH) == 'PAY'
        # the second request reuses the mirror of the first one: one probe,
        # one pair of log lines
        assert capture.concat() == f"""\
INFO | Probing `probe` for "{PATH}": a, b
INFO | Mirror "a" responded in 0ms
INFO | [Mirror `probe`] a
"""
        assert probe.probed == [('a', 'http://a', PATH)]
        assert probe.called == [('a', 'http://a', PATH), ('a', 'http://a', PATH)]
        row = table.select_one(scene='probe', name='')
        assert row.result == 'a'
        assert row.digest == probe.mirrors.digest

    @pytest.mark.trio
    async def test_the_payload_of_the_probe_is_the_answer_of_the_flow(self, fake_clock):
        """A probe that was the request itself answers the flow: its payload is
        returned as-is and the work of the flow does not run."""
        table = FakeMirrorTable()
        probe = StubProbe(self.MIRRORS, results={'a': 'BODY'}, table=table)
        seen = []

        async def work(name, url):
            seen.append(name)
            return 'WORK'

        with logger.mock_capture_writer() as capture:
            assert await probe.fetch(PATH, work) == 'BODY'
        assert capture.concat() == f"""\
INFO | Probing `probe` for "{PATH}": a, b
INFO | Mirror "a" responded in 0ms
INFO | [Mirror `probe`] a
"""
        assert probe.probed == [('a', 'http://a', PATH)]
        assert probe.called == []
        assert seen == []

    @pytest.mark.trio
    async def test_a_probe_that_fetched_nothing_runs_the_work(self, fake_clock):
        """A probe that only proved the availability (a HEAD) hands nothing
        over: the work of the flow runs and fetches the path itself."""
        table = FakeMirrorTable()
        probe = StubProbe(self.MIRRORS, results={'a': None}, table=table)
        seen = []

        async def work(name, url):
            seen.append(name)
            return 'WORK'

        with logger.mock_capture_writer() as capture:
            assert await probe.fetch(PATH, work) == 'WORK'
        assert capture.concat() == f"""\
INFO | Probing `probe` for "{PATH}": a, b
INFO | Mirror "a" responded in 0ms
INFO | [Mirror `probe`] a
"""
        assert probe.probed == [('a', 'http://a', PATH)]
        assert seen == ['a']

    @pytest.mark.trio
    async def test_a_flow_without_a_work_takes_the_payload_as_its_answer(self, fake_clock):
        """A flow that passes no work of its own takes the payload of the probe
        as its answer: the plain request of the flavour does not repeat it."""
        table = FakeMirrorTable()
        probe = StubProbe(self.MIRRORS, results={'a': 'BODY'}, table=table)
        with logger.mock_capture_writer() as capture:
            assert await probe.fetch(PATH) == 'BODY'
        assert capture.concat() == f"""\
INFO | Probing `probe` for "{PATH}": a, b
INFO | Mirror "a" responded in 0ms
INFO | [Mirror `probe`] a
"""
        assert probe.probed == [('a', 'http://a', PATH)]
        assert probe.called == []

    @pytest.mark.trio
    async def test_probe_lock_serializes_the_flows_of_one_key(self, fake_clock):
        """Two flows of the process with the same (scene, name) probe once:
        the second one waits for the lock and reuses the written record."""
        table = FakeMirrorTable()
        entered = trio.Event()

        class SlowProbe(StubProbe):
            async def probe_function(self, name, url, path):
                entered.set()
                await trio.sleep(0.05)
                return await super().probe_function(name, url, path)

        first = SlowProbe(self.MIRRORS, results={'a': 'A'}, requests={'a': 'PAY'}, table=table)
        second = SlowProbe(self.MIRRORS, results={'a': 'A'}, requests={'a': 'PAY'}, table=table)
        with logger.mock_capture_writer() as capture:
            async with trio.open_nursery() as nursery:
                nursery.start_soon(first.fetch, PATH)
                await entered.wait()
                nursery.start_soon(second.fetch, PATH)
        # the second flow re-read the record of the first one: one probe,
        # one pair of log lines
        assert capture.concat() == f"""\
INFO | Probing `probe` for "{PATH}": a, b
INFO | Mirror "a" responded in 0ms
INFO | [Mirror `probe`] a
"""
        # exactly one probe ran, the second flow re-read the record
        assert len(first.probed) + len(second.probed) == 1
        assert first.probed == [('a', 'http://a', PATH)]

    @pytest.mark.trio
    async def test_probe_lock_is_per_key(self):
        """The lock of another (scene, name) does not block the probe."""
        async with probe_lock('probe', 'a'):
            with trio.move_on_after(0.05) as scope:
                async with probe_lock('probe', 'b'):
                    pass
            assert not scope.cancelled_caught

    def test_probe_lock_returns_the_lock_of_the_key(self):
        """probe_lock returns the lock itself, not acquired: one lock per key,
        handed to the caller that holds it with `async with`."""
        lock = probe_lock('probe', 'a')
        assert probe_lock('probe', 'a') is lock
        assert probe_lock('probe', 'b') is not lock
        assert not lock.locked()


class TestFailure:
    """A failed request re-probes the path once and retries once."""

    MIRRORS = {'g': {'a': 'http://a', 'b': 'http://b'}}

    @pytest.mark.trio
    async def test_content_failure_of_the_first_request_reselects(self, fake_clock):
        """The probe of the first request only proves that a candidate serves
        the path (a HEAD): a content failure of the work right after it makes
        the instance probe the path again without that candidate, exactly like
        a failure on a mirror that came from the record."""
        table = FakeMirrorTable()
        probe = StubProbe(self.MIRRORS, table=table)
        # no record: the probe of the path selects a, its request fails
        probe.results = {'a': None, 'b': None}
        probe.requests = {'a': RangeUnsupportedError('no ranges'), 'b': 'PAY'}
        with logger.mock_capture_writer() as capture:
            assert await probe.fetch(PATH) == 'PAY'
        assert capture.concat() == f"""\
INFO | Probing `probe` for "{PATH}": a, b
INFO | Mirror "a" responded in 0ms
INFO | [Mirror `probe`] a
INFO | Probing `probe` for "{PATH}": a, b
INFO | Mirror "b" responded in 0ms
INFO | [Mirror `probe`] b
"""
        # the failed candidate is excluded from the re-probe, the other one wins
        assert probe.probed == [('a', 'http://a', PATH), ('b', 'http://b', PATH)]
        assert probe.called == [('a', 'http://a', PATH), ('b', 'http://b', PATH)]
        assert table.select_one(scene='probe', name='').result == 'b'

    @pytest.mark.trio
    async def test_content_failure_excludes_the_mirror(self, fake_clock):
        """A content failure (the domain's own: the bytes are not usable) does
        not get better by asking the same mirror again: the mirror that failed
        is excluded from the re-probe, another candidate wins and the retry
        succeeds."""
        table = FakeMirrorTable()
        probe = StubProbe(self.MIRRORS, table=table)
        table.seed(scene='probe', name='', digest=probe.mirrors.digest, result='a')
        probe.results = {'a': None, 'b': None}
        probe.requests = {'a': RangeUnsupportedError('no ranges'), 'b': 'PAY'}
        with logger.mock_capture_writer() as capture:
            assert await probe.fetch(PATH) == 'PAY'
        # the re-probe of the failed path: a is skipped, b wins
        assert capture.concat() == f"""\
INFO | Probing `probe` for "{PATH}": a, b
INFO | Mirror "b" responded in 0ms
INFO | [Mirror `probe`] b
"""
        # the failed mirror is not probed again, the winner is the other one
        assert probe.probed == [('b', 'http://b', PATH)]
        assert probe.called == [('a', 'http://a', PATH), ('b', 'http://b', PATH)]
        assert table.select_one(scene='probe', name='').result == 'b'

    @pytest.mark.trio
    async def test_transport_failure_keeps_the_mirror_a_candidate(self, fake_clock):
        """A transport failure may be a blip: the mirror stays a candidate and
        can win the re-probe again (the retry on it is the new attempt)."""
        import httpx2

        table = FakeMirrorTable()
        probe = StubProbe(self.MIRRORS, table=table)
        table.seed(scene='probe', name='', digest=probe.mirrors.digest, result='a')
        probe.results = {'a': None}
        probe.requests = {'a': httpx2.ReadTimeout('slow')}
        with logger.mock_capture_writer() as capture:
            with pytest.raises(httpx2.ReadTimeout):
                await probe.fetch(PATH)
        # the re-probe of the failed path: a is a candidate again and wins
        assert capture.concat() == f"""\
INFO | Probing `probe` for "{PATH}": a, b
INFO | Mirror "a" responded in 0ms
INFO | [Mirror `probe`] a
"""
        # a is probed again and wins: the retry (its request) fails again
        assert probe.probed == [('a', 'http://a', PATH)]
        assert probe.called == [('a', 'http://a', PATH), ('a', 'http://a', PATH)]

    @pytest.mark.trio
    async def test_the_payload_of_the_reprobe_serves_the_retry(self, fake_clock):
        """A re-probe that was the request itself serves the retry: the flow
        consumes its payload instead of repeating the request that failed."""
        import httpx2

        table = FakeMirrorTable()
        probe = StubProbe(self.MIRRORS, table=table)
        table.seed(scene='probe', name='', digest=probe.mirrors.digest, result='a')
        probe.results = {'a': 'BODY'}
        probe.requests = {'a': httpx2.ReadTimeout('slow')}
        with logger.mock_capture_writer() as capture:
            assert await probe.fetch(PATH) == 'BODY'
        assert capture.concat() == f"""\
INFO | Probing `probe` for "{PATH}": a, b
INFO | Mirror "a" responded in 0ms
INFO | [Mirror `probe`] a
"""
        assert probe.probed == [('a', 'http://a', PATH)]
        assert probe.called == [('a', 'http://a', PATH)]

    @pytest.mark.trio
    async def test_retry_failure_is_raised(self, fake_clock):
        """The request is retried exactly once: a failure of the retry is
        raised as-is, no second probe."""
        table = FakeMirrorTable()
        probe = StubProbe(self.MIRRORS, table=table)
        table.seed(scene='probe', name='', digest=probe.mirrors.digest, result='a')
        probe.results = {'b': None}
        probe.requests = {
            'a': RangeUnsupportedError('boom'), 'b': RangeUnsupportedError('boom again')}
        with logger.mock_capture_writer() as capture:
            with pytest.raises(RangeUnsupportedError, match='boom again'):
                await probe.fetch(PATH)
        assert capture.concat() == f"""\
INFO | Probing `probe` for "{PATH}": a, b
INFO | Mirror "b" responded in 0ms
INFO | [Mirror `probe`] b
"""
        assert probe.probed == [('b', 'http://b', PATH)]
        assert probe.called == [('a', 'http://a', PATH), ('b', 'http://b', PATH)]

    @pytest.mark.trio
    async def test_a_probed_path_failure_is_raised(self, fake_clock):
        """A path of this instance was already probed (the first request): a
        later failure is the answer, probing again is meaningless."""
        table = FakeMirrorTable()
        probe = StubProbe(self.MIRRORS, results={'a': None}, requests={'a': 'PAY'}, table=table)
        with logger.mock_capture_writer() as capture:
            assert await probe.fetch(PATH) == 'PAY'
            probe.requests['a'] = RangeUnsupportedError('no ranges')
            with pytest.raises(RangeUnsupportedError, match='no ranges'):
                await probe.fetch(PATH)
        # the second fetch failed on a path this instance probed: no re-probe,
        # the log is the one of the first probe
        assert capture.concat() == f"""\
INFO | Probing `probe` for "{PATH}": a, b
INFO | Mirror "a" responded in 0ms
INFO | [Mirror `probe`] a
"""
        assert len(probe.probed) == 1

    @pytest.mark.trio
    async def test_no_winner_raises_the_original_failure(self):
        """No candidate can serve the path: the original failure is raised
        as-is (the caller keeps its own fallback), the record keeps the name."""
        table = FakeMirrorTable()
        probe = StubProbe(self.MIRRORS, table=table)
        table.seed(scene='probe', name='', digest=probe.mirrors.digest, result='a')
        probe.requests = {'a': RangeUnsupportedError('boom')}
        probe.results = {'a': ValueError('down'), 'b': ValueError('gone')}
        with logger.mock_capture_writer() as capture:
            with pytest.raises(RangeUnsupportedError, match='boom'):
                await probe.fetch(PATH)
        # the re-probe had no winner: a is excluded, the members of its group
        # failed
        assert capture.concat() == f"""\
INFO | Probing `probe` for "{PATH}": a, b
WARNING | Mirror "b" is not usable: ValueError: gone
ERROR | Failed to probe `probe` for "{PATH}": no mirror is usable
"""
        assert table.select_one(scene='probe', name='').result == 'a'

    @pytest.mark.trio
    async def test_single_mirror_failure_is_raised(self):
        """A single mirror set has nothing to reselect: the failure is
        raised as-is."""
        probe = StubProbe('http://only', requests={'default': RuntimeError('boom')})
        with logger.mock_capture_writer() as capture:
            with pytest.raises(RuntimeError, match='boom'):
                await probe.fetch(PATH)
        # nothing to select and nothing to probe: no log
        assert capture.concat() == ''
        assert probe.probed == []


class TestBlockedMirror:
    """The shape of a mirror the network blocks, as it was observed against
    the real hosts (a temporary mirror set of google.com and youtube.com):
    the poisoned DNS of the CN network resolves them to a blackholed address,
    the connect never answers, so every attempt of the request times out and
    the trio backend of the http client gives up with a ConnectTimeout whose
    message is empty (it maps trio.TooSlowError, which carries no message;
    the sync backend maps the socket timeout and says 'timed out', see
    test_sync_client).

    A blocked candidate is a transport error like any other: the request is
    retried REQUEST_ATTEMPTS times, the candidate is unusable, and the wall
    leaves no candidate to fall back on (it blocks every mirror of the set).
    """

    @pytest.mark.trio
    async def test_every_candidate_is_blocked(self, autojump_clock):
        """Every candidate of the set is blocked: the probe retries every
        request, marks every candidate unusable and fails."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            # httpx2.ConnectTimeout('') as the trio backend raises it: the
            # backend maps the underlying timeout (trio.TooSlowError, which
            # carries no message) to a ConnectTimeout of an empty message
            raise httpx2.ConnectTimeout('')

        table = FakeMirrorTable()
        probe = AsyncHttpProbe(BLOCKED_MIRRORS, name='blocked', client=make_aclient(handler))
        probe.RECORD_TABLE = table
        with logger.mock_capture_writer() as capture:
            with pytest.raises(AllMirrorsFailedError) as e:
                await probe.fetch(PATH)
        # every request of every candidate was retried its attempts
        assert requests == (
            ['https://www.google.com/latest.pack'] * 3
            + ['https://www.youtube.com/latest.pack'] * 3
        )
        # the aggregate error carries the reasons as the log does: the
        # message of a blocked connect timeout is empty, stringify_event()
        # renders the type alone rather than a dangling separator
        assert str(e.value) == 'google: ConnectTimeout; youtube: ConnectTimeout'
        assert capture.concat() == f"""\
INFO | Probing `probe/blocked` for "{PATH}": google, youtube
WARNING | Request to "https://www.google.com/latest.pack" failed (attempt 1/3): ConnectTimeout
WARNING | Request to "https://www.google.com/latest.pack" failed (attempt 2/3): ConnectTimeout
WARNING | Request to "https://www.google.com/latest.pack" failed (attempt 3/3): ConnectTimeout
WARNING | Mirror "google" is not usable: ConnectTimeout
WARNING | Request to "https://www.youtube.com/latest.pack" failed (attempt 1/3): ConnectTimeout
WARNING | Request to "https://www.youtube.com/latest.pack" failed (attempt 2/3): ConnectTimeout
WARNING | Request to "https://www.youtube.com/latest.pack" failed (attempt 3/3): ConnectTimeout
WARNING | Mirror "youtube" is not usable: ConnectTimeout
ERROR | Failed to probe `probe/blocked` for "{PATH}": no mirror is usable
"""
        # nothing was recorded: a blocked network is not a wrong mirror
        assert table.upserts == 0
