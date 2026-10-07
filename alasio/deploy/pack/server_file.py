import trio
from msgspec import Struct

from alasio.deploy.httpclient.probe import AllMirrorsFailedError, ProbeBase
from alasio.deploy.pack.decode_base import PackDecodeError
from alasio.deploy.pack.server_url import ServerUrl
from alasio.ext.algorithm.vint import decode_vint
from alasio.ext.cache import cached_class_property
from alasio.logger import logger

# httpx2 is imported lazily in the places that use it: the package resolves
# its own version with importlib.metadata at import time, which the in-memory
# filesystem of the tests cannot answer


class LatestInfo(Struct):
    """
    Latest version and the checksum of its index pack, read from
    latest.pack.

    The checksum is the trailing digest of the pack format, not a checksum
    of the whole pack file: the index checksum of the version, the trailing
    20 bytes of the index section of its index pack (the front part of its
    full pack), the same digest the client compares its local
    .pack/index.pack against, see ServerFile.
    """
    # latest version, e.g. the commit sha1 string
    version: str
    # sha1 checksum of the index pack of that version, hex string
    checksum: str

    @classmethod
    def parse(cls, data):
        """
        Parse the content of a latest.pack file.

        The content is the version in bytes, followed by the 20 bytes sha1
        checksum of the pack. It is the layout ServerFile.get_latest_info()
        requests and the reverse of PackEncodeBase.latest_pack().

        Args:
            data (bytes): Content of latest.pack

        Returns:
            LatestInfo: The parsed version and checksum

        Raises:
            PackDecodeError: If the content is not longer than the 20 bytes
                checksum
        """
        if len(data) <= 20:
            raise PackDecodeError(
                f'Failed to read latest.pack: {len(data)} bytes, expected version + 20 bytes checksum'
            )
        # the version in bytes, then the 20 bytes checksum of the pack
        return cls(
            version=data[:-20].decode('utf-8', errors='replace'),
            checksum=data[-20:].hex(),
        )


class ServerFile(ProbeBase):
    """
    Async HTTP client of the update server, downloads pack files with
    range requests.

    The server urls are a ServerUrl mirror set: every request resolves
    the mirror first (the only mirror, the name recorded in gui.db, or
    the winner of a probe, see _resolve) and is sent to that mirror.
    probe() selects the mirror: the concurrency skeleton is ProbeBase
    (constructed with the groups, run() executes), this class
    implements probe_function() (fetch and parse latest.pack) and
    records the winner. The payload of the probe is handed over, so a
    probe never costs an extra request when the flow needs latest.pack
    anyway. The probe of an instance runs at most once: a later
    failure raises instead of racing again, see _reselect. A data
    request is retried on a transport error, see _http_get. Every
    request of an instance uses the same async http client - the
    injected one, or one created on the first request and reused, see
    _get_aclient - and aclose() closes a client the instance created.
    `async with ServerFile(...) as server:` closes it on exit.

    Every method of the class is async: the requests run on the event
    loop of the caller (trio), so a cancelled task interrupts an
    in-flight request immediately instead of waiting for its timeout.
    Blocking helpers of the callers (decoding a downloaded pack,
    writing files) run in their worker threads, never here.

    The server layout follows the draft in PackEncodeBase:
    - {mirror}/latest.pack: latest version in bytes, followed by the
      20 bytes sha1 checksum of the index pack of that version
    - {mirror}/{version}/full.pack: full pack of a version, the front
      part of it is the index pack of that version
    - {mirror}/{new_version}/from_{old_version}.pack: update pack
      from the old version to the new version

    get_index_pack() downloads the index section with two range
    requests: the header plus the index section length first, then
    the exact range of the index pack.
    """

    # bytes to request first for the header: the pack header plus the
    # index section length vint (at most 8 bytes for a int64 length)
    HEADER_REQUEST_SIZE = 64
    # budget of one latest.pack request in seconds (a probe candidate
    # and the latest.pack of a flow): latest.pack is a small file, an
    # answer slower than the budget is not usable (the default timeout
    # of httpx2.AsyncClient is the same)
    PROBE_TIMEOUT = 5.0

    # timeout of a pack file request: the connection budget stays short
    # (a mirror that is blocked in the user network is dropped
    # quickly), the read/write budget is longer than latest.pack: the
    # range of one file may be large, a slow transfer must not be
    # taken for a stall. Built on the first access, so httpx2 stays
    # lazily imported, see the module note
    @cached_class_property
    def DOWNLOAD_TIMEOUT(cls):
        """
        Returns:
            httpx2.Timeout: Timeout of a pack file request
        """
        import httpx2
        return httpx2.Timeout(connect=cls.PROBE_TIMEOUT, read=30.0, write=30.0, pool=cls.PROBE_TIMEOUT)

    # attempts of one data request: a transport error is retried, up to
    # three attempts (a one-off network blip should not fail the update
    # nor trigger a probe), the probe candidates stay single-shot, see
    # _http_get
    REQUEST_ATTEMPTS = 3
    # seconds to wait before the retry of a data request
    RETRY_BACKOFF = 0.5

    def __init__(self, mirrors, scope='', client=None):
        """
        Args:
            mirrors (ServerUrl | Mirrors | str | dict): Mirror set to
                select from and to fetch the packs from. A ServerUrl
                is used as-is; anything else is wrapped into a
                ServerUrl, see ServerUrl.__init__ and
                Mirrors.from_input() for the accepted forms
            scope (str): Scope of the gui.db record of the built
                ServerUrl, so independent update servers do not
                overwrite each other. Ignored when mirrors is already
                a ServerUrl (its own scope is used). Defaults to ''
            client (httpx2.AsyncClient, optional): Client to use, its
                lifetime belongs to the caller (this class never
                closes an injected client). Defaults to None, a
                client of this instance is created on the first
                request and reused by every later one, aclose()
                closes it
        """
        if not isinstance(mirrors, ServerUrl):
            mirrors = ServerUrl(mirrors, scope=scope)
        # the probe engine of this server, its mirrors are the mirror
        # structure, see ProbeBase (only run() executes it)
        super().__init__(mirrors.mirrors)
        self.server_url = mirrors
        # the http client of this instance: the injected one, or None
        # until the first request creates the client of the instance
        # (the created client is reused, see _get_aclient())
        self._client = client
        # whether the client belongs to this instance: a created one
        # is closed by aclose(), an injected one is left alone
        self._own_client = client is None
        # mirror resolved for this instance, the flow it serves; ''
        # until resolved, see _resolve()
        self._mirror = ''
        # the payload of the succeeded probe of this instance,
        # (name, LatestInfo); None until a probe succeeds. probe()
        # returns it directly on a later access, the probe itself does
        # not run again, and a later failure raises instead of racing
        # again, see _reselect
        self._probe_result = None
        # failure of a failed probe of this instance, re-raised by a
        # later probe attempt; None until a probe fails, see probe()
        self._probe_error = None

    async def aclose(self):
        """
        Close the http client of this instance when it created one.

        An injected client is never closed: its lifetime belongs to
        the caller, see __init__. The client created here is closed
        with its keep-alive connections; a closed instance must not
        send another request (the next request raises). Closing is
        idempotent, `async with ServerFile(...) as server:` closes it
        on exit. Do not close while a request is in flight.
        """
        client = self._client
        if self._own_client and client is not None:
            await client.aclose()

    async def __aenter__(self):
        """
        Returns:
            ServerFile: The instance itself, for the async with
                statement
        """
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        """
        Close the client of this instance on exit, see aclose().
        """
        await self.aclose()

    async def probe(self):
        """
        Probe the mirrors and select the first usable one, recording it
        in gui.db.

        The concurrency skeleton is ProbeBase.run(): the groups
        (ServerUrl.groups) are connectivity paths and run in parallel
        (the earliest group with a usable member wins), the members of
        a group are tried in their declared order (the first usable
        one wins, the order is a preference). probe_function() probes
        one mirror: a candidate is usable when its latest.pack can be
        fetched and parsed (LatestInfo).

        The probe of an instance runs at most once: a succeeded probe
        is recorded in _probe_result and a failed one in _probe_error,
        a later access does not race again - probing again in the same
        flow is meaningless, the network environment does not change
        between its requests (see _reselect).

        The winner is recorded (ServerUrl.set_name) and its payload is
        returned, the caller that needs latest.pack does not request
        it again: the probe request is the prefetch.

        Returns:
            tuple[str, LatestInfo]: The selected mirror name and its
                latest.pack

        Raises:
            AllMirrorsFailedError: If no candidate of any group is
                usable, the message carries every failure
        """
        if self._probe_error is not None:
            # the probe already failed on this instance: re-raise the
            # recorded failure, racing again is meaningless
            raise self._probe_error
        if self._probe_result is not None:
            # the probe already succeeded on this instance: the cached
            # payload is returned, the probe does not race again
            return self._probe_result
        try:
            name, info = await super().run()
        except AllMirrorsFailedError as e:
            # record the failure: the probe of this instance must not
            # race again, see _reselect
            self._probe_error = e
            raise
        self._probe_result = (name, info)
        self.server_url.set_name(name)
        return self._probe_result

    async def probe_function(self, name, url):
        """
        Probe one mirror: fetch and parse its latest.pack.

        Args:
            name (str): Mirror name, for the logs
            url (str): Base url of the mirror

        Returns:
            LatestInfo: The latest version and index pack checksum

        Raises:
            PackDecodeError: If the response is shorter than the
                20 bytes checksum
            httpx2.HTTPError: If the request fails
        """
        return await self._fetch_latest_at(url)

    async def _fetch_latest_at(self, url, attempts=1):
        """
        Fetch and parse latest.pack of a base url.

        Args:
            url (str): Base url of a mirror
            attempts (int): Attempts of the request, the retry of a
                data request lives in _http_get. Defaults to 1, the
                probe candidates stay single-shot

        Returns:
            LatestInfo: The latest version and index pack checksum

        Raises:
            PackDecodeError: If the response is shorter than the
                20 bytes checksum
            httpx2.HTTPError: If the request fails
        """
        response = await self._http_get(f'{url}/latest.pack', timeout=self.PROBE_TIMEOUT, attempts=attempts)
        return LatestInfo.parse(response.content)

    async def _resolve(self):
        """
        Resolve the mirror of this instance, probing only when needed.

        The resolution order: the mirror already resolved, the only
        mirror of a single mirror set (no selection to make), the name
        recorded in gui.db (a hint of the machine network
        environment), then a probe (the last resort).

        Returns:
            tuple[str, LatestInfo | None]: The mirror name and the
                latest.pack payload of a probe that just ran, None
                when no probe was needed; the payload is the prefetch
                of the caller that needs it, see probe()
        """
        if self._mirror:
            return self._mirror, None
        single = self.server_url.single
        if single:
            self._mirror = single
            return single, None
        name = self.server_url.name
        if name:
            self._mirror = name
            return name, None
        name, info = await self.probe()
        self._mirror = name
        return name, info

    async def _reselect(self, failure):
        """
        Reselect the mirror after the resolved one failed, probing only
        when the probe of this instance has not run yet.

        The probe of an instance runs at most once (see probe): when it
        already ran, the selection reflects the current network
        environment and probing again is meaningless - the environment
        does not change between the requests of one flow - so the
        failure at hand is raised as-is. A probe that failed is
        recorded, its error is raised as-is too instead of racing
        again. The fresh probe never consults the recorded name, it is
        the failed one; it overwrites the record with the winner. When
        every mirror fails the record keeps the old name: a total
        failure usually means the machine is offline, not that the
        record is wrong.

        Args:
            failure (Exception): Failure of the resolved mirror, raised
                as-is when the probe of this instance already ran

        Returns:
            tuple[str, LatestInfo]: The newly selected mirror and its
                latest.pack

        Raises:
            Exception: The failure at hand, when the probe of this
                instance already ran
            AllMirrorsFailedError: If the probe finds no usable mirror
        """
        if self._probe_error is not None:
            # the probe of this instance already failed, no mirror was
            # usable: racing again is meaningless
            raise self._probe_error
        if self._probe_result is not None:
            # the mirror was selected by the probe of this instance:
            # probing again would select the same mirror
            raise failure
        logger.warning(f'Mirror "{self._mirror}" failed: {failure}, probing again')
        name, info = await self.probe()
        self._mirror = name
        return name, info

    @staticmethod
    def _mirror_failed(e):
        """
        Whether a failed request of a pack file means the mirror is
        not usable instead of being the answer of the server.

        A failure without a status code means the mirror never answered
        - dns lookup failure, connect refusal, ssl handshake failure,
        timeout, read/write error (e.g. a mirror that is blocked in the
        user network raises a connect error or a timeout, there is no
        status code at all) - another mirror may serve the file, it is
        a mirror failure. With a status code, only the server side
        errors (5xx, 429) are mirror failures; a 4xx is the content
        answer, another mirror would answer the same: a missing update
        pack falls back to a rebuild, a missing file stays in error.

        Args:
            e (httpx2.HTTPError): The request failure

        Returns:
            bool: True if the mirror should be reselected
        """
        import httpx2
        if isinstance(e, httpx2.HTTPStatusError):
            status = e.response.status_code
            return status >= 500 or status == 429
        return True

    async def _request(self, path, headers=None):
        """
        Get a path of the resolved mirror, reselecting the mirror when
        it fails.

        The mirror is resolved before the request (see _resolve). A
        transport error is retried on the same mirror first (see
        _http_get) and the request uses DOWNLOAD_TIMEOUT (see the
        class attribute). A failure that means the mirror is not
        usable (see _mirror_failed) drops it, reselects the mirror
        (see _reselect: the probe of the instance runs at most once)
        and retries the same request once on the new winner; a failure
        of the retry is raised as-is. The pack format validates every
        downloaded byte against its checksum, so a retry on another
        mirror cannot corrupt the update.

        Args:
            path (str): Path of the request, e.g. '/{version}/full.pack'
            headers (dict, optional): Request headers

        Returns:
            httpx2.Response: The response

        Raises:
            httpx2.HTTPError: If the request fails on every tried mirror
            AllMirrorsFailedError: If the reselection finds no usable
                mirror
        """
        import httpx2
        name, _ = await self._resolve()
        try:
            return await self._http_get(
                f'{self.server_url.url_of(name)}{path}', headers,
                timeout=self.DOWNLOAD_TIMEOUT, attempts=self.REQUEST_ATTEMPTS)
        except httpx2.HTTPError as e:
            if self.server_url.single or not self._mirror_failed(e):
                raise
            failure = e
        # the mirror is not usable: reselect (a probe runs only when
        # this instance has not probed yet) and retry the same request
        # once on the new winner, a failure of the retry is raised
        # as-is
        name, _ = await self._reselect(failure)
        return await self._http_get(
            f'{self.server_url.url_of(name)}{path}', headers,
            timeout=self.DOWNLOAD_TIMEOUT, attempts=self.REQUEST_ATTEMPTS)

    async def get_latest_info(self):
        """
        Get the latest version and the checksum of its index pack from
        {mirror}/latest.pack.

        The mirror is resolved before the request (see _resolve). A
        probe that just ran already fetched latest.pack, its payload
        is returned directly without a second request; the payload is
        not cached, a later call fetches again. A transport error is
        retried before the mirror is considered failed (see
        _http_get).

        A failed request reselects the mirror (see _reselect): when the
        probe of this instance has not run yet, the payload of the new
        probe is the retry; once it has run, the failure is raised
        as-is - probing again in the same flow is meaningless, the
        network environment does not change between its requests. A
        total failure raises and the recorded name is kept as-is (a
        machine that is offline should not lose its hint).

        Returns:
            LatestInfo: The latest version and the index pack checksum

        Raises:
            PackDecodeError: If the response is shorter than the
                20 bytes checksum
            httpx2.HTTPStatusError: If the request fails
            AllMirrorsFailedError: If no mirror is usable
        """
        import httpx2
        name, info = await self._resolve()
        if info is not None:
            return info
        try:
            return await self._fetch_latest_at(self.server_url.url_of(name), attempts=self.REQUEST_ATTEMPTS)
        except (httpx2.HTTPError, PackDecodeError) as e:
            if self.server_url.single:
                # nothing to reselect, the failure is the answer
                raise
            failure = e
        # the mirror failed: reselect, the payload of a new probe is
        # the retry (see _reselect)
        _, info = await self._reselect(failure)
        return info

    async def get_file_content(self, version, offset, size):
        """
        Get a range of the full pack of a version from
        {mirror}/{version}/full.pack with an http range request.

        The mirror is resolved (and reselected when it fails) before
        the request, see _request.

        Args:
            version (str): Version to query
            offset (int): Start offset of the range
            size (int): Length of the range

        Returns:
            bytes: File content of the range

        Raises:
            httpx2.HTTPStatusError: If the request fails
            AllMirrorsFailedError: If no mirror is usable
        """
        headers = {'Range': f'bytes={offset}-{offset + size - 1}'}
        response = await self._request(f'/{version}/full.pack', headers)
        # a 200 response means the server ignored the range request,
        # slice the full content then
        if response.status_code == 200:
            return response.content[offset:offset + size]
        return response.content

    async def get_index_pack(self, version):
        """
        Get the index pack of a version from
        {mirror}/{version}/full.pack.

        The index pack is the front part of the full pack: the header
        plus the index section. The section length includes the
        trailing 20 bytes checksum, so the downloaded index pack is
        complete and self-validating with PackDecodeBase. Two range
        requests are made, as planned in PackEncodeBase:
        1. range 0~63, the header and the index section length
        2. range 0 ~ len(header) + len(index section), the index pack

        Args:
            version (str): Version to query

        Returns:
            bytes: Index pack of the version

        Raises:
            PackDecodeError: If the index section length vint is not
                terminated inside the header response
            httpx2.HTTPStatusError: If a request fails
        """
        header = await self.get_file_content(version, 0, self.HEADER_REQUEST_SIZE)
        try:
            # decode_vint raises on a truncated stream (a high byte at the
            # end of the header response), the vint is always terminated here
            length, read = decode_vint(header[5:])
        except ValueError as e:
            raise PackDecodeError(f'Failed to decode index section length: {e}') from e
        # the index pack is the header plus the index section, including
        # the length vint itself
        return await self.get_file_content(version, 0, 5 + read + length)

    async def get_update_pack(self, old_version, new_version):
        """
        Get the update pack from an old version to a new version from
        {mirror}/{new_version}/from_{old_version}.pack.

        The mirror is resolved (and reselected when it fails) before
        the request, see _request. A missing update pack (a 404) is
        the answer of the server and is raised as-is: another mirror
        would answer the same, the caller falls back to a rebuild.

        Args:
            old_version (str): Version of the local files
            new_version (str): Version to update to

        Returns:
            bytes: Update pack data

        Raises:
            httpx2.HTTPStatusError: If the request fails
            AllMirrorsFailedError: If no mirror is usable
        """
        response = await self._request(f'/{new_version}/from_{old_version}.pack')
        return response.content

    def _get_aclient(self):
        """
        The async http client of this instance: the injected one, or
        the client of the instance created on the first request.

        The client is reused by every request of the instance, so the
        keep-alive connections of a flow are reused instead of a new
        connection pool per request. Only a client created here is
        closed by aclose(); an injected one belongs to the caller. No
        lock guards the lazy creation: every task of a flow runs on
        the same thread (the single thread invariant of async code),
        only await points switch tasks and the creation below has no
        await, so nothing races on it - the concurrent probe tasks of
        the first request all observe the same client.

        Returns:
            httpx2.AsyncClient: The client to send the request with
        """
        import httpx2
        if self._client is None:
            self._client = httpx2.AsyncClient()
        return self._client

    async def _http_get(self, url, headers=None, timeout=None, attempts=1):
        """
        Get a url with the client of this instance (the injected one
        or the reusable one created on the first request, see
        _get_aclient()).

        A transport error (dns lookup failure, connect refusal, ssl
        handshake failure, timeout, read/write error - there is no
        status code at all) is retried when more than one attempt is
        requested: a one-off network blip should not fail the update,
        nor be taken for a mirror failure, nor trigger a probe. An
        http status error is never retried here, it is the answer of
        the server and is classified by the caller (see
        _mirror_failed). Every failed attempt is logged with a
        warning, whatever the failure is: an absorbed blip stays
        visible in the log and exhausted retries read as the sequence
        of attempts. The wait before a retry is a trio sleep, so the
        cancellation of the enclosing task interrupts it (like it
        interrupts the in-flight request itself).

        Args:
            url (str): URL to get
            headers (dict, optional): Request headers
            timeout (float, optional): Request timeout in seconds,
                the client default when not given
            attempts (int): Attempts of the request, the retry waits
                RETRY_BACKOFF seconds. Defaults to 1

        Returns:
            httpx2.Response: The response

        Raises:
            httpx2.HTTPStatusError: If the request fails
        """
        import httpx2

        # the timeout is only passed when set: None disables the
        # timeout of httpx instead of using its default
        kwargs = {'timeout': timeout} if timeout is not None else {}
        client = self._get_aclient()
        for attempt in range(attempts):
            if attempt:
                # a transport error is often a one-off blip of the
                # network: wait a moment, then retry the same request
                await trio.sleep(self.RETRY_BACKOFF)
            try:
                response = await client.get(url, headers=headers, **kwargs)
                response.raise_for_status()
                return response
            except httpx2.TransportError as e:
                # a transport error is logged, and retried while an
                # attempt is left: a one-off network blip should not
                # fail the update
                logger.warning(
                    f'Request to "{url}" failed (attempt {attempt + 1}/{attempts}): {type(e).__name__}: {e}')
                if attempt + 1 >= attempts:
                    raise
            except Exception as e:
                # every other failure (an http status error, ...) is
                # logged and raised as-is, it is never retried
                logger.warning(
                    f'Request to "{url}" failed (attempt {attempt + 1}/{attempts}): {type(e).__name__}: {e}')
                raise
