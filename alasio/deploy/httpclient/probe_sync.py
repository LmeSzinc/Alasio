"""
The blocking flavour of the mirror probes: the thread pool race (SyncProbe) and
its HTTP twin (SyncHttpProbe).

The twin of probe_async: the same flow (probe_base.ProbeBase documents it, the
async module implements it first), the same record and lock keys; the groups run
on the shared thread pool here, a blocking request cannot be cancelled from
outside, so the losing probes of a race finish in their own time and their
results are dropped (at most one probe timeout of extra traffic).

The sync flavour is the one of the synchronous tools of the deploy domain, e.g.
the wheel fetch of the pack server (see the module note of sync_client.py): the
backend and the deploy jobs run on the event loop and use probe_async.

httpx2 is imported lazily in the places that use it: the package resolves its
own version with importlib.metadata at import time, which the in-memory
filesystem of the tests cannot answer.
"""
import threading
from time import perf_counter

from alasio.deploy.httpclient.probe_base import ProbeBase
from alasio.deploy.httpclient.probe_mirrors import AllMirrorsFailedError, RangeUnsupportedError
from alasio.deploy.httpclient.sync_client import HttpClient
from alasio.ext.concurrent.threadpool import THREAD_POOL
from alasio.logger import logger

# {(scene, name): threading.Lock}: the blocking twin of probe_async.probe_lock,
# the same keys and the same purpose (one probe per key in this process)
_probe_locks = {}
# guards the get-or-create of _probe_locks: two threads that find no lock for
# the same key at the same moment would otherwise create one lock each, and
# their probes would run beside each other
_create_lock = threading.Lock()


def probe_lock(scene, name):
    """
    The in-process lock of one (scene, name) key, the blocking twin of
    probe_async.probe_lock(): the lock itself, held with
    `with probe_lock(...):`.

    Two threads of the process may find no usable record at the same moment:
    the lock serializes their probes and the second one reads the record again
    after acquiring it (see SyncProbe.resolve).

    Args:
        scene (str): Usage domain of the candidate set, e.g. 'repo'
        name (str): Identity of the set inside the scene, '' allowed

    Returns:
        threading.Lock: The lock of the key, not acquired yet
    """
    key = (scene, name)
    with _create_lock:
        lock = _probe_locks.get(key, None)
        if lock is None:
            lock = _probe_locks[key] = threading.Lock()
    return lock


class _Race:
    """
    The shared state of one race of SyncProbe: the outcome lists and the
    decision event, guarded by a lock (the probes run on several threads of
    the pool, unlike the tasks of the async race which share one thread).
    """

    def __init__(self, group_count):
        """
        Args:
            group_count (int): Number of groups racing
        """
        self.group_count = group_count
        self.decided = threading.Event()
        self.lock = threading.Lock()
        self.winner = []
        self.failures = []

    def win(self, name, payload):
        """
        Record a winner and decide the race.

        Args:
            name (str): Name of the winning candidate
            payload (Any): Payload of the winner
        """
        with self.lock:
            self.winner.append((name, payload))
            self.decided.set()

    def exhaust(self, reasons):
        """
        Record an exhausted group and decide the race when every group is
        exhausted.

        Args:
            reasons (str): Summary of the failures of the group
        """
        with self.lock:
            self.failures.append(reasons)
            if len(self.failures) == self.group_count:
                self.decided.set()


class SyncProbe(ProbeBase):
    """
    The blocking flavour of a mirror probe: the thread pool race and the
    resolution flow (probe_base documents the flow, probe_async implements it
    first; this is the blocking twin, see the module note).

    Attributes:
        FAILURES (tuple): The failure types of the domain that mean "this
            mirror cannot serve the request", see AsyncProbe.
    """

    # failure types of the domain, e.g. (PackDecodeError,)
    FAILURES = ()

    def failures(self):
        """
        The failure types classified by this flavour: the HTTP failures of the
        deploy domain and the FAILURES of the domain, see AsyncProbe.failures().

        Returns:
            tuple[type, ...]: The exception types of a mirror failure
        """
        import httpx2
        return (httpx2.HTTPError, RangeUnsupportedError, *self.FAILURES)

    def fetch(self, path, work=None):
        """
        Serve one path: resolve the mirror, run the work of the flow on it,
        and re-probe the path once when the mirror cannot serve it, see
        AsyncProbe.fetch() for the semantics.

        Args:
            path (str): Path of the request
            work (Callable, optional): The blocking work of the flow on one
                mirror, `work(name, url)`. Defaults to None, the plain request
                of the flavour

        Returns:
            Any: The payload of the flow: the payload of the probe when it was
                the request itself, the payload of the work otherwise

        Raises:
            Exception: The failure of the work when no other mirror serves the
                path, or the failure of the retry
            AllMirrorsFailedError: If the probe of a record-less instance finds
                no usable candidate, before any request of the flow had a
                chance to run
        """
        name, payload = self.resolve(path)
        if payload is not None:
            # the probe was the request itself: its payload is the answer of
            # the flow, no request of its own is made
            return payload
        try:
            return self.work(name, path, work)
        except self.failures() as e:
            failure = e
        if self.mirrors.single or self.was_probed(path):
            raise failure
        try:
            with probe_lock(self.SCENE, self.name):
                name, payload = self.probe(path, exclude=self._exclude(failure, name))
        except AllMirrorsFailedError:
            # no candidate can serve the path: the original failure is the
            # answer, the caller keeps its own fallback
            raise failure
        if payload is not None:
            # the probe was the request itself again: its payload is the retry
            return payload
        return self.work(name, path, work)

    def work(self, name, path, work=None):
        """
        Run the work of a flow on one mirror: the caller's own, or the plain
        request of the flavour, see AsyncProbe.work().

        Args:
            name (str): Mirror name to work on
            path (str): Path of the request
            work (Callable, optional): `work(name, url)`. Defaults to None, the
                plain request of the flavour

        Returns:
            Any: The payload of the work
        """
        if work is None:
            return self.request(name, self.mirrors.url_of(name), path)
        return work(name, self.mirrors.url_of(name))

    def _exclude(self, failure, name):
        """
        The mirrors to skip when a failed path is probed again, see
        AsyncProbe._exclude().

        Args:
            failure (Exception): The failure of the request
            name (str): Mirror the request failed on

        Returns:
            tuple[str, ...]: Names to skip in the probe
        """
        import httpx2
        if isinstance(failure, httpx2.HTTPError):
            return ()
        return (name,)

    def resolve(self, path):
        """
        Resolve the mirror of this instance for one path, probing only when
        needed, see AsyncProbe.resolve() for the semantics.

        Args:
            path (str): Path of the first request of the flow

        Returns:
            tuple[str, Any]: The mirror name, and the payload of the probe
                (None when it fetched nothing)

        Raises:
            AllMirrorsFailedError: If no candidate of any group is usable
        """
        if self._mirror:
            return self._mirror, None
        single = self.mirrors.single
        if single:
            self._mirror = single
            return single, None
        name = self.load_record()
        if name:
            self._mirror = name
            return name, None
        with probe_lock(self.SCENE, self.name):
            # another thread may have probed the key while this one waited
            name = self.load_record()
            if name:
                self._mirror = name
                return name, None
            return self.probe(path)

    def probe(self, path, exclude=()):
        """
        Probe one path: race the candidates, record the winner and mark the
        path probed, see AsyncProbe.probe().

        Args:
            path (str): Path of the request to probe
            exclude (Iterable[str]): Candidates to skip (the mirror whose
                content failed is excluded). Defaults to ()

        Returns:
            tuple[str, Any]: Name and payload of the winner

        Raises:
            AllMirrorsFailedError: If no candidate of any group is usable
        """
        self.mark_probed(path)
        name, payload = self.run(path, exclude=exclude)
        self.save_record(name)
        self._mirror = name
        return name, payload

    def run(self, path, exclude=()):
        """
        Race the candidates of every group for one path on the shared thread
        pool and return the first usable one, see AsyncProbe.run() for the
        semantics. The losing probes are not cancelled (a blocking request
        cannot be interrupted from outside): they finish in their own time and
        their results are dropped.

        Args:
            path (str): Path of the request the candidates are probed for
            exclude (Iterable[str]): Candidate names to skip. Defaults to ()

        Returns:
            tuple[str, Any]: Name and payload of the winner

        Raises:
            AllMirrorsFailedError: If no candidate of any group is usable, the
                message carries every failure
        """
        names = [name for members in self.mirrors.groups.values() for name in members]
        logger.info(f'Probing {self.SCENE}/{self.name} for "{path}": {", ".join(names)}')
        race = _Race(len(self.mirrors.groups))
        exclude = set(exclude)
        for members in self.mirrors.groups.values():
            THREAD_POOL.start_thread_soon(self._probe_group, members, path, exclude, race)
        race.decided.wait()
        if not race.winner:
            raise AllMirrorsFailedError('; '.join(race.failures))
        name, payload = race.winner[0]
        logger.attr('Mirror', name)
        return name, payload

    def _probe_group(self, members, path, exclude, race):
        """
        Probe the members of one group in order, the thread body of run().

        Args:
            members (dict[str, str]): {name: url} of the group in the declared
                order
            path (str): Path of the request the members are probed for
            exclude (set[str]): Candidate names to skip
            race (_Race): Shared state of the race, appended under its lock
        """
        reasons = []
        for name, url in members.items():
            if name in exclude:
                # excluded by the caller: the content of this mirror did not
                # serve the flow, asking it again would not help
                reasons.append(f'{name}: excluded')
                continue
            start = perf_counter()
            try:
                payload = self.probe_function(name, url, path)
            except Exception as e:
                # one candidate must never hang the probe: every failure makes
                # it unusable and the group moves on
                logger.warning(f'Mirror "{name}" is not usable: {type(e).__name__}: {e}')
                reasons.append(f'{name}: {type(e).__name__}: {e}')
                continue
            logger.info(f'Mirror "{name}" responded in {(perf_counter() - start) * 1000:.0f}ms')
            race.win(name, payload)
            return
        race.exhaust('; '.join(reasons))


class SyncHttpProbe(SyncProbe):
    """
    The probe of the HTTP domains for synchronous callers: the blocking twin of
    AsyncHttpProbe, over HttpClient (see its module note for the tools of the
    sync side, e.g. the pack server).
    """

    # the status codes that mean the server does not support the HEAD method
    # (405 Method Not Allowed / 501 Not Implemented): the candidate is probed
    # with a one-byte range GET instead, see head()
    HEAD_UNSUPPORTED = (405, 501)

    def __init__(self, mirrors, name='', client=None):
        """
        Args:
            mirrors (Mirrors | str | dict): The candidate structure, see
                Mirrors.from_input() for the accepted forms
            name (str): Identity of this candidate set inside its scene.
                Defaults to ''
            client (httpx2.Client | HttpClient, optional): Client to send the
                requests with, or an HttpClient whose client is taken. Its
                lifetime belongs to the caller (this class never closes an
                injected client). Defaults to None, a client of this instance is
                created on the first request and close() closes it
        """
        super().__init__(mirrors, name=name)
        self._http = HttpClient(client)

    def close(self):
        """
        Close the http client of this instance when it created one, see
        HttpClient.close(). An injected client is never closed.
        """
        self._http.close()

    def probe_function(self, name, url, path):
        """
        Probe one candidate for one path: a HEAD, see
        AsyncHttpProbe.probe_function().

        Args:
            name (str): Candidate name, for the logs and the errors
            url (str): Base url of the candidate
            path (str): Path of the request to probe

        Returns:
            None: The probe fetched nothing, the flow requests the path itself

        Raises:
            httpx2.HTTPError: If the candidate does not answer 2xx
        """
        self.head(url, path)
        return None

    def request(self, name, url, path):
        """
        Make the plain request of a flow on one mirror: GET the path, see
        AsyncHttpProbe.request().

        Args:
            name (str): Mirror name the request goes to, for the logs
            url (str): Base url of that mirror
            path (str): Path of the request

        Returns:
            bytes: The body of the response

        Raises:
            httpx2.HTTPError: If the request fails
        """
        return self.get_bytes(url, path)

    def head(self, url, path, headers=None):
        """
        HEAD a path of one mirror: the default probe request of a path, see
        AsyncHttpProbe.head() for the semantics and the status codes.

        A server that does not support the HEAD method (405 / 501) is probed
        with a one-byte range GET instead: the status of that answer is the
        availability of the path, and its body is never read (see _drop_body).

        Args:
            url (str): Base url of the mirror
            path (str): Path of the request to probe
            headers (dict, optional): Request headers

        Raises:
            httpx2.HTTPError: If the mirror does not serve the path
        """
        import httpx2
        try:
            self._http.head(
                f'{url}{path}', headers=headers, timeout=HttpClient.PROBE_TIMEOUT)
            return
        except httpx2.HTTPStatusError as e:
            if e.response.status_code not in self.HEAD_UNSUPPORTED:
                raise
        # the server does not support HEAD (405 / 501): a one-byte range GET is
        # the smallest real request, its status is the availability. The request
        # runs outside the except block: the block keeps the handling of the
        # status error only
        self._http.stream(
            f'{url}{path}', self._drop_body,
            headers={**(headers or {}), 'Range': 'bytes=0-0'},
            timeout=HttpClient.PROBE_TIMEOUT)

    @staticmethod
    def _drop_body(response):
        """
        The consumer of a probe that reads no body: the status of the response
        is the whole answer (the client classified it before the consumer), the
        transfer is dropped with the open response.

        Args:
            response (httpx2.Response): The open response to drop
        """

    def get_bytes(self, url, path, headers=None, timeout=None):
        """
        Get the body of one path on one mirror: the real request of a flow,
        see AsyncHttpProbe.get_bytes() for the semantics (a 200 of a request
        carrying a Range header raises RangeUnsupportedError before the body
        is read).

        Args:
            url (str): Base url of the mirror
            path (str): Path of the request
            headers (dict, optional): Request headers, e.g. the range
            timeout (float | httpx2.Timeout, optional): Request timeout, the
                default timeout of the http client when not given

        Returns:
            bytes: The body of the response

        Raises:
            httpx2.HTTPError: If the request fails
            RangeUnsupportedError: If the mirror ignored a range request
        """
        collected = []

        def consume(response):
            if headers is not None and 'Range' in headers and response.status_code == 200:
                # a request that carries a Range header must be answered with
                # the range: a 200 means the server ignored it
                raise RangeUnsupportedError(
                    f'The mirror does not support range requests: {response.url}')
            # the buffer is local until the transfer completes: a failed
            # attempt must leave nothing behind for the retry
            chunks = list(response.iter_bytes())
            collected.clear()
            collected.extend(chunks)

        self._http.stream(f'{url}{path}', consume, headers=headers, timeout=timeout)
        return b''.join(collected)
