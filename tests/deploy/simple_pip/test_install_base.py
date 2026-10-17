"""
Tests for InstallBase: the dependency install of one mod.

InstallBase reads the requirements file of the mod (the requirements_file
of its entry): _dict_update is the intersection of its pins and the names
the entry declares for pack updates (pack_update_deps). install_pack hands
every declared pin to the pack channel of the mod, one PipPack install per
distribution, and leaves the other pins to the pip install of the caller.
The deploy job decides whether the pin is an update or a validation of the
local files, so a dependency without an update is checked and repaired all
the same. cleanup_dist_info removes the metadata directories a version
update left behind, keeping the directory of the version that is kept.

The channels of a test are in-memory channel servers (MockServerFile of
tests.deploy_dev.pack) whose base url carries the path prefix of a
deployment, the packs are built from wheels on the fake filesystem like
test_pip_pack does, so the requests of a flow run on the urls a real
deployment serves.
"""
import os

import httpx2
import pytest

from alasio.config.entry.const import ModEntryInfo
from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.server_file import LatestInfo
from alasio.deploy.simple_pip import install_base, pip_pack
from alasio.deploy.simple_pip.install_base import InstallBase
from alasio.deploy_dev.pack.pack_wheel import PackWheel
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import file_read_bytes, file_write
from alasio.ext.path.iter import iter_files
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy.simple_pip.conftest import abs_path, build_wheel, create_dist, list_files, list_folders, site_packages
from tests.deploy_dev.pack.conftest import MockServerFile, app_folder  # noqa: F401

# the mirrors of the fake mod: the channel base of its tree, the packrepo
# folder of a pack server deployment
MIRROR = 'https://pack.example.com/alas/LmeSzinc_Alasio_master/packrepo'


def dep_channel(dist_key):
    """
    The channel base of a dependency, derived from MIRROR by PipPack.

    Args:
        dist_key (str): PEP 503 normalized distribution name

    Returns:
        str: Base url of the channel of the dependency
    """
    return f'https://pack.example.com/alas/LmeSzinc_Alasio_master/packdep/{dist_key}'


def build_dist(name, version):
    """
    Build the wheel of a demo distribution and encode its install tree.

    Args:
        name (str): Name of the distribution
        version (str): Version of the distribution

    Returns:
        tuple[bytes, bytes]: The full pack data of the install tree and its
            index pack (the ledger of the version)
    """
    wheel = build_wheel(
        f'/w/{name}-{version}-py3-none-any.whl',
        {
            f'{name}/__init__.py': b'',
            f'{name}/core.py': f'VERSION = {version!r}\n'.encode(),
        },
        name=name,
        version=version,
    )
    pack = PackWheel(wheel)
    return b''.join(pack.iter_pack_data()), bytes(pack.index_pack)


def tree_of(pack_data):
    """
    {path: content} of the install tree of a full pack.

    The ledger area (.pack/**) and the deletion markers of the pack are
    not files of the site-packages folder.

    Args:
        pack_data (bytes): Full pack data

    Returns:
        dict[str, bytes]: Files of the installation, by relative path
    """
    decoder = PackDecodeBase(pack_data)
    return {
        path: bytes(decoder.catfile(info))
        for path, info in decoder.fileinfo.items()
        if info.edit != 2 and not path.startswith('.pack/')
    }


def read_tree(root):
    """
    {path: content} of the files under a folder, the ledger area excluded.

    Args:
        root (str): Folder to read

    Returns:
        dict[str, bytes]: Files of the folder, by relative posix path
    """
    root = root.rstrip('/')
    return {
        path[len(root) + 1:]: file_read_bytes(path)
        for path in iter_files(root, recursive=True)
        if not path.startswith(f'{root}/.pack/')
    }


class FakeMod:
    """Mod stand-in: a name, a root and an entry carrying the dependency settings."""

    def __init__(self, name='alas', mirrors=MIRROR, root='/mod',
                 requirements_file='requirements.txt', pack_update_deps=()):
        self.name = name
        self.root = PathStr.new(root)
        self.entry = ModEntryInfo(
            name=name,
            root=root,
            mirrors=mirrors,
            requirements_file=requirements_file,
            pack_update_deps=dict.fromkeys(pack_update_deps),
        )


def make_mod(fake, content=None, root='/mod', **kwargs):
    """
    A FakeMod on the fake filesystem, its requirements file written.

    Args:
        fake (FakeFilesystem): The active fake filesystem
        content (str, optional): Content of the requirements file of the
            mod. Defaults to None, no file is written
        root (str): Root of the mod, a path of the fake filesystem
        **kwargs: Arguments of FakeMod, e.g. pack_update_deps

    Returns:
        FakeMod: The mod, with an absolute root path of the fake filesystem
    """
    root = abs_path(fake, root)
    mod = FakeMod(root=root, **kwargs)
    if content is not None:
        fake.create_file(f'{root}/{mod.entry.requirements_file}', contents=content)
    return mod


def patch_servers(monkeypatch, expected_name, *dists):
    """
    Patch pip_pack.ServerFile with one in-memory channel server per
    distribution.

    The factory is the seam of the test: it asserts the channel PipPack
    derived from the mirrors of the mod (a single url, the packdep folder
    of the distribution) and the scope of its mirror record (the mod name)
    before it hands the served channel over, so a derivation or a scope
    other than the expected one fails the test before any request.

    Args:
        monkeypatch (MonkeyPatch): The active monkeypatch
        expected_name (str): Name the mirror record of every channel is
            expected with, the mod name: the record is shared with the
            channel of the mod tree
        *dists (str): Dist-keys of the channels to serve

    Returns:
        dict[str, MockServerFile]: Server of the channel of each
            distribution, the requests of its install are served by it
    """
    servers = {name: MockServerFile(dep_channel(name)) for name in dists}
    channels = {dep_channel(name): server for name, server in servers.items()}

    def make_server(mirrors, name='', client=None):
        urls = mirrors.urls
        assert len(urls) == 1, 'a dependency channel derives a single url from the mod mirrors'
        url = next(iter(urls.values()))
        assert url in channels, f'the channel of a dependency was not served: {url}'
        assert name == expected_name, 'the mirror record of a channel must be named after the mod'
        return channels[url]

    monkeypatch.setattr(pip_pack, 'ServerFile', make_server)
    return servers


class TestDictUpdate:
    """The declared pins: the intersection of requirements and pack_update_deps."""

    @pytest.mark.trio
    async def test_the_declared_pins(self, fs):
        """Only the pins the entry declares for pack updates are returned:
        the local environment does not filter them."""
        site = site_packages(fs)
        create_dist(fs, {}, name='httpx', version='0.28.1')
        create_dist(fs, {}, name='demo', version='1.0')
        mod = make_mod(fs, content="""\
# the dependencies of the demo
demo==2.0
httpx==0.28.1
ruamel.yaml==0.18.6
missing==3.1
nopin>=1.0
""", pack_update_deps=['Demo', 'missing', 'nopin'])
        install = InstallBase(mod, site)
        # the names compare normalized, a declaration without a pin is not a
        # dependency, and undeclared pins are left to pip
        assert await install._dict_update == {'demo': '2.0', 'missing': '3.1'}

    @pytest.mark.trio
    async def test_the_file_of_the_entry(self, fs):
        """The pins are read from {mod.root}/{entry.requirements_file}."""
        site = site_packages(fs)
        root = abs_path(fs, '/mod')
        fs.create_file(f'{root}/requirements.txt', contents='demo==1.0\n')
        fs.create_file(f'{root}/deploy/requirements-lock.txt', contents='demo==2.0\n')
        mod = FakeMod(root=root, requirements_file='deploy/requirements-lock.txt',
                      pack_update_deps=['demo'])
        install = InstallBase(mod, site)
        assert install.path_requirements == f'{root}/deploy/requirements-lock.txt'
        assert await install._dict_update == {'demo': '2.0'}

    @pytest.mark.trio
    async def test_missing_file(self, fs):
        """A mod without a requirements file has nothing to install."""
        with logger.mock_capture_writer() as capture:
            install = InstallBase(make_mod(fs, pack_update_deps=['demo']), site_packages(fs))
            assert await install._dict_update == {}
        assert capture.fd.any_contains('no requirements file')

    @pytest.mark.trio
    async def test_cached(self, fs):
        """The declared pins are read once and cached on the instance."""
        site = site_packages(fs)
        install = InstallBase(make_mod(fs, content='demo==1.0\n', pack_update_deps=['demo']), site)
        assert await install._dict_update == {'demo': '1.0'}
        # the folder reaching the version does not change the declared pins,
        # and a new instance reads the same file
        create_dist(fs, {}, name='demo', version='1.0')
        assert await install._dict_update == {'demo': '1.0'}
        assert await InstallBase(install.mod, site)._dict_update == {'demo': '1.0'}

    @pytest.mark.trio
    async def test_undeclared_pins_are_ignored(self, fs):
        """A pin of a distribution the mod does not declare is not returned,
        whatever is installed in the folder."""
        site = site_packages(fs)
        create_dist(fs, {}, name='demo', version='2.0')
        _, index = build_dist('demo', '1.0')
        fs.create_file(f'{site}/.pack/demo/index.pack', contents=index)

        assert await InstallBase(make_mod(fs, content='demo==2.0\n'), site)._dict_update == {}


class TestInstallPack:
    """The pack install of the declared pins, served from memory."""

    @pytest.mark.trio
    async def test_every_declared_pin_is_reconciled(self, fs, app_folder, monkeypatch):
        """Every declared pin is handed to its channel: a pin at another local
        version is updated, a satisfied pin is validated, an undeclared pin is
        not touched."""
        site = site_packages(fs)
        fs.create_dir(site)
        create_dist(fs, {}, name='other', version='1.0')
        mod = make_mod(fs, content="""\
demo==2.0
other==1.0
plain==1.0
""", pack_update_deps=['Demo', 'other'])
        demo_full, demo_index = build_dist('demo', '2.0')
        other_full, other_index = build_dist('other', '1.0')
        servers = patch_servers(monkeypatch, 'alas', 'demo', 'other')
        servers['demo'].register_version('2.0', demo_full, demo_index)
        servers['other'].register_version('1.0', other_full, other_index)

        install = InstallBase(mod, site)
        assert await install._dict_update == {'demo': '2.0', 'other': '1.0'}
        assert await install.install_pack() == {'demo': True, 'other': True}

        # the files and the metadata of both channels landed, at their pins
        tree = read_tree(site)
        assert {path: content for path, content in tree.items() if path.startswith('demo')} == tree_of(demo_full)
        assert {path: content for path, content in tree.items() if path.startswith('other')} == tree_of(other_full)
        assert file_read_bytes(f'{site}/.pack/demo/index.pack') == demo_index
        assert file_read_bytes(f'{site}/.pack/other/index.pack') == other_index
        # the undeclared pin is left to pip
        assert not [path for path in tree if path.startswith('plain')]
        # both channels were asked, the satisfied pin included
        assert servers['demo'].requests[0] == f'{dep_channel("demo")}/latest.pack'
        assert servers['other'].requests[0] == f'{dep_channel("other")}/latest.pack'

    @pytest.mark.trio
    async def test_a_satisfied_pin_is_validated(self, fs, app_folder, monkeypatch):
        """A declared pin the folder already satisfies goes through the
        channel and is validated, not skipped locally."""
        site = site_packages(fs)
        fs.create_dir(site)
        mod = make_mod(fs, content='demo==1.0\n', pack_update_deps=['demo'])
        full, index = build_dist('demo', '1.0')
        servers = patch_servers(monkeypatch, 'alas', 'demo')
        servers['demo'].register_version('1.0', full, index)

        assert await InstallBase(mod, site).install_pack() == {'demo': True}
        assert servers['demo'].requests[0] == f'{dep_channel("demo")}/latest.pack'

    @pytest.mark.trio
    async def test_a_failed_dep_does_not_stop_the_others(self, fs, app_folder, monkeypatch):
        """A channel that publishes another version fails its distribution
        alone: it is reported False and the others are installed."""
        site = site_packages(fs)
        fs.create_dir(site)
        mod = make_mod(fs, content='demo==2.0\nother==1.0\n', pack_update_deps=['demo', 'other'])
        full, index = build_dist('other', '1.0')
        demo_full, demo_index = build_dist('demo', '3.0')
        servers = patch_servers(monkeypatch, 'alas', 'demo', 'other')
        # the demo channel publishes another version than the pin
        servers['demo'].register_version('3.0', demo_full, demo_index)
        servers['other'].register_version('1.0', full, index)

        with logger.mock_capture_writer() as capture:
            assert await InstallBase(mod, site).install_pack() == {'demo': False, 'other': True}

        # the refusal is the version check alone, nothing of demo was applied
        assert servers['demo'].requests == [f'{dep_channel("demo")}/latest.pack']
        assert not [path for path in read_tree(site) if path.startswith('demo')]
        # the other distribution was installed and its channel used
        assert file_read_bytes(f'{site}/other/core.py') == b"VERSION = '1.0'\n"
        assert servers['other'].requests[0] == f'{dep_channel("other")}/latest.pack'
        # the failure is logged with the reason
        assert capture.fd.any_contains('Failed to install "demo==2.0"')
        assert capture.fd.any_contains('no valid update')

    @pytest.mark.trio
    async def test_a_network_failure_is_reported(self, fs, app_folder, monkeypatch):
        """A network failure of one install is caught and reported as False,
        the other distributions are installed."""
        site = site_packages(fs)
        fs.create_dir(site)
        mod = make_mod(fs, content='demo==2.0\nother==1.0\n', pack_update_deps=['demo', 'other'])
        full, index = build_dist('other', '1.0')
        servers = patch_servers(monkeypatch, 'alas', 'demo', 'other')
        servers['other'].register_version('1.0', full, index)
        install_original = pip_pack.PipPack.install

        async def install_raising(self, name, version, latest_info=None):
            if name == 'demo':
                raise httpx2.ConnectError('mock network failure')
            return await install_original(self, name, version, latest_info=latest_info)

        monkeypatch.setattr(pip_pack.PipPack, 'install', install_raising)
        with logger.mock_capture_writer() as capture:
            assert await InstallBase(mod, site).install_pack() == {'demo': False, 'other': True}

        assert file_read_bytes(f'{site}/other/core.py') == b"VERSION = '1.0'\n"
        assert capture.fd.any_contains('Failed to install "demo==2.0"')
        assert capture.fd.any_contains('mock network failure')

    @pytest.mark.trio
    async def test_pack_deps_without_a_source(self, fs):
        """A mod that declares pack updates but no update source cannot install
        them: the refusal of PipPack surfaces."""
        site = site_packages(fs)
        mod = make_mod(fs, content='demo==1.0\n', mirrors='', pack_update_deps=['demo'])
        with pytest.raises(ValueError, match='declares no update source'):
            await InstallBase(mod, site).install_pack()

    @pytest.mark.trio
    async def test_one_read_serves_the_install(self, fs, app_folder, monkeypatch):
        """
        One instance is one read of the environment: the declared pins and the
        install share the read of the requirements file (the cached property
        of the instance)
        """
        site = site_packages(fs)
        fs.create_dir(site)
        mod = make_mod(fs, content='demo==1.0\n', pack_update_deps=['demo'])
        full, index = build_dist('demo', '1.0')
        servers = patch_servers(monkeypatch, 'alas', 'demo')
        servers['demo'].register_version('1.0', full, index)
        reads = []
        read_text = install_base.atomic_read_text

        def counting_read_text(path, *args, **kwargs):
            reads.append(path)
            return read_text(path, *args, **kwargs)

        monkeypatch.setattr(install_base, 'atomic_read_text', counting_read_text)
        install = InstallBase(mod, site)
        assert await install._dict_update == {'demo': '1.0'}
        assert await install.install_pack() == {'demo': True}
        assert reads == [install.path_requirements]

    @pytest.mark.trio
    async def test_the_cleanup_lists_the_folder_once(self, fs, monkeypatch):
        """The cleanup scans the site-packages folder once for every pin."""
        site = site_packages(fs)
        create_dist(fs, {}, name='demo', version='0.9')
        create_dist(fs, {}, name='demo', version='1.0')
        create_dist(fs, {}, name='other', version='0.9')
        create_dist(fs, {}, name='other', version='1.0')
        mod = make_mod(fs, content='demo==1.0\nother==1.0\n')
        listings = []
        pip_list_class = install_base.PipList

        class CountingPipList(pip_list_class):
            """The listing of the folder, counted"""

            def pip_list(self):
                listings.append(1)
                return super().pip_list()

        monkeypatch.setattr(install_base, 'PipList', CountingPipList)
        assert await InstallBase(mod, site).cleanup_dist_info() == [
            f'{site}/demo-0.9.dist-info',
            f'{site}/other-0.9.dist-info',
        ]
        assert len(listings) == 1

    @pytest.mark.trio
    async def test_a_metadata_directory_of_another_version_triggers_the_cleanup(self, fs):
        """A residue directory goes when its pinned version is installed."""
        site = site_packages(fs)
        create_dist(fs, {}, name='demo', version='1.0')
        fs.create_dir(f'{site}/demo-0.9.dist-info')
        mod = make_mod(fs, content='demo==1.0\n')
        assert await InstallBase(mod, site).cleanup_dist_info() == [
            f'{site}/demo-0.9.dist-info']
        assert list_folders(site) == ['demo-1.0.dist-info']


class TestInstallPackAllDeclared:
    """The declared pins: every one goes through the channel, the satisfied
    ones included."""

    @pytest.mark.trio
    async def test_a_snapshot_skips_latest_pack(self, fs, app_folder, monkeypatch):
        """A local ledger matching the snapshot is validated without a
        latest.pack request, a damaged file uses the full pack range only."""
        site = site_packages(fs)
        fs.create_dir(site)
        mod = make_mod(fs, content='demo==1.0\n', pack_update_deps=['demo'])
        full, index = build_dist('demo', '1.0')
        servers = patch_servers(monkeypatch, 'alas', 'demo')
        servers['demo'].register_version('1.0', full, index)
        assert await InstallBase(mod, site).install_pack() == {'demo': True}

        snapshot = LatestInfo(version='1.0', checksum=PackDecodeBase(index).index_checksum)
        monkeypatch.setattr(
            install_base.PackDep, 'read',
            staticmethod(lambda root: {'demo': snapshot}))
        servers['demo'].requests.clear()
        assert await InstallBase(mod, site).install_pack() == {'demo': True}
        assert servers['demo'].requests == []

        file_write(f'{site}/demo/core.py', b'damaged\n')
        assert await InstallBase(mod, site).install_pack() == {'demo': True}
        assert servers['demo'].requests == [f'{dep_channel("demo")}/1.0/full.pack']
        assert file_read_bytes(f'{site}/demo/core.py') == b"VERSION = '1.0'\n"

    @pytest.mark.trio
    async def test_the_packdep_is_read_once_per_instance(self, fs, monkeypatch):
        """The dependency snapshot follows the mod path and is cached on the
        instance like the requirements file."""
        site = site_packages(fs)
        mod = make_mod(fs, content='demo==1.0\n', pack_update_deps=['demo'])
        reads = []
        monkeypatch.setattr(
            install_base.PackDep, 'read',
            staticmethod(lambda root: reads.append(root) or {}))
        install = InstallBase(mod, site)
        assert await install._packdep == {}
        assert await install._packdep == {}
        assert reads == [mod.root]

    @pytest.mark.trio
    async def test_a_satisfied_pin_is_verified_and_repaired(self, fs, app_folder, monkeypatch):
        """A dependency the folder already satisfies is validated by the pack
        job of its pin (the local index against the channel, every recorded
        file against the index) and a damaged file is downloaded again."""
        site = site_packages(fs)
        fs.create_dir(site)
        mod = make_mod(fs, content='demo==1.0\n', pack_update_deps=['demo'])
        full, index = build_dist('demo', '1.0')
        servers = patch_servers(monkeypatch, 'alas', 'demo')
        servers['demo'].register_version('1.0', full, index)

        install = InstallBase(mod, site)
        assert await install.install_pack() == {'demo': True}
        assert file_read_bytes(f'{site}/demo/core.py') == b"VERSION = '1.0'\n"
        # the file of the dependency is damaged afterwards
        file_write(f'{site}/demo/core.py', b'damaged\n')
        servers['demo'].requests.clear()

        # a new instance reads the state of the folder as it is now: the pin
        # is satisfied (nothing to install), the file is validated against the
        # channel and repaired with the range of the full pack
        install = InstallBase(mod, site)
        assert await install.install_pack() == {'demo': True}
        assert file_read_bytes(f'{site}/demo/core.py') == b"VERSION = '1.0'\n"
        channel = dep_channel('demo')
        assert servers['demo'].requests == [f'{channel}/latest.pack', f'{channel}/1.0/full.pack']

    @pytest.mark.trio
    async def test_a_missing_pin_is_installed_by_the_same_call(self, fs, app_folder, monkeypatch):
        """A declared pin the folder does not satisfy is installed by the same
        call that verifies the satisfied ones."""
        site = site_packages(fs)
        fs.create_dir(site)
        mod = make_mod(fs, content='demo==1.0\n', pack_update_deps=['demo', 'other'])
        demo_full, demo_index = build_dist('demo', '1.0')
        other_full, other_index = build_dist('other', '2.0')
        servers = patch_servers(monkeypatch, 'alas', 'demo', 'other')
        servers['demo'].register_version('1.0', demo_full, demo_index)
        servers['other'].register_version('2.0', other_full, other_index)
        assert await InstallBase(mod, site).install_pack() == {'demo': True}
        servers['demo'].requests.clear()

        # the requirements of the mod changed (a new pin joined demo): the
        # satisfied demo is verified, the missing other is installed
        file_write(f'{mod.root}/{mod.entry.requirements_file}', b'demo==1.0\nother==2.0\n')
        install = InstallBase(mod, site)
        assert await install.install_pack() == {'demo': True, 'other': True}
        assert file_read_bytes(f'{site}/demo/core.py') == b"VERSION = '1.0'\n"
        assert file_read_bytes(f'{site}/other/core.py') == b"VERSION = '2.0'\n"
        assert servers['demo'].requests == [f'{dep_channel("demo")}/latest.pack']
        assert servers['other'].requests[0] == f'{dep_channel("other")}/latest.pack'

    @pytest.mark.trio
    async def test_nothing_declared_is_a_noop(self, fs, monkeypatch):
        """A mod that declares no pack updated dependency has nothing to
        reconcile: no channel is requested, whatever the requirements file
        pins."""
        site = site_packages(fs)
        mod = make_mod(fs, content='demo==1.0\n')

        def make_server(mirrors, scope='', client=None):
            raise AssertionError('no channel may be requested')

        monkeypatch.setattr(pip_pack, 'ServerFile', make_server)
        assert await InstallBase(mod, site).install_pack() == {}

    @pytest.mark.trio
    async def test_without_a_requirements_file(self, fs):
        """A mod without a requirements file has nothing to reconcile."""
        with logger.mock_capture_writer() as capture:
            install = InstallBase(make_mod(fs, pack_update_deps=['demo']), site_packages(fs))
            assert await install.install_pack() == {}
        assert capture.fd.any_contains('no requirements file')

    @pytest.mark.trio
    async def test_a_channel_that_publishes_another_version(self, fs, app_folder, monkeypatch):
        """The pinned version is the version the channel must publish: a
        channel that moved on provides no valid update for a satisfied pin
        either, the verification is reported as a failure."""
        site = site_packages(fs)
        fs.create_dir(site)
        create_dist(fs, {}, name='demo', version='1.0')
        mod = make_mod(fs, content='demo==1.0\n', pack_update_deps=['demo'])
        full, index = build_dist('demo', '3.0')
        servers = patch_servers(monkeypatch, 'alas', 'demo')
        servers['demo'].register_version('3.0', full, index)

        with logger.mock_capture_writer() as capture:
            assert await InstallBase(mod, site).install_pack() == {'demo': False}
        assert capture.fd.any_contains('Failed to install "demo==1.0"')
        assert capture.fd.any_contains('no valid update')
        # the refusal is the version check alone, the folder is untouched
        assert list_folders(site) == ['demo-1.0.dist-info']

    @pytest.mark.trio
    async def test_the_cleanup_removes_the_metadata_of_another_version(
            self, fs, app_folder, monkeypatch):
        """The install leaves the metadata directories alone; the separate
        cleanup removes the residue after the pins are in place."""
        site = site_packages(fs)
        fs.create_dir(site)
        # the residue of an installation of another version (pip leaves the
        # REQUESTED file of a direct installation behind)
        create_dist(fs, {'demo-0.9.dist-info/REQUESTED': b''}, name='demo', version='0.9')
        mod = make_mod(fs, content='demo==1.0\n', pack_update_deps=['demo'])
        full, index = build_dist('demo', '1.0')
        servers = patch_servers(monkeypatch, 'alas', 'demo')
        servers['demo'].register_version('1.0', full, index)

        assert await InstallBase(mod, site).install_pack() == {'demo': True}
        # the install itself does not touch the metadata of the old version
        assert 'demo-0.9.dist-info' in list_folders(site)

        install = InstallBase(mod, site)
        with logger.mock_capture_writer() as capture:
            assert await install.cleanup_dist_info() == [f'{site}/demo-0.9.dist-info']
        assert capture.fd.any_contains('Removed the metadata directory of "demo" "0.9"')
        folders = list_folders(site)
        assert 'demo-1.0.dist-info' in folders
        assert 'demo-0.9.dist-info' not in folders


class TestCleanupDistInfo:
    """The metadata directories reconciled with the requirements pins."""

    @pytest.mark.trio
    async def test_keeps_only_the_pinned_version(self, fs):
        """Every metadata directory of a pinned distribution but the one of
        its pin is removed, an unpinned distribution is untouched."""
        site = site_packages(fs)
        create_dist(fs, {}, name='demo', version='1.0')
        create_dist(fs, {}, name='demo', version='2.0')
        create_dist(fs, {}, name='demo', version='3.0')
        create_dist(fs, {}, name='other', version='1.0')
        install = InstallBase(make_mod(fs, content='demo==2.0\n'), site)

        # 3.0 is the residue of a downgrade: every other version goes, not
        # only the older ones
        with logger.mock_capture_writer() as capture:
            assert await install.cleanup_dist_info() == [
                f'{site}/demo-1.0.dist-info',
                f'{site}/demo-3.0.dist-info',
            ]
        assert capture.fd.any_contains('Removed the metadata directory of "demo" "1.0"')
        assert capture.fd.any_contains('Removed the metadata directory of "demo" "3.0"')
        assert list_folders(site) == ['demo-2.0.dist-info', 'other-1.0.dist-info']
        # the kept installation is untouched, its files included
        assert list_files(f'{site}/demo-2.0.dist-info') == ['METADATA', 'RECORD']

    @pytest.mark.trio
    async def test_removes_a_directory_with_files_of_no_record(self, fs):
        """The removal is unconditional: a directory that still holds files
        of no pack record, e.g. the files a pip installation left, goes as a
        whole."""
        site = site_packages(fs)
        create_dist(fs, {
            'demo-1.0.dist-info/REQUESTED': b'',
            'demo-1.0.dist-info/direct_url.json': b'{"url": "file:///w/demo"}',
            'demo-1.0.dist-info/leftover/note.txt': b'left by hand\n',
        }, name='demo', version='1.0')
        create_dist(fs, {}, name='demo', version='2.0')
        install = InstallBase(make_mod(fs, content='demo==2.0\n'), site)

        assert await install.cleanup_dist_info() == [f'{site}/demo-1.0.dist-info']
        assert list_folders(site) == ['demo-2.0.dist-info']

    @pytest.mark.trio
    async def test_the_name_is_matched_normalized(self, fs):
        """The metadata names the distribution, the directory name is the
        fallback, and the compare is normalized: any case and separator
        style of the name finds the same directories."""
        site = site_packages(fs)
        create_dist(fs, {
            'ruamel_yaml-0.18.6.dist-info/METADATA':
                b'Metadata-Version: 2.1\nName: ruamel.yaml\nVersion: 0.18.6\n\n',
        }, name='ruamel_yaml', version='0.18.6')
        create_dist(fs, {
            'ruamel_yaml-0.19.0.dist-info/METADATA':
                b'Metadata-Version: 2.1\nName: ruamel.yaml\nVersion: 0.19.0\n\n',
        }, name='ruamel_yaml', version='0.19.0')
        install = InstallBase(make_mod(fs, content='ruamel.yaml==0.19.0\n'), site)

        assert await install.cleanup_dist_info() == [
            f'{site}/ruamel_yaml-0.18.6.dist-info']
        assert list_folders(site) == ['ruamel_yaml-0.19.0.dist-info']

    @pytest.mark.trio
    async def test_the_metadata_decides_the_version(self, fs):
        """The directory carries the version to keep, the metadata another
        one: pip reads the metadata, the entry is an installation of the
        other version and is no proof the version to keep landed."""
        site = site_packages(fs)
        create_dist(fs, {
            'demo-2.0.dist-info/METADATA':
                b'Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n\n',
        }, name='demo', version='2.0')
        create_dist(fs, {}, name='demo', version='1.0')
        install = InstallBase(make_mod(fs, content='demo==2.0\n'), site)

        # no installation of 2.0 is there, whatever the names say: nothing
        # goes, an environment that is not at the pin keeps its metadata
        assert await install.cleanup_dist_info() == []
        assert list_folders(site) == ['demo-1.0.dist-info', 'demo-2.0.dist-info']

        # the pinned version lands: the mismatched directory is an
        # installation of 1.0 and goes with the other 1.0 one
        create_dist(fs, {
            'demo_x-2.0.dist-info/METADATA':
                b'Metadata-Version: 2.1\nName: demo\nVersion: 2.0\n\n',
        }, name='demo_x', version='2.0')
        assert await install.cleanup_dist_info() == [
            f'{site}/demo-1.0.dist-info',
            f'{site}/demo-2.0.dist-info',
        ]
        assert list_folders(site) == ['demo_x-2.0.dist-info']

    @pytest.mark.trio
    async def test_a_copy_of_the_version_under_the_name_of_another(self, fs):
        """The metadata records the version to keep, the directory name
        another one, and an installation of the version under its own name is
        there too: the copy goes."""
        site = site_packages(fs)
        create_dist(fs, {
            'demo-1.0.dist-info/METADATA':
                b'Metadata-Version: 2.1\nName: demo\nVersion: 2.0\n\n',
        }, name='demo', version='1.0')
        create_dist(fs, {}, name='demo', version='2.0')
        install = InstallBase(make_mod(fs, content='demo==2.0\n'), site)

        assert await install.cleanup_dist_info() == [f'{site}/demo-1.0.dist-info']
        assert list_folders(site) == ['demo-2.0.dist-info']

    @pytest.mark.trio
    async def test_the_only_installation_is_kept_whatever_its_name(self, fs):
        """The version to keep is installed under the name of another version
        and is the only metadata of it: it is kept, the other versions go, a
        distribution is never left without metadata."""
        site = site_packages(fs)
        create_dist(fs, {
            'demo-1.0.dist-info/METADATA':
                b'Metadata-Version: 2.1\nName: demo\nVersion: 2.0\n\n',
        }, name='demo', version='1.0')
        create_dist(fs, {}, name='demo', version='3.0')
        install = InstallBase(make_mod(fs, content='demo==2.0\n'), site)

        assert await install.cleanup_dist_info() == [f'{site}/demo-3.0.dist-info']
        assert list_folders(site) == ['demo-1.0.dist-info']

    @pytest.mark.trio
    async def test_a_directory_without_metadata_is_no_installation(self, fs):
        """A directory that holds no METADATA is no installation, PEP 376,
        whatever version its name carries: it goes, the version of the
        metadata alone decides."""
        site = site_packages(fs)
        create_dist(fs, {
            'demo-1.0.dist-info/METADATA':
                b'Metadata-Version: 2.1\nName: demo\nVersion: 2.0\n\n',
        }, name='demo', version='1.0')
        # the empty directories an interrupted install left: one carries the
        # version to keep, both are phantoms pip reads no installation from
        fs.create_dir(f'{site}/demo-2.0.dist-info')
        fs.create_dir(f'{site}/demo-3.0.dist-info')
        install = InstallBase(make_mod(fs, content='demo==2.0\n'), site)

        assert await install.cleanup_dist_info() == [
            f'{site}/demo-2.0.dist-info',
            f'{site}/demo-3.0.dist-info',
        ]
        assert list_folders(site) == ['demo-1.0.dist-info']

    @pytest.mark.trio
    async def test_a_metadata_without_a_version_is_no_installation(self, fs):
        """A METADATA that records no version names no installation pip can
        read: a directory of that shape is no proof the version to keep landed
        and goes with the installations of the other versions."""
        site = site_packages(fs)
        create_dist(fs, {
            'demo-2.0.dist-info/METADATA': b'Metadata-Version: 2.1\nName: demo\n\n',
        }, name='demo', version='2.0')
        create_dist(fs, {}, name='demo', version='1.0')
        install = InstallBase(make_mod(fs, content='demo==2.0\n'), site)

        # the version of the directory name alone is no proof: nothing goes
        assert await install.cleanup_dist_info() == []
        assert list_folders(site) == ['demo-1.0.dist-info', 'demo-2.0.dist-info']

        # the pinned version lands: the entry without a version goes with
        # the installations of the other version
        create_dist(fs, {
            'demo_x-2.0.dist-info/METADATA':
                b'Metadata-Version: 2.1\nName: demo\nVersion: 2.0\n\n',
        }, name='demo_x', version='2.0')
        assert await install.cleanup_dist_info() == [
            f'{site}/demo-1.0.dist-info',
            f'{site}/demo-2.0.dist-info',
        ]
        assert list_folders(site) == ['demo_x-2.0.dist-info']

    @pytest.mark.trio
    async def test_a_pin_that_is_not_installed_is_left(self, fs):
        """No directory of the pinned version: the metadata of the
        installed versions is kept."""
        site = site_packages(fs)
        create_dist(fs, {}, name='demo', version='1.0')
        install = InstallBase(make_mod(fs, content='demo==2.0\n'), site)

        assert await install.cleanup_dist_info() == []
        assert list_folders(site) == ['demo-1.0.dist-info']

    @pytest.mark.trio
    async def test_the_version_to_keep_must_hold_metadata(self, fs):
        """A directory of the version to keep that holds no METADATA is the
        residue of an interrupted install rather than a proof it landed: the
        metadata of the other versions is kept."""
        site = site_packages(fs)
        create_dist(fs, {}, name='demo', version='1.0')
        # the directory of the new version landed, its METADATA never did
        fs.create_dir(f'{site}/demo-2.0.dist-info')
        install = InstallBase(make_mod(fs, content='demo==2.0\n'), site)

        assert await install.cleanup_dist_info() == []
        assert list_folders(site) == ['demo-1.0.dist-info', 'demo-2.0.dist-info']

    @pytest.mark.trio
    async def test_a_linked_metadata_directory_is_left(self, fs):
        """A metadata directory that is a symbolic link is not an
        installation of the folder: it is neither read nor removed."""
        site = site_packages(fs)
        create_dist(fs, {}, name='demo', version='2.0')
        other = abs_path(fs, '/env/other/demo-1.0.dist-info')
        fs.create_file(f'{other}/METADATA', contents=b'Metadata-Version: 2.1\n')
        fs.create_symlink(f'{site}/demo-1.0.dist-info', other)
        install = InstallBase(make_mod(fs, content='demo==2.0\n'), site)

        assert await install.cleanup_dist_info() == []
        # the link and the tree it points at are untouched
        assert os.path.islink(f'{site}/demo-1.0.dist-info')
        assert list_files(other) == ['METADATA']

    @pytest.mark.trio
    async def test_a_failed_removal_is_kept_and_retried(self, fs, monkeypatch):
        """A directory that can not be removed is logged and kept as it is,
        the next call removes it."""
        site = site_packages(fs)
        create_dist(fs, {}, name='demo', version='1.0')
        create_dist(fs, {}, name='demo', version='2.0')
        stale = f'{site}/demo-1.0.dist-info'
        unlink = os.unlink

        def unlink_locked(path, *args, **kwargs):
            if str(path).startswith(stale):
                raise PermissionError(13, 'Permission denied')
            return unlink(path, *args, **kwargs)

        monkeypatch.setattr(os, 'unlink', unlink_locked)
        install = InstallBase(make_mod(fs, content='demo==2.0\n'), site)
        with logger.mock_capture_writer() as capture:
            assert await install.cleanup_dist_info() == []
        assert capture.fd.any_contains('keeping it for the next call')
        assert list_folders(site) == ['demo-1.0.dist-info', 'demo-2.0.dist-info']

        # the next call removes the directory the lock left behind
        monkeypatch.setattr(os, 'unlink', unlink)
        assert await install.cleanup_dist_info() == [stale]
        assert list_folders(site) == ['demo-2.0.dist-info']

    @pytest.mark.trio
    async def test_idempotent(self, fs):
        """A second call finds nothing to remove and logs no removal."""
        site = site_packages(fs)
        create_dist(fs, {}, name='demo', version='1.0')
        install = InstallBase(make_mod(fs, content='demo==1.0\n'), site)

        with logger.mock_capture_writer() as capture:
            assert await install.cleanup_dist_info() == []
            assert await install.cleanup_dist_info() == []
        assert not capture.fd.any_contains('Removed the metadata directory of "demo"')
        assert list_folders(site) == ['demo-1.0.dist-info']
