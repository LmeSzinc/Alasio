"""
Install the dependencies of a mod into a site-packages folder: read the
requirements file of the mod and reconcile the pins the mod updates by pack
with the pack channels of the mod.

The requirements file of the mod (mod.entry.requirements_file, default
'requirements.txt' under the mod root) pins the distributions the mod runs
with, every one pinned to an exact version -- the file style the mod entry
names (ModEntryInfo.requirements_file, see
alasio.deploy.simple_pip.parse_req). The distributions a mod updates by pack are declared on its entry
(mod.entry.pack_update_deps, an entry attribute like mirrors); the other
pins install with pip, by the flow of the caller. install_pack() hands every
declared pin to the pack channel of the mod, one distribution at a time
(PipPack and the deploy job machine): the job updates a different local
version and validates the files when the version is already there, so a
dependency without an update is checked all the same.

Usage:
    install = InstallBase(mod, site_packages)
    print(await install._dict_update)  # {'httpx': '0.28.1'}
    await install.install_pack()       # {'httpx': True}
"""

import trio

from alasio.deploy.httpclient.probe_mirrors import AllMirrorsFailedError
from alasio.deploy.pack.packdep import PackDep
from alasio.deploy.simple_pip.parse_req import Requirements
from alasio.deploy.simple_pip.pip_list import DIST_INFO_SUFFIX, PipList, normalize_name
from alasio.deploy.simple_pip.pip_pack import PipPack
from alasio.ext.cache import cached_property_async
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_text
from alasio.logger import logger


class InstallBase:
    """
    The dependency install of one mod.

    One instance is one read of the environment: its cached properties
    (_requirements_deps, _dict_update and _packdep, see
    cached_property_async) read the requirements file of the mod, the
    declared pins and the dependency snapshot of its packed tree once and
    cache the result, so the instance has to be created after the tree of the
    mod is at the version whose pins are installed -- the flow of a mod
    update applies the tree first, then installs the dependencies of its new
    version. install_pack() reconciles the declared pins of that same read;
    a caller that wants to re-check after the installs (e.g. to report what
    is left for pip) creates a new instance.
    cleanup_dist_info() reconciles the metadata directories of the
    site-packages folder with the requirements pins after the whole
    dependency reconciliation, see the method.

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

    @cached_property_async
    async def _requirements_deps(self):
        """
        The pins of the requirements file of the mod, read on the first await
        and cached on the instance.

        The file is the whole input of the dependency reads of the instance
        (the intersection of _dict_update and the pins install_pack()
        reconciles): one read per instance is enough, every method sees the
        same file -- the instance is one read of the environment,
        see the class docstring. The file read is blocking file IO and runs
        on a worker thread, the parse of the content is cheap and runs in
        place.

        Returns:
            dict[str, str]: PEP 503 normalized name -> pinned version

        Raises:
            ValueError: If two requirements pin two different versions of
                the same name (Requirements)
        """
        path = self.path_requirements
        try:
            content = await trio.to_thread.run_sync(atomic_read_text, path)
        except FileNotFoundError:
            logger.warning(f'The mod "{self.mod.name}" has no requirements file: "{path}"')
            return {}
        return Requirements(content).dict_deps

    @cached_property_async
    async def _packdep(self):
        """
        The trusted dependency snapshots of the packed tree of the mod.

        The file belongs to the tree of the mod, so its path follows the mod
        like the requirements file. PackDep.read() validates it against the
        local index pack before decoding; a missing, damaged or untrusted
        file yields an empty mapping, and the installs use the online channel
        flow. The read is blocking file IO and runs on a worker thread.

        Returns:
            dict[str, LatestInfo]: PEP 503 name -> published version and
                index checksum, empty when the tree has no usable snapshot
        """
        return await trio.to_thread.run_sync(PackDep.read, self.mod.root)

    @cached_property_async
    async def _dict_update(self):
        """
        The pins of the requirements file of the mod that the entry declares
        for pack updates, read on the first await and cached on the instance.

        The result is the intersection of the pins of the requirements file
        and the names of pack_update_deps, keyed by the PEP 503 normalized
        name. It is not a local version check: the deploy job of a
        distribution decides whether the version is an update or a file
        validation, from the local ledger of that distribution. Every
        declared pin is handed to that job, so a dependency without an update
        is validated and repaired all the same. A mod without a requirements
        file has nothing to install: a warning is logged and the result is
        empty.

        Returns:
            dict[str, str]: PEP 503 normalized name -> pinned version, e.g.
                {'httpx': '0.28.1'}. Empty when the mod declares no pack
                updated pin
        """
        pins = await self._requirements_deps
        names = {normalize_name(name) for name in self.mod.entry.pack_update_deps}
        return {
            name: version
            for name, version in pins.items()
            if name in names
        }

    async def install_pack(self):
        """
        Reconcile the requirements pins the mod updates by pack.

        The pins in pack_update_deps are handed to PipPack one distribution
        at a time. The pack job updates a different local version and
        validates or repairs the files of an equal one, so every declared pin
        is checked even when there is no update. The mod's packdep snapshot is
        used when it matches the pin, keeping the dependency latest.pack out
        of the flow. A failure of one distribution is logged and reported
        False; the others continue.

        Returns:
            dict[str, bool]: PEP 503 normalized name -> True when the
                distribution is at the pinned version after the call, False
                when its install failed (the next call retries). Empty when
                the mod declares no pack updated pin

        Raises:
            ValueError: When the mod declares no usable update source
                (PipPack refuses it), or the requirements file pins two
                versions of one name (Requirements)
        """
        deps = await self._dict_update
        if not deps:
            return {}
        # httpx2 is imported lazily in the places that catch its errors, see
        # the note of alasio.deploy.pack.job: the package resolves its own
        # version with importlib.metadata at import time, which the
        # in-memory filesystem of the tests cannot answer
        import httpx2

        packdep = await self._packdep
        pack = PipPack(self.mod, self.site_packages, client=self.client)
        out = {}
        for name, version in deps.items():
            entry = packdep.get(name)
            if entry is not None and entry.version != version:
                # the snapshot is of another version than the pin: it cannot
                # confirm this install, leave the online check to decide
                entry = None
            try:
                ok = await pack.install(name, version, latest_info=entry)
            except (ValueError, httpx2.HTTPError, AllMirrorsFailedError) as e:
                # a failure of one distribution must not stop the others
                logger.warning(f'Failed to install "{name}=={version}": {e}')
                ok = False
            out[name] = ok
        return out

    async def cleanup_dist_info(self):
        """
        Reconcile the metadata directories of the site-packages folder.

        The requirements pins of the mod are the versions to keep: for every
        distribution whose pinned version is installed, the metadata
        directories of the other versions are removed. A pin whose version is
        not installed is left alone (the environment is not at that version),
        and a distribution the requirements file does not pin is never
        touched. The scan is one listing of the folder; the removal is best
        effort, like the pack flow: a directory that can not be removed is
        logged and kept for the next call.

        Returns:
            list[str]: Paths of the removed metadata directories, sorted,
                empty when there was nothing to remove
        """
        pins = await self._requirements_deps
        if not pins:
            return []
        pip_list = PipList(self.site_packages, mod=self.mod)
        listing = await trio.to_thread.run_sync(pip_list.pip_list)
        removed = []
        for name, version in pins.items():
            if not any(
                dist.dist_key == name and dist.metadata_version == version
                for dist in listing
            ):
                # the pinned version is not installed: nothing to keep, so
                # nothing is removed for this distribution
                continue
            removed.extend(await trio.to_thread.run_sync(
                _cleanup_dist_info, listing, name, version))
        return sorted(removed)


def _cleanup_dist_info(listing, name, keep_version):
    """
    Remove the metadata directories of one distribution except its pin.

    The entries come from a PipList listing. A directory whose METADATA
    records keep_version is an installation to keep; other versions, entries
    without a version and empty or broken directories are removed. A copy
    under another version's directory name goes when a correctly named
    installation exists. Symlinks and .egg-info entries are never touched.
    Nothing is removed unless keep_version is installed.

    Args:
        listing (list[Distribution]): The metadata entries of the folder
        name (str): Name of the distribution, e.g. 'httpx' or 'ruamel.yaml',
            any case and separator style
        keep_version (str): Version to keep, e.g. '0.28.1'

    Returns:
        list[str]: Paths of the removed metadata directories
    """
    dist_key = normalize_name(name)
    dists = [
        dist
        for dist in listing
        if dist.dist_key == dist_key and dist.info.endswith(DIST_INFO_SUFFIX)
    ]
    # entries whose METADATA records the pin version; without one nothing
    # is removed
    installed = [dist for dist in dists if dist.metadata_version == keep_version]
    if not installed:
        # the pin is not installed: keep the existing metadata
        logger.warning(
            f'Skipped the cleanup of the metadata directories of "{name}": '
            f'the version to keep is not installed: "{keep_version}"'
        )
        return []
    # entries whose directory name also carries the version decide the kept
    # installation; an entry under another name is a copy
    named = [dist for dist in installed if dist.dist_version == keep_version]
    kept = {dist.info for dist in (named or installed)}
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
