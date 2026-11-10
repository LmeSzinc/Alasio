"""
One run of the pack server: generate the packs of a config, then publish them.

The two halves are independent steps: `PackServer` generates the packs of a run
and `UploadRun` mirrors a workspace, and running them apart is how a generation
ends up on the disk but not on the mirror, or how an operator publishes a
workspace a generator is still writing. This module chains them, one config at
a time:

    config  ->  PackRepoConfig
    clone   ->  PackRepo
    generate ->  Repo.run() + Repo.commit()   # writes latest.pack last
    publish ->  UploadRun.run()               # mirrors the workspace

A config that fails at any step is logged and left out, the other configs still
run (the same rule the generator follows). The whole run is reported at the end,
and a config of which either half failed makes the run fail, so a scheduler sees
it and retries.

The two halves of one config hold the exclusive lock of its channel for the
whole of them: a second run of the same channel is refused (ChannelBusy) instead
of reading a workspace halfway through its replacement, see publish/lock.py.

The publish half never runs on a workspace whose generation failed: a half
written workspace would be refused by the guard of the uploader anyway, but not
asking is cheaper and the log says why.

Usage:
    from alasio.deploy_dev.pack_server.run import PackRun
    await PackRun().run()

The command line has one entry, `python -m alasio.deploy_dev.pack_server.main`.
This module is the library chain of both halves, for a caller that publishes
what it generated.
"""

import trio

from alasio.deploy_dev.pack_server.gate import check_run_dir
from alasio.deploy_dev.pack_server.model import ConfigError, PackRepoConfig, require_valid
from alasio.deploy_dev.pack_server.pack_repo import PackRepo
from alasio.deploy_dev.pack_server.publish.lock import ChannelLock
from alasio.deploy_dev.pack_server.publish.upload import UploadRun
from alasio.deploy_dev.pack_server.repo import Repo
from alasio.ext import env
from alasio.logger import logger
from alasio.logger.utils import stringify_event


class PackRunError(ValueError):
    """
    Raised at the end of a run in which at least one config failed.

    A configuration error is never collected here: it stops the run at once,
    see ConfigError, because another attempt cannot fix a value the operator
    has to edit.

    Attributes:
        errors (list[tuple[str, str]]): (config file, reason) of every failed
            config, in the order of the run
    """

    def __init__(self, errors):
        """
        Args:
            errors (list[tuple[str, str]]): (config file, reason) of every
                failed config
        """
        self.errors = list(errors)
        count = len(self.errors)
        names = ', '.join(name for name, _ in self.errors)
        plural = 's' if count > 1 else ''
        super().__init__(f'Failed to run {count} config{plural}: {names}')


class PackRun:
    """
    Generate and publish every config of the run directory, one by one.

    Usage:
        results = await PackRun().run()
    """

    def __init__(self, client=None):
        """
        Args:
            client (httpx2.AsyncClient): Client the publication sends its
                requests with, e.g. the in-process client of a test. Its
                lifetime belongs to the caller. Defaults to None: every store
                creates its own
        """
        self.client = client

    async def run(self, files=None, remotes=None, dry_run=False):
        """
        Generate and publish every selected config.

        Args:
            files (Iterable[str]): Config files of the call, None for every
                config of the run directory. Defaults to None
            remotes (Iterable[str]): Remote names of the call, None for every
                remote of each config. Defaults to None
            dry_run (bool): Generate nothing and publish nothing, print the
                publication plan of every config. Defaults to False

        Returns:
            list[UploadResult]: The publication result of every config that was
                generated

        Raises:
            RunDirError: If env.PROJECT_ROOT is not a run directory of the pack
                server, see check_run_dir
            ConfigError: If a config or a remote is not usable. The run stops
                at once: the other configs are not run
            PackRunError: If at least one config failed
        """
        check_run_dir()
        names = list(files) if files else list(PackRepoConfig.iter_files())
        if not names:
            folder = env.PROJECT_ROOT.joinpath(PackRepoConfig.CONFIG_FOLDER)
            logger.warning(
                f'No config file in "{folder}", copy '
                f'"{PackRepoConfig.TEMPLATE_FILE}" to a config file and fill it')
            return []

        logger.info(f'Running {len(names)} config(s) of "{env.PROJECT_ROOT}"')
        results = []
        errors = []
        for name in names:
            logger.hr(f'Running config "{name}"', level=1)
            try:
                results += await self.run_config(name, remotes=remotes, dry_run=dry_run)
            except ConfigError:
                # a value the operator has to edit: the other configs are not
                # run, a retry would fail on the same line
                raise
            except Exception as e:  # noqa: BLE001 - one config must not stop the others
                logger.exception(f'Config "{name}" failed')
                errors.append((name, stringify_event(e)))
            else:
                logger.info(f'Config "{name}" done')

        failed = len(errors)
        logger.info(f'Ran {len(names)} config(s): {len(names) - failed} done, {failed} failed')
        if errors:
            raise PackRunError(errors)
        return results

    async def run_config(self, name, remotes=None, dry_run=False):
        """
        Generate the workspace of one config, then publish it.

        The generation runs in a worker thread: it is a long chain of git,
        subprocess and file IO, and the event loop of the publication has
        nothing to do while it runs.

        The lock of the channel is held across both halves: the publication
        reads the tree the generation just wrote, and a second run of the same
        channel must neither generate it halfway through the upload nor upload
        it halfway through the generation. The lock is taken once, here, and
        the steps inside it take none of their own, see publish/lock.py.

        Args:
            name (str): Name of the config file in the config folder
            remotes (Iterable[str]): Remote names of the call, None for every
                remote of the config. Defaults to None
            dry_run (bool): Generate nothing, print the publication plan.
                Defaults to False

        Returns:
            list[UploadResult]: The publication result of every remote

        Raises:
            ChannelBusy: If another run holds the lock of the channel
            ConfigError: If the config or a remote is not usable
            PublishRunError: If at least one remote failed
            RunDirError: If the run directory is not usable
            ValueError: If the config cannot be packed
        """
        try:
            config = await trio.to_thread.run_sync(PackRepoConfig, name)
        except ValueError as e:
            # the reader raises ValueError for a file it cannot decode, e.g. a
            # value of another type; a check of the file raises ConfigError
            raise ConfigError(str(e)) from None
        # the generation reads the config model, the publication reads the same
        # object: one file, one read
        require_valid(config)
        lock = ChannelLock(config.data.workspace_name)
        async with lock.alocked():
            if dry_run:
                logger.info(f'Config "{name}": dry run, the generation is skipped')
            else:
                await trio.to_thread.run_sync(_generate, config)
            results = await UploadRun(config, client=self.client).run(
                remotes=remotes, dry_run=dry_run)
        return results


def _generate(config):
    """
    Generate the workspace of one config, the flow of PackRepoModel.

    Args:
        config (PackRepoConfig): The config of the repo

    Raises:
        ValueError: If the repo of the config cannot be packed
    """
    repo = PackRepo(config.data).run()
    gen = Repo(repo, config.data)
    gen.run()
    # latest.pack is the publication barrier: the uploader only mirrors a
    # generation whose barrier is written, and the leftover files of the
    # previous version stay on the disk until it is
    gen.commit()
