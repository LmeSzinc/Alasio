"""
Fetch the wheels of a distribution version from a PyPI simple index.

A repo of the pack server builds the packs of its python dependencies and
publishes the wheels of the provided ones, the wheels of both jobs are the same
files: this module downloads them from the PyPI simple index (PEP 503) of the
pin into the cache folder of its version:

    {workspace}/_wheel_cache/{distribution}/{version}/index.html   the index page
    {workspace}/_wheel_cache/{distribution}/{version}/{filename}.whl   the wheels

The fetch is the shared wheel fetcher of the deploy domain,
`alasio.deploy.pip_download.PypiFile` (the ported pip helpers parse the page,
select the wheel of a version for a mode, check the bytes against the hash of
their index link and write the file atomically), and this class is the release
side of it. A run of the pack server is repeated on the same workspace, so the
network is spent once and the local files answer the next runs:

- links reads the index page from `index.html` when the snapshot is there, and
  requests the page once when it is not: the page of a distribution is the
  slowest request of the flow and the one that changes least. The snapshot
  lives in the folder of the version, so every version of the window keeps its
  own, see the cache layout. It is a cached property: the page of a run is
  parsed once and shared by its fetches;
- get_wheel() keeps the local file that matches the hash of its index link and
  downloads the one that does not;
- fetch_async(mode) is the two steps: the page, the wheels of the version the
  mode asks for, one file at a time.

A local file that does not match the page is a mismatch of two caches of an
earlier run, and a mismatch does not say which side is stale: the page may
describe a file the index replaced under the same name, or the file may be the
half written leftover of a killed run. The page is downloaded again (once per
instance, at most) and every wheel of the fetch is checked against it again,
the ones checked before the download included: a file that matched the old page
says nothing about the new one. The parsed page and its snapshot are dropped in
fetch_async(), so the second round reads the page from the index again. A wheel
that still does not match after the new page is a source error and is raised.

The modes are the ones of the shared fetcher, see select_wheels():

- DOWNLOAD_MODE_PY3_ANY is the pure python wheel of python3, the only wheel a
  pack build can encode (the abi and the platform tag of the file are
  `none-any`): the input of the dependency packs, one file per version;
- DOWNLOAD_MODE_ALL is every wheel of the version: the files of a provided
  pin, one per platform, the client installs the one of its platform.

The distribution name is normalized with PEP 503 for the index url, the same
dist-key the pack folders use, see parse_dep.normalize_name.

The fetch runs on the event loop of the shared fetcher (trio):
fetch_async() is the entry of a caller that runs on a loop, like RepoWheel. A
fetcher is closed by its caller, see aclose(): the instance keeps the client
and the page of its version for as many fetches as the caller makes.

Usage:
    from alasio.deploy.pip_download.pypi_file import DOWNLOAD_MODE_ALL, DOWNLOAD_MODE_PY3_ANY
    from alasio.deploy_dev.pack_server.fetch_wheel import WheelFetcher

    folder = env.PROJECT_ROOT.joinpath('pack').joinpath('Author_Repo_master') \
        .joinpath('_wheel_cache').joinpath('httpx').joinpath('0.28.1')
    fetcher = WheelFetcher(folder, 'https://pypi.org/simple', 'httpx', '0.28.1')
    try:
        file = (await fetcher.fetch_async(DOWNLOAD_MODE_PY3_ANY))[0]
        files = await fetcher.fetch_async(DOWNLOAD_MODE_ALL)
    finally:
        await fetcher.aclose()
"""

from alasio.deploy.httpclient.client_async import AsyncHttpClient
from alasio.deploy.httpclient.probe_mirrors import Mirrors
from alasio.deploy.pip_download._link import parse_links
from alasio.deploy.pip_download._utils import canonicalize_name
from alasio.deploy.pip_download.pypi_file import (
    DownloadMode, PypiFile, WheelHashError, WheelInfo, WheelNotFoundError, select_wheels
)
from alasio.ext.cache import cached_property, cached_property_async
from alasio.ext.path import PathStr
from alasio.logger import logger

__all__ = [
    'WheelFetcher',
    'WheelHashError',
    'WheelInfo',
    'WheelNotFoundError',
    'create_client',
]

# the snapshot of the index page of a version, in the folder of the version. The
# body is kept as the index served it, byte for byte, see WheelFetcher.links
INDEX_FILE = 'index.html'

# the content type of a PEP 691 JSON page: the snapshot keeps the body only, so
# a page is told from an HTML one by its first character, see WheelFetcher.links
JSON_PAGE = 'application/vnd.pypi.simple.v1+json'


def create_client(proxy=''):
    """
    Create the http client the wheel fetches of one step are sent with.

    A fetcher takes any client (see WheelFetcher.__init__), so the route and the
    lifetime of the requests are decided by the caller: a step of the pack
    server builds one client and hands it to every fetcher of the step, so the
    versions of the window reuse the connections of that client instead of
    opening a connection pool each.

    The environment of the user is ignored, the rule of the clients of the
    deploy domain (see AsyncHttpClient.create_client): the proxy is the one the
    operator configured (PackRepoModel.Env), never the one a proxy tool left in
    the environment of the process.

    Args:
        proxy (str): Proxy url the requests are routed through, e.g.
            'http://127.0.0.1:7890' or 'socks5://127.0.0.1:7890'. Defaults to
            '', a direct connection

    Returns:
        httpx2.AsyncClient: The client, its requests go out directly or through
            the proxy. Nothing is connected by the creation, the first request
            of the client opens the connection
    """
    if not proxy:
        # the factory of the domain: the same trust_env=False without a proxy
        return AsyncHttpClient.create_client()
    import httpx2
    return httpx2.AsyncClient(trust_env=False, proxy=proxy)


class WheelFetcher:
    """
    Fetch the wheels of one distribution version from its PyPI simple index.

    The layout, the cache of the index page and the modes are in the module
    docstring. The fetch is lazy: nothing is requested until fetch_async().
    """

    def __init__(self, folder, mirrors, name, version, client=None):
        """
        Args:
            folder (str): Folder of the version in the cache,
                {workspace}/_wheel_cache/{name}/{version}: the index page and
                the wheels are written into it. The atomic writes create the
                folder when it does not exist
            mirrors (Mirrors | str | dict): The PyPI simple index (PEP 503)
                mirrors to fetch from, see Mirrors.from_input() for the
                accepted forms: a single url, name to url, group to mirrors,
                and the flat/nested mix. The release side passes the one index
                of the pin, so no probe selects between candidates
            name (str): Distribution name, e.g. 'httpx', the name a dependency
                file writes. Normalized with PEP 503 for the index url
            version (str): Exact version of the distribution, e.g. '0.28.1',
                the version string a dependency file pins
            client (httpx2.AsyncClient | AsyncHttpClient, optional): Client to
                send the requests with, or an AsyncHttpClient whose client is
                taken (independent fetchers can share one client, see
                AsyncHttpClient). The lifetime of an injected client belongs to
                the caller (this class never closes it). Defaults to None, a
                client of the fetcher is created on the first request and
                closed by aclose()
        """
        self.folder = PathStr.new(folder)
        self.mirrors = Mirrors.from_input(mirrors)
        self.name = name
        self.version = version
        self.client = client
        # the shared fetcher of the version folder: the index page, the wheels
        # and their checks are its work, see PypiFile
        self.pypi = PypiFile(self.mirrors, self.folder, client=client)
        # whether the index page was downloaded again in this instance, the page
        # of a version is downloaded at most once, see fetch_async()
        self._page_reloaded = False

    @cached_property
    def index_file(self):
        """
        The snapshot of the index page of the version.

        Returns:
            PathStr: {folder}/index.html, the body the index served
        """
        return self.folder.joinpath(INDEX_FILE)

    @cached_property
    def page_url(self):
        """
        The url of the index page of the distribution.

        The base is the first index of the mirrors: the release side gives one
        index per pin, so the snapshot of an earlier run is parsed against the
        same base the page was served at. It is the base of the relative links
        of the page, see parse_links().

        Returns:
            str: {index}/{dist-key}/, e.g. 'https://pypi.org/simple/httpx/'
        """
        return f'{next(iter(self.mirrors.urls.values()))}/{canonicalize_name(self.name)}/'

    @cached_property
    def mirrors_text(self):
        """
        The mirror set as the logs print it: the urls it fetches from, in the
        declared order, a set of one url prints that url alone

        Returns:
            str: The urls of the candidate mirrors
        """
        return ', '.join(dict.fromkeys(self.mirrors.urls.values()))

    async def aclose(self):
        """
        Close the http client of the fetcher, the injected one is left open.

        A fetcher serves as many fetches as the caller makes (the modes of a
        version share one page), so its lifetime belongs to the caller.
        """
        await self.pypi.aclose()

    # ------------------------------------------------------------------
    # The index page of the version
    # ------------------------------------------------------------------

    @cached_property_async
    async def links(self):
        """
        The links of the index page of the distribution, parsed once.

        The snapshot of an earlier run answers when it is there: the page is a
        file of the cache like the wheels, and a run over an up to date
        workspace makes no request for it. A snapshot that does not parse is
        downloaded again, and the page is written to the snapshot when it comes
        from the index. The body is kept only, the content type of the response
        is not stored: a PEP 691 JSON page is told from an HTML one by its
        first character, the object a JSON page starts with.

        The value is a cached property: the page of a run is parsed once and
        shared by its fetches, and a mismatch of the wheels drops it, see
        fetch_async().

        Returns:
            list[Link]: The links of the page, in the order of the page

        Raises:
            WheelNotFoundError: If the index has no page of the distribution (a
                404), or the served page does not parse
            httpx2.HTTPError: If the request fails
            AllMirrorsFailedError: If no mirror is usable
        """
        try:
            body = self.index_file.atomic_read_bytes()
        except FileNotFoundError:
            return await self._download_index()
        content_type = JSON_PAGE if body.lstrip()[:1] == b'{' else ''
        try:
            return parse_links(body, self.page_url, content_type)
        except ValueError as e:
            # the snapshot is a cache: a body that does not parse is dropped
            # and the page is requested, no run is failed by a broken cache
            logger.warning(f'The index snapshot does not parse, downloaded again: "{self.index_file}", {e}')
        return await self._download_index()

    async def _download_index(self):
        """
        Request the index page, write it to the snapshot and read its links.

        The page is the one of the shared fetcher: the request, the probe of
        the mirror and the parse are its work, see PypiFile.get_package_index().

        Returns:
            list[Link]: The links of the page

        Raises:
            WheelNotFoundError: If the index has no page of the distribution (a
                404), or the served page does not parse
            httpx2.HTTPError: If the request fails
        """
        # httpx2 is imported lazily in the places that catch its errors: the
        # package resolves its own version with importlib.metadata at import
        # time, which the in-memory filesystem of the tests cannot answer
        import httpx2
        try:
            index = await self.pypi.get_package_index(self.name)
        except httpx2.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise WheelNotFoundError(f'No index of "{self.name}" in "{self.mirrors_text}"') from None
            raise
        logger.debug(f'Index page of "{self.name}": "{index.url}" -> "{self.index_file}"')
        self.index_file.atomic_write(index.body)
        return index.links

    # ------------------------------------------------------------------
    # The wheels of the version
    # ------------------------------------------------------------------

    async def fetch_async(self, mode: DownloadMode):
        """
        Fetch the wheels of the version that match a mode.

        The mode is the caller's decision, there is no default: a pack build
        asks DOWNLOAD_MODE_PY3_ANY, the pip index of the repo asks
        DOWNLOAD_MODE_ALL (see RepoWheel).

        The page is read (or downloaded) once, the wheels of the mode are
        selected from its links and fetched one at a time.

        A wheel that does not match the page is a mismatch of two caches, see
        the module docstring: the cached links are dropped with the snapshot
        and the round starts again, so the page is downloaded again and every
        wheel of the mode is checked against the new page, the wheels fetched
        before it included. The page is downloaded at most once per instance:
        a wheel that does not match the new page is a failure.

        Args:
            mode: DOWNLOAD_MODE_PY3_ANY (the pure python wheel, the pack build)
            or DOWNLOAD_MODE_ALL (every wheel of the version, the
                pip index)

        Returns:
            list[WheelInfo]: The wheels, in the order of the page

        Raises:
            ValueError: If the mode is not one of DOWNLOAD_MODES
            WheelNotFoundError: If the index has no page of the distribution,
                the page has no wheel of the version that matches the mode, or
                the page downloaded again has no wheel of the mode
            WheelHashError: If a downloaded wheel does not match the hash of its
                link, the page was downloaded again already
            httpx2.HTTPError: If a request fails
            AllMirrorsFailedError: If no mirror is usable
        """
        for _ in range(2):
            candidates = select_wheels(await self.links, self.name, self.version, mode)
            files = []
            try:
                for candidate in candidates:
                    wheel = await self.pypi.get_wheel(candidate.link.url)
                    files.append(wheel)
            except WheelHashError:
                if self._page_reloaded:
                    raise
                # the page and the files are the caches of an earlier run: the
                # parsed page and the snapshot are dropped, so the next round
                # downloads the page and checks every wheel against it
                self._page_reloaded = True
                logger.warning(f'The wheels do not match the index snapshot, downloaded again: "{self.index_file}"')
                cached_property_async.pop(self, 'links')
                self.index_file.atomic_remove()
                continue
            return files
