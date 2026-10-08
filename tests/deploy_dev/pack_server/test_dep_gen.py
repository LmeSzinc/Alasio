"""
Tests for DepGen: generate the wheel channel of the dependencies of a repo.

The dependency files of a mock repo are sampled over the lookback window and
the wheels of the pinned versions are served by an in-memory PyPI mirror
(MockMirror, the mock of the fetch tests): the index pages and the wheel files
come from the memory, no socket is bound. The channel is verified with the
decode side of the library: the full packs decode to the install trees of the
wheels, the update packs are applied to an unpacked old version with
UpdateJob and compared against the tree of the new version, and latest.pack
carries the version and the index pack checksum of the full pack.
"""
import os

import pytest

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy.pack.job_update import UpdateJob
from alasio.deploy.pack.server_file import LatestInfo
from alasio.deploy_dev.pack import pack_wheel
from alasio.deploy_dev.pack._pack_cache import PackCache
from alasio.deploy_dev.pack.pack_wheel import PackWheel
from alasio.deploy_dev.pack_server import dep_gen
from alasio.deploy_dev.pack_server.dep_gen import DepGen
from alasio.deploy_dev.pack_server.model import (
    LookbackConfig, PackRepoModel, PythonDepsConfig, PythonMirrorInfo, RepoConfig
)
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_write, file_read_bytes
from alasio.ext.path.iter import iter_files
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack_server.test_fetch_wheel import MockMirror
from tests.deploy_dev.simple_pip.conftest import build_wheel, site_packages

# the dependency files of the mock repo: httpx is pinned to a new version on
# every commit, demo is pinned but not a managed name of the config, so it is
# not fetched and gets no pack
REQUIREMENTS = {
    'c1': 'httpx==0.27.7\ndemo==1.0\n',
    'c2': 'httpx==0.27.8\ndemo==1.0\n',
    'c3': 'httpx==0.28.1\ndemo==1.0\n',
}

# folder of the packs of the config in the run directory, see PackRepoModel
REPO_FOLDER = 'pack/Author_Repo_master'

# folder of the channel of the dependency of the tests
DEP_FOLDER = f'{REPO_FOLDER}/packdep/httpx'

# the content of the module that changes between two versions of the wheel
CORE_V1 = b''.join(b'VALUE_%d = %d\n' % (index, index) for index in range(80))
CORE_V2 = CORE_V1 + b'VALUE_NEW = 99\n'

# the builtin .gitattributes of the library, copied into the fake filesystem
# by the run_dir fixture: MockGitRepo resolves the eol of a file with it,
# see GitAttributes
BUILTIN_GITATTRIBUTES = env.ALASIO_ROOT.joinpath('.gitattributes').atomic_read_bytes()


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    """
    Fresh cache of the wheel pipeline for one test, see test_pack_wheel.
    """
    monkeypatch.setattr(pack_wheel, 'WHEEL_CACHE', PackCache())


@pytest.fixture
def run_dir(fs, monkeypatch):
    """
    A run directory of the pack server in the fake filesystem.

    The builtin .gitattributes of the library is copied into the fake
    filesystem, so a mock repo of the test resolves the eol of its files.

    Returns:
        PathStr: Absolute path of the run directory
    """
    root = PathStr.new(fs.root_dir.path)
    library = root.joinpath('alasio')
    run = root.joinpath('pack_server')
    fs.create_dir(library)
    fs.create_dir(run)
    fs.create_file(library.joinpath('.gitattributes'), contents=BUILTIN_GITATTRIBUTES)
    monkeypatch.setattr(env, 'ALASIO_ROOT', library)
    monkeypatch.setattr(env, 'PROJECT_ROOT', run)
    return run


@pytest.fixture
def mirror():
    """
    The in-memory PyPI mirror the wheels are fetched from.

    Returns:
        MockMirror:
    """
    return MockMirror()


def join_path(root, *parts):
    """
    Join path parts one by one, PathStr.joinpath takes one part only.

    Args:
        root (str): Root path
        *parts (str): Parts to join

    Returns:
        PathStr: Joined path
    """
    path = PathStr.new(root)
    for part in parts:
        path = path.joinpath(part)
    return path


def make_repo(requirements, branch='master'):
    """
    Build a mock repo of a linear commit chain with a requirements file.

    Args:
        requirements (dict): {commit: content of requirements.txt}, the
            oldest commit first, the parent of a commit is the one before it
        branch (str): Branch of the head. Defaults to 'master'.

    Returns:
        MockGitRepo: Repo, the head of the branch is the newest commit
    """
    repo = MockGitRepo()
    parent = None
    for index, (commit, content) in enumerate(requirements.items()):
        repo.register_file(commit, 'requirements.txt', content.encode('utf-8'))
        repo.register_commit(
            commit, parents=[parent] if parent else None,
            author_name='Author', author_time=index, message=f'commit {commit}')
        parent = commit
    repo.register_branch(branch, parent)
    repo.register_head(parent)
    return repo


def make_config(mirror, pack_update=('httpx',), requirement_files=('requirements.txt',)):
    """
    Build a pack config with the PythonDeps group of the tests.

    Args:
        mirror (str | list): PythonDepsConfig.PypiMirror
        pack_update (Iterable[str]): PythonDepsConfig.PackUpdate. Defaults to
            ('httpx',)
        requirement_files (Iterable[str]): PythonDepsConfig.RequirementFiles.
            Defaults to ('requirements.txt',)

    Returns:
        PackRepoModel: Config
    """
    return PackRepoModel(
        Repo=RepoConfig(
            Remote='https://github.com/Author/Repo',
            Author='Author',
            Repo='Repo',
            Branch='master',
        ),
        Lookback=LookbackConfig(MaxCommitDay=0),
        PythonDeps=PythonDepsConfig(
            RequirementFiles=list(requirement_files),
            PypiMirror=mirror,
            PackUpdate=list(pack_update),
        ),
    )


def demo_files(version, core):
    """
    Files of the demo wheel of a version.

    Args:
        version (str): Version of the wheel
        core (bytes): Content of the module that changes between versions

    Returns:
        dict[str, bytes]: {member of the wheel: content}
    """
    return {
        'httpx/__init__.py': b'',
        'httpx/core.py': core,
        f'httpx/version-{version}.txt': f'{version}\n'.encode('utf-8'),
    }


def register_wheel(mirror, version, core):
    """
    Build a wheel of the tests on the fake filesystem and register it in the mirror.

    Args:
        mirror (MockMirror): Mirror to register the wheel in
        version (str): Version of the wheel
        core (bytes): Content of the module that changes between versions

    Returns:
        bytes: Content of the wheel
    """
    path = f'/w/httpx-{version}-py3-none-any.whl'
    build_wheel(path, demo_files(version, core), name='httpx', version=version)
    content = file_read_bytes(path)
    mirror.register('httpx', version, content=content)
    return content


def register_wheels(mirror):
    """
    Register the three versions of the wheel of the mock repo.

    Args:
        mirror (MockMirror): Mirror to register the wheels in
    """
    register_wheel(mirror, '0.27.7', CORE_V1)
    register_wheel(mirror, '0.27.8', CORE_V1)
    register_wheel(mirror, '0.28.1', CORE_V2)


def wheel_file(run_dir, version):
    """
    Path of the fetched wheel of a version in the run directory.

    Returns:
        PathStr: {run}/pack/Author_Repo_master/wheel/httpx/{version}/httpx-{version}-py3-none-any.whl
    """
    return join_path(
        run_dir, REPO_FOLDER, 'wheel', 'httpx', version,
        f'httpx-{version}-py3-none-any.whl')


def pack_file(run_dir, *parts):
    """
    Path of a file of the channel of the dependency in the run directory.

    Returns:
        PathStr: {run}/pack/Author_Repo_master/packdep/httpx/...
    """
    return join_path(run_dir, DEP_FOLDER, *parts)


def read_tree(root):
    """
    Read a folder of the test as {relative path: content}.

    The log folder of the logger is not part of the tree.

    Args:
        root (str): Root folder

    Returns:
        dict[str, bytes]: {filepath: content}
    """
    root = str(root)
    tree = {}
    for path in iter_files(root, recursive=True):
        relative = os.path.relpath(path, root).replace(os.sep, '/')
        if relative.split('/')[0] == 'log':
            continue
        tree[relative] = file_read_bytes(path)
    return tree


class TestSample:
    """The dependencies and versions the lookback window asks for."""

    def test_sample(self, fs, run_dir, mirror):
        """The pins of the latest commit are the target, the pins of the old commits the history."""
        gen = DepGen(make_repo(REQUIREMENTS), make_config(mirror.base))
        sample = gen.sample
        # demo is pinned by the files but not a managed name: no entry
        assert list(sample) == ['httpx']
        assert [(dep.version, dep.commit, dep.target) for dep in sample['httpx']] == [
            ('0.28.1', 'c3', True),
            ('0.27.8', 'c2', False),
            ('0.27.7', 'c1', False),
        ]

    def test_unmanaged_name(self, fs, run_dir, mirror):
        """A pinned name that PackUpdate does not list is not sampled."""
        gen = DepGen(make_repo(REQUIREMENTS), make_config(mirror.base, pack_update=['demo']))
        assert list(gen.sample) == ['demo']
        assert [dep.version for dep in gen.sample['demo']] == ['1.0']

    def test_name_without_pin(self, fs, run_dir, mirror):
        """A managed name the window does not pin is reported, nothing is built of it."""
        with logger.mock_capture_writer() as capture:
            gen = DepGen(make_repo(REQUIREMENTS), make_config(mirror.base, pack_update=['requests']))
            assert gen.sample == {}
        assert capture.fd.any_contains(
            'Dependency "requests" is not pinned to a version in the lookback window')

    def test_name_is_normalized(self, fs, run_dir, mirror):
        """The names of the config and of the files are compared PEP 503 normalized."""
        requirements = {'c1': 'ruamel.yaml==0.18.6\n'}
        gen = DepGen(make_repo(requirements), make_config(mirror.base, pack_update=['ruamel_yaml']))
        sample = gen.sample
        assert list(sample) == ['ruamel-yaml']
        assert [(dep.version, dep.target) for dep in sample['ruamel-yaml']] == [('0.18.6', True)]


class TestMirror:
    """The download source of the dependencies: PythonDeps.PypiMirror."""

    def test_str(self, fs, run_dir):
        """A str mirror feeds every dependency."""
        gen = DepGen(make_repo(REQUIREMENTS), make_config('http://mirror'))
        assert gen._mirror_of('httpx') == 'http://mirror'
        assert gen._mirror_of('other') == 'http://mirror'

    def test_list(self, fs, run_dir):
        """A list feeds each dependency from the first entry that lists it."""
        config = make_config([
            PythonMirrorInfo(Url='http://a', Deps=['httpx']),
            PythonMirrorInfo(Url='http://b', Deps=['httpx2', 'starlette']),
        ])
        gen = DepGen(make_repo(REQUIREMENTS), config)
        assert gen._mirror_of('httpx') == 'http://a'
        assert gen._mirror_of('starlette') == 'http://b'
        # a name no entry lists is fetched from the official PyPI
        assert gen._mirror_of('requests') == dep_gen.DEFAULT_PYPI_MIRROR
        # the names of the entries are compared PEP 503 normalized
        assert gen._mirror_of('httpx2') == 'http://b'


class TestRun:
    """One run of the generator: fetch, prune, build, prune."""

    def test_channel(self, fs, run_dir, mirror):
        """The wheels are fetched and the channel of the dependency is written."""
        register_wheels(mirror)
        repo = make_repo(REQUIREMENTS)
        with logger.mock_capture_writer() as capture:
            DepGen(repo, make_config(mirror.base), client=mirror.client).run()

        # 1. the wheel of every version is fetched, unmanaged names are not
        for version in ('0.27.7', '0.27.8', '0.28.1'):
            assert wheel_file(run_dir, version).isfile()
            assert join_path(
                run_dir, REPO_FOLDER, 'wheel', 'httpx', version, 'index_data.json').isfile()
        assert not join_path(run_dir, REPO_FOLDER, 'wheel', 'demo').exists()
        assert not any('demo' in url for url in mirror.requests)

        # 3. the full pack of every version and the update pack of every
        # other version to the target
        for version in ('0.27.7', '0.27.8', '0.28.1'):
            decoder = PackDecodeBase(file_read_bytes(pack_file(run_dir, version, 'full.pack')))
            decoder.validate()
            assert decoder.current_version == version
            assert decoder.old_version == ''
        for old in ('0.27.7', '0.27.8'):
            decoder = PackDecodeBase(file_read_bytes(pack_file(run_dir, '0.28.1', f'from_{old}.pack')))
            decoder.validate()
            assert decoder.current_version == '0.28.1'
            assert decoder.old_version == old

        # the channel is exactly the latest info, the full packs and the
        # update packs
        assert sorted(
            os.path.relpath(path, join_path(run_dir, DEP_FOLDER)).replace(os.sep, '/')
            for path in iter_files(join_path(run_dir, DEP_FOLDER), recursive=True)
        ) == [
            '0.27.7/full.pack',
            '0.27.8/full.pack',
            '0.28.1/from_0.27.7.pack',
            '0.28.1/from_0.27.8.pack',
            '0.28.1/full.pack',
            'latest.pack',
        ]

        # latest.pack: the target version and the checksum of its full pack
        data = file_read_bytes(pack_file(run_dir, 'latest.pack'))
        info = LatestInfo.parse(data)
        full = PackDecodeBase(file_read_bytes(pack_file(run_dir, '0.28.1', 'full.pack')))
        assert info.version == '0.28.1'
        assert info.checksum == full.index_checksum
        assert data == b'0.28.1' + bytes(full.extract_index_pack())[-20:]

        # the progress of the run is logged
        assert capture.fd.any_contains('[1/3] Fetching wheel of "httpx==0.28.1"')
        assert capture.fd.any_contains(
            '[1/1] Building dependency packs of "httpx": 3 versions, target=0.28.1')
        assert capture.fd.any_contains('Writing update pack: 0.27.7 -> 0.28.1')
        assert capture.fd.any_contains('Writing latest info: version=0.28.1')

    @pytest.mark.trio
    async def test_full_pack_is_the_install_tree(self, fs, run_dir, mirror):
        """The full pack of a version unpacks to the install tree of its wheel."""
        register_wheels(mirror)
        DepGen(make_repo(REQUIREMENTS), make_config(mirror.base), client=mirror.client).run()

        site = site_packages(fs)
        with logger.mock_capture_writer():
            assert await UnpackJob(
                file_read_bytes(pack_file(run_dir, '0.28.1', 'full.pack')),
                root=site, name='httpx').run()
        pack = PackWheel(wheel_file(run_dir, '0.28.1'))
        for path, file in pack.tree.items():
            assert file_read_bytes(f'{site}/{path}') == file.content, path
        assert file_read_bytes(f'{site}/.pack/httpx/index.pack') == pack.index_pack

    @pytest.mark.trio
    async def test_update_applies(self, fs, run_dir, mirror):
        """The update pack of a version pair upgrades the old tree to the new one."""
        register_wheels(mirror)
        DepGen(make_repo(REQUIREMENTS), make_config(mirror.base), client=mirror.client).run()

        site = site_packages(fs)
        with logger.mock_capture_writer():
            assert await UnpackJob(
                file_read_bytes(pack_file(run_dir, '0.27.8', 'full.pack')),
                root=site, name='httpx').run()

        update = file_read_bytes(pack_file(run_dir, '0.28.1', 'from_0.27.8.pack'))
        job = UpdateJob(update, root=site, name='httpx')
        with logger.mock_capture_writer():
            assert await job.run()
        assert job.error == []

        # the tree of the new version, the ledger included, and the file of
        # the old version the new one does not have is gone
        new = PackWheel(wheel_file(run_dir, '0.28.1'))
        for path, file in new.tree.items():
            assert file_read_bytes(f'{site}/{path}') == file.content, path
        assert not os.path.exists(f'{site}/httpx/version-0.27.8.txt')
        assert file_read_bytes(f'{site}/.pack/httpx/index.pack') == new.index_pack

    def test_no_dependency(self, fs, run_dir, mirror):
        """A config with no managed dependency writes nothing and removes nothing."""
        with logger.mock_capture_writer() as capture:
            DepGen(make_repo(REQUIREMENTS), make_config(mirror.base, pack_update=[])).run()
        assert capture.fd.any_contains('No dependency to build')
        assert not join_path(run_dir, REPO_FOLDER).exists()
        assert mirror.requests == []

    def test_name_without_target(self, fs, run_dir, mirror):
        """A name the latest commit drops is not built, its historical packs are kept."""
        requirements = {
            'c1': 'httpx==0.27.7\n',
            'c2': 'demo==1.0\n',
        }
        # the artifacts of an earlier run, of a version that is in the window
        fs.create_file(join_path(
            run_dir, REPO_FOLDER, 'wheel', 'httpx', '0.27.7', 'httpx-0.27.7-py3-none-any.whl'),
            contents=b'old wheel')
        fs.create_file(pack_file(run_dir, '0.27.7', 'full.pack'), contents=b'old pack')
        fs.create_file(pack_file(run_dir, 'latest.pack'), contents=b'old latest')

        with logger.mock_capture_writer() as capture:
            DepGen(make_repo(requirements), make_config(mirror.base), client=mirror.client).run()

        assert capture.fd.any_contains(
            'Dependency "httpx" is not pinned by the latest commit')
        # nothing is fetched or built, the historical releases are kept
        assert mirror.requests == []
        assert file_read_bytes(pack_file(run_dir, '0.27.7', 'full.pack')) == b'old pack'
        assert file_read_bytes(pack_file(run_dir, 'latest.pack')) == b'old latest'
        assert wheel_file(run_dir, '0.27.7').exists()

    def test_no_pure_wheel(self, fs, run_dir, mirror):
        """A version the mirror has no pure python wheel of is skipped, the others build."""
        register_wheel(mirror, '0.27.8', CORE_V1)
        register_wheel(mirror, '0.28.1', CORE_V2)
        mirror.register('httpx', '0.27.7', tags='cp38-cp38-win_amd64', content=b'compiled')
        repo = make_repo(REQUIREMENTS)
        with logger.mock_capture_writer() as capture:
            DepGen(repo, make_config(mirror.base), client=mirror.client).run()

        assert capture.fd.any_contains('No pure python wheel to pack of "httpx==0.27.7"')
        # the skipped version has no wheel file, no full pack and no update pack
        assert not wheel_file(run_dir, '0.27.7').exists()
        assert not pack_file(run_dir, '0.27.7', 'full.pack').exists()
        assert not pack_file(run_dir, '0.28.1', 'from_0.27.7.pack').exists()
        # the other versions are complete and latest.pack is published
        assert pack_file(run_dir, '0.27.8', 'full.pack').isfile()
        assert pack_file(run_dir, '0.28.1', 'from_0.27.8.pack').isfile()
        assert LatestInfo.parse(file_read_bytes(pack_file(run_dir, 'latest.pack'))).version == '0.28.1'

    def test_mirror_list(self, fs, run_dir, mirror):
        """A list mirror fetches each dependency from its own url."""
        httpx_wheel = build_wheel(
            '/w/httpx-0.28.1-py3-none-any.whl', demo_files('0.28.1', CORE_V2),
            name='httpx', version='0.28.1')
        mirror.register('httpx', '0.28.1', content=file_read_bytes(httpx_wheel))
        demo_wheel = build_wheel(
            '/w/demo-1.0-py3-none-any.whl', {'demo/__init__.py': b''}, name='demo', version='1.0')
        mirror.register('demo', '1.0', content=file_read_bytes(demo_wheel))
        config = make_config(
            [
                PythonMirrorInfo(Url='http://mirror-a', Deps=['httpx']),
                PythonMirrorInfo(Url='http://mirror-b', Deps=['demo']),
            ],
            pack_update=['httpx', 'demo'],
        )
        DepGen(make_repo({'c1': 'httpx==0.28.1\ndemo==1.0\n'}), config, client=mirror.client).run()

        # every request of a dependency goes to the url of its entry
        assert mirror.requests
        httpx_requests = [url for url in mirror.requests if 'httpx' in url]
        demo_requests = [url for url in mirror.requests if 'demo' in url]
        assert httpx_requests and all('mirror-a' in url for url in httpx_requests)
        assert demo_requests and all('mirror-b' in url for url in demo_requests)
        assert len(httpx_requests) + len(demo_requests) == len(mirror.requests)
        # both dependencies got their channel
        assert join_path(
            run_dir, REPO_FOLDER, 'packdep', 'httpx', '0.28.1', 'full.pack').isfile()
        assert join_path(
            run_dir, REPO_FOLDER, 'packdep', 'demo', '1.0', 'full.pack').isfile()
        assert pack_file(run_dir, 'latest.pack').isfile()
        assert join_path(
            run_dir, REPO_FOLDER, 'packdep', 'demo', 'latest.pack').isfile()


class TestExistingPacks:
    """The packs of an earlier run are kept."""

    def test_existing_packs_are_kept(self, fs, run_dir, mirror, monkeypatch):
        """A complete channel is kept as it is: nothing is fetched or built again."""
        register_wheels(mirror)
        repo = make_repo(REQUIREMENTS)
        DepGen(repo, make_config(mirror.base), client=mirror.client).run()
        tree = read_tree(join_path(run_dir, REPO_FOLDER))
        requests = list(mirror.requests)

        # no pack of a version may be built again: the encoder of the second
        # run fails the test when it is constructed, and the writes are collected
        class NoBuild:
            PACK_VERSION = PackWheel.PACK_VERSION

            def __init__(self, *args, **kwargs):
                raise AssertionError('the pack must not be built again')

        written = []
        monkeypatch.setattr(dep_gen, 'PackWheel', NoBuild)
        monkeypatch.setattr(dep_gen, 'PackWheelUpdate', NoBuild)
        monkeypatch.setattr(
            dep_gen, 'atomic_write_stream', lambda file, data: written.append(str(file)))
        DepGen(repo, make_config(mirror.base), client=mirror.client).run()

        assert written == []
        # the fetch of the second run is answered by the local cache: the
        # index data files and the wheels are used as they are
        assert mirror.requests == requests
        assert read_tree(join_path(run_dir, REPO_FOLDER)) == tree

    def test_latest_pack_is_written_again(self, fs, run_dir, mirror):
        """latest.pack is written on every run, from the checksum of the kept full pack."""
        register_wheels(mirror)
        repo = make_repo(REQUIREMENTS)
        DepGen(repo, make_config(mirror.base), client=mirror.client).run()
        latest = pack_file(run_dir, 'latest.pack')
        data = file_read_bytes(latest)
        os.remove(latest)

        DepGen(repo, make_config(mirror.base), client=mirror.client).run()

        assert file_read_bytes(latest) == data

    def test_missing_pack_is_built_again(self, fs, run_dir, mirror, monkeypatch):
        """Only the pack that is missing is built again."""
        register_wheels(mirror)
        repo = make_repo(REQUIREMENTS)
        DepGen(repo, make_config(mirror.base), client=mirror.client).run()
        target = pack_file(run_dir, '0.28.1', 'from_0.27.7.pack')
        data = file_read_bytes(target)
        os.remove(target)

        written = []
        real_write = dep_gen.atomic_write_stream

        def write_stream(file, stream):
            written.append(str(file))
            real_write(file, stream)

        monkeypatch.setattr(dep_gen, 'atomic_write_stream', write_stream)
        DepGen(repo, make_config(mirror.base), client=mirror.client).run()

        assert written == [str(target)]
        assert file_read_bytes(target) == data

    def test_pack_of_another_format_is_written_again(self, fs, run_dir, mirror):
        """A pack of another format is not kept: a channel mixes no formats."""
        register_wheels(mirror)
        repo = make_repo(REQUIREMENTS)
        DepGen(repo, make_config(mirror.base), client=mirror.client).run()

        names = (('0.28.1', 'full.pack'), ('0.28.1', 'from_0.27.7.pack'), ('0.27.8', 'full.pack'))
        original = {parts: file_read_bytes(pack_file(run_dir, *parts)) for parts in names}
        for parts in names:
            file = pack_file(run_dir, *parts)
            data = bytearray(original[parts])
            # the pack version is the single byte behind b'PACK'
            data[4] = 1
            atomic_write(file, bytes(data))

        DepGen(repo, make_config(mirror.base), client=mirror.client).run()

        # the packs are written again, byte for byte the packs of the version
        for parts in names:
            assert file_read_bytes(pack_file(run_dir, *parts)) == original[parts]
        # latest.pack is built from the full pack this run wrote
        info = LatestInfo.parse(file_read_bytes(pack_file(run_dir, 'latest.pack')))
        full = PackDecodeBase(file_read_bytes(pack_file(run_dir, '0.28.1', 'full.pack')))
        assert info.checksum == full.index_checksum

    def test_pack_that_is_not_a_pack_is_written_again(self, fs, run_dir, mirror):
        """A kept file that cannot be read as a pack is overwritten, the run goes on."""
        register_wheels(mirror)
        repo = make_repo(REQUIREMENTS)
        DepGen(repo, make_config(mirror.base), client=mirror.client).run()
        file = pack_file(run_dir, '0.28.1', 'from_0.27.7.pack')
        atomic_write(file, b'NOPE' + b'\x00' * 60)

        with logger.mock_capture_writer() as capture:
            DepGen(repo, make_config(mirror.base), client=mirror.client).run()

        assert capture.fd.any_contains('Failed to read the existing pack')
        decoder = PackDecodeBase(file_read_bytes(file))
        decoder.validate()
        assert decoder.old_version == '0.27.7'

    def test_pack_of_another_version_is_written_again(self, fs, run_dir, mirror):
        """A pack of another version pair is not kept, a channel mixes no versions."""
        register_wheels(mirror)
        repo = make_repo(REQUIREMENTS)
        DepGen(repo, make_config(mirror.base), client=mirror.client).run()
        # the update pack of 0.27.8, put at the path of the update pack of 0.27.7
        source = pack_file(run_dir, '0.28.1', 'from_0.27.8.pack')
        file = pack_file(run_dir, '0.28.1', 'from_0.27.7.pack')
        atomic_write(file, file_read_bytes(source))

        with logger.mock_capture_writer() as capture:
            DepGen(repo, make_config(mirror.base), client=mirror.client).run()

        assert capture.fd.any_contains('Existing pack is not the pack of this version')
        decoder = PackDecodeBase(file_read_bytes(file))
        decoder.validate()
        assert decoder.old_version == '0.27.7'


class TestCleanup:
    """The folders the lookback window does not ask for are removed."""

    def test_stale_wheel_folders_removed(self, fs, run_dir, mirror):
        """The wheel folders of versions and names out of the window are removed."""
        register_wheels(mirror)
        # a version that slid out of the window, a name of the window that is
        # not managed, and a name of another config
        fs.create_file(join_path(
            run_dir, REPO_FOLDER, 'wheel', 'httpx', '0.26.0', 'httpx-0.26.0-py3-none-any.whl'),
            contents=b'stale')
        fs.create_file(join_path(
            run_dir, REPO_FOLDER, 'wheel', 'demo', '1.0', 'demo-1.0-py3-none-any.whl'),
            contents=b'stale')
        fs.create_file(join_path(
            run_dir, REPO_FOLDER, 'wheel', 'other', '1.0', 'other-1.0-py3-none-any.whl'),
            contents=b'stale')

        with logger.mock_capture_writer() as capture:
            DepGen(make_repo(REQUIREMENTS), make_config(mirror.base), client=mirror.client).run()

        assert capture.fd.any_contains('Removing stale wheel folder')
        assert not join_path(run_dir, REPO_FOLDER, 'wheel', 'httpx', '0.26.0').exists()
        assert not join_path(run_dir, REPO_FOLDER, 'wheel', 'demo').exists()
        assert not join_path(run_dir, REPO_FOLDER, 'wheel', 'other').exists()
        # the wheels of the window are kept
        for version in ('0.27.7', '0.27.8', '0.28.1'):
            assert wheel_file(run_dir, version).isfile()

    def test_stale_pack_folders_removed(self, fs, run_dir, mirror):
        """The version folders and dependency folders out of the window are removed."""
        register_wheels(mirror)
        # a version that slid out of the window
        fs.create_file(pack_file(run_dir, '0.26.0', 'full.pack'), contents=b'stale')
        # a dependency of another config (or of a removed PackUpdate entry)
        fs.create_file(join_path(run_dir, REPO_FOLDER, 'packdep', 'other', 'latest.pack'), contents=b'stale')
        fs.create_file(join_path(
            run_dir, REPO_FOLDER, 'packdep', 'other', '1.0', 'full.pack'), contents=b'stale')

        with logger.mock_capture_writer() as capture:
            DepGen(make_repo(REQUIREMENTS), make_config(mirror.base), client=mirror.client).run()

        assert capture.fd.any_contains('Removing stale pack folder')
        assert capture.fd.any_contains('Removing stale dependency folder')
        assert not pack_file(run_dir, '0.26.0').exists()
        assert not join_path(run_dir, REPO_FOLDER, 'packdep', 'other').exists()
        # the channel of the dependency is kept, latest.pack included
        assert pack_file(run_dir, '0.27.7', 'full.pack').isfile()
        assert pack_file(run_dir, 'latest.pack').isfile()

    def test_stale_update_packs_removed(self, fs, run_dir, mirror):
        """An update pack of another target folder, or of a version out of the window, is removed."""
        register_wheels(mirror)
        repo = make_repo(REQUIREMENTS)
        DepGen(repo, make_config(mirror.base), client=mirror.client).run()
        # the leftovers of an earlier run: an update pack in the folder of a
        # version that is not the target, and one of a version out of the window
        fs.create_file(pack_file(run_dir, '0.27.8', 'from_0.27.7.pack'), contents=b'stale')
        fs.create_file(pack_file(run_dir, '0.28.1', 'from_0.26.0.pack'), contents=b'stale')

        with logger.mock_capture_writer() as capture:
            DepGen(repo, make_config(mirror.base), client=mirror.client).run()

        assert capture.fd.any_contains('Removing stale update pack')
        assert not pack_file(run_dir, '0.27.8', 'from_0.27.7.pack').exists()
        assert not pack_file(run_dir, '0.28.1', 'from_0.26.0.pack').exists()
        # the update packs of the window and the full packs are kept
        assert pack_file(run_dir, '0.28.1', 'from_0.27.7.pack').isfile()
        assert pack_file(run_dir, '0.28.1', 'from_0.27.8.pack').isfile()
        assert pack_file(run_dir, '0.27.8', 'full.pack').isfile()

    def test_target_moves(self, fs, run_dir, mirror):
        """A new target gets its update packs, the update packs of the old target are removed."""
        register_wheels(mirror)
        repo = make_repo(REQUIREMENTS)
        DepGen(repo, make_config(mirror.base), client=mirror.client).run()
        assert pack_file(run_dir, '0.28.1', 'from_0.27.7.pack').isfile()

        # the pin of the dependency moves to a new version
        register_wheel(mirror, '0.28.2', CORE_V2 + b'NEWER = True\n')
        repo.register_file('c4', 'requirements.txt', b'httpx==0.28.2\ndemo==1.0\n')
        repo.register_commit(
            'c4', parents=['c3'], author_name='Author', author_time=3, message='commit c4')
        repo.register_branch('master', 'c4')
        repo.register_head('c4')

        DepGen(repo, make_config(mirror.base), client=mirror.client).run()

        # the new target holds an update pack of every other version and
        # latest.pack points at it
        for old in ('0.27.7', '0.27.8', '0.28.1'):
            assert pack_file(run_dir, '0.28.2', f'from_{old}.pack').isfile()
        assert LatestInfo.parse(
            file_read_bytes(pack_file(run_dir, 'latest.pack'))).version == '0.28.2'
        # the folder of the old target keeps its full pack, its update packs
        # are gone: no client addresses them anymore
        assert pack_file(run_dir, '0.28.1', 'full.pack').isfile()
        assert not pack_file(run_dir, '0.28.1', 'from_0.27.7.pack').exists()
        assert not pack_file(run_dir, '0.28.1', 'from_0.27.8.pack').exists()

    def test_removal_is_best_effort(self, fs, run_dir, mirror, monkeypatch):
        """A folder that cannot be removed does not fail the run."""
        register_wheels(mirror)

        def fail(folder):
            raise PermissionError(f'folder is held: {folder}')

        monkeypatch.setattr(dep_gen, 'atomic_rmtree', fail)
        fs.create_file(pack_file(run_dir, '0.26.0', 'full.pack'), contents=b'stale')

        with logger.mock_capture_writer() as capture:
            DepGen(make_repo(REQUIREMENTS), make_config(mirror.base), client=mirror.client).run()

        assert capture.fd.any_contains('Failed to remove stale pack folder')
        assert pack_file(run_dir, '0.26.0', 'full.pack').isfile()
        # the channel is complete, the stale folder is left to the next run
        assert pack_file(run_dir, '0.28.1', 'full.pack').isfile()
        assert pack_file(run_dir, 'latest.pack').isfile()
