"""
Fetch the wheel of a distribution version from a PyPI simple mirror.

A pack config with PythonDeps builds the packs of the dependencies of a repo,
the wheels are the input of the build: this module downloads the wheel of one
(name, version) from the PyPI simple index (PEP 503) of a mirror to the wheel
folder of a repo of the pack server:

    {run directory}/pack/{Author}_{Repo}_{Branch}/wheel/{distribution}/{version}/{filename}.whl

The fetch itself is the shared wheel fetcher of the deploy domain,
`alasio.deploy.pip_download.PypiFile`: the index page is parsed by the ported
pip helpers, the wheel of the version is selected for a mode of the fetcher and
the bytes are checked against the hash of their index link before the atomic
write. The mirror structure is the one the fetcher is given (the pypi_mirrors
of the mod entry of the repo, see DepGen), the shared probe layer selects the
mirror of the page and records the selection. This module is the release side
of it, the difference is the mode:

only the pure python wheels of python3 are fetched, the `py3-any` mode of the
fetcher (the abi and the platform tag of the file are `none-any`, the python
tag covers py3). A version that has no such wheel is not a target of the pack
build (a distribution with compiled extensions is updated with pip, not with a
pack), the fetch raises WheelNotFoundError.

The distribution name is normalized with PEP 503 for the index url and the
folder, the same dist-key the ledger and the pack folders use, see
parse_dep.normalize_name.

The fetch runs on the event loop of the shared fetcher (trio):
fetch_async() is the entry of a caller that runs on a loop, like DepGen.

Usage:
    from alasio.deploy_dev.pack_server.fetch_wheel import WheelFetcher

    # root = {run directory}/pack/{Author}_{Repo}_{Branch}
    root = env.PROJECT_ROOT.joinpath('pack').joinpath('LmeSzinc_AzurLaneAutoScript_master')
    fetcher = WheelFetcher(root, 'https://pypi.org/simple', 'httpx', '0.28.1')
    file = await fetcher.fetch_async()
"""


from alasio.deploy.httpclient.probe_mirrors import Mirrors
from alasio.deploy.pip_download.pypi_file import PypiFile, WheelHashError, WheelNotFoundError, select_wheels
from alasio.deploy_dev.pack_server.parse_dep import normalize_name
from alasio.ext.cache import cached_property
from alasio.ext.path import PathStr

__all__ = [
    'WHEEL_FOLDER',
    'WheelFetcher',
    'WheelHashError',
    'WheelNotFoundError',
]

# folder of the fetched wheels in the repo folder
WHEEL_FOLDER = 'wheel'

# the mode of the fetch: the pure python wheels of python3, the only ones a
# pack build can encode, see select_wheels()
WHEEL_MODE = 'py3-any'


class WheelFetcher:
    """
    Fetch the wheel of one distribution version from PyPI simple mirrors.

    The layout, the selection of the wheel and the checks are in the module
    docstring. The fetch is lazy: nothing is requested until fetch_async().
    """

    def __init__(self, root, mirrors, name, version, client=None):
        """
        Args:
            root (str): Folder of the repo in the pack folder of the run
                directory, e.g. {run directory}/pack/{Author}_{Repo}_{Branch};
                the wheel is put in {root}/wheel/{name}/{version}/
            mirrors (Mirrors | str | dict): The PyPI simple index (PEP 503)
                mirrors to fetch from, see Mirrors.from_input() for the
                accepted forms: a single url, name to url, group to mirrors,
                and the flat/nested mix. The probe layer of the shared fetcher
                selects the mirror of a request.
            name (str): Distribution name, e.g. 'httpx', the name a dependency
                file writes. Normalized with PEP 503 for the index url and the
                folder.
            version (str): Exact version of the distribution, e.g. '0.28.1',
                the version string a dependency file pins
            client (httpx2.AsyncClient | AsyncHttpClient, optional): Client to
                send the requests with, or an AsyncHttpClient whose client is
                taken (independent fetchers can share one client, see
                AsyncHttpClient). The lifetime of an injected client belongs to
                the caller (this class never closes it). Defaults to None, a
                client of the fetch is created on the first request and closed
                when the fetch ends
        """
        self.root = PathStr.new(root)
        self.mirrors = Mirrors.from_input(mirrors)
        self.name = name
        self.version = version
        self.client = client

    @cached_property
    def target_folder(self):
        """
        Folder the wheel is fetched into: {root}/wheel/{name}/{version}

        The name is normalized with PEP 503, like the dist-key of the pack
        files.

        Returns:
            PathStr: Absolute path of the folder
        """
        return self.root.joinpath(WHEEL_FOLDER).joinpath(normalize_name(self.name)).joinpath(self.version)

    @property
    def mirrors_text(self):
        """
        The mirror set as the logs print it: the urls it fetches from, in the
        declared order, a set of one url prints that url alone

        Returns:
            str: The urls of the candidate mirrors
        """
        return ', '.join(dict.fromkeys(self.mirrors.urls.values()))

    @cached_property
    def pypi(self):
        """
        The shared wheel fetcher behind this one.

        The fetcher of the version folder: the index page is requested on a
        mirror the probe layer selects from self.mirrors, the wheel is
        downloaded to target_folder. Its client is the injected one, or a
        client of its own that the fetch closes.

        Returns:
            PypiFile: The fetcher of the version
        """
        return PypiFile(self.mirrors, self.target_folder, client=self.client)

    async def fetch_async(self):
        """
        Fetch the wheel of the distribution version to the target folder.

        The index page is requested on a mirror, the wheel of the version is
        selected from its links (WHEEL_MODE, the first by filename) and
        downloaded to {target_folder}/{filename}. A file that exists and
        matches the hash of its link is kept, no request is made; a link
        without a hash is trusted as it is (TOFU). A client the fetch created
        is closed when the fetch ends, an injected one stays open.

        Returns:
            PathStr: Path of the wheel file, target_folder/{filename}

        Raises:
            WheelNotFoundError: If the mirror has no index of the
                distribution, the index has no wheel of the version, or the
                version has no pure python wheel of python3
            WheelHashError: If the downloaded wheel does not match the hash of
                the index link, no file is put at the final path
            httpx2.HTTPError: If a request fails
            AllMirrorsFailedError: If no mirror is usable
        """
        # httpx2 is imported lazily in the places that catch its errors: the
        # package resolves its own version with importlib.metadata at import
        # time, which the in-memory filesystem of the tests cannot answer
        import httpx2
        try:
            try:
                links = await self.pypi.get_package_index(self.name)
            except httpx2.HTTPStatusError as e:
                if e.response.status_code == 404:
                    raise WheelNotFoundError(
                        f'No index of "{self.name}" in "{self.mirrors_text}"') from None
                raise
            candidates = select_wheels(links, self.name, self.version, WHEEL_MODE)
            return await self.pypi.get_wheel(candidates[0].link.url)
        finally:
            await self.pypi.aclose()
