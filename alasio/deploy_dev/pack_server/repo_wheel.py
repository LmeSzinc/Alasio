"""
Publish the wheels of the indexes a repo provides (steps 4.2 and 5).

A client installs the dependencies of the pins whose source is one of the
indexes the mod entry declares in provide_wheel with pip, and
those indexes are not reachable from every client: the repo serves the files
itself. The step downloads every wheel of those pins to the private cache and
syncs them to the folder a client reads:

    {workspace}/.wheel_cache/{name}/{version}/index.html   the index page
    {workspace}/.wheel_cache/{name}/{version}/{file}.whl    the cached wheels
    {workspace}/wheel/{name}/{version}/{file}.whl           the published wheels

Every wheel of the version is fetched, not only the pure python one: a compiled
extension has one file per platform and the client installs the one of its own.
The pins are the ones of the latest commit only: a client installs the
dependencies of the version it updated to, and an old version keeps the wheels
of the run that published it.

The packs of the dependencies fetch their own wheels (repo_dep): the two steps
share the cache folder and the fetcher, but a name belongs to one of them only,
a pack dep is never a provided wheel (RepoBase.deps), so neither step depends
on the order of the other.

A published wheel is verified against the cached one: a file that is already
there with the sha256 of its cache file is kept as it is, a file that is
missing, truncated or replaced is copied again. The published copy is hashed
directly, without a stat of the two files first: the copy is usually already
there, and one read of it answers both questions at once. A wheel of a name and
a version is immutable, so a published file that differs from the cache is a
broken copy and never a new release: the index page of every client may have
cached it.

The files of the run are verified and copied a few at a time (SYNC_CONCURRENT):
the work of one file is a read of the disk that ends in a skip, so a run that
follows the previous one hashes gigabytes and the reads overlap.

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

# the files one step verifies and copies at once. The work of a file is a read
# of the disk that usually ends in a skip, so the reads of a run overlap: the
# limit is the queue of the disk rather than the cores, and a copy of a wheel
# is one streamed read and one streamed write
SYNC_CONCURRENT = 8


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

        Both halves run on the one event loop of the step: the fetch overlaps
        the transfers of a version, the sync overlaps the verification and the
        copy of every file.

        Raises:
            WheelNotFoundError: If a provided pin has no usable wheel
            WheelHashError: If a downloaded wheel does not match its index link
            httpx2.HTTPError: If a request fails
            OSError: If a wheel cannot be written
        """
        self.publish_files = {}
        trio.run(self._run)

    async def _run(self):
        """
        Fetch the wheels, then sync them, see run().
        """
        await self._fetch()
        await self._sync()

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
        # one client for the whole step, see RepoBase.fetch_client: the proxy of
        # the run when it is set, None means a client per fetcher
        async with self.base.fetch_client() as client:
            for index, (name, pin) in enumerate(targets, start=1):
                fetcher = WheelFetcher(
                    self.base.wheel_cache_of(name, pin.version), pin.source or DEFAULT_INDEX,
                    name, pin.version, client=client)
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

    async def _sync(self):
        """
        Copy the wheels of the run from the cache to the pip index.

        A published file with the sha256 of its cache file is kept, so a rerun
        of an up to date workspace writes nothing, see _sync_wheel(). The
        wheels of one pin run a few at a time (SYNC_CONCURRENT), each one in a
        worker thread: the verification of a warm file is a read of the disk,
        and the reads would otherwise wait for each other. The next pin starts
        once the wheels of the previous one are written, so the line of a pin
        is the head of its own work rather than an announcement of a whole run
        of lines to come.

        Raises:
            OSError: If a wheel cannot be copied. The failure names the file,
                the other wheels of the pin are still synced, and the pins
                after it are not started
        """
        if not self.publish_files:
            return
        total = len(self.publish_files)
        # one budget for the whole step: the pins are written one after the
        # other, so the transfers of two of them never overlap anyway
        limiter = trio.CapacityLimiter(SYNC_CONCURRENT)

        async def sync(failures, source, target, sha256):
            try:
                await trio.to_thread.run_sync(
                    _sync_wheel, source, target, sha256, limiter=limiter)
            except OSError as e:
                # trio.Cancelled is a BaseException: a cancelled step is not a
                # failure of the file it interrupted
                failures.append(OSError(f'Failed to publish "{target}": {e}'))

        for index, ((name, version), wheels) in enumerate(
                self.publish_files.items(), start=1):
            folder = self.base.wheel_folder.joinpath(name).joinpath(version)
            logger.info(f'[{index}/{total}] Publishing wheels of "{name}=={version}"')
            failures = []
            async with trio.open_nursery() as nursery:
                for wheel in wheels:
                    target = folder.joinpath(wheel.file.name)
                    # the record of the cleanup is a task of the trio thread,
                    # the worker thread only reads and writes the file
                    self.base.touch(target)
                    nursery.start_soon(sync, failures, wheel.file, target, wheel.hashes['sha256'])
            if failures:
                raise failures[0]


def _sync_wheel(source, target, sha256):
    """
    Make the published file the copy of its cache file.

    The work of one wheel of the run: the verification of the published copy,
    and the atomic copy of the cache file over it when it is not the copy. It
    is one function because it is one trip to a worker thread, see
    RepoWheel._sync().

    Args:
        source (PathStr): File in the cache
        target (PathStr): File in the pip index
        sha256 (str): Lowercase hex digest of the file in the cache

    Returns:
        bool: True when the target was already the copy, False when the file
            was written

    Raises:
        OSError: If the target cannot be read or written
    """
    if _is_synced(target, sha256):
        logger.info(f'Wheel exists, skipped: "{target}"')
        return True
    logger.info(f'Copy wheel: "{source}" -> "{target}"')
    # the write is atomic and creates the folder of the version: a client
    # never reads a half copied wheel
    atomic_copy(source, target)
    return False


def _is_synced(target, sha256):
    """
    Whether the published file is the copy of its cache file, hashed in place.

    The published file is hashed directly: the copy of an up to date workspace
    is usually already there, and one read of it answers both questions at
    once. A file of another size has another digest too, so a stat of the two
    files first would only add two calls before the read that decides it. A
    file that is missing, truncated or replaced under the same name does not
    hash to the digest of the cache file, and is copied again.

    Args:
        target (PathStr): File in the pip index
        sha256 (str): Lowercase hex digest of the file in the cache

    Returns:
        bool: True when the target is the file of the cache
    """
    digest = hashlib.sha256()
    try:
        for chunk in atomic_read_bytes_stream(target):
            digest.update(chunk)
    except OSError:
        # missing, unreadable, or replaced while it was read: not the copy
        return False
    return digest.hexdigest() == sha256
