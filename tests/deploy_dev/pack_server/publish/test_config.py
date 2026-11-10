"""
The publish settings of the config of a repo, and the credentials of a remote:
the parts of the flow that do not talk to a mirror.

The settings are the Publish group of the config file the pack server reads, so
the model of the flow and the model of the run are one file, read by the same
YamlConfig of the project.
"""

import msgspec
import pytest

from alasio.deploy_dev.pack_server.model import (
    ConfigError, CosRemote, PackRepoConfig, PackRepoModel, RepoConfig, WebDavRemote, require_valid
)
from alasio.deploy_dev.pack_server.publish.drivers import create_store, driver_name
from alasio.deploy_dev.pack_server.publish.env import env_value
from alasio.ext.path import PathStr
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack_server.publish.conftest import (
    BUCKET, CHANNEL, CONFIG_FILE, ENV_PREFIX, REMOTE, REMOTE_PREFIX, make_model, make_remote, write_config
)

pytestmark = [
    pytest.mark.parallel,
]

# a config of the tests written by hand, with one remote, so a test can break a
# single line of it; the Config of the fake service fills the rest
REPO_TEXT = """\
Repo:
  Remote: https://github.com/LmeSzinc/Repo
  Author: LmeSzinc
  Repo: Repo
  Branch: master
Publish:
  cos-main:
    Driver: cos
    Bucket: alasio-test-1250000000
    Region: ap-shanghai
"""


def write_text(run_dir, text):
    """
    Write a config file of the run directory verbatim.

    Args:
        run_dir (PathStr): Run directory
        text (str): Content of config/{CONFIG_FILE}
    """
    folder = PathStr.new(run_dir).joinpath(PackRepoConfig.CONFIG_FOLDER)
    folder.makedirs(exist_ok=True)
    with open(str(folder.joinpath(CONFIG_FILE)), 'w', encoding='utf-8') as f:
        f.write(text)


class TestPublishModel:
    """The Publish map of the config of a repo."""

    def test_the_default_is_no_remote(self):
        """A config that publishes nowhere is valid, the flow has nothing to do."""
        assert PackRepoModel().Publish == {}

    def test_the_settings_are_per_remote(self):
        """Grace, concurrency and timeout belong to the remote, not to the repo."""
        remote = make_remote()
        assert remote.Concurrency == 4
        assert remote.GraceSeconds == 3600
        assert remote.TimeoutSeconds == 120
        assert remote.EnvPrefix == ENV_PREFIX

    def test_the_driver_key_picks_the_variant(self):
        """The tagged union reads the Driver key, the same shape the file uses."""
        model = msgspec.json.decode(msgspec.json.encode({
            'Repo': {'Author': 'A', 'Repo': 'B', 'Branch': 'master'},
            'Publish': {
                'cos-main': {'Driver': 'cos', 'Bucket': 'b', 'Region': 'r'},
                'nas': {'Driver': 'webdav', 'BaseUrl': 'https://nas.example/dav'},
            },
        }), type=PackRepoModel)
        assert isinstance(model.Publish['cos-main'], CosRemote)
        assert isinstance(model.Publish['nas'], WebDavRemote)

    def test_the_workspace_name(self):
        """The folder of the workspace is {Author}_{Repo}_{Branch}."""
        model = PackRepoModel(Repo=RepoConfig(Author='LmeSzinc', Repo='AzurLaneAutoScript', Branch='master'))
        assert model.workspace_name == 'LmeSzinc_AzurLaneAutoScript_master'

    def test_a_branch_with_a_slash_is_refused(self):
        """A name that is not a single path component cannot be a folder of pack/."""
        model = PackRepoModel(Repo=RepoConfig(Author='A', Repo='B', Branch='feature/x'))
        with pytest.raises(ConfigError):
            _ = model.workspace_name

    def test_an_unknown_field_is_an_error(self):
        """A typo in a remote is refused instead of being dropped."""
        with pytest.raises(msgspec.ValidationError):
            msgspec.json.decode(
                b'{"Publish": {"cos-main": {"Driver": "cos", "Typo": 1}}}',
                type=PackRepoModel)

    def test_the_removed_settings_are_gone(self):
        """ContentCheck and EnvVars are not settings of a remote any more."""
        remote = make_remote()
        assert not hasattr(remote, 'ContentCheck')
        assert not hasattr(remote, 'EnvVars')


class TestPublishConfig:
    """The Publish group read from the config folder of the run directory."""

    def test_the_template_holds_the_group(self, fs, run_dir):
        """--init writes the defaults of the group with their comments, once.

        The help of a field of a remote is not rendered: build_help_map() walks
        Struct fields only, and the value of the Dict is a Union of Structs.
        The example of the group comment is the documentation of the remote
        fields instead.
        """
        assert PackRepoConfig.write_template() is True
        with open(f'{run_dir}/config/template.yaml', encoding='utf-8') as f:
            text = f.read()
        assert 'Publish: {}' in text
        assert '# Each key creates one independent mirror with its own settings' in text
        assert "#       Driver: 'cos'" in text

    def test_roundtrip(self, fs, run_dir):
        """A config written from the model reads back with the same values."""
        config = write_config(run_dir)
        assert config.file.endswith(CONFIG_FILE)
        model = config.data
        remote = model.Publish[REMOTE]
        assert isinstance(remote, CosRemote)
        assert remote.Bucket == BUCKET
        assert remote.Prefix == REMOTE_PREFIX
        assert remote.EnvPrefix == ENV_PREFIX
        assert remote.GraceSeconds == 3600
        assert remote.Concurrency == 4
        assert model.workspace_name == CHANNEL

    def test_a_broken_config_is_refused(self, fs, run_dir):
        """A value the reader refused is not a run with a default instead."""
        write_text(run_dir, REPO_TEXT + '    Concurrency: abc\n')
        config = PackRepoConfig(CONFIG_FILE)
        # the reader falls back to the default of the value, and records why
        assert config.data.Publish[REMOTE].Concurrency == 4
        with pytest.raises(ConfigError, match='Concurrency'):
            require_valid(config)

    def test_an_unknown_field_drops_the_remote_and_names_itself(self, fs, run_dir):
        """A typo that drops the whole map is reported by its own name.

        A struct fails as a whole, so the remote with the unknown field is
        dropped: a run that trusted the result would publish nowhere and report
        success, which is what require_valid() refuses.
        """
        write_text(run_dir, REPO_TEXT + '    Typo: 1\n')
        config = PackRepoConfig(CONFIG_FILE)
        assert config.data.Publish == {}
        with pytest.raises(ConfigError, match='Typo'):
            require_valid(config)

    def test_an_unknown_driver_is_refused(self, fs, run_dir):
        """A remote of a driver that does not exist is not dropped silently."""
        write_text(run_dir, REPO_TEXT.replace("Driver: cos", "Driver: s3"))
        config = PackRepoConfig(CONFIG_FILE)
        assert config.data.Publish == {}
        with pytest.raises(ConfigError, match='Driver'):
            require_valid(config)

    def test_a_valid_config_passes_the_check(self, fs, run_dir):
        """The config of a repo that publishes to the fake service is usable."""
        config = write_config(run_dir)
        assert require_valid(config) is None
        assert config.errors == []

    def test_the_driver_key_is_kept(self, fs, run_dir):
        """The file keeps saying which driver a remote is, through a rewrite."""
        write_config(run_dir, make_model({
            REMOTE: make_remote(),
            'nas': WebDavRemote(BaseUrl='https://nas.example/dav/pack'),
        }))
        with open(f'{run_dir}/config/{CONFIG_FILE}', encoding='utf-8') as f:
            text = f.read()
        assert 'Driver: cos' in text
        assert 'Driver: webdav' in text
        model = PackRepoConfig(CONFIG_FILE).data
        assert isinstance(model.Publish['nas'], WebDavRemote)

    def test_a_negative_grace_is_refused(self, fs, run_dir):
        """A negative grace would delete a generation before it was published."""
        with pytest.raises(ConfigError, match='GraceSeconds'):
            write_config(run_dir, make_model({REMOTE: make_remote(GraceSeconds=-1)}))

    def test_a_timeout_of_zero_is_refused(self, fs, run_dir):
        """A timeout of zero fails every request of the remote."""
        with pytest.raises(ConfigError, match='TimeoutSeconds'):
            write_config(run_dir, make_model({REMOTE: make_remote(TimeoutSeconds=0)}))


class TestCredentials:
    """The environment variables of a remote."""

    def test_driver_name(self):
        """The driver of a remote is the tag of its variant."""
        assert driver_name(make_remote()) == 'cos'
        assert driver_name(WebDavRemote(BaseUrl='https://nas.example/dav')) == 'webdav'

    def test_env_value(self):
        """A value is read from the environment, an unset one is ''."""
        environ = {f'{ENV_PREFIX}_SECRET_ID': 'id'}
        assert env_value(ENV_PREFIX, 'SECRET_ID', environ=environ) == 'id'
        assert env_value(ENV_PREFIX, 'SESSION_TOKEN', environ=environ) == ''

    def test_a_missing_required_value_names_the_variable(self):
        """The error names the variable, never a value."""
        environ = {f'{ENV_PREFIX}_SECRET_ID': 'the-id-must-not-be-printed'}
        with pytest.raises(ConfigError) as caught:
            env_value(ENV_PREFIX, 'SECRET_KEY', required=True, environ=environ)
        message = str(caught.value)
        assert f'{ENV_PREFIX}_SECRET_KEY' in message
        assert 'the-id-must-not-be-printed' not in message

    def test_the_default_prefix_of_the_driver(self, monkeypatch):
        """A remote without EnvPrefix reads the variables of the driver prefix."""
        remote = make_remote(EnvPrefix='')
        monkeypatch.setenv('ALASIO_COS_SECRET_ID', 'a')
        monkeypatch.setenv('ALASIO_COS_SECRET_KEY', 'b')
        # a store that read the two values instead of refusing the remote
        assert create_store(REMOTE, remote, CHANNEL) is not None
        # and one variable missing is refused
        monkeypatch.delenv('ALASIO_COS_SECRET_KEY')
        with pytest.raises(ConfigError, match='ALASIO_COS_SECRET_KEY'):
            create_store(REMOTE, remote, CHANNEL)


class TestCreateStore:
    """Building the store is the check: there is no separate validate pass."""

    @pytest.fixture(autouse=True)
    def env(self, monkeypatch):
        """The credentials of the fake service, so only the field checks fail."""
        monkeypatch.setenv(f'{ENV_PREFIX}_SECRET_ID', 'id')
        monkeypatch.setenv(f'{ENV_PREFIX}_SECRET_KEY', 'key')

    def test_an_empty_bucket(self):
        """A remote of the cos driver needs a bucket."""
        with pytest.raises(ConfigError, match='Bucket'):
            create_store(REMOTE, make_remote(Bucket=''), CHANNEL)

    def test_an_empty_region(self):
        """A remote of the cos driver needs a region."""
        with pytest.raises(ConfigError, match='Region'):
            create_store(REMOTE, make_remote(Region=''), CHANNEL)

    def test_an_unusable_bucket_name(self):
        """A bucket name with a path separator cannot be a host."""
        with pytest.raises(ConfigError, match='Bucket'):
            create_store(REMOTE, make_remote(Bucket='a/b'), CHANNEL)

    def test_a_missing_credential_is_refused_by_the_same_call(self, monkeypatch):
        """The store is built and its credentials read in one call, so a missing
        variable cannot slip through between a check and a build."""
        monkeypatch.delenv(f'{ENV_PREFIX}_SECRET_KEY')
        with pytest.raises(ConfigError, match='SECRET_KEY'):
            create_store(REMOTE, make_remote(), CHANNEL)

    def test_the_webdav_driver_is_not_implemented(self):
        """A webdav remote is accepted by the model, refused before any request."""
        with pytest.raises(ConfigError, match='webdav'):
            create_store('nas', WebDavRemote(BaseUrl='https://nas.example/dav'), CHANNEL)

    def test_a_usable_remote_builds_a_store(self):
        """The store of the fake service is bound to the workspace of the config."""
        store = create_store(REMOTE, make_remote(), CHANNEL)
        assert store.prefix.rstrip('/') == f'{REMOTE_PREFIX}/{CHANNEL}'

    def test_the_default_config_of_a_repo_has_nothing_to_publish(self):
        """An empty Publish map is valid: the repo publishes nowhere."""
        assert PackRepoModel().Publish == {}
