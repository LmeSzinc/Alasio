"""
Tests for the flow of a repo of the pack server: Repo.

One run generates the workspace of the config in the run directory, from a
mock git repo and an in-memory pypi mirror (MockMirror, the mirror of the fetch
tests): the wheels of the dependency versions, the packs of the dependencies,
the packs of the repo tree, and latest.pack, the publication barrier. The
channel is verified with the decode side of the library: the full pack of the
target decodes to the install tree of its wheel, the update packs are applied
to an unpacked old version with UpdateJob and compared against the tree of the
new version.
"""
import os

import pytest
import trio

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy.pack.packdep import PACKDEP_PATH, decode_packdep
from alasio.deploy_dev.pack import pack_wheel
from alasio.deploy_dev.pack._pack_cache import PackCache
from alasio.deploy_dev.pack_server import repo_dep
from alasio.deploy_dev.pack_server.fetch_wheel import WheelNotFoundError
from alasio.deploy_dev.pack_server.model import LookbackConfig, PackRepoModel, RepoConfig
from alasio.deploy_dev.pack_server.parse_entry import ENTRY_FILE
from alasio.deploy_dev.pack_server.repo import Repo
from alasio.deploy_dev.pack_server.repo_base import DEFAULT_INDEX
from alasio.ext.path.atomic import file_read_bytes
from alasio.ext.path.iter import iter_files
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy.simple_pip.conftest import build_wheel, site_packages
from tests.deploy_dev.pack_server.conftest import join_path

# the dependency files of the mock repo: httpx is pinned to a new version on
# every commit, demo is pinned but is not a name the entry declares, so it gets
# no pack, and msgspec is provided to pip with the wheels of its index

# base url of the mock mirror, the index the pins of the dependency files are
# annotated with: a compiled dependency file annotates every pin with the index
# it was resolved from
MIRROR_URL = 'http://mirror'

# base url of the index the repo provides: a second base of the mock mirror,
# with a path, like the index of a real mod
PROVIDED_URL = 'http://provided.example/simple'

# base url of an index that is neither the mirror of the pins nor the index of
# the repo, the source of a pin the repo does not provide
OTHER_URL = 'https://other.example/simple'


def requirement(pin, source=MIRROR_URL):
    """
    Build a pin line with the '# from' annotation of a compiled dependency file.

    Args:
        pin (str): Pin as written in the file, e.g. 'httpx==0.28.1'
        source (str): Base url of the index the pin came from. Defaults to
            MIRROR_URL, the mock mirror of the tests

    Returns:
        str: The pin and its annotation, each on a line of its own
    """
    return f'{pin}\n    # from {source}\n'


REQUIREMENTS = {
    'c1': requirement('httpx==0.27.7') + requirement('demo==1.0'),
    'c2': requirement('httpx==0.27.8') + requirement('demo==1.0'),
    'c3': (
        requirement('httpx==0.28.1') + requirement('demo==1.0')
        + requirement('msgspec==0.22.0', f'{PROVIDED_URL}/')
    ),
}

# entry of the mock repo: by default it declares the dependency the tests build
# a pack channel of, see entry_source
DEFAULT_ENTRY = "entry = ModEntryInfo(name='m', update_pack_deps=('httpx',))\n"

# folder of the workspace of the config in the run directory
WORKSPACE = 'pack/Author_Repo_master'

# folder of the channel of the dependency of the tests
DEP_FOLDER = f'{WORKSPACE}/packdep/httpx'

# the content of the module that changes between two versions of the wheel
CORE_V1 = b''.join(b'VALUE_%d = %d\n' % (index, index) for index in range(80))
CORE_V2 = CORE_V1 + b'VALUE_NEW = 99\n'


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    """
    Fresh cache of the wheel pipeline for one test, see test_pack_wheel.
    """
    monkeypatch.setattr(pack_wheel, 'WHEEL_CACHE', PackCache())


def entry_source(*names, provide=None, requirements_file=None):
    """
    Build the source of a mod entry declaring update_pack_deps.

    Args:
        *names (str): Names to declare, none for an entry without the
            declaration (an entry never declares the mirror the pack server
            fetches from: the pins of the dependency file do)
        provide (Iterable[str] | None): Value of the
            update_provide_pypi_mirrors declaration, None for a declaration of
            the entry that omits the key (the default of ModEntryInfo)
        requirements_file (str | None): Value of the requirements_file
            declaration, None for a declaration of the entry that omits the key

    Returns:
        str: Source of the entry module
    """
    declared = ''
    if provide is not None:
        declared += f', update_provide_pypi_mirrors={tuple(provide)!r}'
    if requirements_file is not None:
        declared += f', requirements_file={requirements_file!r}'
    if not names:
        return f"entry = ModEntryInfo(name='m'{declared})\n"
    return f"entry = ModEntryInfo(name='m'{declared}, update_pack_deps={tuple(names)!r})\n"


def make_repo(requirements=None, branch='master', entry=DEFAULT_ENTRY):
    """
    Build a mock repo of a linear commit chain with a requirements file.

    Args:
        requirements (dict | None): {commit: content of requirements.txt}, the
            oldest commit first, the parent of a commit is the one before it.
            Defaults to REQUIREMENTS
        branch (str): Branch of the head. Defaults to 'master'.
        entry (str | dict | None): Source of the mod entry. A str is
            registered at every commit, a dict is {commit: source} (a commit
            without an entry is not registered), None registers no entry.
            Defaults to DEFAULT_ENTRY, an entry declaring httpx.

    Returns:
        MockGitRepo: Repo, the head of the branch is the newest commit
    """
    requirements = REQUIREMENTS if requirements is None else requirements
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


def make_config():
    """
    Build a pack config of the tests: the window only, the rest comes from the
    mod entry of the repo.

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
    )


def demo_files(version, core):
    """
    Files of the httpx wheel of a version.

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


def cache_file(run_dir, version, name='httpx'):
    """
    Path of the fetched wheel of a version in the cache of the workspace.

    Returns:
        PathStr: {run}/pack/Author_Repo_master/_wheel_cache/httpx/{version}/...
    """
    return join_path(
        run_dir, WORKSPACE, '_wheel_cache', name, version,
        f'{name}-{version}-py3-none-any.whl')


def wheel_file(run_dir, version, name='httpx'):
    """
    Path of the provided wheel of a version in the pip index of the workspace.

    Returns:
        PathStr: {run}/pack/Author_Repo_master/wheel/httpx/{version}/...
    """
    return join_path(
        run_dir, WORKSPACE, 'wheel', name, version,
        f'{name}-{version}-py3-none-any.whl')


def pack_file(run_dir, *parts):
    """
    Path of a file of the channel of the dependency in the run directory.

    Returns:
        PathStr: {run}/pack/Author_Repo_master/packdep/httpx/...
    """
    return join_path(run_dir, DEP_FOLDER, *parts)


def repo_file(run_dir, *parts):
    """
    Path of a file of the packs of the repo tree in the run directory.

    Returns:
        PathStr: {run}/pack/Author_Repo_master/packrepo/...
    """
    return join_path(run_dir, WORKSPACE, 'packrepo', *parts)


def run_commit(repo, config, client):
    """
    Run and commit a Repo, the normal flow after the repo is cloned.

    Returns:
        Repo: The flow that ran
    """
    gen = Repo(repo, config, client=client)
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


class TestDeps:
    """The dependencies of the window, and what each of them asks for."""

    def test_deps(self, fs, run_dir, mirror):
        """A declared name carries the pins of the window, the latest first."""
        gen = Repo(make_repo(), make_config())
        deps = gen.base.deps
        # demo is pinned by the files but is neither declared nor provided: no
        # entry, nothing to build of it
        assert list(deps) == ['httpx']
        info = deps['httpx']
        assert info.name == 'httpx'
        assert info.latest.version == '0.28.1'
        assert list(info.lookbacks) == ['0.27.8', '0.27.7']
        assert [pin.version for pin in info.versions] == ['0.28.1', '0.27.8', '0.27.7']
        # the entry declares httpx for pack updates, its source is not provided
        assert info.pack_dep is True
        assert info.provide_wheel is False

    def test_undeclared_name(self, fs, run_dir, mirror):
        """A pinned name the mod entry does not declare is not in the mapping."""
        gen = Repo(make_repo(entry=entry_source('demo')), make_config())
        deps = gen.base.deps
        assert list(deps) == ['demo']
        assert deps['demo'].latest.version == '1.0'
        assert deps['demo'].lookbacks == {}
        assert deps['demo'].pack_dep is True

    def test_name_without_pin(self, fs, run_dir, mirror):
        """A declared name the window does not pin is reported, nothing is built of it."""
        with logger.mock_capture_writer() as capture:
            gen = Repo(make_repo(entry=entry_source('requests')), make_config())
            assert gen.base.deps == {}
        assert capture.concat() == """\
INFO | Using update_pack_deps of module/config/const.py at c3: requests
WARNING | Dependency "requests" is not pinned to a version in the lookback window, nothing is built of it; a dependency of the mod entry update_pack_deps must be pinned with "==" in the dependency file of the repo
"""

    def test_name_is_normalized(self, fs, run_dir, mirror):
        """The names of the entry and of the files are compared PEP 503 normalized."""
        requirements = {'c1': requirement('ruamel.yaml==0.18.6')}
        gen = Repo(make_repo(requirements, entry=entry_source('ruamel_yaml')), make_config())
        deps = gen.base.deps
        assert list(deps) == ['ruamel-yaml']
        assert deps['ruamel-yaml'].latest.version == '0.18.6'

    def test_entry_without_update_pack_deps(self, fs, run_dir, mirror):
        """An entry without update_pack_deps declares no dependency."""
        gen = Repo(make_repo(entry=entry_source()), make_config())
        assert gen.base.deps == {}

    def test_entry_missing(self, fs, run_dir, mirror):
        """A repo without an entry builds no dependency, the reason is logged."""
        with logger.mock_capture_writer() as capture:
            gen = Repo(make_repo(entry=None), make_config())
            assert gen.base.deps == {}
        assert capture.concat() == """\
INFO | No update_pack_deps declared by module/config/const.py at c3, no dependency pack is built
"""

    def test_entry_broken(self, fs, run_dir, mirror):
        """A dynamic declaration fails the sample, no dependency is silently dropped."""
        entry = "entry = ModEntryInfo(name='m', update_pack_deps=get_deps())\n"
        gen = Repo(make_repo(entry=entry), make_config())
        with pytest.raises(ValueError, match='unsupported call'):
            _ = gen.base.deps

    def test_name_without_latest(self, fs, run_dir, mirror):
        """A name the latest commit drops is not built, its historical packs are kept."""
        requirements = {
            'c1': requirement('httpx==0.27.7'),
            'c2': requirement('demo==1.0'),
        }
        gen = Repo(make_repo(requirements), make_config())
        with logger.mock_capture_writer() as capture:
            assert gen.base.deps == {}
        assert capture.concat() == """\
INFO | Using update_pack_deps of module/config/const.py at c2: httpx
WARNING | Dependency "httpx" is not pinned by the latest commit, no pack is built of it, its historical packs are kept
"""


class TestFetchIndex:
    """The index of a fetch: the source the dependency file annotates the pin with."""

    def test_pin_source(self, fs, run_dir, mirror):
        """A pin is fetched from its own index, not from a mirror of the entry."""
        mirror.alias(OTHER_URL)
        register_wheels(mirror)
        requirements = {'c1': requirement('httpx==0.28.1', f'{OTHER_URL}/')}
        run_commit(make_repo(requirements), make_config(), mirror.client)
        assert mirror.requests == [
            f'{OTHER_URL}/httpx/',
            'https://other.example/packages/httpx-0.28.1-py3-none-any.whl',
        ]
        assert cache_file(run_dir, '0.28.1').isfile()

    def test_pin_without_source(self, fs, run_dir, mirror):
        """A pin without an annotation is fetched from the official PyPI index."""
        mirror.alias(DEFAULT_INDEX)
        register_wheels(mirror)
        requirements = {'c1': 'httpx==0.28.1\n'}
        run_commit(make_repo(requirements), make_config(), mirror.client)
        assert mirror.requests == [
            f'{DEFAULT_INDEX}/httpx/',
            'https://pypi.org/packages/httpx-0.28.1-py3-none-any.whl',
        ]

    def test_pin_index_is_normalized(self, fs, run_dir, mirror):
        """The case of the host and the trailing slash of the annotation do not change the index."""
        mirror.alias(OTHER_URL)
        register_wheels(mirror)
        requirements = {'c1': requirement('httpx==0.28.1', 'https://Other.Example/simple/')}
        run_commit(make_repo(requirements), make_config(), mirror.client)
        # the index is requested as the annotation normalized it
        assert mirror.requests[0] == f'{OTHER_URL}/httpx/'


class TestProvidedPins:
    """The pins whose index the repo provides, and the index they are fetched from."""

    def test_update_provide_pypi_mirrors_default(self, fs, run_dir):
        """An entry that declares no provided index keeps the default of ModEntryInfo."""
        from alasio.config.entry.const import DEFAULT_UPDATE_PROVIDE_PYPI_MIRRORS

        gen = Repo(make_repo(), make_config())
        url = DEFAULT_UPDATE_PROVIDE_PYPI_MIRRORS[0]
        assert gen.base.update_provide_pypi_mirrors == {url.rstrip('/'): url}

    def test_update_provide_pypi_mirrors_normalized(self, fs, run_dir):
        """The declared indexes are normalized, the repeated one is kept once."""
        entry = entry_source('httpx', provide=(
            'http://Deps.Example:80/simple/',
            'http://deps.example/simple',
        ))
        gen = Repo(make_repo(entry=entry), make_config())
        assert gen.base.update_provide_pypi_mirrors == {
            'http://deps.example/simple': 'http://Deps.Example:80/simple/',
        }

    def test_provided_pins(self, fs, run_dir):
        """Every pin of the latest commit from a provided index is provided, declared or not."""
        requirements = {
            'c1': requirement('httpx==0.27.7'),
            'c2': requirement('httpx==0.27.8', f'{PROVIDED_URL}/'),
            'c3': (
                requirement('httpx==0.28.1', f'{PROVIDED_URL}/')
                + requirement('demo==1.0', PROVIDED_URL)
            ),
        }
        entry = entry_source('httpx', provide=(f'{PROVIDED_URL}/',))
        gen = Repo(make_repo(requirements, entry=entry), make_config())
        deps = gen.base.deps
        assert list(deps) == ['httpx', 'demo']
        # httpx is declared by the entry, so it is a pack channel too
        assert deps['httpx'].latest.version == '0.28.1'
        assert deps['httpx'].provide_wheel is True
        assert deps['httpx'].pack_dep is True
        # demo is not declared, it is provided anyway: the client installs it
        # with pip
        assert deps['demo'].latest.version == '1.0'
        assert deps['demo'].provide_wheel is True
        assert deps['demo'].pack_dep is False
        assert deps['demo'].lookbacks == {}

    def test_provided_pin_of_an_unprovided_source(self, fs, run_dir):
        """A pin from an index the repo does not provide is not provided."""
        requirements = {'c1': requirement('httpx==0.28.1', f'{OTHER_URL}/')}
        entry = entry_source('httpx', provide=(PROVIDED_URL,))
        gen = Repo(make_repo(requirements, entry=entry), make_config())
        info = gen.base.deps['httpx']
        assert info.provide_wheel is False
        assert info.pack_dep is True

    def test_provided_pins_of_a_repo_without_pack_deps(self, fs, run_dir):
        """A repo that declares no pack channel still provides its pins."""
        requirements = {'c1': requirement('httpx==0.28.1', f'{PROVIDED_URL}/')}
        entry = entry_source(provide=(PROVIDED_URL,))
        gen = Repo(make_repo(requirements, entry=entry), make_config())
        info = gen.base.deps['httpx']
        assert info.provide_wheel is True
        assert info.pack_dep is False


class TestRun:
    """Generate the workspace of a repo."""

    def test_channel(self, fs, run_dir, mirror):
        """The wheels, the packs of the dependency and the packs of the tree are generated."""
        register_wheels(mirror)
        gen = Repo(make_repo(), make_config(), client=mirror.client)
        assert gen.run() is None

        # 1. the wheel of every version is fetched to the private cache
        for version in ('0.27.7', '0.27.8', '0.28.1'):
            assert cache_file(run_dir, version).isfile()
        assert not any('demo' in url for url in mirror.requests)

        # 2. the full pack of the target version and the update pack of every
        # other version to it
        decoder = PackDecodeBase(file_read_bytes(pack_file(run_dir, '0.28.1', 'full.pack')))
        decoder.validate()
        assert decoder.current_version == '0.28.1'
        assert decoder.old_version == ''
        for old in ('0.27.7', '0.27.8'):
            update = PackDecodeBase(
                file_read_bytes(pack_file(run_dir, '0.28.1', f'update_{old}.pack')))
            update.validate()
            assert update.old_version == old
        # the channel of the dependency has the folder of the target only
        assert list(join_path(run_dir, DEP_FOLDER).iter_foldernames()) == ['0.28.1']

        # 3. the packs of the repo tree, the full pack carries the packdep
        full = PackDecodeBase(file_read_bytes(repo_file(run_dir, 'c3', 'full.pack')))
        full.validate()
        assert full.current_version == 'c3'
        entries = decode_packdep(bytes(full.catfile(full.fileinfo[PACKDEP_PATH])))
        assert list(entries) == ['httpx']
        assert entries['httpx'].version == '0.28.1'
        assert entries['httpx'].index_checksum == \
            bytes(decoder.extract_index_pack())[-20:]

        # 4. latest.pack is not written by run(), it is the barrier
        assert not repo_file(run_dir, 'latest.pack').exists()

        gen.commit()
        assert repo_file(run_dir, 'latest.pack').isfile()
        assert gen.dep.published == {
            'httpx': ('0.28.1', bytes(decoder.extract_index_pack())[-20:]),
        }

    def test_no_pip_index_without_provided_pins(self, fs, run_dir, mirror):
        """A repo that provides no index publishes no wheel."""
        register_wheels(mirror)
        run_commit(make_repo(), make_config(), mirror.client)
        assert not join_path(run_dir, WORKSPACE, 'wheel').exists()

    def test_no_dependency(self, fs, run_dir, mirror):
        """A repo whose entry declares no dependency still publishes the tree packs."""
        with logger.mock_capture_writer() as capture:
            gen = Repo(make_repo(entry=None), make_config())
            assert gen.run() is None
        assert capture.concat() == """\
INFO | No update_pack_deps declared by module/config/const.py at c3, no dependency pack is built
INFO | No dependency to build of "Author_Repo_master", skipped
INFO | Generating packs of "Author_Repo_master": 3 versions, latest=c3
INFO | Writing full pack: "{run_dir}/pack/Author_Repo_master/packrepo/c3/full.pack"
INFO | [1/2] Packing update pack from c2
INFO | Writing update pack: c2 -> c3, "{run_dir}/pack/Author_Repo_master/packrepo/c3/update_c2.pack"
INFO | [2/2] Packing update pack from c1
INFO | Writing update pack: c1 -> c3, "{run_dir}/pack/Author_Repo_master/packrepo/c3/update_c1.pack"
INFO | Packs of "Author_Repo_master" generated, latest=c3
""".replace('{run_dir}', str(run_dir))
        assert gen.dep.published == {}
        assert not join_path(run_dir, DEP_FOLDER).exists()
        assert mirror.requests == []

    def test_full_pack_is_the_install_tree(self, fs, run_dir, mirror):
        """The full pack of a version unpacks to the install tree of its wheel."""
        register_wheels(mirror)
        Repo(make_repo(), make_config(), client=mirror.client).run()

        site = site_packages(fs)
        with logger.mock_capture_writer():
            assert trio.run(UnpackJob(
                file_read_bytes(pack_file(run_dir, '0.28.1', 'full.pack')),
                root=site, name='httpx'
            ).run)
        job = None
        new = pack_wheel.PackWheel(cache_file(run_dir, '0.28.1'))
        for path, file in new.tree.items():
            assert file_read_bytes(f'{site}/{path}') == file.content, path
        assert file_read_bytes(f'{site}/.pack/httpx/index.pack') == new.index_pack

    def test_missing_target_wheel_fails(self, fs, run_dir, mirror):
        """A target without a usable wheel fails the run, no latest.pack is written."""
        register_wheel(mirror, '0.27.8', CORE_V1)
        register_wheel(mirror, '0.27.7', CORE_V1)
        gen = Repo(make_repo(), make_config(), client=mirror.client)
        with pytest.raises(WheelNotFoundError, match='No usable wheel of the target dependency'):
            gen.run()
        assert not repo_file(run_dir, 'latest.pack').exists()

    def test_missing_historical_wheel_fails(self, fs, run_dir, mirror):
        """A historical version without a usable wheel fails the complete build."""
        register_wheel(mirror, '0.28.1', CORE_V2)
        register_wheel(mirror, '0.27.7', CORE_V1)
        gen = Repo(make_repo(), make_config(), client=mirror.client)
        with pytest.raises(WheelNotFoundError, match='No usable wheel of the historical dependency'):
            gen.run()
        assert not repo_file(run_dir, 'latest.pack').exists()

    def test_commit_requires_run(self, fs, run_dir, mirror):
        """commit() before run() cannot publish a version whose packs are missing."""
        gen = Repo(make_repo(), make_config())
        with pytest.raises(RuntimeError, match='run\\(\\) must be called'):
            gen.commit()


class TestProvidedIndex:
    """The pip index of the provided pins."""

    def test_provided_wheels_published(self, fs, run_dir, mirror):
        """Every wheel of a provided pin is copied from the cache to the pip index."""
        mirror.alias(PROVIDED_URL)
        register_wheels(mirror)
        mirror.register('msgspec', '0.22.0', tags='cp38-cp38-win_amd64')
        mirror.register('msgspec', '0.22.0', tags='cp38-cp38-manylinux_2_17_x86_64')
        requirements = {
            'c1': requirement('httpx==0.27.7'),
            'c2': requirement('httpx==0.27.8'),
            'c3': (
                requirement('httpx==0.28.1')
                + requirement('msgspec==0.22.0', f'{PROVIDED_URL}/')
            ),
        }
        entry = entry_source('httpx', provide=(PROVIDED_URL,))
        run_commit(make_repo(requirements, entry=entry), make_config(), mirror.client)

        # the provided pin is in the cache and in the pip index, one file per
        # platform, the version folder of the index
        folder = join_path(run_dir, WORKSPACE, 'wheel', 'msgspec', '0.22.0')
        assert sorted(folder.iter_filenames()) == [
            'msgspec-0.22.0-cp38-cp38-manylinux_2_17_x86_64.whl',
            'msgspec-0.22.0-cp38-cp38-win_amd64.whl',
        ]
        # the declared dependency is not provided: its wheel stays private
        assert not join_path(run_dir, WORKSPACE, 'wheel', 'httpx').exists()
        # the fetch of the provided pin used its own index
        assert f'{PROVIDED_URL}/msgspec/' in mirror.requests

    def test_provided_pin_fetched_from_its_index(self, fs, run_dir, mirror):
        """A provided pin of the pack channel is fetched from its own index, once."""
        mirror.alias(PROVIDED_URL)
        register_wheels(mirror)
        requirements = {
            'c1': requirement('httpx==0.27.7'),
            'c2': requirement('httpx==0.27.8'),
            'c3': requirement('httpx==0.28.1', f'{PROVIDED_URL}/'),
        }
        entry = entry_source('httpx', provide=(PROVIDED_URL,))
        run_commit(make_repo(requirements, entry=entry), make_config(), mirror.client)

        assert f'{PROVIDED_URL}/httpx/' in mirror.requests
        assert 'http://mirror/httpx/' in mirror.requests
        # it is both a pack input and a published wheel, and the two jobs of
        # the version share one fetcher: the page of its index and the pure
        # wheel are downloaded once
        wheel_url = 'http://provided.example/packages/httpx-0.28.1-py3-none-any.whl'
        assert mirror.requests.count(wheel_url) == 1
        assert mirror.requests.count(f'{PROVIDED_URL}/httpx/') == 1
        assert cache_file(run_dir, '0.28.1').isfile()
        assert wheel_file(run_dir, '0.28.1').isfile()

    def test_unprovided_pin_not_published(self, fs, run_dir, mirror):
        """A pin of an index the repo does not provide is fetched, not published."""
        mirror.alias(OTHER_URL)
        register_wheels(mirror)
        requirements = {'c1': requirement('httpx==0.28.1', f'{OTHER_URL}/')}
        entry = entry_source('httpx', provide=(PROVIDED_URL,))
        run_commit(make_repo(requirements, entry=entry), make_config(), mirror.client)

        assert cache_file(run_dir, '0.28.1').isfile()
        assert not join_path(run_dir, WORKSPACE, 'wheel').exists()


class TestExistingPacks:
    """The packs and wheels of an earlier run are kept."""

    def test_existing_packs_are_kept(self, fs, run_dir, mirror, monkeypatch):
        """A complete workspace is kept as it is: nothing is fetched or built again."""
        register_wheels(mirror)
        repo = make_repo()
        Repo(repo, make_config(), client=mirror.client).run()
        tree = read_tree(join_path(run_dir, WORKSPACE))
        requests = list(mirror.requests)

        # no pack of a version may be built again: the encoders of the second
        # run fail the test when they are constructed, the writes are collected
        class NoBuild:
            PACK_VERSION = pack_wheel.PackWheel.PACK_VERSION

            def __init__(self, *args, **kwargs):
                raise AssertionError('the pack must not be built again')

        written = []
        monkeypatch.setattr(repo_dep, 'PackWheel', NoBuild)
        monkeypatch.setattr(repo_dep, 'PackWheelUpdate', NoBuild)
        monkeypatch.setattr(
            repo_dep, 'atomic_write_stream', lambda file, data: written.append(str(file)))
        Repo(repo, make_config(), client=mirror.client).run()

        assert written == []
        # the second run is answered by the local files alone: the snapshot of
        # the index page of every version and the wheels are in the cache, no
        # request is made at all
        assert mirror.requests == requests
        assert read_tree(join_path(run_dir, WORKSPACE)) == tree


class TestCleanup:
    """The folders the lookback window does not ask for are removed."""

    def test_stale_cache_removed(self, fs, run_dir, mirror):
        """The cache folders of versions and names out of the window are removed."""
        register_wheels(mirror)
        # a version that slid out of the window, a name of the window the
        # entry does not declare, and a name of another config
        fs.create_file(join_path(
            run_dir, WORKSPACE, '_wheel_cache', 'httpx', '0.26.0', 'httpx-0.26.0-py3-none-any.whl'),
            contents=b'stale')
        fs.create_file(join_path(
            run_dir, WORKSPACE, '_wheel_cache', 'demo', '1.0', 'demo-1.0-py3-none-any.whl'),
            contents=b'stale')
        fs.create_file(join_path(
            run_dir, WORKSPACE, '_wheel_cache', 'other', '1.0', 'other-1.0-py3-none-any.whl'),
            contents=b'stale')

        with logger.mock_capture_writer() as capture:
            run_commit(make_repo(), make_config(), mirror.client)

        assert capture.concat() == f"""\
INFO | Using update_pack_deps of module/config/const.py at c3: httpx
INFO | [1/3] Fetching wheel of "httpx==0.28.1", index="http://mirror", role=target
INFO | Download index page: "http://mirror/httpx/"
INFO | Download wheel: "http://mirror/packages/httpx-0.28.1-py3-none-any.whl" -> "{run_dir}/pack/Author_Repo_master/_wheel_cache/httpx/0.28.1/httpx-0.28.1-py3-none-any.whl"
INFO | [2/3] Fetching wheel of "httpx==0.27.8", index="http://mirror", role=historical
INFO | Download index page: "http://mirror/httpx/"
INFO | Download wheel: "http://mirror/packages/httpx-0.27.8-py3-none-any.whl" -> "{run_dir}/pack/Author_Repo_master/_wheel_cache/httpx/0.27.8/httpx-0.27.8-py3-none-any.whl"
INFO | [3/3] Fetching wheel of "httpx==0.27.7", index="http://mirror", role=historical
INFO | Download index page: "http://mirror/httpx/"
INFO | Download wheel: "http://mirror/packages/httpx-0.27.7-py3-none-any.whl" -> "{run_dir}/pack/Author_Repo_master/_wheel_cache/httpx/0.27.7/httpx-0.27.7-py3-none-any.whl"
INFO | Generating the dependency packs of "Author_Repo_master": 1 dependencies, 3 versions
INFO | [1/1] Building dependency packs of "httpx": 3 versions, target=0.28.1
INFO | Writing full pack: "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.28.1/full.pack"
INFO | Writing update pack: 0.27.8 -> 0.28.1, "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.28.1/update_0.27.8.pack"
INFO | Writing update pack: 0.27.7 -> 0.28.1, "{run_dir}/pack/Author_Repo_master/packdep/httpx/0.28.1/update_0.27.7.pack"
INFO | Dependency packs of "Author_Repo_master" generated
INFO | Generating packs of "Author_Repo_master": 3 versions, latest=c3
INFO | Writing full pack: "{run_dir}/pack/Author_Repo_master/packrepo/c3/full.pack"
INFO | [1/2] Packing update pack from c2
INFO | Writing update pack: c2 -> c3, "{run_dir}/pack/Author_Repo_master/packrepo/c3/update_c2.pack"
INFO | [2/2] Packing update pack from c1
INFO | Writing update pack: c1 -> c3, "{run_dir}/pack/Author_Repo_master/packrepo/c3/update_c1.pack"
INFO | Packs of "Author_Repo_master" generated, latest=c3
INFO | Writing latest info: version=c3, "{run_dir}/pack/Author_Repo_master/packrepo/latest.pack"
INFO | Packs of "Author_Repo_master" committed, latest=c3
INFO | Dependency packs of "Author_Repo_master" committed
INFO | Removing stale wheel cache: "{run_dir}/pack/Author_Repo_master/_wheel_cache/httpx/0.26.0"
INFO | Removing stale wheel cache: "{run_dir}/pack/Author_Repo_master/_wheel_cache/demo/1.0"
INFO | Removing stale wheel cache: "{run_dir}/pack/Author_Repo_master/_wheel_cache/other/1.0"
INFO | Repo "Author_Repo_master" committed
"""
        assert not join_path(run_dir, WORKSPACE, '_wheel_cache', 'httpx', '0.26.0').exists()
        assert not join_path(run_dir, WORKSPACE, '_wheel_cache', 'demo').exists()
        assert not join_path(run_dir, WORKSPACE, '_wheel_cache', 'other').exists()
        # the wheels of the window are kept
        for version in ('0.27.7', '0.27.8', '0.28.1'):
            assert cache_file(run_dir, version).isfile()

    def test_stale_packdep_removed(self, fs, run_dir, mirror):
        """The dependency folders and the pack folders out of the window are removed."""
        register_wheels(mirror)
        # a version that slid out of the window, and a dependency of another
        # config (or of a name removed from update_pack_deps)
        fs.create_file(pack_file(run_dir, '0.26.0', 'full.pack'), contents=b'stale')
        fs.create_file(pack_file(run_dir, '0.27.8', 'full.pack'), contents=b'stale')
        fs.create_file(join_path(run_dir, DEP_FOLDER.rsplit('/', 1)[0], 'other', 'latest.pack'),
                       contents=b'stale')

        gen = Repo(make_repo(), make_config(), client=mirror.client)
        gen.run()
        # run() only generates: the old packs stay until commit()
        assert pack_file(run_dir, '0.26.0', 'full.pack').isfile()
        gen.commit()

        assert not pack_file(run_dir, '0.26.0').exists()
        assert not pack_file(run_dir, '0.27.8').exists()
        assert not join_path(run_dir, WORKSPACE, 'packdep', 'other').exists()
        # the channel of the dependency is kept: the full pack of the target
        # and its update packs
        assert pack_file(run_dir, '0.28.1', 'full.pack').isfile()
        assert pack_file(run_dir, '0.28.1', 'update_0.27.7.pack').isfile()
        assert pack_file(run_dir, '0.28.1', 'update_0.27.8.pack').isfile()

    def test_stale_wheel_index_removed(self, fs, run_dir, mirror):
        """A published version the latest commit does not ask for is removed."""
        mirror.alias(PROVIDED_URL)
        register_wheels(mirror)
        mirror.register('msgspec', '0.22.0', tags='cp38-cp38-win_amd64')
        requirements = {
            'c1': f'httpx==0.28.1\n    # from {PROVIDED_URL}/\n',
        }
        entry = entry_source('httpx', provide=(PROVIDED_URL,))
        repo = make_repo(requirements, entry=entry)
        run_commit(repo, make_config(), mirror.client)
        assert wheel_file(run_dir, '0.28.1').isfile()

        # a wheel of a version the pin left behind
        fs.create_file(
            join_path(run_dir, WORKSPACE, 'wheel', 'httpx', '0.27.7', 'httpx-0.27.7-py3-none-any.whl'),
            contents=b'stale')
        with logger.mock_capture_writer() as capture:
            run_commit(repo, make_config(), mirror.client)
        assert 'Removing stale wheel' in capture.concat()
        assert not join_path(run_dir, WORKSPACE, 'wheel', 'httpx', '0.27.7').exists()
        assert wheel_file(run_dir, '0.28.1').isfile()

    def test_wheels_removed_when_nothing_is_provided(self, fs, run_dir, mirror):
        """A repo that provides no pin removes the wheels an earlier run published."""
        register_wheels(mirror)
        # the index of an entry that provided a pin in an earlier run
        fs.create_file(
            join_path(run_dir, WORKSPACE, 'wheel', 'demo', '1.0', 'demo-1.0-py3-none-any.whl'),
            contents=b'stale')

        run_commit(make_repo(), make_config(), mirror.client)

        folder = join_path(run_dir, WORKSPACE, 'wheel')
        assert folder.isdir()
        assert list(folder.iter_foldernames()) == []

    def test_stale_packrepo_removed(self, fs, run_dir, mirror):
        """The packs of the version that was the latest one in an earlier run are removed."""
        register_wheels(mirror)
        repo = make_repo()
        run_commit(repo, make_config(), mirror.client)
        assert repo_file(run_dir, 'c3').isdir()

        # a new commit moves the latest version
        register_wheel(mirror, '0.28.2', CORE_V2 + b'NEWER = True\n')
        requirements = requirement('httpx==0.28.2') + requirement('demo==1.0')
        repo.register_file('c4', 'requirements.txt', requirements.encode('utf-8'))
        repo.register_file('c4', ENTRY_FILE, DEFAULT_ENTRY.encode('utf-8'))
        repo.register_commit(
            'c4', parents=['c3'], author_name='Author', author_time=4, message='commit c4')
        repo.register_branch('master', 'c4')
        with logger.mock_capture_writer() as capture:
            run_commit(repo, make_config(), mirror.client)
        assert 'Removing stale pack folder' in capture.concat()
        assert not repo_file(run_dir, 'c3').exists()
        assert repo_file(run_dir, 'c4', 'full.pack').isfile()


class TestWheelCache:
    """The private cache of the wheels."""

    def test_cache_layout(self, fs, run_dir, mirror):
        """The wheel of a version is fetched to the cache folder of the version."""
        register_wheels(mirror)
        Repo(make_repo(), make_config(), client=mirror.client).run()
        file = cache_file(run_dir, '0.28.1')
        assert file.isfile()
        assert not join_path(run_dir, WORKSPACE, 'wheel').exists()

    def test_cache_is_not_published(self, fs, run_dir, mirror):
        """The cache folder is private: the name starts with '_'."""
        register_wheels(mirror)
        Repo(make_repo(), make_config(), client=mirror.client).run()
        folders = list(join_path(run_dir, WORKSPACE).iter_foldernames())
        assert sorted(folders) == ['_wheel_cache', 'packdep', 'packrepo']

    def test_wheel_sync_is_idempotent(self, fs, run_dir, mirror):
        """A second run keeps the published wheels, the copies are not rewritten."""
        mirror.alias(PROVIDED_URL)
        register_wheels(mirror)
        requirements = {
            'c1': f'httpx==0.28.1\n    # from {PROVIDED_URL}/\n',
        }
        entry = entry_source('httpx', provide=(PROVIDED_URL,))
        repo = make_repo(requirements, entry=entry)
        run_commit(repo, make_config(), mirror.client)
        file = wheel_file(run_dir, '0.28.1')
        stat = file.stat()

        with logger.mock_capture_writer() as capture:
            run_commit(repo, make_config(), mirror.client)
        assert 'Wheel exists, skipped' in capture.concat()
        assert file.stat().st_mtime_ns == stat.st_mtime_ns

    def test_published_wheel_verified(self, fs, run_dir, mirror):
        """A published wheel that is not the cached file is copied again."""
        mirror.alias(PROVIDED_URL)
        register_wheels(mirror)
        requirements = {
            'c1': f'httpx==0.28.1\n    # from {PROVIDED_URL}/\n',
        }
        entry = entry_source('httpx', provide=(PROVIDED_URL,))
        repo = make_repo(requirements, entry=entry)
        run_commit(repo, make_config(), mirror.client)
        file = wheel_file(run_dir, '0.28.1')
        content = file.atomic_read_bytes()
        # a published file of the right size whose bytes were replaced: the
        # copy of an interrupted upload, not the wheel of the cache
        file.atomic_write(bytes(len(content)))

        with logger.mock_capture_writer() as capture:
            run_commit(repo, make_config(), mirror.client)

        assert f'Copy wheel: "{cache_file(run_dir, "0.28.1")}"' in capture.concat()
        assert file.atomic_read_bytes() == content
