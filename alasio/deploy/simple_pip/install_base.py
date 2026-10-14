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
does not satisfy as get_dict_update(), the local check of an update.

The distributions a mod updates by pack are declared on its entry
(mod.entry.pack_update_deps, an entry attribute like mirrors), the other
pins install with pip, by the flow of the caller: install_pack() installs
the out-of-date pins that are declared from the pack channels of the mod,
one distribution at a time (PipPack and the deploy job machine).

Usage:
    install = InstallBase(mod, site_packages)
    print(await install.get_dict_update())  # {'httpx': '0.28.1'}
    await install.install_pack()            # {'httpx': True}
"""

import trio

from alasio.deploy.httpclient.probe import AllMirrorsFailedError
from alasio.deploy.simple_pip.parse_req import Requirements
from alasio.deploy.simple_pip.pip_list import DIST_INFO_SUFFIX, PipList, normalize_name
from alasio.deploy.simple_pip.pip_pack import PipPack
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_text
from alasio.logger import logger


class InstallBase:
    """
    The dependency install of one mod.

    One instance is one read of the environment: get_dict_update() reads
    the requirements file of the mod, the metadata of the site-packages
    folder and the pack ledgers of the mod once and caches the result, so
    the instance has to be created after the tree of the mod is at the
    version whose pins are installed -- the flow of a mod update applies
    the tree first, then installs the dependencies of its new version.
    install_pack() installs the declared pack updated distributions of
    that same read; a caller that wants to re-check after the installs
    (e.g. to report what is left for pip) creates a new instance.
    cleanup_dist_info() removes the metadata directories a version update
    of a distribution left behind: the pack flow only removes the files
    its records describe, so a metadata directory of an old version that
    still holds a file of no record survives it, see the method.

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
        # The pins of the one read of get_dict_update(), None until the
        # first call, see the class docstring
        self._dict_update = None

    @property
    def path_requirements(self):
        """
        Path of the requirements file of the mod.

        Returns:
            PathStr: {mod.root}/{entry.requirements_file}
        """
        return self.mod.root.joinpath(self.mod.entry.requirements_file)

    async def get_dict_update(self):
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

        The read of the folder is blocking file IO and runs on a worker
        thread (PipList is synchronous), so the caller awaits it without
        blocking the event loop. The result is read once per instance and
        cached, see the class docstring.

        Returns:
            dict[str, str]: PEP 503 normalized name -> pinned version of
                the pins to install or update, e.g. {'httpx': '0.28.1'}.
                Empty when every pin is satisfied
        """
        if self._dict_update is None:
            self._dict_update = await trio.to_thread.run_sync(self._read_dict_update)
        return self._dict_update

    def _read_dict_update(self):
        """
        Read the pins of the mod and the installed distributions of the
        folder: the work of get_dict_update(), blocking file IO, called on
        a worker thread.

        Returns:
            dict[str, str]: PEP 503 normalized name -> pinned version of
                the pins to install or update
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

        Every pin of get_dict_update() whose name is declared in
        pack_update_deps (mod.entry.pack_update_deps, any case and
        separator style) is installed at the pinned version by PipPack,
        from the pack channel of the mod: the pinned version is the version
        the install requires the channel to publish as its latest one
        (DeployJob.update(version)), a deployment that publishes another
        version provides no valid update and the install is refused. Every
        other pin is left to the pip install of the caller, an undeclared
        distribution is not touched here. The local check
        (get_dict_update()) runs first, on a worker thread (it reads the
        requirements file and the metadata of the folder, blocking file
        IO); when nothing is out of date, nothing is requested and no
        update source is needed.

        One distribution is installed after another; a failure of one does
        not stop the others (the distributions of the pack flow are
        independent of each other): a channel that publishes another
        version, a network failure and a failed file operation are logged
        and reported as False, the next call retries.
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
        dict_update = await self.get_dict_update()
        # The declared pins of the check: the names of pack_update_deps,
        # any case and separator style (the compare is on the normalized
        # name, PEP 503)
        names = {normalize_name(name) for name in self.mod.entry.pack_update_deps}
        deps = {
            name: version
            for name, version in dict_update.items()
            if name in names
        }
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

    async def cleanup_dist_info(self, name, keep_version):
        """
        Remove the metadata directories of the other versions of one
        distribution, whatever files they still hold: keep the directories
        of keep_version, remove every other one.

        A version update renames the metadata directory of a distribution
        ({name}-{old_version}.dist-info -> {name}-{new_version}.dist-info).
        The pack flow removes the files its records describe, but a
        directory that still holds a file of no record -- e.g. the
        REQUESTED or the direct_url.json a pip installation left, or the
        directory pip installed itself -- survives the update, and pip and
        importlib.metadata then see the distribution twice. This cleanup
        removes every metadata directory of the distribution that is not
        the directory of keep_version, whatever files it still holds.

        The candidates are the "{name}-{version}.dist-info" directories of
        the top level of the site-packages folder whose metadata names the
        distribution: the name is read like PipList reads it (the Name
        header of the metadata, the name of the directory as the fallback)
        and compared in the PEP 503 normalized form, so any case and
        separator style of the name finds the same directories. A symbolic
        link and a .egg-info entry are never touched.

        The version of an installation is the one its metadata records,
        the version pip and importlib.metadata read. A directory whose name
        carries another version than its metadata is the version of the
        metadata: "{name}-2.0.dist-info" holding a 1.0 METADATA is a 1.0
        installation and goes with the other versions. A directory whose
        metadata records the version to keep is an installation of that
        version whatever its name says: it is removed only when another
        installation of the version carries the version in its directory
        name too (the shape a flow writes, "{name}-2.0.dist-info" for a 2.0
        METADATA), it is a copy under the name of another version then. A
        directory whose metadata records no version -- an empty or a broken
        one, e.g. the residue of an interrupted install -- is no
        installation pip can read (PEP 376): it is removed with the others.

        The cleanup runs only when the installation of the version to keep
        landed (the metadata of a directory records that version): an
        environment that is not at the version keeps the metadata it has,
        nothing is removed and a warning is logged then. The removal is
        best effort, like the cleanup of the pack flow: a directory that
        can not be removed, e.g. a file held by a running process on
        Windows, is logged and kept, the next call retries it.

        The blocking file IO runs on a worker thread.

        Args:
            name (str): Name of the distribution, e.g. 'httpx' or
                'ruamel.yaml', any case and separator style
            keep_version (str): Version to keep, e.g. '0.28.1', the
                version the dependency was installed at (the Version of
                the METADATA of the wheel)

        Returns:
            list[str]: Paths of the removed metadata directories, sorted,
                empty when there was nothing to remove
        """
        return await trio.to_thread.run_sync(self._cleanup_dist_info, name, keep_version)

    def _cleanup_dist_info(self, name, keep_version):
        """
        Remove the metadata directories of the other versions of one
        distribution: the blocking work of cleanup_dist_info(), called on
        a worker thread.

        Args:
            name (str): Name of the distribution
            keep_version (str): Version to keep

        Returns:
            list[str]: Paths of the removed metadata directories
        """
        dist_key = normalize_name(name)
        dists = [
            dist
            for dist in PipList(self.site_packages).pip_list()
            if dist.dist_key == dist_key and dist.info.endswith(DIST_INFO_SUFFIX)
        ]
        # The installations of the version to keep: the entries whose
        # metadata records the version, pip reads the installations from the
        # metadata. The entries of the other versions, the entries whose
        # metadata records no version and the entries that hold no metadata
        # at all, e.g. the empty directory of an interrupted install, are the
        # removal candidates besides them
        keep = [dist for dist in dists if dist.metadata_version == keep_version]
        if not keep:
            # The version to keep must be there before the metadata of the
            # other versions goes: an environment that is not at the version
            # keeps the metadata it has
            logger.warning(
                f'Skipped the cleanup of the metadata directories of "{name}": '
                f'the version to keep is not installed: "{keep_version}"'
            )
            return []
        # The installations of the version whose directory name carries the
        # version too (PEP 427, the shape a flow writes,
        # "{name}-{version}.dist-info"): they decide on the copies of the
        # version, an installation of the version under the name of another
        # one is a copy of them. Without a directory of that shape the
        # installations of the version are the only metadata of the
        # distribution and every one of them is kept
        named = [dist for dist in keep if dist.dist_version == keep_version]
        kept = {dist.info for dist in (named or keep)}
        removed = []
        for dist in dists:
            if dist.info in kept:
                continue
            if dist.info.folder_rmtree():
                removed.append(dist.info)
                logger.info(f'Removed the metadata directory of "{name}" "{dist.version}": {dist.info}')
            else:
                logger.warning(
                    f'Failed to remove the metadata directory of "{name}" "{dist.version}", '
                    f'keeping it for the next call: {dist.info}'
                )
        return removed
