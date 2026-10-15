"""
Async HTTP client of the update server, downloads pack files with range
requests.

The client of one mirror set (Mirrors): every request resolves the mirror first
- the only mirror, the record of the machine (probe_db), or a probe of the
path itself when no record is usable (the flow is documented by
probe_base.ProbeBase, the concurrency and the probe lock by probe_async) - and
is sent to that mirror. The instance serves one flow: the mirror it resolved is
cached, so a caller builds a new instance per check / per deploy job instead of
keeping one for the process; warmup() reads the record before the first request
(the read is blocking sqlite IO, it runs in the trio worker thread).

The server layout follows the draft in PackEncodeBase:

- {mirror}/latest.pack: latest version in bytes, followed by the 20 bytes sha1
  checksum of the index pack of that version
- {mirror}/{version}/full.pack: full pack of a version, the front part of it is
  the index pack of that version
- {mirror}/{new_version}/from_{old_version}.pack: update pack from the old
  version to the new version

The public methods return the business types of the pack format and validate
what they return (see each method): a payload the flow cannot use is a failure
of the mirror - another mirror may serve a better copy - so the mirror is
reselect and the request is retried once (see AsyncProbe.fetch()), and the
callers (the deploy jobs) never parse the same bytes a second time. A pack file
is fetched with range requests: a mirror that does not support ranges is not
usable (RangeUnsupportedError, see probe_mirrors), the whole pack would be
transferred for every file of it.

httpx2 is imported lazily in the places that use it: the package resolves its
own version with importlib.metadata at import time, which the in-memory
filesystem of the tests cannot answer.
"""
import trio
from msgspec import Struct

from alasio.deploy.httpclient.client_async import AsyncHttpClient
from alasio.deploy.httpclient.probe_async import AsyncHttpProbe
from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.ext.algorithm.vint import decode_vint


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


class ServerFile(AsyncHttpProbe):
    """
    Async HTTP client of the update server: the pack API of one mirror set.

    The mirror selection (the record, the probe of a path, the reselection
    after a failure) is the probe layer (probe_base / probe_async); this class
    owns the pack format: the paths of the flow, the range requests and the
    validation of what the server serves.

    Attributes:
        SCENE (str): The usage domain of the pack channels ('repo')
        FAILURES (tuple): Payload failures that mean the mirror cannot serve
            the flow (a pack that does not decode or validate)
    """

    # the usage domain of the pack channels in the record of the machine
    SCENE = 'repo'
    # a payload that does not decode or does not validate is a failure of the
    # mirror, not of the flow: the mirror is reselected and the request is
    # retried once
    FAILURES = (PackDecodeError,)

    # the path of the latest.pack file: the only path whose probe is the
    # request itself (the file is short and its content decides whether the
    # mirror is usable at all), see probe_function()
    LATEST_PATH = '/latest.pack'

    # bytes to request first for the header: the pack header plus the
    # index section length vint (at most 8 bytes for a int64 length)
    HEADER_REQUEST_SIZE = 64

    def __init__(self, mirrors, name='', client=None):
        """
        Args:
            mirrors (Mirrors | str | dict): Mirror set to select from and to
                fetch the packs from, see Mirrors.from_input() for the accepted
                forms
            name (str): Identity of this update server in the record of the
                machine, so independent mirror sets (the channels of the mods)
                do not overwrite each other: the name of the mod, '' for the
                project tree. Defaults to ''
            client (httpx2.AsyncClient | AsyncHttpClient, optional): Client to
                send the requests with, or an AsyncHttpClient whose client is
                taken (independent servers can share one client, see
                AsyncHttpClient). Its lifetime belongs to the caller (this
                class never closes an injected client). Defaults to None, a
                client of this instance is created on the first request and
                reused by every later one, aclose() closes it
        """
        super().__init__(mirrors, name=name, client=client)

    async def warmup(self):
        """
        Resolve the record of the machine before the first request.

        Only the record read is warmed up: it is blocking sqlite IO (the mirror
        selected by an earlier flow) and the first request would otherwise pay
        it on the event loop. The probe is left to the first request on purpose:
        a probe hands its payload (latest.pack) over to the request that needs
        it, warming it up here would waste that prefetch.

        Idempotent: an instance whose mirror was already resolved does nothing
        (the resolution of an instance runs at most once).
        """
        if self._mirror or self.mirrors.single:
            return
        name = await trio.to_thread.run_sync(self.load_record)
        if name:
            self._mirror = name

    # ------------------------------------------------------------------
    # The probe of a path
    # ------------------------------------------------------------------

    async def probe_function(self, name, url, path):
        """
        Probe one candidate for one path: the request itself for latest.pack,
        a HEAD for every other path.

        latest.pack is short and its content decides whether the mirror is
        usable at all: the probe is the real request, the body is parsed here
        to prove the mirror serves a usable one (a mirror that serves a broken
        latest.pack never wins the race) and the parsed object is returned as
        the payload of the flow, which consumes it instead of requesting the
        path again (no second request, no second parse).

        Args:
            name (str): Candidate name, for the logs and the errors
            url (str): Base url of the candidate
            path (str): Path of the request to probe

        Returns:
            LatestInfo | None: The parsed latest.pack, None for a HEAD probe

        Raises:
            PackDecodeError: If latest.pack does not parse
            httpx2.HTTPError: If the candidate does not answer 2xx
        """
        if path != self.LATEST_PATH:
            return await super().probe_function(name, url, path)
        body = await self.get_bytes(url, path, timeout=AsyncHttpClient.PROBE_TIMEOUT)
        return LatestInfo.parse(body)

    # ------------------------------------------------------------------
    # The pack API of a flow
    # ------------------------------------------------------------------

    async def get_latest_info(self):
        """
        Get the latest version and the checksum of its index pack from
        {mirror}/latest.pack.

        latest.pack is the one path whose probe is the request itself (see
        probe_function()): when no record served this instance, the probe
        fetched the body, parsed it to prove the mirror serves a usable one and
        returned it as the payload of the flow, which consumes it without a
        request of its own (the work below runs only when the probe fetched
        nothing); otherwise the work fetches and parses it. Either way the flow
        makes one request, and the payload is parsed once (the probe of a
        broken latest.pack raises inside the race, the candidate is dropped).

        Returns:
            LatestInfo: The latest version and the index pack checksum

        Raises:
            PackDecodeError: If the response does not parse (the mirror is
                reselected and the request is retried once)
            httpx2.HTTPError: If the request fails
            AllMirrorsFailedError: If no mirror is usable
        """
        async def work(name, url):
            body = await self.get_bytes(
                url, self.LATEST_PATH, timeout=AsyncHttpClient.PROBE_TIMEOUT)
            return LatestInfo.parse(body)

        return await self.fetch(self.LATEST_PATH, work)

    async def get_index_pack(self, version, checksum=''):
        """
        Get the index pack of a version from {mirror}/{version}/full.pack.

        The index pack is the front part of the full pack: the header plus the
        index section. The section length includes the trailing 20 bytes
        checksum, so the downloaded index pack is complete and self-validating
        with PackDecodeBase. Two range requests are made, as planned in
        PackEncodeBase:

        1. range 0~63, the header and the index section length
        2. range 0 ~ len(header) + len(index section), the index pack

        The returned decoder is validated (validate_index()) and, when a
        checksum is given, its index checksum must match it (the checksum
        latest.pack advertised): a mirror that serves an index pack its own
        latest.pack does not advertise cannot be used, it is reselected and the
        download is retried once on the winner. When no mirror has a good
        copy the failure is raised as-is (the flow cannot converge, see
        DeployJob.update()).

        Args:
            version (str): Version to query
            checksum (str, optional): Expected index pack checksum, hex string,
                the one get_latest_info() returned. Defaults to '', no extra
                check

        Returns:
            PackDecodeBase: Decoder of the index pack, validated

        Raises:
            PackDecodeError: If the header does not decode, the pack does not
                validate, or the checksum mismatches
            httpx2.HTTPError: If a request fails
            AllMirrorsFailedError: If no mirror is usable
        """
        path = f'/{version}/full.pack'

        async def work(name, url):
            return await self._download_index_pack(url, path, checksum)

        return await self.fetch(path, work)

    async def _download_index_pack(self, url, path, checksum):
        """
        The two range requests of an index pack, assembled and validated: the
        work of get_index_pack() on one mirror.

        Args:
            url (str): Base url of the mirror
            path (str): Path of the full pack
            checksum (str): Expected index pack checksum, '' for no check

        Returns:
            PackDecodeBase: Decoder of the index pack, validated

        Raises:
            PackDecodeError: If the header does not decode, the pack does not
                validate, or the checksum mismatches
            httpx2.HTTPError: If a request fails
        """
        header = await self.get_bytes(
            url, path, headers={'Range': f'bytes=0-{self.HEADER_REQUEST_SIZE - 1}'})
        try:
            # decode_vint raises on a truncated stream (a high byte at the
            # end of the header response), the vint is always terminated here
            length, read = decode_vint(header[5:])
        except ValueError as e:
            raise PackDecodeError(f'Failed to decode index section length: {e}') from e
        # the index pack is the header plus the index section, including
        # the length vint itself
        data = await self.get_bytes(
            url, path, headers={'Range': f'bytes=0-{5 + read + length - 1}'})
        decoder = PackDecodeBase(data)
        decoder.validate_index()
        if checksum and decoder.index_checksum != checksum:
            raise PackDecodeError(
                f'Failed to download the index pack: checksum mismatch, '
                f'expected {checksum}, got {decoder.index_checksum}')
        return decoder

    async def get_update_pack(self, old_version, new_version):
        """
        Get the update pack from an old version to a new version from
        {mirror}/{new_version}/from_{old_version}.pack.

        The pack is validated before it is returned (validate()): the checksums
        of both sections of the format are checked, a mirror that serves a
        broken update pack is dropped and the download is retried once on
        another one - the flow applies the validity of the pack next, it
        must not decode it a second time. A missing update pack (a 404) is the
        answer of the server and is raised as-is: another mirror would answer
        the same, the caller falls back to a rebuild (see DeployJob.update()).

        Args:
            old_version (str): Version of the local files
            new_version (str): Version to update to

        Returns:
            PackDecodeBase: Decoder of the update pack, validated

        Raises:
            PackDecodeError: If the pack does not decode or validate
            httpx2.HTTPError: If the request fails
            AllMirrorsFailedError: If no mirror is usable
        """
        path = f'/{new_version}/from_{old_version}.pack'

        async def work(name, url):
            data = await self.get_bytes(url, path)
            decoder = PackDecodeBase(data)
            decoder.validate()
            return decoder

        return await self.fetch(path, work)

    async def get_update_pack_checksum(self, old_version, new_version, size):
        """
        Get the whole-pack checksum of the update pack from
        {mirror}/{new_version}/from_{old_version}.pack.

        The pack format keeps a 20 bytes digest as the trailing bytes of the
        pack file, the sha1 of everything before it (PackDecodeBase validates it
        as the data section checksum): equal digests mean the two files are
        byte-identical. The caller compares it with the digest of the pack it
        kept from an interrupted run, the server's word that the kept pack is
        still the one it serves, see DeployJob._get_update_pack. One range
        request is made, for the last 20 bytes of the pack: `size` is the size
        of the kept pack, the range is a hint, a size that does not match the
        served file only makes the digests differ (the caller downloads the pack
        as usual).

        Args:
            old_version (str): Version of the local files
            new_version (str): Version to update to
            size (int): Size of the pack kept by the caller

        Returns:
            str: The checksum, hex string

        Raises:
            httpx2.HTTPError: If the request fails; a 416 (the kept pack is
                larger than the served one) is raised as-is and the caller
                downloads the pack as usual
            AllMirrorsFailedError: If no mirror is usable
        """
        path = f'/{new_version}/from_{old_version}.pack'

        async def work(name, url):
            headers = {'Range': f'bytes={size - 20}-{size - 1}'}
            body = await self.get_bytes(url, path, headers=headers)
            return body.hex()

        return await self.fetch(path, work)

    async def get_file_data(self, decoder, info):
        """
        Get the content of one file record from the full pack of its version:
        the range of the record, decoded and validated against the record.

        The record of an index pack carries the offset, the length, the
        compression and the size + sha1 of the file: the range is fetched, the
        content is decompressed, checked against the record and converted to the
        checkout line ending, so the caller writes the working tree bytes as
        they are. A content that does not match the record is a failure of the
        mirror: the mirror is reselected and the download is retried once;
        when no mirror serves a good copy the failure is raised as-is and the
        caller keeps the record in error (see ResetJob.download()). The call is
        for the records of a full pack (a reset / rebuild and the leftover files
        of an update); the records of an update pack are applied from the pack
        by UpdateJob.

        Args:
            decoder (PackDecodeBase): Decoder of the index pack the record
                belongs to (it carries the version of the full pack)
            info (IdxInfo): Record of the file to download

        Returns:
            bytes: Working tree content of the file, validated against the
                record

        Raises:
            PackDecodeError: If the content does not match the record
            httpx2.HTTPError: If the request fails
            AllMirrorsFailedError: If no mirror is usable
        """
        if not info.data_size:
            # a record without data (an empty file): no range to request
            return b''
        path = f'/{decoder.current_version}/full.pack'

        async def work(name, url):
            headers = {'Range': f'bytes={info.data_start}-{info.data_start + info.data_size - 1}'}
            data = await self.get_bytes(url, path, headers=headers)
            return decoder.decode_content(info, data)

        return await self.fetch(path, work)
