"""
Tests for DepGen: generate the wheel channel of the dependencies of a repo.

The dependency files of a mock repo are sampled over the lookback window and
the wheels of the pinned versions are served by an in-memory PyPI mirror
(MockMirror, the mock of the fetch tests): the index pages and the wheel files
come from the memory, no socket is bound. The channel is verified with the
decode side of the library: the full pack of the target decodes to the install
tree of its wheel, the update packs are applied to an unpacked old version
with UpdateJob and compared against the tree of the new version. Only the
target version has a full pack, see the module docstring of dep_gen.
"""
import os

import pytest
import trio

from alasio.config.entry.const import DEFAULT_PYPI_MIRRORS
from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy.pack.job_update import UpdateJob
from alasio.deploy.pack.packdep import PACKDEP_PATH, decode_packdep
from alasio.deploy.pip_download.pypi_file import PypiFile
from alasio.deploy_dev.pack import pack_wheel
from alasio.deploy_dev.pack._pack_cache import PackCache
from alasio.deploy_dev.pack.encode_packdep import encode_packdep
from alasio.deploy_dev.pack.pack_wheel import PackWheel
from alasio.deploy_dev.pack_server import dep_gen
from alasio.deploy_dev.pack_server.dep_gen import DepGen
from alasio.deploy_dev.pack_server.fetch_wheel import WheelNotFoundError
from alasio.deploy_dev.pack_server.model import LookbackConfig, PackRepoModel, PythonDepsConfig, RepoConfig
from alasio.deploy_dev.pack_server.pack_gen import PackRepoGen
from alasio.deploy_dev.pack_server.parse_entry import ENTRY_FILE
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_write, file_read_bytes
from alasio.ext.path.iter import iter_files
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack.conftest import FakeMirrorTable
from tests.deploy_dev.pack_server.test_fetch_wheel import MockMirror

# the client of the mirror is a starlette TestClient and the code under test
# passes the timeout of its own http client: the deprecation warning of
# starlette about the timeout argument is noise here, see the mock
pytestmark = pytest.mark.filterwarnings(
    'ignore::starlette.exceptions.StarletteDeprecationWarning')
from tests.deploy.simple_pip.conftest import build_wheel, site_packages

# the dependency files of the mock repo: httpx is pinned to a new version on
# every commit, demo is pinned but not a managed name of the config, so it is
# not fetched and gets no pack
REQUIREMENTS = {
    'c1': 'httpx==0.27.7\ndemo==1.0\n',
    'c2': 'httpx==0.27.8\ndemo==1.0\n',
    'c3': 'httpx==0.28.1\ndemo==1.0\n',
}

# base url of the mock mirror, the pypi_mirrors the entry of the mock repo
# declares: the pack server fetches the wheels from the mirrors of the mod
# entry at the latest commit, not from the config
MIRROR_URL = 'http://mirror'

# entry of the mock repo: by default it declares the dependency the tests
# build a channel of, see entry_source
DEFAULT_ENTRY = "entry = ModEntryInfo(name='m', pypi_mirrors='http://mirror', update_pack_deps=('httpx',))\n"


def entry_source(*names, pypi_mirrors=MIRROR_URL):
    """
    Build the source of a mod entry declaring update_pack_deps.

    Args:
        *names (str): Names to declare, none for an entry without the
            declaration
        pypi_mirrors (str | dict): Value of the pypi_mirrors declaration,
            the source of the wheels. Defaults to MIRROR_URL, the mock mirror
            of the tests.

    Returns:
        str: Source of the entry module
    """
    declared = f', pypi_mirrors={pypi_mirrors!r}'
    if not names:
        return f"entry = ModEntryInfo(name='m'{declared})\n"
    return f"entry = ModEntryInfo(name='m'{declared}, update_pack_deps={tuple(names)!r})\n"


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


def make_repo(requirements, branch='master', entry=DEFAULT_ENTRY):
    """
    Build a mock repo of a linear commit chain with a requirements file.

    Args:
        requirements (dict): {commit: content of requirements.txt}, the
            oldest commit first, the parent of a commit is the one before it
        branch (str): Branch of the head. Defaults to 'master'.
        entry (str | dict | None): Source of the mod entry. A str is
            registered at every commit, a dict is {commit: source} (a commit
            without an entry is not registered), None registers no entry.
            Defaults to DEFAULT_ENTRY, an entry declaring httpx.

    Returns:
        MockGitRepo: Repo, the head of the branch is the newest commit
    """
    repo = MockGitRepo()
    parent = None
    for index, (commit, content) in enumerate(requirements.items()):
        repo.register_file(commit, 'requirements.txt', content.encode('utf-8'))
        source = entry.get(commit) if isinstance(entry, dict) else entry
        if source is not None:
            repo.register_file(commit, ENTRY_FILE, source.encode('utf-8'))
        repo.register_commit(
            commit, parents=[parent] if parent else None,
            author_name='Author', author_time=index, message=f'commit {commit}')
        parent = commit
    repo.register_branch(branch, parent)
    repo.register_head(parent)
    return repo


def make_config(requirement_files=('requirements.txt',)):
    """
    Build a pack config with the PythonDeps group of the tests.

    Args:
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


def run_commit(repo, config, client):
    """
    Run and commit a DepGen, the normal flow after the repo is published.

    Returns:
        DepGen: The generator that ran
    """
    gen = DepGen(repo, config, client=client)
    gen.run()
    gen.commit()
    return gen


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
        gen = DepGen(make_repo(REQUIREMENTS), make_config())
        sample = gen.sample
        # demo is pinned by the files but not a managed name: no entry
        assert list(sample) == ['httpx']
        assert [(dep.version, dep.commit, dep.target) for dep in sample['httpx']] == [
            ('0.28.1', 'c3', True),
            ('0.27.8', 'c2', False),
            ('0.27.7', 'c1', False),
        ]

    def test_unmanaged_name(self, fs, run_dir, mirror):
        """A pinned name the mod entry does not declare is not sampled."""
        gen = DepGen(
            make_repo(REQUIREMENTS, entry=entry_source('demo')),
            make_config())
        assert list(gen.sample) == ['demo']
        assert [dep.version for dep in gen.sample['demo']] == ['1.0']

    def test_name_without_pin(self, fs, run_dir, mirror):
        """A declared name the window does not pin is reported, nothing is built of it."""
        with logger.mock_capture_writer() as capture:
            gen = DepGen(
                make_repo(REQUIREMENTS, entry=entry_source('requests')),
                make_config())
            assert gen.sample == {}
        assert capture.concat() == """\
INFO | Using update_pack_deps of module/config/const.py at c3: requests
WARNING | Dependency "requests" is not pinned to a version in the lookback window, nothing is built of it; a dependency of the mod entry update_pack_deps must be pinned with "==" in a dependency file of the config
"""

    def test_name_is_normalized(self, fs, run_dir, mirror):
        """The names of the entry and of the files are compared PEP 503 normalized."""
        requirements = {'c1': 'ruamel.yaml==0.18.6\n'}
        gen = DepGen(
            make_repo(requirements, entry=entry_source('ruamel_yaml')),
            make_config())
        sample = gen.sample
        assert list(sample) == ['ruamel-yaml']
        assert [(dep.version, dep.target) for dep in sample['ruamel-yaml']] == [('0.18.6', True)]

    def test_entry_without_update_pack_deps(self, fs, run_dir, mirror):
        """An entry without update_pack_deps declares no dependency."""
        gen = DepGen(
            make_repo(REQUIREMENTS, entry=entry_source()),
            make_config())
        assert gen.sample == {}

    def test_entry_missing(self, fs, run_dir, mirror):
        """A repo without an entry builds no dependency, the reason is logged."""
        with logger.mock_capture_writer() as capture:
            gen = DepGen(make_repo(REQUIREMENTS, entry=None), make_config())
            assert gen.sample == {}
        assert capture.concat() == """\
INFO | No update_pack_deps declared by module/config/const.py at c3, no dependency pack is built
"""

    def test_entry_broken(self, fs, run_dir, mirror):
        """A dynamic declaration fails the sample, no dependency is silently dropped."""
        entry = "entry = ModEntryInfo(name='m', update_pack_deps=get_deps())\n"
        gen = DepGen(make_repo(REQUIREMENTS, entry=entry), make_config())
        with pytest.raises(ValueError, match='unsupported call'):
            _ = gen.sample

    def test_entry_latest_only(self, fs, run_dir, mirror):
        """The names are declared by the entry of the latest commit only."""
        entries = {
            'c1': entry_source('demo'),
            'c2': entry_source('httpx'),
            'c3': entry_source('httpx'),
        }
        gen = DepGen(make_repo(REQUIREMENTS, entry=entries), make_config())
        assert list(gen.sample) == ['httpx']

    def test_no_requirement_files(self, fs, run_dir, mirror, monkeypatch):
        """A config without dependency files does not read the entry."""
        gen = DepGen(
            make_repo(REQUIREMENTS),
            make_config(requirement_files=()))

        def fail(commit):
            raise AssertionError('the entry must not be read')

        monkeypatch.setattr(gen.entry, 'update_pack_deps', fail)
        assert gen.sample == {}


class TestMirror:
    """The download source of the dependencies: the pypi_mirrors of the entry."""

    def test_single(self, fs, run_dir):
        """A single url of the entry is the only mirror of the fetch."""
        gen = DepGen(make_repo(REQUIREMENTS), make_config())
        assert gen.mirrors.urls == {'default': MIRROR_URL}

    def test_groups(self, fs, run_dir):
        """The groups and the members of the dict form are kept."""
        entry = entry_source('httpx', pypi_mirrors={
            'cn': {'aliyun': 'https://a.example/simple'},
            'global': {'pypi': 'https://b.example/simple'},
        })
        gen = DepGen(make_repo(REQUIREMENTS, entry=entry), make_config())
        assert list(gen.mirrors.groups) == ['cn', 'global']
        assert gen.mirrors.urls == {
            'aliyun': 'https://a.example/simple',
            'pypi': 'https://b.example/simple',
        }

    def test_default(self, fs, run_dir):
        """An entry that declares no mirror uses the default of ModEntryInfo."""
        entry = "entry = ModEntryInfo(name='m', update_pack_deps=('httpx',))\n"
        gen = DepGen(make_repo(REQUIREMENTS, entry=entry), make_config())
        assert list(gen.mirrors.groups) == list(DEFAULT_PYPI_MIRRORS)
        assert gen.mirrors.urls == {
            name: url
            for members in DEFAULT_PYPI_MIRRORS.values()
            for name, url in members.items()
        }

    def test_entry_missing(self, fs, run_dir):
        """A repo without an entry uses the default mirrors too."""
        gen = DepGen(make_repo(REQUIREMENTS, entry=None), make_config())
        assert gen.mirrors.urls['pypi'] == 'https://pypi.org/simple'

    def test_latest_commit(self, fs, run_dir):
        """The mirrors come from the entry of the latest commit."""
        entries = {
            'c1': entry_source('httpx', pypi_mirrors='http://old'),
            'c2': entry_source('httpx', pypi_mirrors='http://new'),
            'c3': entry_source('httpx', pypi_mirrors='http://new'),
        }
        gen = DepGen(make_repo(REQUIREMENTS, entry=entries), make_config())
        assert gen.mirrors.urls == {'default': 'http://new'}

    def test_invalid(self, fs, run_dir):
        """A declaration that is not a mirror structure fails the run."""
        gen = DepGen(make_repo(REQUIREMENTS, entry=entry_source('httpx', pypi_mirrors='not a url')),
                     make_config())
        with pytest.raises(ValueError, match='Invalid pypi_mirrors of module/config/const.py at commit c3'):
            _ = gen.mirrors

    def test_dynamic(self, fs, run_dir):
        """A declaration the parser cannot read is refused, not silently ignored."""
        entry = (
            "entry = ModEntryInfo(name='m', pypi_mirrors=get_mirrors(), "
            "update_pack_deps=('httpx',))\n")
        gen = DepGen(make_repo(REQUIREMENTS, entry=entry), make_config())
        with pytest.raises(ValueError, match='pypi_mirrors: unsupported call'):
            _ = gen.mirrors


class TestRun:
    """One run of the generator: fetch, prune, build, prune."""

    def test_channel(self, fs, run_dir, mirror):
        """The wheels are fetched and the channel of the dependency is written."""
        register_wheels(mirror)
        repo = make_repo(REQUIREMENTS)
        gen = DepGen(repo, make_config(), client=mirror.client)
        with logger.mock_capture_writer() as capture:
            assert gen.run() is None
        payload = encode_packdep(gen.published)

        # 1. the wheel of every version is fetched, unmanaged names are not
        for version in ('0.27.7', '0.27.8', '0.28.1'):
            assert wheel_file(run_dir, version).isfile()
        assert not join_path(run_dir, REPO_FOLDER, 'wheel', 'demo').exists()
        assert not any('demo' in url for url in mirror.requests)

        # 3. the full pack of the target version and the update pack of every
        # other version to it
        decoder = PackDecodeBase(file_read_bytes(pack_file(run_dir, '0.28.1', 'full.pack')))
        decoder.validate()
        assert decoder.current_version == '0.28.1'
        assert decoder.old_version == ''
        for old in ('0.27.7', '0.27.8'):
            decoder = PackDecodeBase(file_read_bytes(pack_file(run_dir, '0.28.1', f'update_{old}.pack')))
            decoder.validate()
            assert decoder.current_version == '0.28.1'
            assert decoder.old_version == old

        # the channel is exactly the full pack of the target and the update
        # packs: the versions of the window have no full pack, and the per
        # dependency latest.pack is no longer written
        assert sorted(
            os.path.relpath(path, join_path(run_dir, DEP_FOLDER)).replace(os.sep, '/')
            for path in iter_files(join_path(run_dir, DEP_FOLDER), recursive=True)
        ) == [
            '0.28.1/full.pack',
            '0.28.1/update_0.27.7.pack',
            '0.28.1/update_0.27.8.pack',
        ]
        assert not pack_file(run_dir, '0.27.7').exists()
        assert not pack_file(run_dir, '0.27.8').exists()
        assert not pack_file(run_dir, 'latest.pack').exists()

        full = PackDecodeBase(file_read_bytes(pack_file(run_dir, '0.28.1', 'full.pack')))

        # the packdep payload carries the generated target snapshot
        assert payload is not None
        entries = decode_packdep(payload)
        assert list(entries) == ['httpx']
        assert entries['httpx'].version == '0.28.1'
        assert entries['httpx'].index_checksum == bytes(full.extract_index_pack())[-20:]
        assert gen.published == {'httpx': ('0.28.1', bytes(full.extract_index_pack())[-20:])}

        # the progress of the run is logged
        assert capture.concat() == f"""\
INFO | Using update_pack_deps of module/config/const.py at c3: httpx
INFO | Generating the dependency packs of "Author_Repo_master": 1 dependencies, 3 versions
INFO | [1/3] Fetching wheel of "httpx==0.28.1", mirror="http://mirror", commit=c3
INFO | Download wheel: "http://mirror/packages/httpx-0.28.1-py3-none-any.whl" -> "{run_dir}/pack/Author_Repo_master/wheel/httpx/0.28.1/httpx-0.28.1-py3-none-any.whl"
INFO | [2/3] Fetching wheel of "httpx==0.27.8", mirror="http://mirror", commit=c2
INFO | Download wheel: "http://mirror/packages/httpx-0.27.8-py3-none-any.whl" -> "{run_dir}/pack/Author_Repo_master/wheel/httpx/0.27.8/httpx-0.27.8-py3-none-any.whl"
INFO | [3/3] Fetching wheel of "httpx==0.27.7", mirror="http://mirror", commit=c1
INFO | Download wheel: "http://mirror/packages/httpx-0.27.7-py3-none-any.whl" -> "{run_dir}/pack/Author_Repo_master/wheel/httpx/0.27.7/httpx-0.27.7-py3-none-any.whl"
INFO | [1/1] Building dependency packs of "httpx": 3 versions, target=0.28.1
INFO | Writing full pack: "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.28.1/full.pack"
INFO | Writing update pack: 0.27.8 -> 0.28.1, "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.28.1/update_0.27.8.pack"
INFO | Writing update pack: 0.27.7 -> 0.28.1, "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.28.1/update_0.27.7.pack"
INFO | Dependency packs of "Author_Repo_master" generated
"""

    def test_commit_requires_run(self, fs, run_dir, mirror):
        """commit() before run() cannot clean a channel that was not generated."""
        gen = DepGen(make_repo(REQUIREMENTS), make_config(), client=mirror.client)
        with pytest.raises(RuntimeError, match='run\\(\\) must be called'):
            gen.commit()

    def test_full_pack_is_the_install_tree(self, fs, run_dir, mirror):
        """The full pack of a version unpacks to the install tree of its wheel."""
        register_wheels(mirror)
        DepGen(make_repo(REQUIREMENTS), make_config(), client=mirror.client).run()

        site = site_packages(fs)
        with logger.mock_capture_writer():
            assert trio.run(UnpackJob(
                file_read_bytes(pack_file(run_dir, '0.28.1', 'full.pack')),
                root=site, name='httpx'
            ).run)
        pack = PackWheel(wheel_file(run_dir, '0.28.1'))
        for path, file in pack.tree.items():
            assert file_read_bytes(f'{site}/{path}') == file.content, path
        assert file_read_bytes(f'{site}/.pack/httpx/index.pack') == pack.index_pack

    def test_update_applies(self, fs, run_dir, mirror):
        """The update pack of a version pair upgrades the old tree to the new one."""
        register_wheels(mirror)
        DepGen(make_repo(REQUIREMENTS), make_config(), client=mirror.client).run()

        # the client that has been running the old version: the channel has no
        # full pack of an old version, the tree of the version is the install
        # tree of its wheel (what the client installed before the update)
        site = site_packages(fs)
        with logger.mock_capture_writer():
            assert trio.run(UnpackJob(
                b''.join(PackWheel(wheel_file(run_dir, '0.27.8')).iter_pack_data()),
                root=site, name='httpx'
            ).run)

        update = file_read_bytes(pack_file(run_dir, '0.28.1', 'update_0.27.8.pack'))
        job = UpdateJob(update, root=site, name='httpx')
        with logger.mock_capture_writer():
            assert trio.run(job.run)
        assert job.error == []

        # the tree of the new version, the ledger included, and the file of
        # the old version the new one does not have is gone
        new = PackWheel(wheel_file(run_dir, '0.28.1'))
        for path, file in new.tree.items():
            assert file_read_bytes(f'{site}/{path}') == file.content, path
        assert not os.path.exists(f'{site}/httpx/version-0.27.8.txt')
        assert file_read_bytes(f'{site}/.pack/httpx/index.pack') == new.index_pack

    def test_no_per_dependency_latest_pack(self, fs, run_dir, mirror):
        """Neither run() nor commit() writes a per dependency latest.pack."""
        register_wheels(mirror)
        repo = make_repo(REQUIREMENTS)
        gen = DepGen(repo, make_config(), client=mirror.client)
        assert gen.run() is None
        assert not pack_file(run_dir, 'latest.pack').exists()
        gen.commit()
        assert not pack_file(run_dir, 'latest.pack').exists()

    def test_packdep_is_carried_by_the_repo_pack(self, fs, run_dir, mirror):
        """The payload of DepGen is a generated file of the repo version."""
        register_wheels(mirror)
        repo = make_repo(REQUIREMENTS)
        config = make_config()
        dep = DepGen(repo, config, client=mirror.client)
        assert dep.run() is None
        assert dep.published
        payload = encode_packdep(dep.published)

        PackRepoGen(repo, config, dep_gen=dep).run()

        full = PackDecodeBase(file_read_bytes(join_path(
            run_dir, REPO_FOLDER, 'packrepo', 'c3', 'full.pack')))
        full.validate()
        info = full.fileinfo[PACKDEP_PATH]
        assert bytes(full.catfile(info)) == payload
        entries = decode_packdep(payload)
        dep_full = PackDecodeBase(file_read_bytes(pack_file(run_dir, '0.28.1', 'full.pack')))
        assert list(entries) == ['httpx']
        assert entries['httpx'].version == '0.28.1'
        assert entries['httpx'].index_checksum == bytes(dep_full.extract_index_pack())[-20:]

    def test_no_dependency(self, fs, run_dir, mirror):
        """A repo whose entry declares no dependency writes nothing and removes nothing."""
        with logger.mock_capture_writer() as capture:
            gen = DepGen(make_repo(REQUIREMENTS, entry=None), make_config())
            assert gen.run() is None
        assert capture.concat() == """\
INFO | No update_pack_deps declared by module/config/const.py at c3, no dependency pack is built
INFO | No dependency to build of "Author_Repo_master", skipped
"""
        assert gen.published == {}
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
            DepGen(make_repo(requirements), make_config(), client=mirror.client).run()

        assert capture.concat() == """\
INFO | Using update_pack_deps of module/config/const.py at c2: httpx
WARNING | Dependency "httpx" is not pinned by the latest commit, no pack is built of it, its historical packs are kept
INFO | Generating the dependency packs of "Author_Repo_master": 0 dependencies, 0 versions
INFO | Dependency packs of "Author_Repo_master" generated
"""
        # nothing is fetched or built, the historical releases are kept
        assert mirror.requests == []
        assert file_read_bytes(pack_file(run_dir, '0.27.7', 'full.pack')) == b'old pack'
        assert file_read_bytes(pack_file(run_dir, 'latest.pack')) == b'old latest'
        assert wheel_file(run_dir, '0.27.7').exists()

    def test_historical_wheel_missing_fails(self, fs, run_dir, mirror):
        """A historical version without a usable wheel fails the complete build."""
        register_wheel(mirror, '0.27.8', CORE_V1)
        register_wheel(mirror, '0.28.1', CORE_V2)
        mirror.register('httpx', '0.27.7', tags='cp38-cp38-win_amd64', content=b'compiled')
        repo = make_repo(REQUIREMENTS)

        with pytest.raises(
                WheelNotFoundError, match='historical dependency "httpx==0.27.7"'):
            DepGen(repo, make_config(), client=mirror.client).run()

        # every version of the channel is required: nothing of the target is
        # published when an update side is missing
        assert not pack_file(run_dir, '0.28.1', 'full.pack').exists()
        assert not pack_file(run_dir, '0.28.1', 'update_0.27.7.pack').exists()
        assert not pack_file(run_dir, '0.28.1', 'update_0.27.8.pack').exists()
        assert not pack_file(run_dir, 'latest.pack').exists()

    def test_target_wheel_missing_fails(self, fs, run_dir, mirror):
        """A target without a usable wheel fails the run, no pack is published."""
        register_wheel(mirror, '0.27.8', CORE_V1)
        mirror.register('httpx', '0.28.1', tags='cp38-cp38-win_amd64', content=b'compiled')
        repo = make_repo(REQUIREMENTS)

        with pytest.raises(
                WheelNotFoundError, match='target dependency "httpx==0.28.1"'):
            DepGen(repo, make_config(), client=mirror.client).run()

        # the run stopped before any pack of the target was written
        assert not wheel_file(run_dir, '0.28.1').exists()
        assert not pack_file(run_dir, '0.28.1', 'full.pack').exists()
        assert not pack_file(run_dir, '0.28.1', 'update_0.27.8.pack').exists()
        assert not pack_file(run_dir, 'latest.pack').exists()

    def test_mirror_set(self, fs, run_dir, mirror, monkeypatch):
        """A mirror set fetches from the first candidate that serves the page."""
        # the selection of a mirror set is recorded in gui.db: the in-memory
        # table keeps the run off the real database
        monkeypatch.setattr(PypiFile, 'RECORD_TABLE', FakeMirrorTable())
        register_wheels(mirror)
        entry = entry_source('httpx', pypi_mirrors={
            # a base url of the mock server that serves nothing: a 404 on the
            # index page, the probe falls through to the next member
            'cn': {'dead': 'http://mirror/missing', 'mock': MIRROR_URL},
        })
        DepGen(make_repo(REQUIREMENTS, entry=entry), make_config(), client=mirror.client).run()

        # the fetch fell back to the mirror of the second member
        assert any(url.startswith(f'{MIRROR_URL}/httpx/') for url in mirror.requests)
        assert join_path(
            run_dir, REPO_FOLDER, 'packdep', 'httpx', '0.28.1', 'full.pack').isfile()
        assert not pack_file(run_dir, 'latest.pack').exists()


class TestExistingPacks:
    """The packs of an earlier run are kept."""

    def test_existing_packs_are_kept(self, fs, run_dir, mirror, monkeypatch):
        """A complete channel is kept as it is: nothing is fetched or built again."""
        register_wheels(mirror)
        repo = make_repo(REQUIREMENTS)
        DepGen(repo, make_config(), client=mirror.client).run()
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
        DepGen(repo, make_config(), client=mirror.client).run()

        assert written == []
        # the wheels of the second run are answered by the local files: the
        # index page of every version is requested, no wheel is downloaded
        assert mirror.requests == requests + ['http://mirror/httpx/'] * 3
        assert read_tree(join_path(run_dir, REPO_FOLDER)) == tree

    def test_missing_pack_is_built_again(self, fs, run_dir, mirror, monkeypatch):
        """Only the pack that is missing is built again."""
        register_wheels(mirror)
        repo = make_repo(REQUIREMENTS)
        DepGen(repo, make_config(), client=mirror.client).run()
        target = pack_file(run_dir, '0.28.1', 'update_0.27.7.pack')
        data = file_read_bytes(target)
        os.remove(target)

        written = []
        real_write = dep_gen.atomic_write_stream

        def write_stream(file, stream):
            written.append(str(file))
            real_write(file, stream)

        monkeypatch.setattr(dep_gen, 'atomic_write_stream', write_stream)
        DepGen(repo, make_config(), client=mirror.client).run()

        assert written == [str(target)]
        assert file_read_bytes(target) == data

    def test_pack_of_another_format_is_written_again(self, fs, run_dir, mirror):
        """A pack of another format is not kept: a channel mixes no formats."""
        register_wheels(mirror)
        repo = make_repo(REQUIREMENTS)
        DepGen(repo, make_config(), client=mirror.client).run()

        names = (('0.28.1', 'full.pack'), ('0.28.1', 'update_0.27.7.pack'), ('0.28.1', 'update_0.27.8.pack'))
        original = {parts: file_read_bytes(pack_file(run_dir, *parts)) for parts in names}
        for parts in names:
            file = pack_file(run_dir, *parts)
            data = bytearray(original[parts])
            # the pack version is the single byte behind b'PACK'
            data[4] = 1
            atomic_write(file, bytes(data))

        DepGen(repo, make_config(), client=mirror.client).run()

        # the packs are written again, byte for byte the packs of the version
        for parts in names:
            assert file_read_bytes(pack_file(run_dir, *parts)) == original[parts]
        assert not pack_file(run_dir, 'latest.pack').exists()

    def test_pack_that_is_not_a_pack_is_written_again(self, fs, run_dir, mirror):
        """A kept file that cannot be read as a pack is overwritten, the run goes on."""
        register_wheels(mirror)
        repo = make_repo(REQUIREMENTS)
        DepGen(repo, make_config(), client=mirror.client).run()
        file = pack_file(run_dir, '0.28.1', 'update_0.27.7.pack')
        atomic_write(file, b'NOPE' + b'\x00' * 60)

        with logger.mock_capture_writer() as capture:
            DepGen(repo, make_config(), client=mirror.client).run()

        assert capture.concat() == f"""\
INFO | Using update_pack_deps of module/config/const.py at c3: httpx
INFO | Generating the dependency packs of "Author_Repo_master": 1 dependencies, 3 versions
INFO | [1/3] Fetching wheel of "httpx==0.28.1", mirror="http://mirror", commit=c3
INFO | Wheel exists, skipped: "{run_dir}/pack/Author_Repo_master/wheel/httpx/0.28.1/httpx-0.28.1-py3-none-any.whl"
INFO | [2/3] Fetching wheel of "httpx==0.27.8", mirror="http://mirror", commit=c2
INFO | Wheel exists, skipped: "{run_dir}/pack/Author_Repo_master/wheel/httpx/0.27.8/httpx-0.27.8-py3-none-any.whl"
INFO | [3/3] Fetching wheel of "httpx==0.27.7", mirror="http://mirror", commit=c1
INFO | Wheel exists, skipped: "{run_dir}/pack/Author_Repo_master/wheel/httpx/0.27.7/httpx-0.27.7-py3-none-any.whl"
INFO | [1/1] Building dependency packs of "httpx": 3 versions, target=0.28.1
INFO | Full pack exists, skipped: "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.28.1/full.pack"
INFO | Update pack exists, skipped: "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.28.1/update_0.27.8.pack"
WARNING | Failed to read the existing pack, writing it again: Failed to read the pack: not a pack file: b'NOPE', "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.28.1/update_0.27.7.pack"
INFO | Writing update pack: 0.27.7 -> 0.28.1, "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.28.1/update_0.27.7.pack"
INFO | Dependency packs of "Author_Repo_master" generated
"""
        decoder = PackDecodeBase(file_read_bytes(file))
        decoder.validate()
        assert decoder.old_version == '0.27.7'

    def test_pack_of_another_version_is_written_again(self, fs, run_dir, mirror):
        """A pack of another version pair is not kept, a channel mixes no versions."""
        register_wheels(mirror)
        repo = make_repo(REQUIREMENTS)
        DepGen(repo, make_config(), client=mirror.client).run()
        # the update pack of 0.27.8, put at the path of the update pack of 0.27.7
        source = pack_file(run_dir, '0.28.1', 'update_0.27.8.pack')
        file = pack_file(run_dir, '0.28.1', 'update_0.27.7.pack')
        atomic_write(file, file_read_bytes(source))

        with logger.mock_capture_writer() as capture:
            DepGen(repo, make_config(), client=mirror.client).run()

        assert capture.concat() == f"""\
INFO | Using update_pack_deps of module/config/const.py at c3: httpx
INFO | Generating the dependency packs of "Author_Repo_master": 1 dependencies, 3 versions
INFO | [1/3] Fetching wheel of "httpx==0.28.1", mirror="http://mirror", commit=c3
INFO | Wheel exists, skipped: "{run_dir}/pack/Author_Repo_master/wheel/httpx/0.28.1/httpx-0.28.1-py3-none-any.whl"
INFO | [2/3] Fetching wheel of "httpx==0.27.8", mirror="http://mirror", commit=c2
INFO | Wheel exists, skipped: "{run_dir}/pack/Author_Repo_master/wheel/httpx/0.27.8/httpx-0.27.8-py3-none-any.whl"
INFO | [3/3] Fetching wheel of "httpx==0.27.7", mirror="http://mirror", commit=c1
INFO | Wheel exists, skipped: "{run_dir}/pack/Author_Repo_master/wheel/httpx/0.27.7/httpx-0.27.7-py3-none-any.whl"
INFO | [1/1] Building dependency packs of "httpx": 3 versions, target=0.28.1
INFO | Full pack exists, skipped: "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.28.1/full.pack"
INFO | Update pack exists, skipped: "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.28.1/update_0.27.8.pack"
WARNING | Existing pack is not the pack of this version, writing it again: "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.28.1/update_0.27.7.pack"
INFO | Writing update pack: 0.27.7 -> 0.28.1, "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.28.1/update_0.27.7.pack"
INFO | Dependency packs of "Author_Repo_master" generated
"""
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
            run_commit(make_repo(REQUIREMENTS), make_config(), mirror.client)

        assert capture.concat() == f"""\
INFO | Using update_pack_deps of module/config/const.py at c3: httpx
INFO | Generating the dependency packs of "Author_Repo_master": 1 dependencies, 3 versions
INFO | [1/3] Fetching wheel of "httpx==0.28.1", mirror="http://mirror", commit=c3
INFO | Download wheel: "http://mirror/packages/httpx-0.28.1-py3-none-any.whl" -> "{run_dir}/pack/Author_Repo_master/wheel/httpx/0.28.1/httpx-0.28.1-py3-none-any.whl"
INFO | [2/3] Fetching wheel of "httpx==0.27.8", mirror="http://mirror", commit=c2
INFO | Download wheel: "http://mirror/packages/httpx-0.27.8-py3-none-any.whl" -> "{run_dir}/pack/Author_Repo_master/wheel/httpx/0.27.8/httpx-0.27.8-py3-none-any.whl"
INFO | [3/3] Fetching wheel of "httpx==0.27.7", mirror="http://mirror", commit=c1
INFO | Download wheel: "http://mirror/packages/httpx-0.27.7-py3-none-any.whl" -> "{run_dir}/pack/Author_Repo_master/wheel/httpx/0.27.7/httpx-0.27.7-py3-none-any.whl"
INFO | [1/1] Building dependency packs of "httpx": 3 versions, target=0.28.1
INFO | Writing full pack: "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.28.1/full.pack"
INFO | Writing update pack: 0.27.8 -> 0.28.1, "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.28.1/update_0.27.8.pack"
INFO | Writing update pack: 0.27.7 -> 0.28.1, "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.28.1/update_0.27.7.pack"
INFO | Dependency packs of "Author_Repo_master" generated
INFO | Removing stale wheel folder: "{run_dir}/pack/Author_Repo_master/wheel/httpx/0.26.0"
INFO | Removing stale wheel folder: "{run_dir}/pack/Author_Repo_master/wheel/demo/1.0"
INFO | Removing stale wheel folder: "{run_dir}/pack/Author_Repo_master/wheel/other/1.0"
INFO | Dependency packs of "Author_Repo_master" committed
"""
        assert not join_path(run_dir, REPO_FOLDER, 'wheel', 'httpx', '0.26.0').exists()
        assert not join_path(run_dir, REPO_FOLDER, 'wheel', 'demo').exists()
        assert not join_path(run_dir, REPO_FOLDER, 'wheel', 'other').exists()
        # the wheels of the window are kept
        for version in ('0.27.7', '0.27.8', '0.28.1'):
            assert wheel_file(run_dir, version).isfile()

    def test_stale_pack_folders_removed(self, fs, run_dir, mirror):
        """The version folders out of the window and the full packs of the old versions are removed."""
        register_wheels(mirror)
        # a version that slid out of the window, and the full packs of an
        # earlier run that kept one per version (the layout has one full pack
        # of the target only)
        fs.create_file(pack_file(run_dir, '0.26.0', 'full.pack'), contents=b'stale')
        fs.create_file(pack_file(run_dir, '0.27.8', 'full.pack'), contents=b'stale')
        # a dependency of another config (or of a name removed from update_pack_deps)
        fs.create_file(join_path(run_dir, REPO_FOLDER, 'packdep', 'other', 'latest.pack'), contents=b'stale')
        fs.create_file(join_path(
            run_dir, REPO_FOLDER, 'packdep', 'other', '1.0', 'full.pack'), contents=b'stale')

        with logger.mock_capture_writer() as capture:
            gen = DepGen(
                make_repo(REQUIREMENTS), make_config(), client=mirror.client)
            gen.run()
        # run() only generates: the old packs stay until commit()
        assert pack_file(run_dir, '0.26.0', 'full.pack').isfile()
        assert join_path(run_dir, REPO_FOLDER, 'packdep', 'other').isdir()
        assert capture.concat() == f"""\
INFO | Using update_pack_deps of module/config/const.py at c3: httpx
INFO | Generating the dependency packs of "Author_Repo_master": 1 dependencies, 3 versions
INFO | [1/3] Fetching wheel of "httpx==0.28.1", mirror="http://mirror", commit=c3
INFO | Download wheel: "http://mirror/packages/httpx-0.28.1-py3-none-any.whl" -> "{run_dir}/pack/Author_Repo_master/wheel/httpx/0.28.1/httpx-0.28.1-py3-none-any.whl"
INFO | [2/3] Fetching wheel of "httpx==0.27.8", mirror="http://mirror", commit=c2
INFO | Download wheel: "http://mirror/packages/httpx-0.27.8-py3-none-any.whl" -> "{run_dir}/pack/Author_Repo_master/wheel/httpx/0.27.8/httpx-0.27.8-py3-none-any.whl"
INFO | [3/3] Fetching wheel of "httpx==0.27.7", mirror="http://mirror", commit=c1
INFO | Download wheel: "http://mirror/packages/httpx-0.27.7-py3-none-any.whl" -> "{run_dir}/pack/Author_Repo_master/wheel/httpx/0.27.7/httpx-0.27.7-py3-none-any.whl"
INFO | [1/1] Building dependency packs of "httpx": 3 versions, target=0.28.1
INFO | Writing full pack: "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.28.1/full.pack"
INFO | Writing update pack: 0.27.8 -> 0.28.1, "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.28.1/update_0.27.8.pack"
INFO | Writing update pack: 0.27.7 -> 0.28.1, "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.28.1/update_0.27.7.pack"
INFO | Dependency packs of "Author_Repo_master" generated
"""

        with logger.mock_capture_writer() as capture:
            gen.commit()

        assert capture.concat() == f"""\
INFO | Removing stale pack folder: "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.26.0"
INFO | Removing stale pack folder: "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.27.8"
INFO | Removing stale dependency folder: "{run_dir}/pack/Author_Repo_master/packdep/other"
INFO | Dependency packs of "Author_Repo_master" committed
"""
        assert not pack_file(run_dir, '0.26.0').exists()
        assert not pack_file(run_dir, '0.27.8').exists()
        assert not join_path(run_dir, REPO_FOLDER, 'packdep', 'other').exists()
        # the channel of the dependency is kept: the full pack of the target
        # and its update packs
        assert pack_file(run_dir, '0.28.1', 'full.pack').isfile()
        assert not pack_file(run_dir, 'latest.pack').exists()

    def test_stale_update_packs_removed(self, fs, run_dir, mirror):
        """An update pack of another target folder, or of a version out of the window, is removed."""
        register_wheels(mirror)
        repo = make_repo(REQUIREMENTS)
        DepGen(repo, make_config(), client=mirror.client).run()
        # the leftovers of an earlier run: an update pack in the folder of a
        # version that is not the target, and one of a version out of the window
        fs.create_file(pack_file(run_dir, '0.27.8', 'update_0.27.7.pack'), contents=b'stale')
        fs.create_file(pack_file(run_dir, '0.28.1', 'update_0.26.0.pack'), contents=b'stale')

        with logger.mock_capture_writer() as capture:
            run_commit(repo, make_config(), mirror.client)

        assert capture.concat() == f"""\
INFO | Using update_pack_deps of module/config/const.py at c3: httpx
INFO | Generating the dependency packs of "Author_Repo_master": 1 dependencies, 3 versions
INFO | [1/3] Fetching wheel of "httpx==0.28.1", mirror="http://mirror", commit=c3
INFO | Wheel exists, skipped: "{run_dir}/pack/Author_Repo_master/wheel/httpx/0.28.1/httpx-0.28.1-py3-none-any.whl"
INFO | [2/3] Fetching wheel of "httpx==0.27.8", mirror="http://mirror", commit=c2
INFO | Wheel exists, skipped: "{run_dir}/pack/Author_Repo_master/wheel/httpx/0.27.8/httpx-0.27.8-py3-none-any.whl"
INFO | [3/3] Fetching wheel of "httpx==0.27.7", mirror="http://mirror", commit=c1
INFO | Wheel exists, skipped: "{run_dir}/pack/Author_Repo_master/wheel/httpx/0.27.7/httpx-0.27.7-py3-none-any.whl"
INFO | [1/1] Building dependency packs of "httpx": 3 versions, target=0.28.1
INFO | Full pack exists, skipped: "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.28.1/full.pack"
INFO | Update pack exists, skipped: "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.28.1/update_0.27.8.pack"
INFO | Update pack exists, skipped: "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.28.1/update_0.27.7.pack"
INFO | Dependency packs of "Author_Repo_master" generated
INFO | Removing stale update pack: "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.28.1/update_0.26.0.pack"
INFO | Removing stale pack folder: "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.27.8"
INFO | Dependency packs of "Author_Repo_master" committed
"""
        assert not pack_file(run_dir, '0.27.8').exists()
        assert not pack_file(run_dir, '0.28.1', 'update_0.26.0.pack').exists()
        # the update packs of the window and the full pack of the target are kept
        assert pack_file(run_dir, '0.28.1', 'update_0.27.7.pack').isfile()
        assert pack_file(run_dir, '0.28.1', 'update_0.27.8.pack').isfile()
        assert pack_file(run_dir, '0.28.1', 'full.pack').isfile()

    def test_target_moves(self, fs, run_dir, mirror):
        """A new target gets its update packs, the update packs of the old target are removed."""
        register_wheels(mirror)
        repo = make_repo(REQUIREMENTS)
        DepGen(repo, make_config(), client=mirror.client).run()
        assert pack_file(run_dir, '0.28.1', 'update_0.27.7.pack').isfile()

        # the pin of the dependency moves to a new version
        register_wheel(mirror, '0.28.2', CORE_V2 + b'NEWER = True\n')
        repo.register_file('c4', 'requirements.txt', b'httpx==0.28.2\ndemo==1.0\n')
        repo.register_file('c4', ENTRY_FILE, DEFAULT_ENTRY.encode('utf-8'))
        repo.register_commit(
            'c4', parents=['c3'], author_name='Author', author_time=3, message='commit c4')
        repo.register_branch('master', 'c4')
        repo.register_head('c4')

        run_commit(repo, make_config(), mirror.client)

        # the new target holds the only full pack and an update pack of every
        # other version
        for old in ('0.27.7', '0.27.8', '0.28.1'):
            assert pack_file(run_dir, '0.28.2', f'update_{old}.pack').isfile()
        # the folder of the old target is gone as a whole: the channel has the
        # folder of the target only
        assert not pack_file(run_dir, '0.28.1').exists()

    def test_removal_is_best_effort(self, fs, run_dir, mirror, monkeypatch):
        """A folder that cannot be removed does not fail the run."""
        register_wheels(mirror)

        def fail(folder):
            raise PermissionError(f'folder is held: {folder}')

        monkeypatch.setattr(dep_gen, 'atomic_rmtree', fail)
        fs.create_file(pack_file(run_dir, '0.26.0', 'full.pack'), contents=b'stale')

        with logger.mock_capture_writer() as capture:
            run_commit(make_repo(REQUIREMENTS), make_config(), mirror.client)

        assert capture.concat() == f"""\
INFO | Using update_pack_deps of module/config/const.py at c3: httpx
INFO | Generating the dependency packs of "Author_Repo_master": 1 dependencies, 3 versions
INFO | [1/3] Fetching wheel of "httpx==0.28.1", mirror="http://mirror", commit=c3
INFO | Download wheel: "http://mirror/packages/httpx-0.28.1-py3-none-any.whl" -> "{run_dir}/pack/Author_Repo_master/wheel/httpx/0.28.1/httpx-0.28.1-py3-none-any.whl"
INFO | [2/3] Fetching wheel of "httpx==0.27.8", mirror="http://mirror", commit=c2
INFO | Download wheel: "http://mirror/packages/httpx-0.27.8-py3-none-any.whl" -> "{run_dir}/pack/Author_Repo_master/wheel/httpx/0.27.8/httpx-0.27.8-py3-none-any.whl"
INFO | [3/3] Fetching wheel of "httpx==0.27.7", mirror="http://mirror", commit=c1
INFO | Download wheel: "http://mirror/packages/httpx-0.27.7-py3-none-any.whl" -> "{run_dir}/pack/Author_Repo_master/wheel/httpx/0.27.7/httpx-0.27.7-py3-none-any.whl"
INFO | [1/1] Building dependency packs of "httpx": 3 versions, target=0.28.1
INFO | Writing full pack: "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.28.1/full.pack"
INFO | Writing update pack: 0.27.8 -> 0.28.1, "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.28.1/update_0.27.8.pack"
INFO | Writing update pack: 0.27.7 -> 0.28.1, "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.28.1/update_0.27.7.pack"
INFO | Dependency packs of "Author_Repo_master" generated
INFO | Removing stale pack folder: "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.26.0"
WARNING | Failed to remove stale pack folder "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.26.0": folder is held: {run_dir}/pack/Author_Repo_master/packdep/httpx/0.26.0
INFO | Dependency packs of "Author_Repo_master" committed
"""
        assert pack_file(run_dir, '0.26.0', 'full.pack').isfile()
        # the channel is complete, the stale folder is left to the next run
        assert pack_file(run_dir, '0.28.1', 'full.pack').isfile()
