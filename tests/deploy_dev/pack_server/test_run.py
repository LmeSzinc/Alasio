"""
Tests for PackRun: one config is generated and then published, in that order.

The generation (PackRepo / Repo) is faked on the run module, the config reader is
real and the publication runs over the in-process COS service, so the tests check
the chain itself: a config that fails to generate is not published, a config that
fails to publish is reported, and the other configs still run.
"""

import pytest

import alasio.deploy_dev.pack_server.run as run_module
from alasio.deploy_dev.pack_server.model import ConfigError, PackRepoModel, RepoConfig
from alasio.deploy_dev.pack_server.publish.channel import ChannelUpload
from alasio.deploy_dev.pack_server.publish.lock import ChannelLock
from alasio.deploy_dev.pack_server.run import PackRun, PackRunError
from alasio.ext import env
from alasio.ext.file.yamlconfig import YamlConfig
from alasio.ext.path import PathStr
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack_server.conftest import use_memory_lock
from tests.deploy_dev.pack_server.publish import conftest as publish_fixtures
from tests.deploy_dev.pack_server.publish.conftest import (
    AUTHOR, BRANCH, BROKEN_VALUE_CONFIG, CHANNEL, REPO, Generation, make_model, make_remote, write_text_config
)
from tests.deploy_dev.pack_server.publish.test_lock import holder_thread

# the fixtures of the publication, reused here: the services of the mirror and the
# credentials belong to that package, and the run drives the real publication
credentials_env = publish_fixtures.credentials_env
fake_cos = publish_fixtures.fake_cos

pytestmark = [
    pytest.mark.parallel,
]

FILE = 'LmeSzinc_Repo_master.yaml'


@pytest.fixture
def run_dir(fs, monkeypatch):
    """
    A run directory of the pack server, set as env.PROJECT_ROOT.

    The lock of a channel is swapped for the in-memory lock of the tests, see
    the conftest of the pack server.

    Returns:
        PathStr: Absolute path of the run directory
    """
    root = PathStr.new(fs.root_dir.path).joinpath('pack_server')
    root.makedirs(exist_ok=True)
    monkeypatch.setattr(env, 'PROJECT_ROOT', root)
    use_memory_lock(monkeypatch)
    return root


def write_config(root, name=FILE, remotes=None, branch=BRANCH):
    """
    Write a config file of a run directory.

    Args:
        root (PathStr): Run directory
        name (str): File name in the config folder. Defaults to FILE
        remotes (dict): The remotes of the config, one fake COS by default
        branch (str): Branch of the repo, the last part of the workspace name.
            Defaults to BRANCH
    """
    folder = root.joinpath('config')
    folder.makedirs(exist_ok=True)
    model = make_model(remotes)
    model.Repo.Branch = branch
    YamlConfig(folder.joinpath(name), model=PackRepoModel).write_data(model)


def stub_generation(monkeypatch, generated=None, fail=()):
    """
    Replace the generation step with one that writes a generation on the disk.

    Args:
        monkeypatch (pytest.MonkeyPatch): The fixture of the test
        generated (list): Names of the configs that were generated, filled here
        fail (tuple): Config names whose generation fails
    """
    generated = [] if generated is None else generated

    def generate(config):
        name = config.data.workspace_name
        if name in fail:
            raise ValueError(f'cannot pack {name}')
        # the generator writes pack/{workspace}; the name of the fixture channel
        # is the one of make_model()
        generation = Generation(env.PROJECT_ROOT.joinpath('pack').joinpath(name))
        generation.write('aaaaaaaa', {
            'packrepo/aaaaaaaa/full.pack': b'full pack',
        })
        generated.append(name)

    monkeypatch.setattr(run_module, '_generate', generate)
    return generated


class TestPackRun:
    """Generate then publish, one config at a time."""

    @pytest.mark.trio
    async def test_a_config_is_generated_then_published(self, run_dir, credentials_env,
                                                        monkeypatch):
        """The workspace of the config reaches the mirror of the config."""
        write_config(run_dir)
        generated = stub_generation(monkeypatch)
        results = await PackRun(client=credentials_env.client()).run()
        assert generated == [CHANNEL]
        assert [result.status for result in results] == ['ok']
        # the content file, the barrier and the manifest
        assert results[0].uploaded == 3
        assert f'pack/{CHANNEL}/manifest.json' in credentials_env.objects

    @pytest.mark.trio
    async def test_a_failed_generation_is_not_published(self, run_dir, credentials_env,
                                                        monkeypatch):
        """A config whose generation failed is reported and its mirror untouched."""
        write_config(run_dir)
        stub_generation(monkeypatch, fail=(CHANNEL,))
        with pytest.raises(PackRunError) as caught:
            await PackRun(client=credentials_env.client()).run()
        assert [name for name, _reason in caught.value.errors] == [FILE]
        assert credentials_env.objects == {}

    @pytest.mark.trio
    async def test_one_failed_config_does_not_stop_the_other(self, run_dir, credentials_env,
                                                             monkeypatch):
        """Every config is run: the failed one is reported, the other is published.

        The second config is another branch of the repo, so it is another
        workspace: a failure of the first one must neither stop its generation
        nor keep it off the mirror.
        """
        write_config(run_dir, FILE)
        write_config(run_dir, 'other.yaml', remotes={'cos-2': make_remote()}, branch='dev')
        stub_generation(monkeypatch, fail=(CHANNEL,))
        with pytest.raises(PackRunError) as caught:
            await PackRun(client=credentials_env.client()).run()
        assert [name for name, _reason in caught.value.errors] == [FILE]
        assert f'pack/{AUTHOR}_{REPO}_dev/manifest.json' in credentials_env.objects
        assert f'pack/{CHANNEL}/manifest.json' not in credentials_env.objects

    @pytest.mark.trio
    async def test_the_lock_covers_the_generation_and_the_publication(
            self, run_dir, credentials_env, monkeypatch):
        """Both halves of a config run under the lock of its channel.

        The publication reads the tree the generation wrote: a second run of the
        channel must not slip in between them, so the lock is taken before the
        generation and released after the last upload.
        """
        write_config(run_dir)
        held = []
        generated = stub_generation(monkeypatch)
        generate = run_module._generate

        def checked_generate(config):
            held.append(ChannelLock(CHANNEL).is_locked)
            generate(config)

        original = ChannelUpload.run

        async def checked_upload(self, dry_run=False):
            held.append(ChannelLock(CHANNEL).is_locked)
            return await original(self, dry_run)

        monkeypatch.setattr(run_module, '_generate', checked_generate)
        monkeypatch.setattr(ChannelUpload, 'run', checked_upload)
        results = await PackRun(client=credentials_env.client()).run()
        assert held == [True, True]
        assert generated == [CHANNEL]
        assert [result.status for result in results] == ['ok']
        assert not ChannelLock(CHANNEL).is_locked

    @pytest.mark.trio
    async def test_a_busy_channel_is_reported(self, run_dir, credentials_env, monkeypatch):
        """A channel another run holds is reported, and it is not generated.

        The holder is a generation or a publication of the same workspace; the
        scheduler retries the channel on its next round, so this run leaves it
        alone instead of waiting.
        """
        write_config(run_dir)
        generated = stub_generation(monkeypatch)
        thread, release = holder_thread(ChannelLock(CHANNEL))
        try:
            with pytest.raises(PackRunError) as caught:
                await PackRun(client=credentials_env.client()).run()
        finally:
            release.set()
            thread.join(30)
        assert generated == []
        assert credentials_env.objects == {}
        assert 'is being generated or published by another run' in str(
            caught.value.errors[0][1])

    @pytest.mark.trio
    async def test_a_dry_run_skips_the_generation(self, run_dir, credentials_env,
                                                 monkeypatch):
        """A dry run prints the plan of a workspace that is already there."""
        write_config(run_dir)
        Generation(run_dir.joinpath('pack').joinpath(CHANNEL)).write('aaaaaaaa', {
            'packrepo/aaaaaaaa/full.pack': b'full pack',
        })
        stub_generation(monkeypatch, generated=[])
        with logger.mock_capture_writer() as capture:
            results = await PackRun(client=credentials_env.client()).run(dry_run=True)
        assert [result.status for result in results] == ['dry-run']
        assert 'dry run, the generation is skipped' in capture.concat()
        assert credentials_env.objects == {}

    @pytest.mark.trio
    async def test_no_config_is_not_a_failure(self, run_dir):
        """An empty config folder is a run with nothing to do."""
        results = await PackRun().run()
        assert results == []

    @pytest.mark.trio
    async def test_a_value_that_fell_back_is_refused(self, run_dir, credentials_env):
        """A value the reader replaced with its default stops the run.

        YamlConfig falls back to the default of a value that fails validation
        and records the failure; the run must not publish with a value nobody
        wrote, so require_valid() refuses the file before the first request.
        """
        write_text_config(run_dir, BROKEN_VALUE_CONFIG, FILE)
        with pytest.raises(ConfigError, match='Concurrency'):
            await PackRun(client=credentials_env.client()).run()
        assert credentials_env.objects == {}

    @pytest.mark.trio
    async def test_a_broken_config_stops_the_run(self, run_dir, credentials_env, monkeypatch):
        """A config that has to be edited stops the run before the next config.

        It is a ConfigError, not a failed config: another attempt cannot fix a
        value the operator has to fill in, so the other configs are not run.
        """
        write_config(run_dir, FILE)
        write_config(run_dir, 'other.yaml', remotes={'cos-2': make_remote()})
        YamlConfig(run_dir.joinpath('config').joinpath(FILE), model=PackRepoModel).write_data(
            PackRepoModel(Repo=RepoConfig(Remote='u', Author='', Repo=REPO, Branch=BRANCH)))
        generated = stub_generation(monkeypatch)
        with pytest.raises(ConfigError, match='Empty Author'):
            await PackRun(client=credentials_env.client()).run()
        assert generated == []
        assert credentials_env.objects == {}
