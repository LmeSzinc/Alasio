"""
Tests for PackRun: one config is generated and then published, in that order.

The generation (PackRepo / Repo) is faked on the run module, the config reader is
real and the publication runs over the in-process COS service, so the tests check
the chain itself: a config that fails to generate is not published, a config that
fails to publish is reported, and the other configs still run.
"""

import pytest

import alasio.deploy_dev.pack_server.run as run_module
from alasio.deploy_dev.pack_server.model import PackRepoModel, RepoConfig
from alasio.deploy_dev.pack_server.run import PackRun, PackRunError
from alasio.ext import env
from alasio.ext.file.yamlconfig import YamlConfig
from alasio.ext.path import PathStr
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack_server.publish import conftest as publish_fixtures
from tests.deploy_dev.pack_server.publish.conftest import (
    AUTHOR, BRANCH, CHANNEL, REPO, Generation, make_model, make_remote
)

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

    Returns:
        PathStr: Absolute path of the run directory
    """
    root = PathStr.new(fs.root_dir.path).joinpath('pack_server')
    root.makedirs(exist_ok=True)
    monkeypatch.setattr(env, 'PROJECT_ROOT', root)
    return root


def write_config(root, name=FILE, remotes=None):
    """
    Write a config file of a run directory.

    Args:
        root (PathStr): Run directory
        name (str): File name in the config folder. Defaults to FILE
        remotes (dict): The remotes of the config, one fake COS by default
    """
    folder = root.joinpath('config')
    folder.makedirs(exist_ok=True)
    YamlConfig(folder.joinpath(name), model=PackRepoModel).write_data(
        make_model(remotes))


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
        """Every config is run, the failed one is reported at the end."""
        write_config(run_dir, FILE)
        write_config(run_dir, 'other.yaml', remotes={'cos-2': make_remote()})
        stub_generation(monkeypatch, fail=(CHANNEL,))
        with pytest.raises(PackRunError) as caught:
            await PackRun(client=credentials_env.client()).run()
        assert len(caught.value.errors) == 2

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
    async def test_a_broken_config_is_reported(self, run_dir, credentials_env):
        """A config the reader could not validate fails its own config only."""
        write_config(run_dir)
        YamlConfig(run_dir.joinpath('config').joinpath(FILE), model=PackRepoModel).write_data(
            PackRepoModel(Repo=RepoConfig(Remote='u', Author=AUTHOR, Repo=REPO, Branch=BRANCH)))
        with pytest.raises(PackRunError):
            await PackRun(client=credentials_env.client()).run()
        assert credentials_env.objects == {}
