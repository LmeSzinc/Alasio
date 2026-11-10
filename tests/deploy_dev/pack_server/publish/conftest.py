"""
Fixtures of the publish flow: a run directory of the fake filesystem with a
config and a workspace, a fake COS service, and the store that points one at the
other.

Every test of this package runs against the in-process COS service of
tests/deploy_dev/store/cos/fake_cos.py: no socket is bound, the client of the
store parses a real answer, and the service verifies the signature, Content-MD5
and the listing on its own. The faithfulness of that service is not assumed
here: it is pinned by the real bucket tests of the store
(tests/deploy_dev/store/cos/test_real.py), which check the same properties
against Tencent. The flow tests then drive it as many times as they need, with a
failure injected where a real service cannot be made to fail.

The config of the flow is the config file of a repo, the same file the pack
server reads: the workspace of the repo and the remotes it is mirrored to are
one file, and the flow takes that file as its only input.
"""

import hashlib

import pytest

from alasio.deploy_dev.pack_server.manifest import MANIFEST_FILE, Manifest
from alasio.deploy_dev.pack_server.model import CosRemote, PackRepoConfig, PackRepoModel, RepoConfig
from alasio.deploy_dev.pack_server.publish.drivers import create_store
from alasio.ext import env
from alasio.ext.file.yamlconfig import YamlConfig
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_write
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack_server.conftest import use_memory_lock
from tests.deploy_dev.store.cos.fake_cos import FakeCos

# the environment prefix of the remote of the tests
ENV_PREFIX = 'ALASIO_TEST_COS'
BUCKET = 'alasio-test-1250000000'
REGION = 'ap-shanghai'
# the endpoint of the fake service: no socket is bound, the host only has to be
# stable, and a port that is not the default one keeps the authority in the url
ENDPOINT = 'http://fake-cos.local:9000'
# the prefix of the remote inside the bucket
REMOTE_PREFIX = 'pack'

# the repo of the config: the workspace is {Author}_{Repo}_{Branch}
AUTHOR = 'LmeSzinc'
REPO = 'Repo'
BRANCH = 'master'
CHANNEL = f'{AUTHOR}_{REPO}_{BRANCH}'
# the config file of the repo, the input of the flow
CONFIG_FILE = f'{CHANNEL}.yaml'
REMOTE = 'cos-main'


@pytest.fixture
async def fake_cos():
    """
    The fake COS service of one test.

    Yields:
        FakeCos: The service, its clients are closed when the test ends
    """
    service = FakeCos()
    try:
        yield service
    finally:
        await service.aclose()


@pytest.fixture
def credentials_env(monkeypatch, fake_cos):
    """
    The environment variables of the remote of the tests.

    Returns:
        FakeCos: The service the values belong to
    """
    monkeypatch.setenv(f'{ENV_PREFIX}_SECRET_ID', fake_cos.secret_id)
    monkeypatch.setenv(f'{ENV_PREFIX}_SECRET_KEY', fake_cos.secret_key)
    return fake_cos


@pytest.fixture
def run_dir(fs, monkeypatch):
    """
    A run directory of the pack server, set as env.PROJECT_ROOT.

    The lock of a channel is swapped for the in-memory lock of the tests: the
    real lock is a SQLite database, opened in the C layer of the interpreter
    where the in-memory filesystem cannot serve it, see the conftest of the
    pack server.

    Returns:
        PathStr: Absolute path of the run directory
    """
    root = PathStr.new(fs.root_dir.path).joinpath('pack_server')
    root.makedirs(exist_ok=True)
    monkeypatch.setattr(env, 'PROJECT_ROOT', root)
    use_memory_lock(monkeypatch)
    return root


def make_remote(**kwargs):
    """
    The remote of the tests, pointed at the fake service.

    Args:
        **kwargs: Overrides of the config fields (GraceSeconds, Concurrency, ...)

    Returns:
        CosRemote: The remote
    """
    fields = dict(
        Bucket=BUCKET,
        Region=REGION,
        Endpoint=ENDPOINT,
        Prefix=REMOTE_PREFIX,
        EnvPrefix=ENV_PREFIX,
    )
    fields.update(kwargs)
    return CosRemote(**fields)


def make_model(remotes=None):
    """
    The config of a repo, in memory.

    Args:
        remotes (dict): Name -> remote, {'cos-main': make_remote()} by default

    Returns:
        PackRepoModel: The config, not read from a file
    """
    return PackRepoModel(
        Repo=RepoConfig(
            Remote='https://github.com/LmeSzinc/Repo',
            Author=AUTHOR,
            Repo=REPO,
            Branch=BRANCH,
        ),
        Publish=remotes if remotes is not None else {REMOTE: make_remote()},
    )


def write_config(run_dir, model=None, name=CONFIG_FILE):
    """
    Write the config file of a repo and read it back.

    The file is written with the model of the pack server, then read through
    PackRepoConfig, the reader the flow uses.

    Args:
        run_dir (PathStr): Run directory
        model (PackRepoModel): The config to write, make_model() by default
        name (str): File name in the config folder. Defaults to CONFIG_FILE

    Returns:
        PackRepoConfig: The config, reading the file
    """
    model = make_model() if model is None else model
    folder = PathStr.new(run_dir).joinpath(PackRepoConfig.CONFIG_FOLDER)
    folder.makedirs(exist_ok=True)
    YamlConfig(folder.joinpath(name), model=PackRepoModel).write_data(model)
    return PackRepoConfig(name)


def make_store(remote, workspace, service):
    """
    The store of one workspace on the fake service.

    The credentials are read from the environment by the driver, so the
    credentials_env fixture has to be active.

    Args:
        remote (CosRemote): The configured remote
        workspace (str): Name of the workspace
        service (FakeCos): The service to talk to

    Returns:
        CosStore: The store bound to the workspace inside the remote prefix
    """
    return create_store(REMOTE, remote, workspace, client=service.client())


class Generation:
    """
    One generation of a workspace on the fake filesystem.

    A generation is what the generator of the pack server leaves behind: the
    files of the run, the published contract next to them and the barrier inside
    `packrepo/`. The helper writes all three in the order of the generator, so a
    test that builds two generations gets exactly the pair of files the flow is
    written for.

    Attributes:
        workspace (PathStr): Absolute path of the workspace
        version (str): Version of the current generation
        checksum (str): Index pack checksum of the current generation
        files (dict): Relative key -> bytes of the content of the generation
    """

    def __init__(self, workspace):
        """
        Args:
            workspace (PathStr): Absolute path of the workspace, created here
        """
        self.workspace = PathStr.new(workspace)
        self.workspace.makedirs(exist_ok=True)
        self.version = ''
        self.checksum = ''
        self.files = {}

    def write(self, version, files, generated_at=None):
        """
        Write one generation: the files, then the barrier, then the contract.

        Args:
            version (str): Version the client switches to, e.g. '3f2b9c1d'
            files (dict): Relative key -> bytes of the content
            generated_at (int): Time of the contract, now when None

        Returns:
            Manifest: The contract that was written
        """
        self.version = version
        self.checksum = hashlib.sha1(version.encode()).hexdigest()
        self.files = dict(files)
        for relative, data in files.items():
            self._write(relative, data)
        barrier = version.encode() + bytes.fromhex(self.checksum)
        self._write('packrepo/latest.pack', barrier)
        manifest = Manifest.build(
            channel=self.workspace.name,
            version=version,
            checksum=self.checksum,
            files=sorted(files) + ['packrepo/latest.pack'],
            generated_at=generated_at,
        )
        manifest.write(self.path(MANIFEST_FILE))
        return manifest

    def remove(self, relative):
        """
        Remove a file of the workspace, as a broken generation would have it.

        Args:
            relative (str): Key to remove
        """
        import os
        os.remove(str(self.path(relative)))

    def path(self, relative):
        """
        The absolute path of a relative key.

        Args:
            relative (str): '/'-separated key

        Returns:
            PathStr: Absolute path
        """
        path = self.workspace
        for part in relative.split('/'):
            path = path.joinpath(part)
        return path

    def _write(self, relative, data):
        """
        Write a file, creating its folders.

        Args:
            relative (str): '/'-separated key
            data (bytes): Content
        """
        parts = relative.split('/')[:-1]
        folder = self.workspace
        for part in parts:
            folder = folder.joinpath(part)
        if parts:
            folder.makedirs(exist_ok=True)
        atomic_write(self.path(relative), data)


@pytest.fixture
def workspace(run_dir):
    """
    A workspace of the fake filesystem with one generation in it.

    Returns:
        Generation: The generation, `write()` puts another one in the same folder
    """
    generation = Generation(PathStr.new(run_dir).joinpath('pack').joinpath(CHANNEL))
    generation.write('aaaaaaaa', {
        'packrepo/aaaaaaaa/full.pack': b'full pack of aaaaaaaa',
        'packrepo/aaaaaaaa/update_00000001.pack': b'update pack',
        'wheel/msgspec/0.22.0/msgspec-0.22.0-py3-none-any.whl': b'wheel bytes',
    })
    return generation


@pytest.fixture
def config(run_dir, workspace):
    """
    The config of the repo of the workspace, pointed at the fake service.

    Returns:
        PackRepoConfig: The config, reading config/{CHANNEL}.yaml
    """
    return write_config(run_dir)


# the key prefix of the workspace of the tests, inside the bucket
PREFIX = f'{REMOTE_PREFIX}/{CHANNEL}/'


async def fill(workspace, service, remote=None, concurrency=1):
    """
    Publish the generation of the workspace to the service.

    Args:
        workspace (Generation): The workspace
        service (FakeCos): The service
        remote (CosRemote): The remote, the one of the tests when None
        concurrency (int): Reads and transfers in flight. Defaults to 1, so the
            order of a run is the order of the manifest

    Returns:
        CosStore: The store, closed by the caller
    """
    # the imports stay here: the flow modules import the config model, which
    # this module builds, and a module level import would be a cycle
    from alasio.deploy_dev.pack_server.publish.channel import ChannelUpload
    from alasio.deploy_dev.pack_server.publish.local import LocalGeneration
    remote = remote or make_remote()
    store = make_store(remote, CHANNEL, service)
    local = LocalGeneration(workspace.workspace, concurrency)
    await ChannelUpload(local, store, remote_name='cos-main', concurrency=concurrency).run()
    return store


def age_objects(service, seconds, keys=None):
    """
    Move the objects of the workspace into the past.

    The retirement decides the age of a generation from the LastModified of its
    barrier, so a test that wants a generation to be old backdates what the
    service reports instead of waiting an hour.

    Args:
        service (FakeCos): The service
        seconds (float): Seconds to subtract from every last_modified
        keys (Iterable[str]): Only these keys, every key of the prefix by default
    """
    wanted = keys if keys is not None else service.objects
    for key in wanted:
        if key.startswith(PREFIX):
            service.objects[key].last_modified -= seconds
