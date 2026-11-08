"""
One run of the pack server: generate the packs of a config, then publish them.

The two halves are independent programs with their own entry (`main.py` generates,
`upload.py` publishes), and running them apart is how a generation ends up on the
disk but not on the mirror, or how an operator publishes a workspace a generator
is still writing. This module chains them, one config at a time:

    config  ->  PackRepoConfig
    clone   ->  PackRepo
    generate ->  Repo.run() + Repo.commit()   # writes latest.pack last
    publish ->  UploadRun.run()               # mirrors the workspace

A config that fails at any step is logged and left out, the other configs still
run (the same rule the generator follows). The whole run is reported at the end,
and a config of which either half failed makes the run fail, so a scheduler sees
it and retries.

The publish half never runs on a workspace whose generation failed: a half
written workspace would be refused by the guard of the uploader anyway, but not
asking is cheaper and the log says why.

Usage:
    python -m alasio.deploy_dev.pack_server.run --root D:/AlasPack

    from alasio.deploy_dev.pack_server.run import PackRun
    await PackRun().run()

See doc/2026-11-07_pack-server-publish-upload-cleanup.md §6.10.
"""

import argparse

import trio

from alasio.deploy_dev.pack_server.gate import check_run_dir
from alasio.deploy_dev.pack_server.model import PackRepoConfig
from alasio.deploy_dev.pack_server.pack_repo import PackRepo
from alasio.deploy_dev.pack_server.publish.common import ConfigError, PublishRunError, require_valid
from alasio.deploy_dev.pack_server.publish.upload import UploadRun
from alasio.deploy_dev.pack_server.repo import Repo
from alasio.ext import env
from alasio.logger import logger


class PackRunError(ValueError):
    """
    Raised at the end of a run in which at least one config failed.

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
            except Exception as e:  # noqa: BLE001 - one config must not stop the others
                logger.exception(f'Config "{name}" failed')
                errors.append((name, f'{type(e).__name__}: {e}'))
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

        Args:
            name (str): Name of the config file in the config folder
            remotes (Iterable[str]): Remote names of the call, None for every
                remote of the config. Defaults to None
            dry_run (bool): Generate nothing, print the publication plan.
                Defaults to False

        Returns:
            list[UploadResult]: The publication result of every remote

        Raises:
            ConfigError: If the config or a remote is not usable
            PublishRunError: If at least one remote failed
            RunDirError: If the run directory is not usable
            ValueError: If the config cannot be packed
        """
        config = await trio.to_thread.run_sync(PackRepoConfig, name)
        # the generation reads the config model, the publication reads the same
        # object: one file, one read
        require_valid(config)
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


def main():
    """
    Command line entry: generate and publish the configs of a run directory.

    Raises:
        SystemExit: 0 when every config succeeded, 1 when at least one failed
            or a remote refused the publication, 2 when the arguments or the
            run directory are not usable
    """
    parser = argparse.ArgumentParser(
        description='Generate the packs of every config of the run directory and publish them',
    )
    parser.add_argument(
        '-r', '--root', default='',
        help='run directory of the pack server, default to env.PROJECT_ROOT',
    )
    parser.add_argument(
        '-c', '--config', action='append', default=None,
        help='config file to run, repeatable, default to every config of the run directory',
    )
    parser.add_argument(
        '-m', '--remote', action='append', default=None,
        help='remote to publish to, repeatable, default to every remote',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='print the publication plan of every config, generate nothing',
    )
    args = parser.parse_args()
    if args.root:
        env.set_project_root(args.root)
    # the project root must be set before the first log: the log file of the
    # process is decided by the first write
    logger.hr('Start', level=0)

    try:
        results = trio.run(PackRun().run, args.config, args.remote, args.dry_run)
    except ConfigError as e:
        logger.error(str(e))
        raise SystemExit(2) from None
    except PackRunError as e:
        logger.error(str(e))
        raise SystemExit(1) from None
    except PublishRunError as e:
        logger.error(str(e))
        raise SystemExit(1) from None
    logger.info(_summary(results))


def _summary(results):
    """
    Format the closing line of a run.

    Args:
        results (Iterable[UploadResult]): The results of every remote of every
            config

    Returns:
        str: E.g. 'Published 2 workspace(s) to 3 remote(s): 12 file(s), 2.3MB,
            40 key(s) retired, 0 failed'
    """
    results = list(results)
    channels = len({result.channel for result in results})
    remotes = len({result.remote for result in results})
    uploaded = sum(result.uploaded for result in results)
    size = sum(result.size for result in results)
    deleted = sum(result.deleted for result in results)
    failed = sum(1 for result in results if not result.ok)
    skipped = sum(1 for result in results if result.status == 'skipped')
    return (
        f'Published {channels} workspace(s) to {remotes} remote(s): '
        f'{uploaded} file(s), {size} bytes, {deleted} key(s) retired, '
        f'{skipped} skipped, {failed} failed')


if __name__ == '__main__':
    main()
