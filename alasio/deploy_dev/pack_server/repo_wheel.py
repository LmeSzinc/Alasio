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
from alasio.ext.path.atomic import atomic_copy, atomic_read_bytes_stream
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
        trio.run(self._fetch)
        self._sync()

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
                    wheels = await fetcher.fetch_async(DOWNLOAD_MODE_ALL)
                    self.publish_files[(name, pin.version)] = wheels
                    # the files of the run only: a wheel an earlier run left in
                    # the cache folder is a leftover, the cleanup removes it
                    for wheel in wheels:
                        self.base.touch(wheel.file)
                    self.base.touch(fetcher.index_file)
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
                self.base.touch(file)
                if _is_synced(wheel.file, file, wheel.hashes['sha256']):
                    logger.info(f'Wheel exists, skipped: "{file}"')
                    continue
                logger.info(f'Copy wheel: "{wheel.file}" -> "{file}"')
                # the write is atomic and creates the folder of the version:
                # a client never reads a half copied wheel
                atomic_copy(wheel.file, file)

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
