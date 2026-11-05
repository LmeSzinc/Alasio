"""
Tests for the packs of the python dependencies of a repo: repo_dep.RepoDep.

The step fetches the pure python wheel of every version of the window to the
private cache and writes the channel of every dependency the mod entry declares
in pack_deps: the full pack of the target version and an update pack
from every lookback version to it.

The tests run the step on its own, the way repo.Repo wires it, and verify the
output with the decode side of the library: the packs are decoded and applied
to an unpacked old version with UpdateJob, the result is compared to the
install tree of the target wheel, so the test covers the path from the index to
the client. The mirror is the MockMirror of the fetch tests, the git repo is a
MockGitRepo of a linear chain.
"""
import pytest
import trio

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy.pack.job_update import UpdateJob
from alasio.deploy.simple_pip.parse_req import DepInfo
from alasio.deploy_dev.pack import pack_wheel
from alasio.deploy_dev.pack._pack_cache import PackCache
from alasio.deploy_dev.pack.pack_wheel import PackWheel
from alasio.deploy_dev.pack_server import repo_base, repo_dep
from alasio.deploy_dev.pack_server.fetch_wheel import WheelNotFoundError
from alasio.deploy_dev.pack_server.model import LookbackConfig, PackRepoModel
from alasio.deploy_dev.pack_server.pack_check import PackChecksum
from alasio.deploy_dev.pack_server.repo_base import DepBuildInfo, RepoBase
from alasio.deploy_dev.pack_server.repo_dep import RepoDep
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_write, file_read_bytes
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy.simple_pip.conftest import build_wheel
from tests.deploy_dev.pack_server.conftest import join_path
from tests.deploy_dev.pack_server.test_repo import (
    CORE_V1, CORE_V2, PROXY, make_config, make_proxy_config, make_repo, pack_file, read_tree, register_wheel,
    register_wheels, requirement
)

# folder of the workspace of the config in the run directory
WORKSPACE = 'pack/Author_Repo_master'

# folder of the private cache of the workspace
CACHE_ROOT = f'{WORKSPACE}/.wheel_cache'

# folder of the channels of the dependencies
CHANNEL_ROOT = f'{WORKSPACE}/packdep'


@pytest.fixture(autouse=True)
def fresh_wheel_cache(monkeypatch):
    """
    Fresh cache of the wheel pipeline for one test, see test_pack_wheel.
    """
    monkeypatch.setattr(pack_wheel, 'WHEEL_CACHE', PackCache())


class DepFlow:
    """
    The dependency packs of a repo, for the tests.

    The flow of repo.Repo runs RepoDep between the wheel step and the repo tree
    pack; the tests of this module run the step on its own, so what it reads
    from the base is only the workspace and the window.
    """

    def __init__(self, repo, config=None, client=None):
        """
        Args:
            repo (MockGitRepo): Git repo to pack
            config (PackRepoModel, optional): Config of the repo. Defaults to
                None, the config of the tests
            client (httpx2.AsyncClient, optional): Client of the mock mirror
        """
        self.base = RepoBase(repo, config if config is not None else make_config(), client)
        self.dep = RepoDep(self.base)

    def run(self):
        """Fetch the wheels and write the packs of the channels."""
        self.dep.run()

    def generate(self):
        """Run the step, the whole work of the step."""
        self.run()
        return self


def cache_folder(run_dir, name, version):
    """
    Folder of a version in the private cache of the workspace.

    Returns:
        PathStr: {run}/pack/Author_Repo_master/.wheel_cache/{name}/{version}
    """
    return join_path(run_dir, CACHE_ROOT, name, version)


def channel_folder(run_dir, name):
    """
    Folder of the channel of a dependency.

    Returns:
        PathStr: {run}/pack/Author_Repo_master/packdep/{name}
    """
    return join_path(run_dir, CHANNEL_ROOT, name)


def register_demo_wheel(mirror, version='1.0', core=b'DEMO = 1\n'):
    """
    Build a wheel of demo on the fake filesystem and register it in the mirror.

    Args:
        mirror (MockMirror): Mirror to register the wheel in
        version (str): Version of the wheel. Defaults to '1.0'
        core (bytes): Content of the module of the wheel

    Returns:
        bytes: Content of the wheel
    """
    path = f'/w/demo-{version}-py3-none-any.whl'
    build_wheel(path, {'demo/__init__.py': core}, name='demo', version=version)
    content = file_read_bytes(path)
    mirror.register('demo', version, content=content)
    return content


def install_tree(pack, name):
    """
    The files a client holds after unpacking the pack of a wheel.

    Args:
        pack (PackWheel): Pack of the wheel
        name (str): Distribution name, the folder of the index pack

    Returns:
        dict[str, bytes]: {path: content}
    """
    tree = {path: file.content for path, file in pack.tree.items()}
    tree[f'.pack/{name}/index.pack'] = pack.index_pack
    return tree


def make_client(monkeypatch, fs, name='client'):
    """
    Move env.PROJECT_ROOT to a client folder of the fake filesystem.

    Args:
        monkeypatch (MonkeyPatch): Patcher of the test
        fs (FakeFilesystem): Fake filesystem of the test
        name (str): Name of the folder. Defaults to 'client'

    Returns:
        PathStr: Path of the client folder
    """
    client = PathStr.new(fs.root_dir.path).joinpath(name)
    fs.create_dir(client)
    monkeypatch.setattr(env, 'PROJECT_ROOT', client)
    return client


class TestFetch:
    """The wheels of the window, the input of the build."""

    def test_fetch_of_every_version(self, fs, run_dir, mirror):
        """The pure python wheel of every version is fetched to its cache folder."""
        register_wheels(mirror)
        gen = DepFlow(make_repo(), client=mirror.client)

        with logger.mock_capture_writer() as capture:
            gen.run()

        assert capture.concat() == f"""\
INFO | Using pack_deps of module/config/_index/mod_entry.yaml at c3: httpx
INFO | [1/3] Fetching wheel of "httpx==0.28.1", index="http://mirror", role=target
INFO | Download index page: "http://mirror/httpx/"
INFO | Download wheel: "http://mirror/packages/httpx-0.28.1-py3-none-any.whl" -> "{cache_folder(run_dir, 'httpx', '0.28.1')}/httpx-0.28.1-py3-none-any.whl"
INFO | [2/3] Fetching wheel of "httpx==0.27.8", index="http://mirror", role=historical
INFO | Download index page: "http://mirror/httpx/"
INFO | Download wheel: "http://mirror/packages/httpx-0.27.8-py3-none-any.whl" -> "{cache_folder(run_dir, 'httpx', '0.27.8')}/httpx-0.27.8-py3-none-any.whl"
INFO | [3/3] Fetching wheel of "httpx==0.27.7", index="http://mirror", role=historical
INFO | Download index page: "http://mirror/httpx/"
INFO | Download wheel: "http://mirror/packages/httpx-0.27.7-py3-none-any.whl" -> "{cache_folder(run_dir, 'httpx', '0.27.7')}/httpx-0.27.7-py3-none-any.whl"
INFO | Generating the dependency packs of "Author_Repo_master": 1 dependencies, 3 versions
INFO | [1/1] Building dependency packs of "httpx": 3 versions, target=0.28.1
INFO | Writing full pack: "{channel_folder(run_dir, 'httpx')}/0.28.1/full.pack"
INFO | Writing update pack: 0.27.8 -> 0.28.1, "{channel_folder(run_dir, 'httpx')}/0.28.1/update_0.27.8.pack"
INFO | Writing update pack: 0.27.7 -> 0.28.1, "{channel_folder(run_dir, 'httpx')}/0.28.1/update_0.27.7.pack"
INFO | Dependency packs of "Author_Repo_master" generated
"""
        # every version keeps its own snapshot of the page in its own folder:
        # the page of the distribution is read once per version of the window
        assert mirror.requests.count('http://mirror/httpx/') == 3
        for version in ('0.27.7', '0.27.8', '0.28.1'):
            assert cache_folder(run_dir, 'httpx', version).joinpath(
                f'httpx-{version}-py3-none-any.whl').isfile()

    def test_up_to_date_cache_makes_no_request(self, fs, run_dir, mirror):
        """A run over the cache and the packs of an earlier run writes nothing."""
        register_wheels(mirror)
        repo = make_repo()
        DepFlow(repo, client=mirror.client).run()
        requests = list(mirror.requests)
        tree = read_tree(channel_folder(run_dir, 'httpx'))

        with logger.mock_capture_writer() as capture:
            DepFlow(repo, client=mirror.client).run()

        assert capture.concat() == f"""\
INFO | Using pack_deps of module/config/_index/mod_entry.yaml at c3: httpx
INFO | [1/3] Fetching wheel of "httpx==0.28.1", index="http://mirror", role=target
INFO | Wheel exists, skipped: "{cache_folder(run_dir, 'httpx', '0.28.1')}/httpx-0.28.1-py3-none-any.whl"
INFO | [2/3] Fetching wheel of "httpx==0.27.8", index="http://mirror", role=historical
INFO | Wheel exists, skipped: "{cache_folder(run_dir, 'httpx', '0.27.8')}/httpx-0.27.8-py3-none-any.whl"
INFO | [3/3] Fetching wheel of "httpx==0.27.7", index="http://mirror", role=historical
INFO | Wheel exists, skipped: "{cache_folder(run_dir, 'httpx', '0.27.7')}/httpx-0.27.7-py3-none-any.whl"
INFO | Generating the dependency packs of "Author_Repo_master": 1 dependencies, 3 versions
INFO | [1/1] Building dependency packs of "httpx": 3 versions, target=0.28.1
INFO | Full pack exists, skipped: "{channel_folder(run_dir, 'httpx')}/0.28.1/full.pack"
INFO | Update pack exists, skipped: "{channel_folder(run_dir, 'httpx')}/0.28.1/update_0.27.8.pack"
INFO | Update pack exists, skipped: "{channel_folder(run_dir, 'httpx')}/0.28.1/update_0.27.7.pack"
INFO | Dependency packs of "Author_Repo_master" generated
"""
        # the snapshot of the page and the wheel answer every version, the
        # packs of the channel are kept: no request and no write at all
        assert mirror.requests == requests
        assert read_tree(channel_folder(run_dir, 'httpx')) == tree

    def test_missing_target_wheel(self, fs, run_dir, mirror):
        """A target without a usable wheel fails the run before a pack is written."""
        register_wheel(mirror, '0.27.8', CORE_V1)
        register_wheel(mirror, '0.27.7', CORE_V1)
        gen = DepFlow(make_repo(), client=mirror.client)

        with pytest.raises(WheelNotFoundError) as e:
            gen.run()

        assert str(e.value) == (
            'No usable wheel of the target dependency "httpx==0.28.1", '
            'the dependency packs cannot be generated: '
            'No wheel of "httpx==0.28.1" in the index')
        # the fetch of the whole window runs before the first pack: nothing of
        # the channel is on the disk
        assert not channel_folder(run_dir, 'httpx').exists()
        assert gen.dep.published == {}

    def test_missing_historical_wheel(self, fs, run_dir, mirror):
        """A lookback version without a usable wheel fails the complete build."""
        register_wheel(mirror, '0.28.1', CORE_V2)
        register_wheel(mirror, '0.27.7', CORE_V1)
        gen = DepFlow(make_repo(), client=mirror.client)

        with pytest.raises(WheelNotFoundError) as e:
            gen.run()

        assert str(e.value) == (
            'No usable wheel of the historical dependency "httpx==0.27.8", '
            'the dependency packs cannot be generated: '
            'No wheel of "httpx==0.27.8" in the index')
        assert not channel_folder(run_dir, 'httpx').exists()

    def test_no_dependency(self, fs, run_dir, mirror):
        """A repo whose entry pins no pack dependency fetches nothing."""
        register_wheels(mirror)
        gen = DepFlow(make_repo(entry=None), client=mirror.client)

        with logger.mock_capture_writer() as capture:
            gen.run()
        assert capture.concat() == """\
INFO | Using pack_deps of module/config/_index/mod_entry.yaml at c3: alasio
WARNING | Dependency "alasio" is not pinned to a version in the lookback window, nothing is built of it; a dependency of the mod entry pack_deps must be pinned with "==" in the dependency file of the repo
INFO | No dependency to build of "Author_Repo_master", skipped
"""
        assert mirror.requests == []
        assert gen.dep.published == {}


class TestProxy:
    """The downloads go through the client of the step, see RepoBase.fetch_client."""

    def test_wheels_through_the_proxy(self, fs, run_dir, mirror, monkeypatch):
        """A step without an injected client fetches with the client the proxy built."""
        register_wheels(mirror)
        created = []

        def create_client(proxy):
            created.append(proxy)
            return mirror.client

        monkeypatch.setattr(repo_base, 'create_client', create_client)
        gen = DepFlow(make_repo(), config=make_proxy_config())

        with logger.mock_capture_writer() as capture:
            gen.run()

        assert capture.concat() == f"""\
INFO | Using pack_deps of module/config/_index/mod_entry.yaml at c3: httpx
INFO | Downloading the wheels through the proxy "{PROXY}"
INFO | [1/3] Fetching wheel of "httpx==0.28.1", index="http://mirror", role=target
INFO | Download index page: "http://mirror/httpx/"
INFO | Download wheel: "http://mirror/packages/httpx-0.28.1-py3-none-any.whl" -> "{cache_folder(run_dir, 'httpx', '0.28.1')}/httpx-0.28.1-py3-none-any.whl"
INFO | [2/3] Fetching wheel of "httpx==0.27.8", index="http://mirror", role=historical
INFO | Download index page: "http://mirror/httpx/"
INFO | Download wheel: "http://mirror/packages/httpx-0.27.8-py3-none-any.whl" -> "{cache_folder(run_dir, 'httpx', '0.27.8')}/httpx-0.27.8-py3-none-any.whl"
INFO | [3/3] Fetching wheel of "httpx==0.27.7", index="http://mirror", role=historical
INFO | Download index page: "http://mirror/httpx/"
INFO | Download wheel: "http://mirror/packages/httpx-0.27.7-py3-none-any.whl" -> "{cache_folder(run_dir, 'httpx', '0.27.7')}/httpx-0.27.7-py3-none-any.whl"
INFO | Generating the dependency packs of "Author_Repo_master": 1 dependencies, 3 versions
INFO | [1/1] Building dependency packs of "httpx": 3 versions, target=0.28.1
INFO | Writing full pack: "{channel_folder(run_dir, 'httpx')}/0.28.1/full.pack"
INFO | Writing update pack: 0.27.8 -> 0.28.1, "{channel_folder(run_dir, 'httpx')}/0.28.1/update_0.27.8.pack"
INFO | Writing update pack: 0.27.7 -> 0.28.1, "{channel_folder(run_dir, 'httpx')}/0.28.1/update_0.27.7.pack"
INFO | Dependency packs of "Author_Repo_master" generated
"""
        # the wheels are the files of the mirror, fetched by the client the
        # proxy built, and the client is closed with the step
        assert created == [PROXY]
        assert mirror.client.is_closed is True
        assert join_path(channel_folder(run_dir, 'httpx'), '0.28.1', 'full.pack').isfile()

    def test_one_client_of_the_step(self, fs, run_dir, mirror, monkeypatch):
        """Without a proxy the versions of the window share one client of the step."""
        register_wheels(mirror)
        created = []

        def create_client(proxy):
            created.append(proxy)
            return mirror.client

        monkeypatch.setattr(repo_base, 'create_client', create_client)
        gen = DepFlow(make_repo())

        with logger.mock_capture_writer() as capture:
            gen.run()

        # three versions of the window, one client: the pages and the wheels of
        # all of them go through the same connection pool, closed at the end
        assert created == ['']
        assert 'Downloading the wheels through the proxy' not in capture.concat()
        assert mirror.requests == [
            'http://mirror/httpx/',
            'http://mirror/packages/httpx-0.28.1-py3-none-any.whl',
            'http://mirror/httpx/',
            'http://mirror/packages/httpx-0.27.8-py3-none-any.whl',
            'http://mirror/httpx/',
            'http://mirror/packages/httpx-0.27.7-py3-none-any.whl',
        ]
        assert mirror.client.is_closed is True


class TestPacks:
    """The channel of a dependency: the layout and its content."""

    def test_full_pack_is_the_wheel_tree(self, fs, run_dir, mirror):
        """The full pack of the target decodes to the install tree of its wheel."""
        register_wheels(mirror)
        DepFlow(make_repo(), client=mirror.client).run()

        decoder = PackDecodeBase(file_read_bytes(pack_file(run_dir, '0.28.1', 'full.pack')))
        decoder.validate()
        assert decoder.current_version == '0.28.1'
        assert decoder.old_version == ''
        pack = PackWheel(join_path(
            cache_folder(run_dir, 'httpx', '0.28.1'), 'httpx-0.28.1-py3-none-any.whl'))
        for path, wheel_file in pack.tree.items():
            info = decoder.fileinfo[path]
            assert info.edit != 2
            assert bytes(decoder.catfile(info)) == wheel_file.content, path
        # the ledger of the version is the index section of the pack, not a
        # record of the tree: the client writes it from the index pack
        assert '.pack/index.pack' not in decoder.fileinfo

    def test_update_packs(self, fs, run_dir, mirror):
        """An update pack from every lookback version to the target is written."""
        register_wheels(mirror)
        DepFlow(make_repo(), client=mirror.client).run()

        for old in ('0.27.7', '0.27.8'):
            file = pack_file(run_dir, '0.28.1', f'update_{old}.pack')
            assert file.isfile()
            decoder = PackDecodeBase(file_read_bytes(file))
            decoder.validate()
            assert decoder.current_version == '0.28.1'
            assert decoder.old_version == old
            # the ledger of the dependency is patched by the update
            assert '.pack/index.pack' in decoder.fileinfo
        # every pack of the channel lives in the folder of the target version
        assert list(channel_folder(run_dir, 'httpx').iter_foldernames()) == ['0.28.1']
        assert sorted(channel_folder(run_dir, 'httpx').joinpath('0.28.1').iter_filenames()) == [
            'full.pack', 'update_0.27.7.pack', 'update_0.27.8.pack']

    def test_update_pack_records(self, fs, run_dir, mirror):
        """The update pack records the changes of the install tree, not the tree."""
        register_wheels(mirror)
        DepFlow(make_repo(), client=mirror.client).run()

        decoder = PackDecodeBase(file_read_bytes(
            pack_file(run_dir, '0.28.1', 'update_0.27.8.pack')))
        # the module of the wheel changed
        modified = decoder.fileinfo['httpx/core.py']
        assert modified.edit == 1
        assert modified.data_size > 0
        # the version marker of the old release is gone, the new one is added
        assert decoder.fileinfo['httpx/version-0.27.8.txt'].edit == 2
        assert decoder.fileinfo['httpx/version-0.28.1.txt'].edit == 0

    def test_published_snapshot(self, fs, run_dir, mirror):
        """The published snapshot is the version and the checksum of the full pack."""
        register_wheels(mirror)
        gen = DepFlow(make_repo(), client=mirror.client)
        gen.run()

        file = pack_file(run_dir, '0.28.1', 'full.pack')
        assert gen.dep.published == {
            'httpx': ('0.28.1', PackChecksum.from_file(file).index_checksum)}

    def test_single_version(self, fs, run_dir, mirror):
        """A repo of one commit has the full pack only, no update pack."""
        register_wheels(mirror)
        DepFlow(make_repo({'c1': requirement('httpx==0.28.1')}), client=mirror.client).run()

        assert list(channel_folder(run_dir, 'httpx').joinpath('0.28.1').iter_filenames()) == [
            'full.pack']

    def test_lookback_window(self, fs, run_dir, mirror):
        """Only the versions of the lookback window get an update pack."""
        register_wheels(mirror)
        config = PackRepoModel(
            Repo=make_config().Repo, Lookback=LookbackConfig(MaxCommitCount=2, MaxCommitDay=0))
        DepFlow(make_repo(), config, client=mirror.client).run()

        # the latest commit counts into MaxCommitCount: 0.27.8 is the only
        # lookback version, 0.27.7 is out of the window and is not fetched
        target = channel_folder(run_dir, 'httpx').joinpath('0.28.1')
        assert target.joinpath('update_0.27.8.pack').isfile()
        assert not target.joinpath('update_0.27.7.pack').exists()
        assert not cache_folder(run_dir, 'httpx', '0.27.7').exists()

    def test_multiple_dependencies(self, fs, run_dir, mirror):
        """Every pack channel of the entry gets a channel of its own."""
        register_wheels(mirror)
        register_demo_wheel(mirror)
        requirements = {
            'c1': requirement('httpx==0.27.7') + requirement('demo==1.0'),
            'c2': requirement('httpx==0.27.8') + requirement('demo==1.0'),
            'c3': requirement('httpx==0.28.1') + requirement('demo==1.0'),
        }
        entry = 'Mod:\n  name: m\nUpdate:\n  pack_deps:\n  - httpx\n  - demo\n'
        gen = DepFlow(make_repo(requirements, entry=entry), client=mirror.client)

        with logger.mock_capture_writer() as capture:
            gen.run()

        log = capture.concat()
        assert 'INFO | [1/2] Building dependency packs of "httpx": 3 versions, target=0.28.1\n' in log
        assert 'INFO | [2/2] Building dependency packs of "demo": 1 versions, target=1.0\n' in log
        assert list(gen.dep.published) == ['httpx', 'demo']
        assert pack_file(run_dir, '0.28.1', 'full.pack').isfile()
        assert join_path(channel_folder(run_dir, 'demo'), '1.0', 'full.pack').isfile()

    def test_update_applies(self, fs, run_dir, mirror, monkeypatch):
        """The update pack upgrades the install tree of the old version."""
        register_wheels(mirror)
        DepFlow(make_repo(), client=mirror.client).run()
        old_pack = PackWheel(join_path(
            cache_folder(run_dir, 'httpx', '0.27.8'), 'httpx-0.27.8-py3-none-any.whl'))
        new_pack = PackWheel(join_path(
            cache_folder(run_dir, 'httpx', '0.28.1'), 'httpx-0.28.1-py3-none-any.whl'))
        update = file_read_bytes(pack_file(run_dir, '0.28.1', 'update_0.27.8.pack'))

        # the step fetches on a loop of its own, the client of the channel is
        # applied on another one
        client = make_client(monkeypatch, fs)
        trio.run(self._apply, client, old_pack, new_pack, update)

    @staticmethod
    async def _apply(client, old_pack, new_pack, update):
        """
        Install the old version of the channel, then apply its update pack.
        """
        with logger.mock_capture_writer():
            assert await UnpackJob(b''.join(old_pack.iter_pack_data()), name='httpx').run()
        assert read_tree(client) == install_tree(old_pack, 'httpx')

        job = UpdateJob(update, name='httpx')
        with logger.mock_capture_writer():
            assert await job.run()

        assert job.error == []
        assert read_tree(client) == install_tree(new_pack, 'httpx')
        assert not client.joinpath('.pack/workspace').exists()


class TestKeptPacks:
    """The packs of an earlier run are kept, only the missing ones are built."""

    def test_complete_channel_is_kept(self, fs, run_dir, mirror, monkeypatch):
        """A complete channel is kept as it is: nothing is built again."""
        register_wheels(mirror)
        repo = make_repo()
        DepFlow(repo, client=mirror.client).run()
        tree = read_tree(channel_folder(run_dir, 'httpx'))

        class NoBuild:
            PACK_VERSION = PackWheel.PACK_VERSION

            def __init__(self, *args, **kwargs):
                raise AssertionError('the pack of the channel must not be built again')

        written = []
        monkeypatch.setattr(repo_dep, 'PackWheel', NoBuild)
        monkeypatch.setattr(repo_dep, 'PackWheelUpdate', NoBuild)
        monkeypatch.setattr(
            repo_dep, 'atomic_write_stream', lambda file, data: written.append(str(file)))
        DepFlow(repo, client=mirror.client).run()

        assert written == []
        assert read_tree(channel_folder(run_dir, 'httpx')) == tree

    def test_missing_update_pack_is_built(self, fs, run_dir, mirror, monkeypatch):
        """Only the update pack that is missing is built again."""
        register_wheels(mirror)
        repo = make_repo()
        DepFlow(repo, client=mirror.client).run()
        target = pack_file(run_dir, '0.28.1', 'update_0.27.7.pack')
        data = file_read_bytes(target)
        target.atomic_remove()

        built = []
        real_update = repo_dep.PackWheelUpdate

        class CountingUpdate:
            def __init__(self, new_pack, old_pack):
                built.append(True)
                self._update = real_update(new_pack, old_pack)

            def iter_pack_data(self):
                return self._update.iter_pack_data()

        monkeypatch.setattr(repo_dep, 'PackWheelUpdate', CountingUpdate)
        DepFlow(repo, client=mirror.client).run()

        assert len(built) == 1
        assert file_read_bytes(target) == data

    def test_pack_of_another_version_is_written_again(self, fs, run_dir, mirror):
        """A pack of another version pair is not kept, a channel mixes no versions."""
        register_wheels(mirror)
        repo = make_repo()
        DepFlow(repo, client=mirror.client).run()
        # the update pack of 0.27.8, put at the path of the update pack of 0.27.7
        atomic_write(pack_file(run_dir, '0.28.1', 'update_0.27.7.pack'), file_read_bytes(
            pack_file(run_dir, '0.28.1', 'update_0.27.8.pack')))

        with logger.mock_capture_writer() as capture:
            DepFlow(repo, client=mirror.client).run()

        assert capture.concat() == f"""\
INFO | Using pack_deps of module/config/_index/mod_entry.yaml at c3: httpx
INFO | [1/3] Fetching wheel of "httpx==0.28.1", index="http://mirror", role=target
INFO | Wheel exists, skipped: "{cache_folder(run_dir, 'httpx', '0.28.1')}/httpx-0.28.1-py3-none-any.whl"
INFO | [2/3] Fetching wheel of "httpx==0.27.8", index="http://mirror", role=historical
INFO | Wheel exists, skipped: "{cache_folder(run_dir, 'httpx', '0.27.8')}/httpx-0.27.8-py3-none-any.whl"
INFO | [3/3] Fetching wheel of "httpx==0.27.7", index="http://mirror", role=historical
INFO | Wheel exists, skipped: "{cache_folder(run_dir, 'httpx', '0.27.7')}/httpx-0.27.7-py3-none-any.whl"
INFO | Generating the dependency packs of "Author_Repo_master": 1 dependencies, 3 versions
INFO | [1/1] Building dependency packs of "httpx": 3 versions, target=0.28.1
INFO | Full pack exists, skipped: "{channel_folder(run_dir, 'httpx')}/0.28.1/full.pack"
INFO | Update pack exists, skipped: "{channel_folder(run_dir, 'httpx')}/0.28.1/update_0.27.8.pack"
WARNING | Existing pack is not the pack of this version, writing it again: "{channel_folder(run_dir, 'httpx')}/0.28.1/update_0.27.7.pack"
INFO | Writing update pack: 0.27.7 -> 0.28.1, "{channel_folder(run_dir, 'httpx')}/0.28.1/update_0.27.7.pack"
INFO | Dependency packs of "Author_Repo_master" generated
"""
        decoder = PackDecodeBase(file_read_bytes(
            pack_file(run_dir, '0.28.1', 'update_0.27.7.pack')))
        decoder.validate()
        assert decoder.old_version == '0.27.7'

    def test_file_that_is_not_a_pack_is_written_again(self, fs, run_dir, mirror):
        """A kept file that cannot be read as a pack is overwritten."""
        register_wheels(mirror)
        repo = make_repo()
        DepFlow(repo, client=mirror.client).run()
        file = pack_file(run_dir, '0.28.1', 'update_0.27.7.pack')
        atomic_write(file, b'NOPE' + b'\x00' * 60)

        with logger.mock_capture_writer() as capture:
            DepFlow(repo, client=mirror.client).run()

        assert (
            f'WARNING | Failed to read the existing pack, writing it again: '
            f'Failed to read the pack: not a pack file: b\'NOPE\', "{file}"\n'
        ) in capture.concat()
        decoder = PackDecodeBase(file_read_bytes(file))
        decoder.validate()
        assert decoder.old_version == '0.27.7'

    def test_full_pack_of_another_version_is_written_again(self, fs, run_dir, mirror):
        """A full pack that is the pack of another version is written again."""
        register_wheels(mirror)
        repo = make_repo()
        DepFlow(repo, client=mirror.client).run()
        file = pack_file(run_dir, '0.28.1', 'full.pack')
        atomic_write(file, file_read_bytes(pack_file(run_dir, '0.28.1', 'update_0.27.7.pack')))

        with logger.mock_capture_writer() as capture:
            DepFlow(repo, client=mirror.client).run()

        assert (f'WARNING | Existing pack is not the pack of this version, writing it again: '
                f'"{file}"\n' in capture.concat())
        decoder = PackDecodeBase(file_read_bytes(file))
        decoder.validate()
        assert decoder.old_version == ''


class TestGuards:
    """The names the packdep of the repo tree cannot carry are left out."""

    def test_unsafe_name_is_left_out_of_packdep(self, fs, run_dir, mirror):
        """A channel name that is not a packdep name is written but not published."""
        path = '/w/Demo-1.0-py3-none-any.whl'
        build_wheel(path, {'Demo/__init__.py': b''}, name='Demo', version='1.0')
        base = RepoBase(make_repo(), make_config())
        dep = RepoDep(base)
        # the name of a dependency is normalized by RepoBase.deps, this is the
        # guard of a value that reached the step another way
        info = DepBuildInfo(
            name='Demo', latest=DepInfo(name='Demo', version='1.0'), pack_dep=True)

        with logger.mock_capture_writer() as capture:
            dep._write_dep_packs(info, {('Demo', '1.0'): PathStr.new(path)})

        folder = channel_folder(run_dir, 'Demo')
        assert capture.concat() == f"""\
INFO | Writing full pack: "{folder}/1.0/full.pack"
WARNING | The channel of "Demo==1.0" has no packdep-safe name or version, \
the dependency is left out of packdep: Packdep name is not PEP 503 normalized: 'Demo'
"""
        assert dep.published == {}
        assert join_path(folder, '1.0', 'full.pack').isfile()
