"""
Install the dependencies of a mod into a site-packages folder: read the
requirements file of the mod, compare its pins with the installed
distributions, and update the out-of-date declared ones from the pack
channels of the mod.

The requirements file of the mod (mod.entry.requirements_file, default
'requirements.txt' under the mod root) pins the distributions the mod runs
with, every one pinned to an exact version -- the file style the deploy
config names (Deploy.Python.RequirementsFile, see
alasio.deploy.simple_pip.parse_req). The site-packages folder has to
satisfy the pins: this module reads the pins (Requirements), compares them
with the installed distributions (PipList) and reports the pins the folder
does not satisfy as dict_update, the local check of an update.

The distributions a mod updates by pack are declared on its entry
(mod.entry.pack_update_deps, an entry attribute like mirrors), the other
pins install with pip, by the flow of the caller: install_pack() installs
the out-of-date pins that are declared from the pack channels of the mod,
one distribution at a time (PipPack and the deploy job machine, see
doc/2026-09-30_python-dist-pack-update-flow.md).

Usage:
    install = InstallBase(mod, site_packages)
    print(install.dict_update)        # {'httpx': '0.28.1'}
    await install.install_pack()      # {'httpx': True}
"""

import trio

from alasio.deploy.httpclient.probe import AllMirrorsFailedError
from alasio.deploy.simple_pip.parse_req import Requirements
from alasio.deploy.simple_pip.pip_list import PipList, normalize_name
from alasio.deploy.simple_pip.pip_pack import PipPack
from alasio.ext.cache import cached_property
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_text
from alasio.logger import logger


class InstallBase:
    """
    The dependency install of one mod.

    One instance is one read of the environment: dict_update reads the
    requirements file of the mod, the metadata of the site-packages
    folder and the pack ledgers of the mod once and caches the result, so
    the instance has to be created after the tree of the mod is at the
    version whose pins are installed -- the flow of a mod update applies
    the tree first, then installs the dependencies of its new version.
    install_pack() installs the declared pack updated distributions of
    that same read; a caller that wants to re-check after the installs
    (e.g. to report what is left for pip) creates a new instance.

    Attributes:
        mod (Mod): The mounted mod, its entry carries the requirements
            file and the pack updated names (entry.requirements_file and
            entry.pack_update_deps are read, entry.mirrors is handed to
            PipPack)
        site_packages (PathStr): Folder the distributions are checked in
            and installed into, the root of the deploy jobs
        client (httpx2.AsyncClient | AsyncHttpClient | None): Client the
            pack requests are sent with, handed to PipPack, None to create
            one per install
    """

    def __init__(self, mod, site_packages, client=None):
        """
        Args:
            mod (Mod): The mounted mod whose requirements file pins the
                dependencies
            site_packages (str | PathStr): Path of the site-packages folder
                to check and install into
            client (httpx2.AsyncClient | AsyncHttpClient, optional): Client
                the pack requests are sent with, e.g. the process client of
                the caller, see PipPack. Its lifetime belongs to the caller,
                it is never closed here. Defaults to None
        """
        self.mod = mod
        self.site_packages = PathStr.new(site_packages)
        self.client = client

    @property
    def path_requirements(self):
        """
        Path of the requirements file of the mod.

        Returns:
            PathStr: {mod.root}/{entry.requirements_file}
        """
        return self.mod.root.joinpath(self.mod.entry.requirements_file)

    @cached_property
    def dict_update(self):
        """
        The pins of the requirements file of the mod the site-packages
        folder does not satisfy.

        A pin is out of date when the folder holds no distribution of its
        name, or the installed one is at another version: the versions are
        the display versions of the pack channel (the Version of the
        metadata of a wheel), compared as exact strings. The installed
        distributions are read once (PipList: a scan of the folder and the
        pack ledgers of the mod, no interpreter is involved); the listing
        reports every distribution once, the version of its pack ledger
        wins over the metadata when the two disagree -- an update the
        metadata already shows can be interrupted before the ledger is
        committed, see PipList.list(). The check is version-level: the
        files of a distribution are verified and repaired by a deploy job
        when one runs for it, a damaged file at the right version is not
        an update here. A mod without a requirements file has nothing to
        install: a warning is logged and the pins are empty.

        Returns:
            dict[str, str]: PEP 503 normalized name -> pinned version of
                the pins to install or update, e.g. {'httpx': '0.28.1'}.
                Empty when every pin is satisfied
        """
        path = self.path_requirements
        try:
            content = atomic_read_text(path)
        except FileNotFoundError:
            logger.warning(f'The mod "{self.mod.name}" has no requirements file: "{path}"')
            return {}
        pins = Requirements(content).dict_deps
        # The installed versions, from one read of the listing of the
        # folder and the pack ledgers of the mod: one entry per name, the
        # version of the pack ledger wins over the metadata, see
        # PipList.list()
        versions = {
            name: dist.version
            for name, dist in PipList(self.site_packages, mod=self.mod).list().items()
        }
        return {
            name: version
            for name, version in pins.items()
            if versions.get(name) != version
        }

    async def install_pack(self):
        """
        Install the out-of-date pins the mod updates by pack.

        Every pin of dict_update whose name is declared in pack_update_deps
        (mod.entry.pack_update_deps, any case and separator style) is
        installed at the pinned version by PipPack, from the pack channel of
        the mod: the pinned version is the version the install requires the
        channel to publish as its latest one (DeployJob.update(version)), a
        deployment that publishes another version provides no valid update
        and the install is refused. Every other pin is left to the pip
        install of the caller, an undeclared distribution is not touched
        here. The local check (dict_update) runs on a worker thread first
        (it reads the requirements file and the metadata of the folder,
        blocking file IO); when nothing is out of date, nothing is
        requested and no update source is needed.

        One distribution is installed after another; a failure of one does
        not stop the others (the per-dist independence of the pack flow,
        see doc/2026-09-30_python-dist-pack-update-flow.md §3.3): a channel
        that publishes another version, a network failure and a failed file
        operation are logged and reported as False, the next call retries.
        Every install holds the exclusive lock of the ledger of its
        distribution for its whole flow (DeployJob.update), so two installs
        of one distribution never interleave.

        Returns:
            dict[str, bool]: PEP 503 normalized name -> True when the
                distribution is at the pinned version after the call, False
                when its install failed (the next call retries). Empty when
                no declared pin is out of date

        Raises:
            ValueError: When a declared pin is out of date but the mod
                declares no usable update source (PipPack refuses it)
        """
        deps = await trio.to_thread.run_sync(self._pack_deps)
        if not deps:
            return {}
        # httpx2 is imported lazily in the places that catch its errors, see
        # the note of alasio.deploy.pack.job: the package resolves its own
        # version with importlib.metadata at import time, which the
        # in-memory filesystem of the tests cannot answer
        import httpx2

        pack = PipPack(self.mod, self.site_packages, client=self.client)
        out = {}
        for name, version in deps.items():
            try:
                ok = await pack.install(name, version)
            except (ValueError, httpx2.HTTPError, AllMirrorsFailedError) as e:
                # a failure of one distribution must not stop the others
                logger.warning(f'Failed to install "{name}=={version}": {e}')
                ok = False
            out[name] = ok
        return out

    def _pack_deps(self):
        """
        The out-of-date pins the mod updates by pack: the work of
        install_pack() before the installs, blocking file IO, called on a
        worker thread.

        Returns:
            dict[str, str]: PEP 503 normalized name -> pinned version of
                the pins of dict_update declared in pack_update_deps
        """
        names = {normalize_name(name) for name in self.mod.entry.pack_update_deps}
        return {
            name: version
            for name, version in self.dict_update.items()
            if name in names
        }
