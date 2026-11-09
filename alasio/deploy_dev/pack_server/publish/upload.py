"""
The publication of one repo config to every remote it names.

`UploadRun` is the entry of the publish side: it reads the config of a repo,
builds the local generation of its workspace once (see local.py), and drives one
`ChannelUpload` per remote (see channel.py). A remote that fails is reported and
the run continues with the next one: an unreachable mirror must not stop the
publication to the others.

See doc/2026-11-07_pack-server-publish-upload-cleanup.md §6.
"""

from alasio.deploy_dev.pack_server.publish.channel import STATUS_FAILED, ChannelUpload, UploadResult
from alasio.deploy_dev.pack_server.publish.common import ConfigError, PublishError, PublishRunError, require_valid
from alasio.deploy_dev.pack_server.publish.drivers import create_store
from alasio.deploy_dev.pack_server.publish.local import LocalGeneration
from alasio.ext import env
from alasio.ext.cache import cached_property
from alasio.ext.path import PathStr
from alasio.logger import logger

# the folder of the workspaces, inside the run directory, the same one the
# generator writes (see repo_base.PACK_FOLDER)
PACK_FOLDER = 'pack'


class UploadRun:
    """
    The workspace of one repo config, on every remote it names.

    A remote that fails is logged and reported, and the run continues with the
    next one: an unreachable mirror must not stop the publication to the others.
    A pair that is skipped (a generation that is not complete) is not a failure.

    The run takes no lock of the channel: the entry point that runs the whole
    channel holds it, see publish/lock.py. PackRun keeps it across the
    generation and this publication, which is the whole reason the lock exists;
    a caller that publishes a workspace on its own wraps this run in a
    ChannelLock itself, so the workspace is never read halfway through a
    generation.

    Usage:
        config = PackRepoConfig('LmeSzinc_AzurLaneAutoScript_master.yaml')
        with ChannelLock(config.data.workspace_name).locked():
            results = await UploadRun(config).run(dry_run=False)
    """

    def __init__(self, config, client=None, concurrency=None):
        """
        Args:
            config (PackRepoConfig): The config of the repo to publish
            client (httpx2.AsyncClient): Client the stores send their requests
                with, e.g. the in-process client of a test. Its lifetime belongs
                to the caller. Defaults to None: every store creates its own
            concurrency (int): Overrides the config value, for a test that wants
                a deterministic order. Defaults to None
        """
        self.config = config
        self.client = client
        self.concurrency = concurrency
        # the local generation, read once for every remote of the channel:
        # the first mirror digests it, the others await the same result
        self.local = LocalGeneration(self.workspace)

    @cached_property
    def workspace(self):
        """
        The folder this run mirrors: {run}/pack/{workspace_name}.

        The name is a single path component (see PackRepoModel.workspace_name),
        and the folder is joined once: every step reads the attribute, no step
        takes the path as an argument.

        Returns:
            PathStr: Absolute path of the workspace
        """
        return PathStr.new(env.PROJECT_ROOT).joinpath(PACK_FOLDER).joinpath(
            self.config.data.workspace_name)

    async def run(self, remotes=None, dry_run=False):
        """
        Publish the workspace to every selected remote.

        Args:
            remotes (Iterable[str]): Remote names to publish to, None for every
                remote of the config. Defaults to None
            dry_run (bool): Print the plan and write nothing. Defaults to False

        Returns:
            list[UploadResult]: One result per remote, in a stable order

        Raises:
            ConfigError: If a remote of the config is not usable, or a
                requested remote is not in the config
            PublishRunError: If at least one remote failed
        """
        model = self.config.data
        require_valid(self.config)
        selected = self._select_remotes(model, remotes)

        results = []
        failed = []
        for name in selected:
            remote = model.Publish[name]
            concurrency = self.concurrency or remote.Concurrency
            result = await self._one(name, remote, concurrency, dry_run)
            results.append(result)
            if result.status == STATUS_FAILED:
                failed.append((result.channel, result.remote, result.reason))
        if failed:
            raise PublishRunError(failed)
        return results

    async def _one(self, name, remote, concurrency, dry_run):
        """
        Publish the workspace to one remote, catching what it raises.

        Args:
            name (str): Name of the remote
            remote (RemoteConfig): The configured remote
            concurrency (int): Concurrent puts
            dry_run (bool): Print the plan and write nothing

        Returns:
            UploadResult: The result of the pair
        """
        logger.hr(f'Publish {self.workspace.name} -> {name}', level=2)
        store = None
        try:
            store = create_store(name, remote, self.workspace.name, client=self.client)
            upload = ChannelUpload(
                self.local, store, remote_name=name, grace=remote.GraceSeconds,
                concurrency=concurrency)
            return await upload.run(dry_run=dry_run)
        except PublishError as e:
            return self._failed(name, str(e))
        except Exception as e:  # noqa: BLE001 - one pair must not stop the run
            logger.exception(f'[{name}] {self.workspace.name}: publish failed')
            return self._failed(name, f'{type(e).__name__}: {e}')
        finally:
            if store is not None:
                await store.aclose()

    def _failed(self, name, reason):
        """
        Log a failure and build its result.

        Args:
            name (str): Name of the remote
            reason (str): Why it failed

        Returns:
            UploadResult: The failed result
        """
        logger.error(f'[{name}] {self.workspace.name}: {reason}')
        return UploadResult(
            channel=self.workspace.name, remote=name, status=STATUS_FAILED, reason=reason)

    def _select_remotes(self, model, remotes):
        """
        The remotes this run publishes to.

        Args:
            model (PackRepoModel): The config
            remotes (Iterable[str]): Remote names of the call, None for every
                remote of the config

        Returns:
            list[str]: Names of the remotes, sorted

        Raises:
            ConfigError: If a requested remote is not in the config
        """
        configured = model.Publish
        wanted = set(remotes) if remotes else set(configured)
        unknown = sorted(wanted - set(configured))
        if unknown:
            raise ConfigError(
                f'No remote named {", ".join(unknown)} in Publish of '
                f'"{self.config.file}"')
        return sorted(wanted)
