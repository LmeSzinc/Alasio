"""
The async flavour of the mirror probes: the trio race (AsyncProbe) and its HTTP
twin (AsyncHttpProbe).

The flow is documented by probe_base.ProbeBase, the identity of a probe
(mirrors, name, SCENE) and the record calls are shared; this module owns the
concurrency (a trio nursery per probe), the probe lock of the process
(probe_lock) and the HTTP transfer of the deploy domain.

The async flavour is the one of the trio callers: the backend update checks and
the deploy jobs (ServerFile, PypiFile). A cancelled task interrupts an in-flight
request, the local phases of a flow are awaited on the event loop of the caller.

httpx2 is imported lazily in the places that use it: the package resolves its
own version with importlib.metadata at import time, which the in-memory
filesystem of the tests cannot answer.
"""
import threading
from time import perf_counter

import trio

from alasio.deploy.httpclient.client_async import AsyncHttpClient
from alasio.deploy.httpclient.probe_base import ProbeBase
from alasio.deploy.httpclient.probe_mirrors import AllMirrorsFailedError, RangeUnsupportedError
from alasio.logger import logger

# {(scene, name): trio.Lock}: the probe of one candidate set runs at most once
# at a time inside this process. The keys are bounded by the scenes times the
# set names of the process (tens of an installation), the locks live for the
# life of the process and are never collected.
_probe_locks = {}
# guards the get-or-create of _probe_locks: two flows that find no lock for the
# same key at the same moment would otherwise create one lock each, and their
# probes would run beside each other. A plain threading lock on purpose: it is
# held for the two dict operations only, never while probing, and the caller
# needs the lock synchronously (it acquires it with `async with`)
_create_lock = threading.Lock()


def probe_lock(scene, name):
    """
    The in-process lock of one (scene, name) key: the lock itself, held with
    `async with probe_lock(...):`.

    Two flows of the process (two mods, a check and an update, a dep channel of
    the same mod) may find no usable record at the same moment. The lock
    serializes their probes, and the second one reads the record again after
    acquiring it: exactly one probe per key runs, the other flow reuses the
    winner it wrote (see AsyncProbe.resolve). It only guards the probe of the
    key (a few seconds of network IO), never the whole flow: the resolved
    mirror is cached by the instance afterwards, the requests of a flow do not
    take the lock.

    The lock is per process on purpose: the record is a hint and the worst case
    of two processes probing at once is a few extra requests, a database lock
    would guard nothing (see probe_db).

    Args:
        scene (str): Usage domain of the candidate set, e.g. 'repo'
        name (str): Identity of the set inside the scene, '' allowed

    Returns:
        trio.Lock: The lock of the key, not acquired yet
    """
    key = (scene, name)
    with _create_lock:
        lock = _probe_locks.get(key, None)
        if lock is None:
            lock = _probe_locks[key] = trio.Lock()
    return lock


class AsyncProbe(ProbeBase):
    """
    The async flavour of a mirror probe: the trio race and the resolution flow
    (probe_base documents the flow, this class implements it).

    Attributes:
        FAILURES (tuple): The failure types of the domain that mean "this
            mirror cannot serve the request". The flavour adds the HTTP
            failures on top (failures()); Exception is never added on purpose,
            a coding error (KeyError, ...) must propagate instead of being read
            as a failure of the mirror.
    """

    # failure types of the domain, e.g. ServerFile.FAILURES = (PackDecodeError,)
    FAILURES = ()

    def failures(self):
        """
        The failure types classified by this flavour: the HTTP failures of the
        deploy domain and the FAILURES of the domain.

        A failure of one of them makes the instance probe the path once and
        retry the request on the winner; every other exception (a coding error)
        propagates as-is.

        Returns:
            tuple[type, ...]: The exception types of a mirror failure
        """
        import httpx2
        return (httpx2.HTTPError, RangeUnsupportedError, *self.FAILURES)

    async def fetch(self, path, work=None):
        """
        Serve one path: resolve the mirror, run the work of the flow on it,
        and re-probe the path once when the mirror cannot serve it.

        The payload of the probe is the answer of the flow when the probe was
        the request itself (a small path, see probe_function): the probe
        fetched and validated it, the flow consumes it and makes no request of
        its own. When the probe fetched nothing (a HEAD), the work of the flow
        runs: the caller's own, or the plain request of the flavour. When the
        work fails, the instance probes the same path once, unless it already
        probed that path (one probe per path per instance), and runs the work
        once more on the winner - or takes the payload of the new probe when it
        was the request itself again; a second failure is raised as-is. With no
        candidate able to serve the path, the original failure is raised as-is,
        so the caller keeps its own fallback (see DeployJob.update()).

        Args:
            path (str): Path of the request
            work (Callable, optional): The async work of the flow on one
                mirror, `await work(name, url)`. Defaults to None, the plain
                request of the flavour (request()); a flow that needs more than
                it (the ranges of the index pack, the validation of a file
                record) passes its own work, so the reselection and the retry
                cover it too (see ServerFile.get_index_pack).

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
        name, payload = await self.resolve(path)
        if payload is not None:
            # the probe was the request itself: its payload is the answer of
            # the flow, no request of its own is made
            return payload
        try:
            return await self.work(name, path, work)
        except self.failures() as e:
            failure = e
        if self.mirrors.single or self.was_probed(path):
            # nothing to select from, or the path was already probed by this
            # instance: the failure is the answer
            raise failure
        try:
            async with probe_lock(self.SCENE, self.name):
                name, payload = await self.probe(path, exclude=self._exclude(failure, name))
        except AllMirrorsFailedError:
            # no candidate can serve the path: the original failure is the
            # answer, the caller keeps its own fallback
            raise failure
        if payload is not None:
            # the probe was the request itself again: its payload is the retry
            return payload
        return await self.work(name, path, work)

    async def work(self, name, path, work=None):
        """
        Run the work of a flow on one mirror: the caller's own, or the plain
        request of the flavour.

        Args:
            name (str): Mirror name to work on
            path (str): Path of the request
            work (Callable, optional): `await work(name, url)`. Defaults to
                None, the plain request of the flavour (request())

        Returns:
            Any: The payload of the work
        """
        if work is None:
            return await self.request(name, self.mirrors.url_of(name), path)
        return await work(name, self.mirrors.url_of(name))

    def _exclude(self, failure, name):
        """
        The mirrors to skip when a failed path is probed again.

        A content failure (the domain's own: the bytes are not usable) does
        not get better by asking the same mirror again, so the mirror that
        failed is excluded and the probe makes progress (every retry tries
        another candidate). A transport or status failure may be a blip: the
        mirror stays a candidate.

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

    async def resolve(self, path):
        """
        Resolve the mirror of this instance for one path, probing only when
        needed.

        The order: the mirror already resolved by this instance, the only
        mirror of a single mirror set (no selection to make, the record is
        skipped), the record of the machine (probe_db), and only then a probe
        of the path itself. The probe runs under probe_lock (one probe per
        key in this process) and reads the record again inside the lock: a flow
        that waited for the lock reuses the winner the other one just recorded.
        The winner is recorded and every later request of the instance goes to
        it (optimistic reuse), see the docstring of the class.

        Args:
            path (str): Path of the first request of the flow; the probe
                proves that a candidate can serve it

        Returns:
            tuple[str, Any]: The mirror name, and the payload of the probe
                (None when it fetched nothing, see probe_function)

        Raises:
            AllMirrorsFailedError: If no candidate of any group is usable
        """
        if self._mirror:
            return self._mirror, None
        single = self.mirrors.single
        if single:
            # a single mirror set has nothing to select, and no record to read
            self._mirror = single
            return single, None
        name = await trio.to_thread.run_sync(self.load_record)
        if name:
            self._mirror = name
            return name, None
        async with probe_lock(self.SCENE, self.name):
            # another flow of the process may have probed the key while this
            # one waited for the lock: the record it wrote is the answer
            name = await trio.to_thread.run_sync(self.load_record)
            if name:
                self._mirror = name
                return name, None
            return await self.probe(path)

    async def probe(self, path, exclude=()):
        """
        Probe one path: race the candidates of every group, record the winner
        and mark the path probed (one probe per path per instance).

        The caller brings the path to the probe: the candidates are probed
        with it (the real request for a small path, its HEAD otherwise, see
        probe_function), so a mirror is only selected when it can serve what
        the flow needs.

        Args:
            path (str): Path of the request to probe
            exclude (Iterable[str]): Candidates to skip (the mirror whose
                content failed is excluded, see _exclude). Defaults to ()

        Returns:
            tuple[str, Any]: Name and payload of the winner; the payload is
                what probe_function() returned (None for a HEAD probe)

        Raises:
            AllMirrorsFailedError: If no candidate of any group is usable
        """
        self.mark_probed(path)
        name, payload = await self.run(path, exclude=exclude)
        await trio.to_thread.run_sync(self.save_record, name)
        self._mirror = name
        return name, payload

    async def run(self, path, exclude=()):
        """
        Race the candidates of every group for one path and return the first
        usable one (the concurrency skeleton, see ProbeBase.probe_function()).

        Groups are connectivity paths and run in parallel (one task per group
        in a trio nursery): the earliest group with a usable member wins, the
        fastest reachable path is selected. The members of a group are probed
        in their declared order, the first usable one wins: the order is a
        preference (e.g. a free mirror before a metered one), a failed member
        falls through to the next one. Losing groups are cancelled as soon as a
        winner is found, instead of running to their timeouts.

        Result collection is plain lists, no channel or queue: every task runs
        on the thread of the nursery, only await points switch tasks, and the
        synchronous code between them has no race (the single thread invariant
        of async code).

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
        exclude = set(exclude)
        winner = []
        failures = []
        decided = trio.Event()
        async with trio.open_nursery() as nursery:
            for members in self.mirrors.groups.values():
                nursery.start_soon(
                    self._probe_group, members, path, exclude, winner, failures, decided)
            # wait for the verdict of a group: a winner or the last exhausted
            # group; every other exit is a cancellation
            await decided.wait()
            if winner:
                # the losing groups are cancelled instead of running to their
                # timeouts, their results are dropped
                nursery.cancel_scope.cancel()
        if not winner:
            raise AllMirrorsFailedError('; '.join(failures))
        name, payload = winner[0]
        logger.attr('Mirror', name)
        return name, payload

    async def _probe_group(self, members, path, exclude, winner, failures, decided):
        """
        Probe the members of one group in order and record the outcome of the
        group.

        The outcome goes into the shared lists directly: the tasks run on one
        thread, only await points switch them, so no channel or lock is needed.

        Args:
            members (dict[str, str]): {name: url} of the group in the declared
                order
            path (str): Path of the request the members are probed for
            exclude (set[str]): Candidate names to skip
            winner (list): Appended with (name, payload) when a member is
                usable, the first append wins the race
            failures (list): Appended with the failure summary of the group when
                every member is exhausted
            decided (trio.Event): Set when the probe has a verdict: a winner was
                recorded, or every group was exhausted
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
                payload = await self.probe_function(name, url, path)
            except Exception as e:
                # one candidate must never hang the probe: every failure (http
                # error, malformed payload, anything a broken candidate raises)
                # makes it unusable and the group moves on. trio.Cancelled is a
                # BaseException, it is never caught here and always propagates
                logger.warning(f'Mirror "{name}" is not usable: {type(e).__name__}: {e}')
                reasons.append(f'{name}: {type(e).__name__}: {e}')
                continue
            logger.info(f'Mirror "{name}" responded in {(perf_counter() - start) * 1000:.0f}ms')
            winner.append((name, payload))
            decided.set()
            return
        failures.append('; '.join(reasons))
        if len(failures) == len(self.mirrors.groups):
            # every group is exhausted, no usable candidate
            decided.set()


class AsyncHttpProbe(AsyncProbe):
    """
    The probe of the HTTP domains (pack, pypi): the HTTP probe request of a
    path and the transfer helpers of a flow.

    The default probe of a path is a HEAD: a HEAD learns whether the mirror
    serves the path without transferring it, the flow makes its own request
    afterwards and the validation of the content stays in the domain. A domain
    whose path is short and whose content decides whether the mirror is usable
    at all overrides probe_function() and returns the fetched payload, so the
    flow consumes it instead of requesting the path again (ServerFile and its
    latest.pack).

    The client of the instance is the injected AsyncHttpClient, or one created
    on the first request and reused by every later one; aclose() closes a
    client the instance created, an injected one belongs to the caller.
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
            name (str): Identity of this candidate set inside its scene,
                Defaults to ''
            client (httpx2.AsyncClient | AsyncHttpClient, optional): Client to
                send the requests with, or an AsyncHttpClient whose client is
                taken (see AsyncHttpClient). Its lifetime belongs to the caller
                (this class never closes an injected client). Defaults to None,
                a client of this instance is created on the first request and
                aclose() closes it
        """
        super().__init__(mirrors, name=name)
        # the retrying http client of this instance: the injected http client
        # wrapped, or one that creates its own on the first request
        self._http = AsyncHttpClient(client)

    async def aclose(self):
        """
        Close the http client of this instance when it created one, see
        AsyncHttpClient.aclose(). An injected client is never closed.
        """
        await self._http.aclose()

    async def probe_function(self, name, url, path):
        """
        Probe one candidate for one path: a HEAD.

        The probe answers whether the candidate serves the path (a 2xx, see
        head()) and fetches nothing: its payload is None and the flow requests
        the path itself. A domain whose path is short and must be validated on
        the spot overrides this method, fetches the path and returns its
        payload (ServerFile and its latest.pack), which the flow consumes
        instead of requesting the path again.

        Args:
            name (str): Candidate name, for the logs and the errors
            url (str): Base url of the candidate
            path (str): Path of the request to probe

        Returns:
            None: The probe fetched nothing, the flow requests the path itself

        Raises:
            httpx2.HTTPError: If the candidate does not answer 2xx
        """
        await self.head(url, path)
        return None

    async def request(self, name, url, path):
        """
        Make the plain request of a flow on one mirror: GET the path.

        The default work of the HTTP flavours, used by a flow that passes no
        work of its own (see AsyncProbe.fetch): the bytes of the path. A flow
        that needs more (a range, a parse, a validation) passes its own work.

        Args:
            name (str): Mirror name the request goes to, for the logs
            url (str): Base url of that mirror
            path (str): Path of the request

        Returns:
            bytes: The body of the response

        Raises:
            httpx2.HTTPError: If the request fails
        """
        return await self.get_bytes(url, path)

    async def head(self, url, path, headers=None):
        """
        HEAD a path of one mirror: the default probe request of a path.

        The status of the answer decides: a 2xx means the candidate serves the
        path, any other status means it does not (the status error is raised,
        the race moves on to the next candidate). A server that does not
        support the HEAD method (405 / 501) is probed with a one-byte range GET
        instead: the status of that answer is the availability of the path, and
        its body is never read (a server that ignores the range would otherwise
        stream the whole file, see _drop_body).

        The request keeps the retry policy of the http client (a transport
        error is retried: a one-off blip of the network must not discard a
        candidate) and uses the short budget of the domain (PROBE_TIMEOUT): an
        answer slower than the budget is not usable.

        Args:
            url (str): Base url of the mirror
            path (str): Path of the request to probe
            headers (dict, optional): Request headers

        Raises:
            httpx2.HTTPError: If the mirror does not serve the path: a
                transport error of every attempt, or the status error of the
                answer
        """
        import httpx2
        try:
            await self._http.head(
                f'{url}{path}', headers=headers, timeout=AsyncHttpClient.PROBE_TIMEOUT)
            return
        except httpx2.HTTPStatusError as e:
            if e.response.status_code not in self.HEAD_UNSUPPORTED:
                raise
        # the server does not support HEAD (405 / 501): a one-byte range GET is
        # the smallest real request, its status is the availability. The request
        # runs outside the except block: the block keeps the handling of the
        # status error only
        await self._http.stream(
            f'{url}{path}', self._drop_body,
            headers={**(headers or {}), 'Range': 'bytes=0-0'},
            timeout=AsyncHttpClient.PROBE_TIMEOUT)

    @staticmethod
    async def _drop_body(response):
        """
        The consumer of a probe that reads no body: the status of the response
        is the whole answer (the client classified it before the consumer), the
        transfer is dropped with the open response.

        Args:
            response (httpx2.Response): The open response to drop
        """

    async def get_bytes(self, url, path, headers=None, timeout=None):
        """
        Get the body of one path on one mirror: the real request of the flow.

        A request that carries a Range header must be answered with the range
        (206): a 200 means the server ignored the range (the whole pack would
        be transferred for every file of it), so the mirror is not usable
        (RangeUnsupportedError) and its body is never read - the consumer
        raises before reading, the open response is dropped with the attempt.
        A transport error is retried by the http client (see
        AsyncHttpClient.request), every attempt restarts the transfer and the
        buffer of a failed attempt is dropped with it.

        Args:
            url (str): Base url of the mirror
            path (str): Path of the request
            headers (dict, optional): Request headers, e.g. the range
            timeout (float | httpx2.Timeout, optional): Request timeout, the
                default timeout of the http client when not given (a range of
                a pack may be large, a slow transfer must not be taken for a
                stall). The short probe budget is passed for a small path
                (latest.pack), see ServerFile

        Returns:
            bytes: The body of the response

        Raises:
            httpx2.HTTPError: If the request fails
            RangeUnsupportedError: If the mirror ignored a range request
        """
        collected = []

        async def consume(response):
            if headers is not None and 'Range' in headers and response.status_code == 200:
                # a request that carries a Range header must be answered with
                # the range: a 200 means the server ignored it
                raise RangeUnsupportedError(
                    f'The mirror does not support range requests: {response.url}')
            # the buffer is local until the transfer completes: a failed
            # attempt must leave nothing behind for the retry
            chunks = [chunk async for chunk in response.aiter_bytes()]
            collected.clear()
            collected.extend(chunks)

        await self._http.stream(f'{url}{path}', consume, headers=headers, timeout=timeout)
        return b''.join(collected)
