"""
The metadata of a distribution version, read once per run and shared.

Two tools of the developer side ask the same question of an index: which
dependencies does a pinned version declare, and which wheels does it publish.
:class:`~alasio.deploy_dev.pack_deps.PackDepsManager` asks it to decide the pack
channel of a mod, the compiler will ask it to resolve a lock. Both read the same
pages and the same METADATA files, so the answers are read here, once, and kept
in memory for the rest of the process.

The provider is the one place the four ways of reading a METADATA are chosen
(see :meth:`MetadataProvider.metadata`):

- the PEP 658 metadata file next to a wheel, one request of a few KB;
- the METADATA member of the wheel, read by range requests: the tail of the
  archive gives the central directory, the entry gives the member, and the
  whole wheel is only read when neither works (alasio.deploy_dev.metadata.zip_reader);
- the whole wheel in memory, the fallback of an index and a host that serve no
  range.

Nothing is written to the disk. The caches are the attributes of one provider,
so the reuse is the run: a page or a METADATA asked twice costs no request, and
a name reached by two branches of a closure is requested once (see
:meth:`MetadataProvider.metadata`, which merges the concurrent callers of one
key). A later run reads the index again, which is the price of not keeping a
cache that would have to be invalidated.

The concurrency of the provider is the concurrency of the requests it sends,
not of the distributions it reads: the limiter wraps every request, so a
version that needs three requests does not hold three places of the budget
while it parses. The first round runs alone (one request at a time) until the
capabilities of a source are known, then the budget opens to the configured
concurrency: a burst of requests that all discover the same missing feature
would otherwise waste one request each (see :meth:`MetadataProvider.capabilities`).

Usage:
    from alasio.deploy_dev.metadata import MetadataProvider

    provider = MetadataProvider(client)
    meta = await provider.metadata('httpx2', '2.12.0', 'https://pypi.org/simple')
    meta.requires_dist            # ['httpcore2==2.4.0', 'h11>=0.16', ...]
    await provider.aclose()
"""

import zipfile
from typing import Dict, List

import httpx2
import trio
from msgspec import Struct, field
from packaging.utils import canonicalize_name
from yarl import URL

from alasio.deploy.httpclient.client_async import AsyncHttpClient
from alasio.deploy.pip_download._wheel import is_pure_wheel
from alasio.deploy.pip_download.pypi_file import DOWNLOAD_MODE_ALL, PypiFile, WheelNotFoundError, select_wheels
from alasio.deploy_dev.metadata.requirements import requirement_names, requires_dist, wheel_metadata
from alasio.deploy_dev.metadata.sdist import is_sdist, read_sdist_metadata, sdist_name_of
from alasio.deploy_dev.metadata.zip_reader import (
    LOCAL_HEADER_SIZE, TAIL_SIZE, WINDOW_PADDING, ZipLayoutError, central_directory, content_range, decompress_member,
    end_of_central_directory, find_metadata, local_data_offset
)
from alasio.logger import logger

__all__ = [
    'DEFAULT_CONCURRENCY',
    'DEFAULT_INDEX',
    'MODE_FULL',
    'MODE_PEP658',
    'MODE_RANGE',
    'MODE_RANGE_NO_SUFFIX',
    'MODE_SDIST',
    'ClosureResult',
    'ClosureRoot',
    'DistributionMetadata',
    'MetadataError',
    'MetadataProvider',
    'SourceCapabilities',
    'readable_wheels',
    'sdist_of',
]

# The index a distribution is read from when the caller names none: the
# official PyPI simple index, the value of RepoBase.DEFAULT_INDEX
DEFAULT_INDEX = 'https://pypi.org/simple'

# Requests in flight once the capabilities of the sources are known: a version
# costs 1 (PEP 658) to 3 (range) small requests, and the graph is walked ahead
# of the decisions (see Resolver._speculate), so the budget is what bounds a
# compile. Measured on the reference repo (86 pins, 297 requests through a
# proxy): 22s at 16, 17-20s at 32, 15-22s at 48, 26s at 64 - the proxy answers
# slower once a burst outruns it, and 32 keeps the tail of the run short
# without one. uv downloads at 50, which is for whole wheels
DEFAULT_CONCURRENCY = 32

# How a METADATA was read, kept on the answer: the evidence of the cost of a run
MODE_PEP658 = 'pep658'
MODE_RANGE = 'range'
MODE_RANGE_NO_SUFFIX = 'range-explicit'
MODE_FULL = 'full'
# the metadata of a version that publishes no wheel, read from its sdist
MODE_SDIST = 'sdist'

# PEP 658 is declared by the link of a wheel, not by the index, so the answer of
# a file that is not there means "this wheel serves no metadata", not "this
# source does not"
_PEP658_MISSING = (403, 404, 405)

# Statuses with which a host refuses a range request: 501 is the S3 host of a
# GitHub release asset answering a suffix range, 416 is a span past the end of
# the file. They are the answer of this file, so the version falls back to
# another way of reading it (see MetadataProvider._read_by_range)
_RANGE_REFUSED = (416, 501)

# The statuses that say something about the host rather than about this file: a
# 501 refuses the shape of the request, while a 416 may be one odd archive. Only
# the first corrects a declared capability (see MetadataProvider._demote)
_RANGE_HOST_REFUSED = (501,)


class MetadataError(ValueError):
    """
    Raised when the metadata of a distribution version cannot be read.

    The message names the distribution, the version and the reason; a caller
    that catches it keeps the closure incomplete rather than guessing.
    """


class _ModeUnavailable(Exception):
    """
    Raised inside the provider when one way of reading a METADATA does not
    apply to this file.

    The modes are tried from the cheapest to the dearest (see
    MetadataProvider.metadata): this error is the signal to try the next one,
    it never leaves the provider.

    Attributes:
        refused (bool): True when the host refused the shape of the request
            rather than the bytes of this file being unusable. A refusal is a
            fact about the host, so it corrects a declared capability; a layout
            that does not hold says nothing about the next file of the source
    """

    def __init__(self, reason='', refused=False):
        """
        Args:
            reason (str): Why the mode does not apply. Defaults to ''
            refused (bool): True when the host refused the request itself.
                Defaults to False
        """
        super().__init__(reason)
        self.refused = refused


class DistributionMetadata(Struct):
    """
    What one version of a distribution declares and where it was read.

    Attributes:
        name (str): PEP 503 normalized name
        version (str): Version the caller asked for
        source (str): Base url of the index the version was read from
        requires_dist (List[str]): The ``Requires-Dist`` values of the METADATA
        origin (str): Url the METADATA was read from, the metadata file of the
            wheel, the wheel itself, or the sdist of a version without a wheel
        mode (str): How it was read, one of the MODE_* constants. MODE_SDIST
            means the version publishes no wheel the target can install: its
            dependencies were read from the static metadata of its sdist
    """
    name: str
    version: str
    source: str
    requires_dist: List[str]
    origin: str
    mode: str


class ClosureRoot(Struct, frozen=True):
    """
    One root of a closure walk: a distribution the caller asks for.

    Attributes:
        name (str): PEP 503 normalized name
        extras (frozenset[str]): Extras the caller asked, the empty extra is
            always expanded
        recursive (bool): True to walk the dependencies of the distribution
    """
    name: str
    extras: frozenset = frozenset()
    recursive: bool = True


class ClosureResult(Struct):
    """
    The answer of one closure walk.

    The walk fills the collections as it goes, so the fields are the empty
    containers of a run and the struct is mutable: the answer of a closure is
    one object with four views, not four answers that have to be zipped
    together.

    Attributes:
        metadata (Dict[str, DistributionMetadata]): The name to the metadata,
            for every name that was expanded
        names (List[str]): Every name the walk reached, in the order it reached
            them, the roots included
        missing (List[str]): The names a dependency asked for and the lock does
            not pin, the closure stops there
        errors (Dict[str, str]): {name: reason} for the names whose metadata
            could not be read: the closure behind them is unknown
    """
    metadata: Dict[str, DistributionMetadata] = field(default_factory=dict)
    names: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    errors: Dict[str, str] = field(default_factory=dict)


class SourceCapabilities:
    """
    What an index is known to serve in this run.

    The three ways of reading a METADATA over range differ by what the host of
    the files supports, and the hosts differ within one run: PyPI serves a
    suffix range, a GitHub release asset does not (its S3 host answers 501 to
    ``bytes=-N`` and 206 to an explicit range). A capability is discovered once
    per source, by one request (see MetadataProvider._probe), and the sources
    are remembered for the run only: the next run probes again, because the
    host of an index may have changed its server.

    ``None`` means "not known" and is read as "try it": a source whose probe
    failed on the network is not degraded, the request that follows fails on
    its own and is classified then.

    Attributes:
        range (bool | None): True when an explicit byte range is served
        suffix_range (bool | None): True when ``bytes=-N`` is served
        ready (bool): True when the capabilities are known, by a probe or by a
            declaration of KNOWN_SOURCES
        assumed (bool): True when the record is a declaration rather than an
            observation: a range the host refuses replaces it
        lock (trio.Lock): The single flight of the probe of the source
    """

    __slots__ = ('assumed', 'lock', 'range', 'ready', 'suffix_range')

    def __init__(self):
        # created on the first use of the source, inside the run of the caller
        self.lock = trio.Lock()
        self.range = None
        self.suffix_range = None
        self.ready = False
        self.assumed = False

    @classmethod
    def known(cls, range_served, suffix_served):
        """
        A record that declares what a host serves, for KNOWN_SOURCES.

        The record is ready and marked as assumed: it is what this project
        believes of the host, not what the run observed
        (see MetadataProvider._demote).

        Args:
            range_served (bool): True when the host serves an explicit range
            suffix_served (bool): True when the host serves ``bytes=-N``

        Returns:
            SourceCapabilities: The record
        """
        state = cls()
        state.range = range_served
        state.suffix_range = suffix_served
        state.ready = True
        state.assumed = True
        return state

    def assume(self, other):
        """
        Merge what another record knows into this one.

        A value the other record knows replaces the one here, a value it does
        not know leaves this one as it is, and the flags are the union of both:
        merging a declaration makes this record assumed, and merging the same
        declaration twice changes nothing (the records are values, not state).

        The lock is never merged: it belongs to the reader of the source, and
        a declaration has no reader.

        Args:
            other (SourceCapabilities): The record to merge
        """
        if other.range is not None:
            self.range = other.range
        if other.suffix_range is not None:
            self.suffix_range = other.suffix_range
        self.ready = self.ready or other.ready
        self.assumed = self.assumed or other.assumed

    def __repr__(self):
        return (
            f'{self.__class__.__name__}(range={self.range}, '
            f'suffix_range={self.suffix_range}, ready={self.ready}, '
            f'assumed={self.assumed})')


class _Flight:
    """
    One metadata or page being read, the waiters of the same key meet here.

    Attributes:
        done (trio.Event): Set when the reader finished, success or not
        error (BaseException | None): What the reader raised, None on success
    """

    __slots__ = ('done', 'error')

    def __init__(self):
        self.done = trio.Event()
        self.error = None


def readable_wheels(links, name, version):
    """
    The wheels of a version, the ones a METADATA is read from first.

    The order is the cost of the read: not yanked before yanked (PEP 592 lets a
    pinned version keep its yanked files, which is why they are kept at all), a
    pure wheel before a platform build (it is the file the pack channel builds
    from, and the smallest), then the filename, so the answer is deterministic
    between runs.

    Args:
        links (Iterable[Link]): Links of the index page of the distribution
        name (str): Distribution name, any case and separator style
        version (str): Exact version of the distribution

    Returns:
        list[WheelCandidate]: The wheels of the version, cheapest to read first

    Raises:
        WheelNotFoundError: If the page lists no wheel of the version
    """
    candidates = select_wheels(links, name, version, DOWNLOAD_MODE_ALL)
    return sorted(candidates, key=lambda candidate: (
        candidate.link.is_yanked,
        not is_pure_wheel(candidate.wheel.file_tags),
        candidate.filename,
    ))


# What the hosts a compile of this project reads are declared to serve, so a
# run does not spend a request finding out. {host: SourceCapabilities}
#
# A declaration is not an observation: a range the host refuses replaces it (see
# MetadataProvider._demote), so a stale entry costs one request of the run and
# never a wrong answer. A host that is not here is probed as before.
#
# The records are shared by every run and are never mutated: bootstrap() merges
# a copy into the record of the run (see SourceCapabilities.assume).
#
# Every entry below was measured: the page of a distribution, then a suffix
# range and a one byte range against a pure wheel of it, on five packages of
# 30 KiB to 19 MiB, checking that the suffix answer is the tail of the file and
# that no redirect hid another host behind the index. A mirror measured as
# refusing a range is not listed as "no range": a 403 says the network path
# refused the request, not the mirror, and an entry that declared it would
# turn a route problem into a wrong capability. A host that is not listed is
# probed once per run, which is what a run without this table does.
KNOWN_SOURCES = {
    # the official index serves its files from files.pythonhosted.org, which
    # answers both shapes of a range
    'pypi.org': SourceCapabilities.known(range_served=True, suffix_served=True),
    'files.pythonhosted.org': SourceCapabilities.known(range_served=True, suffix_served=True),
    # the backport index publishes GitHub release assets: their host answers
    # 501 to a suffix range and 206 to an explicit one
    'py38deps.github.io': SourceCapabilities.known(range_served=True, suffix_served=False),
    'github.com': SourceCapabilities.known(range_served=True, suffix_served=False),
    # the mirrors of the cn group of ModEntry (DEFAULT_PYPI_MIRRORS) and the
    # public ones a mod may declare: each serves the file of the page itself,
    # both shapes of a range, and no PEP 658 metadata next to it, so the range
    # path is the one a compile of these indexes reads by
    'mirrors.aliyun.com': SourceCapabilities.known(range_served=True, suffix_served=True),
    'mirrors.cloud.tencent.com': SourceCapabilities.known(range_served=True, suffix_served=True),
    'mirrors.ustc.edu.cn': SourceCapabilities.known(range_served=True, suffix_served=True),
    'repo.huaweicloud.com': SourceCapabilities.known(range_served=True, suffix_served=True),
    'mirror.nju.edu.cn': SourceCapabilities.known(range_served=True, suffix_served=True),
}

# The mirrors a run may read that are deliberately not above: pypi.tuna.tsinghua
# and mirrors.bfsu refused every file request of the survey (a 403 on five
# packages, whatever the User-Agent and Referer), and mirror.sjtu serves some
# packages from its own host and redirects the others to tsinghua, so no single
# pair of flags describes it. A run on those sources probes once, which is
# correct whatever their answer actually is.


def known_source(source):
    """
    What a host of an index is declared to serve, see KNOWN_SOURCES.

    Args:
        source (str): Base url of an index, e.g. 'https://pypi.org/simple'

    Returns:
        SourceCapabilities | None: The declared record of the host, None when
            the host is not one this project declares
    """
    host = URL(source).host
    if not host:
        return None
    return KNOWN_SOURCES.get(host.lower())


def sdist_of(links, name, version):
    """
    The source distribution of one version of a distribution.

    Args:
        links (Iterable[Link]): Links of the index page of the distribution
        name (str): Distribution name, any case and separator style
        version (str): Exact version of the distribution

    Returns:
        Link | None: The link of the sdist, None when the version publishes
            none
    """
    dist = canonicalize_name(name)
    for link in links:
        if not is_sdist(link.filename):
            continue
        parsed = sdist_name_of(link.filename)
        if parsed is None:
            continue
        if canonicalize_name(parsed[0]) != dist or parsed[1] != version:
            continue
        return link
    return None


class MetadataProvider:
    """
    Read the index pages and the METADATA files of a run, once each.

    One instance is one run: the caches live in the instance and die with it,
    nothing is written to the disk (see the module docstring). The client is
    injected, so the route and the lifetime of the connections belong to the
    caller, which is also the one that closes it.

    Attributes:
        concurrency (int): Requests in flight once the capabilities are known
        dest (str): Folder a fetcher would write a wheel into; the provider
            reads every file in memory, so nothing is written and the folder is
            only handed to the page fetcher
    """

    def __init__(self, client=None, concurrency=DEFAULT_CONCURRENCY, dest=''):
        """
        Args:
            client (httpx2.AsyncClient | AsyncHttpClient | None): Client the
                requests are sent with. Defaults to None, a client of this
                provider, closed by aclose()
            concurrency (int): Requests in flight once the capabilities of the
                sources are known. Defaults to DEFAULT_CONCURRENCY
            dest (str): Folder the page fetcher may write into, unused by the
                provider itself. Defaults to ''
        """
        self._client = AsyncHttpClient(client)
        self.concurrency = max(int(concurrency), 1)
        self.dest = dest

        # the answers of the run, keyed by what identifies them
        # {(source, name): PackageIndex}
        self._pages = {}
        # {(source, name, version): DistributionMetadata}
        self._metadata = {}
        # {key: _Flight}, a read in progress and the callers waiting for it
        self._flights = {}
        # {(source, name, version): {mode: reason}}, what did not apply to a version
        self._notes = {}
        # {source: SourceCapabilities}, what an index is known to serve
        self._capabilities = {}
        # {source: PypiFile}, the page fetcher of an index
        self._fetchers = {}

        # the requests in flight: created on the first request, inside the run
        self._limiter = None
        # whether the budget of the run is open, see _open_budget()
        self._budget_open = False

    def __repr__(self):
        return f'{self.__class__.__name__}(concurrency={self.concurrency})'

    def bootstrap(self, sources):
        """
        Adopt what is declared of the sources of a run, before the first request.

        A source whose host is in KNOWN_SOURCES is marked ready with what the
        host serves, so no request is spent probing it. A source that is not
        there stays unknown and is probed by the first caller that needs it,
        which is the behaviour of a run without the table.

        When every source is known the request budget opens at once: the gate
        exists for a run that has to find out whether the route works at all
        (see _open_budget), and a caller that declared its sources is a caller
        that already knows what it is talking to. A run that includes an
        unknown source keeps the gate, because that source is what the first
        request is for.

        Args:
            sources (Iterable[str]): Base urls of the indexes of the run

        Returns:
            bool: True when there is at least one source and every one of them
                was known, False when at least one will be probed
        """
        sources = list(sources)
        known = True
        for source in sources:
            source = source or DEFAULT_INDEX
            state = self._capabilities.get(source)
            if state is not None and state.ready:
                continue
            declared = known_source(source)
            if declared is None:
                known = False
                # a source nobody declared keeps no record: it stays unasked,
                # and capabilities_of() answers None for it
                continue
            if state is None:
                state = self._capabilities[source] = SourceCapabilities()
            state.assume(declared)
        if sources and known:
            self._open_budget()
        return bool(sources) and known

    async def aclose(self):
        """
        Close the http client of this provider when it created one.

        An injected client belongs to the caller and is never closed here.
        """
        await self._client.aclose()

    """
    The answers
    """

    async def page(self, name, source=''):
        """
        The index page of a distribution, read once per run.

        Args:
            name (str): Distribution name, any case and separator style
            source (str): Base url of the index. Defaults to '', DEFAULT_INDEX

        Returns:
            PackageIndex: The page and its links

        Raises:
            WheelNotFoundError: If the index has no page of the distribution
            AllMirrorsFailedError: If no mirror of the index is usable
            httpx2.HTTPError: If a request fails
        """
        source = source or DEFAULT_INDEX
        key = (source, canonicalize_name(name))
        page = self._pages.get(key)
        if page is not None:
            return page
        return await self._single_flight(self._pages, key, lambda: self._read_page(name, source))

    async def metadata(self, name, version, source=''):
        """
        The metadata of one version of a distribution, read once per run.

        The ways of reading it are tried from the cheapest to the dearest, and
        a way that does not apply to this file is remembered for the version,
        not for the source: an index that serves PEP 658 for the new versions
        of a distribution and not for its old ones must not degrade the new
        ones, and one wheel a host refuses to serve by range must not degrade
        the others.

        A failure of the network or of the bytes is not a downgrade: it is
        raised (wrapped in MetadataError) rather than retried on a dearest mode,
        because a wheel that does not match the hash of its link is not made
        right by downloading more of it.

        Args:
            name (str): Distribution name, any case and separator style
            version (str): Exact version of the distribution
            source (str): Base url of the index. Defaults to '', DEFAULT_INDEX

        Returns:
            DistributionMetadata: The metadata of the version

        Raises:
            MetadataError: If no wheel of the version can be read
            WheelNotFoundError: If the index page lists no wheel of the version
            httpx2.HTTPError: If a request fails
        """
        source = source or DEFAULT_INDEX
        key = (source, canonicalize_name(name), version)
        meta = self._metadata.get(key)
        if meta is not None:
            return meta
        return await self._single_flight(
            self._metadata, key, lambda: self._read(key, name, version, source))

    def capabilities_of(self, source):
        """
        What a source is known to serve, without probing it.

        A caller that is about to guess reads this first: a source already known
        to serve no range would answer the guess with a whole file, which is not
        worth reading ahead of a decision. Nothing is requested here, so an
        unprobed source answers None and reads as "try it" (see
        SourceCapabilities).

        Args:
            source (str): Base url of the index. Defaults to '', DEFAULT_INDEX

        Returns:
            SourceCapabilities | None: The record of the source, None when the
                run never asked anything of it
        """
        return self._capabilities.get(source or DEFAULT_INDEX)

    async def closure(self, roots, resolve, target):
        """
        Walk the dependency closure of some roots, every node at once.

        A node spawns its children as soon as its metadata is read, so the
        rounds of requests follow the depth of the dependency chain and not the
        number of distributions at one level: five levels cost five round trips,
        whatever the width. The limiter of the provider decides how many
        requests are in flight, it never delays a node that has its answer.

        A name is expanded once per set of extras: a name reached again with a
        new extra is expanded again for that extra only, because the
        dependencies of an extra are not the ones of the base (``httpx2[http2]``
        pulls ``h2``).

        Args:
            roots (Iterable[ClosureRoot]): The distributions the caller asks for
            resolve (Callable[[str], tuple[str, str] | None]): The pin of a
                name, ``(version, source)``, or None when the lock the caller
                reads does not pin it
            target (TargetPython): The interpreter the markers are evaluated for

        Returns:
            ClosureResult: The metadata of the walk, the names it reached, the
                names the lock does not pin, and the names that could not be read
        """
        result = ClosureResult()
        visited = set()
        expanded = {}

        async def visit(nursery, name, extras, recursive):
            # the empty extra is always expanded: the base dependencies are the
            # ones no marker asks for
            extras = frozenset(extras) | {''}
            if name in visited:
                if not recursive or extras <= expanded.get(name, frozenset()):
                    return
            pin = resolve(name)
            if pin is None:
                # a name the lock does not pin is not going to be installed by
                # this mod, the closure stops there (see the module docstring)
                if name not in result.missing:
                    result.missing.append(name)
                return
            if name not in visited:
                visited.add(name)
                result.names.append(name)
            if not recursive:
                return
            new = extras - expanded.get(name, frozenset())
            if not new:
                return
            expanded[name] = expanded.get(name, frozenset()) | new

            version, source = pin
            try:
                meta = await self.metadata(name, version, source)
            except Exception as e:
                # a name whose metadata is unknown is not a dependency of
                # nothing: the caller refuses the closure, see PackDepsManager
                logger.warning(f'Failed to read the dependencies of "{name}=={version}": {e}')
                result.errors[name] = str(e)
                return
            result.metadata[name] = meta
            for child, wanted in requirement_names(meta.requires_dist, new, target).items():
                nursery.start_soon(visit, nursery, child, wanted, True)

        async with trio.open_nursery() as nursery:
            for root in roots:
                nursery.start_soon(visit, nursery, root.name, root.extras, root.recursive)
        return result

    """
    The ways of reading one METADATA
    """

    async def _read(self, key, name, version, source):
        """
        Read the metadata of one version, trying the ways in order.

        Args:
            key (tuple): (source, name, version), the key of the caches
            name (str): Distribution name
            version (str): Exact version
            source (str): Base url of the index

        Returns:
            DistributionMetadata: The metadata

        Raises:
            MetadataError: If no wheel of the version answers
            WheelNotFoundError: If the page lists no wheel of the version
        """
        page = await self.page(name, source)
        normalized = canonicalize_name(name)
        try:
            candidates = readable_wheels(page.links, name, version)
        except WheelNotFoundError as e:
            # a version that only publishes an sdist: its metadata is read from
            # the archive itself, see the fallback below
            candidates = []
            logger.debug(f'"{name}=={version}" publishes no wheel: {e}')

        # 1. the PEP 658 file of a wheel: a few KB, one request
        for candidate in candidates:
            url = candidate.link.metadata_url
            if not url:
                continue
            try:
                text = await self._read_metadata_file(source, url, candidate.link.metadata_hashes)
            except _ModeUnavailable as e:
                # the wheels of one version declare the same dependencies, so a
                # host that serves no metadata file for one of them serves none
                # for the others: the next wheel is not asked, which is what
                # keeps a broken index from costing one request per platform
                logger.debug(
                    f'The wheel "{candidate.filename}" serves no metadata file: {e}')
                break
            self._open_budget()
            return DistributionMetadata(
                name=normalized, version=version, source=page.url,
                requires_dist=requires_dist(text), origin=url.partition('#')[0],
                mode=MODE_PEP658)

        # 2. the METADATA inside the wheel: the range reader, then the whole file
        capabilities = await self.capabilities(source, candidates)
        reasons = []
        for candidate in candidates:
            try:
                text, mode = await self._read_wheel(key, source, candidate, capabilities)
            except _ModeUnavailable as e:
                reasons.append(str(e))
                continue
            except (ValueError, zipfile.BadZipFile) as e:
                logger.warning(
                    f'Failed to read the metadata of "{candidate.filename}": {e}')
                reasons.append(str(e))
                continue
            self._open_budget()
            return DistributionMetadata(
                name=normalized, version=version, source=page.url,
                requires_dist=requires_dist(text), origin=candidate.link.url_without_fragment,
                mode=mode)

        return await self._read_sdist(
            normalized, version, source, page, '; '.join(dict.fromkeys(reasons)))

    async def _read_sdist(self, name, version, source, page, detail):
        """
        Read the metadata of a version from its source distribution.

        A version that publishes no wheel the target can install is still
        installable: pip builds its sdist. The dependencies are read from the
        archive without building it, see
        alasio.deploy_dev.metadata.sdist, and a version whose sdist declares
        them nowhere is refused: a lock that silently misses the dependencies
        of a distribution is worse than a compile that fails.

        Args:
            name (str): PEP 503 normalized name
            version (str): Exact version
            source (str): Base url of the index
            page (PackageIndex): The page of the distribution
            detail (str): Why the wheels of the version could not be read,
                the tail of the error message

        Returns:
            DistributionMetadata: The metadata, its mode MODE_SDIST

        Raises:
            MetadataError: If the version has no sdist, or its sdist declares
                no static dependency metadata
        """
        link = sdist_of(page.links, name, version)
        if link is None:
            raise MetadataError(
                f'Cannot read the metadata of "{name}=={version}": '
                f'{detail or "no wheel of the version"}')
        content = await self._fetcher_of(source).get_bytes(link.url)
        self._open_budget()
        try:
            requires = read_sdist_metadata(content, link.filename)
        except ValueError as e:
            raise MetadataError(f'Cannot read "{name}=={version}": {e}') from None
        return DistributionMetadata(
            name=name, version=version, source=page.url, requires_dist=requires,
            origin=link.url_without_fragment, mode=MODE_SDIST)

    async def _read_metadata_file(self, source, url, hashes):
        """
        Read the PEP 658 metadata file of a wheel.

        Args:
            source (str): Base url of the index, the owner of the connection
            url (str): Url of the metadata file, the fragment included
            hashes (dict[str, str]): Digests the page declares for it

        Returns:
            str: Content of the METADATA

        Raises:
            _ModeUnavailable: If the file is not served for this wheel
        """
        expected = hashes.get('sha256', '')
        try:
            async with self._limit:
                content = await self._fetcher_of(source).get_metadata(url, expected)
        except httpx2.HTTPStatusError as e:
            if e.response.status_code not in _PEP658_MISSING:
                raise
            raise _ModeUnavailable(f'HTTP {e.response.status_code}') from None
        try:
            return content.decode('utf-8')
        except UnicodeDecodeError as e:
            raise _ModeUnavailable(f'the metadata file is not UTF-8: {e}') from e

    async def _read_wheel(self, key, source, candidate, capabilities):
        """
        Read the METADATA of one wheel, by range when the source serves one.

        Args:
            key (tuple): (source, name, version), the key of the notes
            source (str): Base url of the index
            candidate (WheelCandidate): The wheel to read
            capabilities (SourceCapabilities): What the source is known to serve

        Returns:
            tuple[str, str]: (content of the METADATA, the mode it was read in)

        Raises:
            _ModeUnavailable: If neither the range reader nor the whole wheel
                answers, the next wheel of the version is tried
            ValueError: If the wheel is not a readable archive
            zipfile.BadZipFile: If the bytes are not a zip archive
        """
        link = candidate.link
        url = link.url_without_fragment
        if capabilities.suffix_range is not False and not self._noted(key, MODE_RANGE):
            try:
                return await self._read_by_range(url, suffix=True), MODE_RANGE
            except _ModeUnavailable as e:
                self._note(key, MODE_RANGE, e)
                self._demote(source, suffix=True, refused=e.refused)
        if capabilities.range is not False and not self._noted(key, MODE_RANGE_NO_SUFFIX):
            try:
                return await self._read_by_range(url, suffix=False), MODE_RANGE_NO_SUFFIX
            except _ModeUnavailable as e:
                self._note(key, MODE_RANGE_NO_SUFFIX, e)
                self._demote(source, suffix=False, refused=e.refused)
        self._note(key, MODE_FULL, 'the whole wheel is read')
        # the fetcher checks the bytes against the hash of the link, the only
        # check a whole file can afford
        content = await self._fetcher_of(source).get_bytes(link.url)
        return wheel_metadata(content), MODE_FULL

    async def _read_by_range(self, url, suffix):
        """
        Read the METADATA member of a wheel over range requests.

        The tail of the archive locates the central directory, the entry of the
        member locates its bytes, and the member is read from the tail itself
        when it is in it (the dist-info of a wheel is written last, so it often
        is). The directory and the member that the tail does not hold cost one
        request each.

        Args:
            url (str): Url of the wheel without the fragment
            suffix (bool): True to ask for the tail as a suffix range, which
                needs one request; False to probe the size first, which the
                host of a GitHub release asset needs (it answers 501 to a
                suffix range)

        Returns:
            str: Content of the METADATA

        Raises:
            _ModeUnavailable: If the source or the archive does not answer the
                range requests, or the member is not where the index says
        """
        try:
            total, tail, tail_start = await self._tail(url, suffix)
            central = end_of_central_directory(tail, tail_start)
            if central.offset >= tail_start:
                directory = tail[central.offset - tail_start:]
            else:
                directory = await self._get_range(
                    url, central.offset, central.offset + central.size - 1)
            entry = find_metadata(central_directory(directory, central.count))

            start = entry.header_offset
            span = LOCAL_HEADER_SIZE + WINDOW_PADDING + entry.compress_size
            window = _slice(tail, tail_start, start, start + span)
            if window is None:
                window = await self._get_range(url, start, start + span - 1)
            offset = local_data_offset(window)
            if offset + entry.compress_size > len(window):
                # the filename and the extra field of the local header are
                # longer than the slack: the exact window is one more request
                window = await self._get_range(
                    url, start, start + offset + entry.compress_size - 1)
                offset = local_data_offset(window)
            data = window[offset:offset + entry.compress_size]
            if len(data) != entry.compress_size:
                raise _ModeUnavailable(
                    f'the member "{entry.filename}" is not in the bytes read')
            return decompress_member(data, entry).decode('utf-8')
        except ZipLayoutError as e:
            raise _ModeUnavailable(str(e)) from e
        except UnicodeDecodeError as e:
            raise _ModeUnavailable(f'the METADATA is not UTF-8: {e}') from e
        except httpx2.HTTPStatusError as e:
            # A host may refuse one shape of a range: the S3 host of a GitHub
            # release asset answers 501 to a suffix range and 206 to an
            # explicit one, and a span past the end of the file is a 416. The
            # version is read another way, and the source is not degraded by
            # it: another file of the same index may be served by another host
            # (PEP 503 allows it), which is what a per-version downgrade keeps
            # correct, see the module note of _read_wheel
            if e.response.status_code in _RANGE_REFUSED:
                raise _ModeUnavailable(
                    f'the source refused the range with HTTP {e.response.status_code}',
                    refused=e.response.status_code in _RANGE_HOST_REFUSED) from None
            raise

    async def _tail(self, url, suffix):
        """
        Read the tail of a wheel, and the size of the whole file with it.

        Args:
            url (str): Url of the wheel without the fragment
            suffix (bool): True to ask for the tail as a suffix range

        Returns:
            tuple[int, bytes, int]: (size of the file, tail bytes, absolute
                offset of the first byte of the tail)

        Raises:
            _ModeUnavailable: If the host does not serve the range, or does not
                tell the size of the file
        """
        if suffix:
            response = await self._get(url, {'Range': f'bytes=-{TAIL_SIZE}'})
            if response.status_code != 206:
                # the host ignored the header and sent the whole file, or
                # answered something that is not a range at all: no range
                raise _ModeUnavailable(
                    f'the source answered {response.status_code} to a suffix range',
                    refused=True)
            span = content_range(response.headers.get('content-range', ''))
            if span is None or span.total < 0:
                raise _ModeUnavailable('the answer of the suffix range names no size')
            total = span.total
            tail = response.content
            if not tail or total < len(tail):
                raise _ModeUnavailable('the answer of the suffix range is not a tail')
            return total, tail, total - len(tail)

        total = await self._length(url)
        start = max(total - TAIL_SIZE, 0)
        tail = await self._get_range(url, start, total - 1)
        if not tail:
            raise _ModeUnavailable('the source answered no bytes for the tail')
        return total, tail, total - len(tail)

    async def _length(self, url):
        """
        The size of a file, as a one byte range request tells it.

        Args:
            url (str): Url of the file without the fragment

        Returns:
            int: Size of the file in bytes

        Raises:
            _ModeUnavailable: If the host does not answer a range request
        """
        response = await self._get(url, {'Range': 'bytes=0-0'})
        if response.status_code != 206:
            raise _ModeUnavailable(
                f'the source answered {response.status_code} to a range request',
                refused=True)
        span = content_range(response.headers.get('content-range', ''))
        if span is None or span.total <= 0:
            raise _ModeUnavailable('the answer of the range request names no size')
        return span.total

    async def _get_range(self, url, start, end):
        """
        Read a span of a file.

        Args:
            url (str): Url of the file without the fragment
            start (int): First byte of the span
            end (int): Last byte of the span, inclusive

        Returns:
            bytes: The bytes of the span

        Raises:
            _ModeUnavailable: If the host does not answer with the span
        """
        response = await self._get(url, {'Range': f'bytes={start}-{end}'})
        if response.status_code != 206:
            raise _ModeUnavailable(
                f'the source answered {response.status_code} to the range "{start}-{end}"',
                refused=True)
        return response.content

    """
    The capabilities of a source
    """

    async def capabilities(self, source, candidates=()):
        """
        What an index is known to serve, probed once per run.

        The probe is one request against a wheel of the source (two when the
        suffix range is refused): the first caller of the source probes, every
        other caller waits for its answer. That is what keeps a burst of eight
        requests from discovering the same missing feature eight times, and
        why the first round of the run is one request at a time (see
        MetadataProvider._open_budget).

        Args:
            source (str): Base url of the index
            candidates (Iterable[WheelCandidate]): Wheels of the source to
                probe with. Defaults to (), no probe: a source no caller has a
                file of stays unknown

        Returns:
            SourceCapabilities: The capabilities of the source
        """
        source = source or DEFAULT_INDEX
        state = self._capabilities.get(source)
        if state is None:
            state = self._capabilities[source] = SourceCapabilities()
        if state.ready:
            return state
        async with state.lock:
            if not state.ready:
                # a wheel without a metadata file is the one the range reader
                # would be used on: it is the file the probe asks about
                url = next(
                    (candidate.link.url_without_fragment for candidate in candidates
                     if not candidate.link.metadata_url), '')
                if url:
                    await self._probe(state, url)
                # a probe that failed on the network learned nothing: the next
                # caller of the source asks again, so one blip does not leave
                # the source unknown (and every version trying the suffix
                # range first) for the rest of the run
                state.ready = state.suffix_range is not None
        return state

    def _demote(self, source, suffix, refused):
        """
        Correct a declared capability with what a host just answered.

        A declaration that is wrong would otherwise cost one wasted request per
        version: every version of the source would try the shape the host
        refuses before falling back. The observation replaces the guess, so the
        run pays one request and then behaves as if it had probed.

        Only a refusal of the request itself is an observation (see
        _RANGE_HOST_REFUSED); an archive whose layout is unexpected says
        nothing about the next file, and is left to the per-version notes.

        Args:
            source (str): Base url of the index
            suffix (bool): True for the suffix range, False for an explicit one
            refused (bool): True when the host refused the shape of the request
        """
        if not refused:
            return
        state = self._capabilities.get(source or DEFAULT_INDEX)
        if state is None or not state.assumed:
            return
        if suffix:
            state.suffix_range = False
        else:
            state.range = False
            if state.suffix_range is False:
                # both shapes refused: nothing of the declaration is left to correct
                state.assumed = False
        logger.debug(
            f'The declared capability of "{source}" was corrected: '
            f'{"suffix" if suffix else "explicit"} range refused')

    async def _probe(self, state, url):
        """
        Ask one file of a source what the host serves.

        Args:
            state (SourceCapabilities): The record to fill
            url (str): Url of a wheel of the source
        """
        status = await self._probe_status(url, f'bytes=-{TAIL_SIZE}')
        if status == 206:
            state.suffix_range = True
            state.range = True
            return
        if status == 200:
            # the host ignored the header and sent the whole file: no range at all
            state.suffix_range = False
            state.range = False
            return
        if status is None:
            # the network failed: the record stays unknown, which reads as
            # "try it", and the request that follows classifies the failure
            return
        state.suffix_range = False
        status = await self._probe_status(url, 'bytes=0-0')
        if status == 206:
            state.range = True
        elif status == 200:
            state.range = False

    async def _probe_status(self, url, value):
        """
        The status a host answers to a range request, without its body.

        The body is not read: a host that ignores the header would send the
        whole wheel, which is the transfer the probe exists to avoid.

        Args:
            url (str): Url of the file
            value (str): Value of the Range header

        Returns:
            int | None: The status, None when the request failed on the network
        """
        status = []

        async def consume(response):
            # the probe asks for the status only: the body is dropped with the
            # response, which closes the connection instead of reading it
            status.append(response.status_code)

        try:
            async with self._limit:
                await self._client.stream(
                    url, consume, headers={'Range': value}, attempts=1,
                    follow_redirects=True)
        except httpx2.HTTPStatusError as e:
            return e.response.status_code
        except httpx2.HTTPError as e:
            logger.debug(f'The capability probe of the range failed: "{url}", {e}')
            return None
        return status[0] if status else None

    def _open_budget(self):
        """
        Open the request budget after the first answer of the run.

        The first round runs one request at a time: it is the round that
        discovers whether the run works at all (a mirror that does not answer, a
        proxy that is not there), and a burst of eight failures is neither
        faster nor clearer than one. The budget opens on the first answer the
        run reads, a page or a metadata: the first page is the first request of
        a compile, so the gate would otherwise hold the whole first level of
        pages, one round trip per page.

        A source whose capabilities are unknown later costs one probe whatever
        the budget is: the callers of a source wait on its lock while the first
        of them probes (see capabilities()), so the burst the gate exists for
        cannot happen anyway.
        """
        if self._budget_open:
            return
        self._budget_open = True
        self._release()

    def _release(self):
        """
        Raise the request budget of a limiter that is already created.

        The limiter is created by the first request, so a budget opened before
        that one is set when the limiter is created, see _limit.
        """
        limiter = self._limiter
        if limiter is None or limiter.total_tokens >= self.concurrency:
            return
        limiter.total_tokens = self.concurrency

    """
    The shared machinery
    """

    async def _single_flight(self, cache, key, factory):
        """
        Run a read once per key, every concurrent caller gets its answer.

        Two branches of a closure reach the same distribution constantly, and
        the answer is the same for both: the first caller reads, the others
        wait on its event. A failure is delivered to every waiter, so a broken
        index fails the run rather than being retried once per branch.

        Args:
            cache (dict): The answers of the run, the key is looked up here
            key (tuple): Identity of the answer
            factory (Callable[[], Awaitable]): The read

        Returns:
            Any: The answer of factory()
        """
        cached = cache.get(key)
        if cached is not None:
            return cached
        flight = self._flights.get(key)
        if flight is not None:
            await flight.done.wait()
            if flight.error is not None:
                raise flight.error
            return cache[key]
        flight = self._flights[key] = _Flight()
        try:
            value = await factory()
        except BaseException as e:
            flight.error = e
            raise
        finally:
            flight.done.set()
        cache[key] = value
        return value

    @property
    def _limit(self):
        """
        The limiter of the requests of this run.

        Returns:
            trio.CapacityLimiter: The limiter, created on the first request
        """
        if self._limiter is None:
            # the first round is one request at a time, see _open_budget()
            self._limiter = trio.CapacityLimiter(
                self.concurrency if self._budget_open else 1)
        return self._limiter

    async def _get(self, url, headers=None):
        """
        Send one request of the run, inside the budget.

        Args:
            url (str): Url to get
            headers (dict, optional): Request headers, the Range of a span

        Returns:
            httpx2.Response: The response, its body read

        Raises:
            httpx2.HTTPError: If the request fails
        """
        async with self._limit:
            return await self._client.get(url, headers=headers, follow_redirects=True)

    async def _read_page(self, name, source):
        """
        Read the index page of one distribution, inside the budget.

        Args:
            name (str): Distribution name
            source (str): Base url of the index

        Returns:
            PackageIndex: The page
        """
        async with self._limit:
            page = await self._fetcher_of(source).get_package_index(name)
        # the first page that answers is the proof the route works, see
        # _open_budget()
        self._open_budget()
        return page

    def _fetcher_of(self, source):
        """
        The fetcher of one index, created once per run.

        Args:
            source (str): Base url of an index

        Returns:
            PypiFile: The fetcher, sharing the client of the run
        """
        fetcher = self._fetchers.get(source)
        if fetcher is None:
            fetcher = PypiFile(source, self.dest, client=self._client)
            self._fetchers[source] = fetcher
        return fetcher

    def _noted(self, key, mode):
        """
        Whether a way of reading a METADATA is known not to apply.

        Args:
            key (tuple): (source, name, version)
            mode (str): One of the MODE_* constants

        Returns:
            bool: True when the mode already failed for this version
        """
        return mode in self._notes.get(key, {})

    def _note(self, key, mode, reason):
        """
        Remember that a way of reading a METADATA did not apply.

        The note is per version: it never changes what the source is known to
        serve, so one old wheel without a metadata file does not degrade the
        new ones (see MetadataProvider.metadata).

        Args:
            key (tuple): (source, name, version)
            mode (str): One of the MODE_* constants
            reason (str | Exception): Why it did not apply
        """
        self._notes.setdefault(key, {})[mode] = str(reason)


def _slice(buffer, buffer_start, start, end):
    """
    The span of a file a window already holds, clipped to the window.

    A window that holds the start of the span answers what it has: the caller
    checks whether the member it wants is complete before it uses the bytes, so
    a clipped span is a shorter window, not a wrong one.

    Args:
        buffer (bytes): Bytes of a window
        buffer_start (int): Absolute offset of the first byte of buffer
        start (int): Absolute offset of the first byte wanted
        end (int): Absolute offset after the last byte wanted

    Returns:
        bytes | None: The span, None when the window does not hold its first byte
    """
    if start < buffer_start or start > buffer_start + len(buffer):
        return None
    offset = start - buffer_start
    return buffer[offset:offset + (end - start)]
