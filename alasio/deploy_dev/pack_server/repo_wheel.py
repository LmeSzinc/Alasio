"""
Publish the wheels of the indexes a repo provides (steps 4.2 and 5).

A client installs the dependencies of the pins whose source is one of the
indexes the mod entry declares in update_provide_pypi_mirrors with pip, and
those indexes are not reachable from every client: the repo serves the files
itself. The step downloads every wheel of those pins to the private cache and
syncs them to the folder a client reads:

    {workspace}/_wheel_cache/{name}/{version}/index.html   the index page
    {workspace}/_wheel_cache/{name}/{version}/{file}.whl    the cached wheels
    {workspace}/wheel/{name}/{version}/{file}.whl           the published wheels

Every wheel of the version is fetched, not only the pure python one: a compiled
extension has one file per platform and the client installs the one of its own.
The pins are the ones of the latest commit only: a client installs the
dependencies of the version it updated to, and an old version keeps the wheels
of the run that published it.

The packs of the dependencies fetch their own wheels (repo_dep): the two steps
share the cache folder, and a version that both fetch is downloaded by the
first of them and answered by the local files of the second, see fetch_wheel.
Neither step depends on the order of the other.

A published wheel is verified against the cached one: a file that is already
there with the sha256 of its cache file is kept as it is, a file that is
missing, truncated or replaced is copied again. A wheel of a name and a version
is immutable, so a published file that differs from the cache is a broken copy
and never a new release: the index page of every client may have cached it.

The index pages a client reads on top of the folder are repo_pypi.

Usage:
    from alasio.deploy_dev.pack_server.repo_base import RepoBase
    from alasio.deploy_dev.pack_server.repo_wheel import RepoWheel

    base = RepoBase(repo, config.data)
    wheel = RepoWheel(base)
    wheel.run()
    wheel.publish_files   # {(name, version): [WheelInfo]} of the provided pins
"""

import hashlib

import trio

from alasio.deploy.pip_download.pypi_file import DOWNLOAD_MODE_ALL
from alasio.deploy_dev.pack_server.fetch_wheel import WheelFetcher, WheelNotFoundError
from alasio.deploy_dev.pack_server.repo_base import DEFAULT_INDEX
from alasio.ext.path.atomic import atomic_copy, atomic_read_bytes_stream, atomic_rmtree, folder_rmtree_empty
from alasio.logger import logger


class RepoWheel:
    """
    Publish the wheels of the provided pins, see the module docstring.

    Usage:
        wheel = RepoWheel(base)
        wheel.run()
    """

    def __init__(self, base):
        """
        Args:
            base (RepoBase): Workspace and declarations of the repo
        """
        self.base = base
        # {(name, version): [WheelInfo]} of the provided pins fetched by
        # run(), the files the pip index publishes, see repo_pypi
        self.publish_files = {}
        # whether run() completed, the precondition of commit(): a failed run
        # must not remove the wheels the previous run published
        self._generated = False

    def run(self):
        """
        Download the wheels of the provided pins and sync them to the pip index.

        The fetch is the only async work of the step: it runs on an event loop
        of its own, then the files of the run are copied from the cache.

        Raises:
            WheelNotFoundError: If a provided pin has no usable wheel
            WheelHashError: If a downloaded wheel does not match its index link
            httpx2.HTTPError: If a request fails
            OSError: If a wheel cannot be written
        """
        self.publish_files = {}
        self._generated = False
        trio.run(self._fetch)
        self._sync()
        self._generated = True

    def commit(self):
        """
        Remove the cached and published wheels the window does not ask for.

        Called after the repo version is published: a pin that left the
        provided set, a version the latest commit does not pin anymore, and a
        cache folder neither step asked for are the leftovers of an earlier
        run. The cache folder is shared with repo_dep, which fetches the pure
        python wheels of the pack channels, so its cleanup lives here as well:
        the version of a pack channel is needed whichever step fetched it. A
        removal that fails is logged and retried by the next run.

        Raises:
            RuntimeError: If run() was not called
        """
        if not self._generated:
            raise RuntimeError('RepoWheel.run() must be called before commit()')
        self._remove_stale_cache()
        self._remove_stale_wheels()

    async def _fetch(self):
        """
        Download every wheel of every provided pin to the private cache.

        Raises:
            WheelNotFoundError: If a provided pin has no usable wheel
            WheelHashError: If a downloaded wheel does not match its index link
            httpx2.HTTPError: If a request fails
            OSError: If a wheel cannot be written
        """
        targets = [
            (info.name, info.latest) for info in self.base.deps.values() if info.provide_wheel]
        total = len(targets)
        for index, (name, pin) in enumerate(targets, start=1):
            fetcher = WheelFetcher(
                self.base.wheel_cache_of(name, pin.version), pin.source or DEFAULT_INDEX,
                name, pin.version, client=self.base.client)
            logger.info(
                f'[{index}/{total}] Fetching wheels of "{name}=={pin.version}", '
                f'index="{fetcher.mirrors_text}"')
            try:
                try:
                    # every wheel of the version: the client installs the one
                    # of its platform, see DOWNLOAD_MODE_ALL
                    self.publish_files[(name, pin.version)] = await fetcher.fetch_async(
                        DOWNLOAD_MODE_ALL)
                except WheelNotFoundError as e:
                    raise WheelNotFoundError(
                        f'No usable wheel of the provided dependency "{name}=={pin.version}", '
                        f'the pip index cannot be generated: {e}') from e
            finally:
                await fetcher.aclose()

    def _sync(self):
        """
        Copy the wheels of the run from the cache to the pip index.

        A published file with the sha256 of its cache file is kept, so a rerun
        of an up to date workspace writes nothing, see _is_synced().

        Raises:
            OSError: If a wheel cannot be copied
        """
        total = len(self.publish_files)
        for index, ((name, version), wheels) in enumerate(self.publish_files.items(), start=1):
            folder = self.base.wheel_folder.joinpath(name).joinpath(version)
            logger.info(f'[{index}/{total}] Publishing wheels of "{name}=={version}"')
            for wheel in wheels:
                file = folder.joinpath(wheel.file.name)
                if _is_synced(wheel.file, file, wheel.hashes['sha256']):
                    logger.info(f'Wheel exists, skipped: "{file}"')
                    continue
                logger.info(f'Copy wheel: "{wheel.file}" -> "{file}"')
                # the write is atomic and creates the folder of the version:
                # a client never reads a half copied wheel
                atomic_copy(wheel.file, file)

    def _remove_stale_cache(self):
        """
        Remove the cache folders the window does not ask for.

        The needed versions are the ones of both steps: the pack channels of
        repo_dep (the latest version and the lookbacks) and the provided pin of
        repo_wheel. The folder of a name is left empty when every version of
        the dependency was removed.
        """
        needed = set()
        for info in self.base.deps.values():
            if info.pack_dep:
                needed.update((info.name, pin.version) for pin in info.versions)
            if info.provide_wheel:
                needed.add((info.name, info.latest.version))
        folder = self.base.wheel_cache_folder
        for name in folder.iter_foldernames():
            name_folder = folder.joinpath(name)
            for version in name_folder.iter_foldernames():
                if (name, version) in needed:
                    continue
                version_folder = name_folder.joinpath(version)
                logger.info(f'Removing stale wheel cache: "{version_folder}"')
                try:
                    atomic_rmtree(version_folder)
                except OSError as e:
                    logger.warning(f'Failed to remove stale wheel cache "{version_folder}": {e}')
            folder_rmtree_empty(name_folder)

    def _remove_stale_wheels(self):
        """
        Remove the published wheels and the name folders the run does not publish.

        A name that leaves the provided set goes with its folder: it holds
        nothing a client may install after this run.
        """
        needed = set(self.publish_files)
        needed_names = {name for name, _ in needed}
        folder = self.base.wheel_folder
        for name in folder.iter_foldernames():
            name_folder = folder.joinpath(name)
            for version in name_folder.iter_foldernames():
                if (name, version) in needed:
                    continue
                version_folder = name_folder.joinpath(version)
                logger.info(f'Removing stale wheel: "{version_folder}"')
                try:
                    atomic_rmtree(version_folder)
                except OSError as e:
                    logger.warning(f'Failed to remove stale wheel "{version_folder}": {e}')
            if name in needed_names:
                continue
            logger.info(f'Removing stale wheel: "{name_folder}"')
            try:
                atomic_rmtree(name_folder)
            except OSError as e:
                logger.warning(f'Failed to remove stale wheel "{name_folder}": {e}')


def _is_synced(source, target, sha256):
    """
    Whether the published file is the copy of its cache file.

    The size is checked first: a file of another size is not the copy, and a
    hash of it would only be a longer way to the same answer. The content is
    verified when the sizes agree, so a file of the right size that was
    truncated and padded, or replaced under the same name, is copied again.

    Args:
        source (PathStr): File in the cache
        target (PathStr): File in the pip index
        sha256 (str): Lowercase hex digest of the file in the cache

    Returns:
        bool: True when the target is the file of the cache
    """
    try:
        if target.stat().st_size != source.stat().st_size:
            return False
        return _sha256_of(target) == sha256
    except OSError:
        return False


def _sha256_of(file):
    """
    The sha256 of a file, streamed in chunks.

    Args:
        file (PathStr): Path of the file

    Returns:
        str: Lowercase hex digest
    """
    digest = hashlib.sha256()
    for chunk in atomic_read_bytes_stream(file):
        digest.update(chunk)
    return digest.hexdigest()
