"""
Tests for InstallBase: the dependency install of one mod.

InstallBase reads the requirements file of the mod (the requirements_file
of its entry) and compares its pins with the distributions installed in
the site-packages folder (PipList): get_dict_update() is the pins the
folder does not satisfy, missing locally or at another version.
install_pack installs the out-of-date pins that the entry declares for
pack updates (pack_update_deps) from the pack channels of the mod, one
PipPack install per distribution, and leaves the other pins to the pip
install of the caller.

The channels of a test are in-memory channel servers (MockServerFile of
tests.deploy_dev.pack) whose base url carries the path prefix of a
deployment, the packs are built from wheels on the fake filesystem like
test_pip_pack does, so the requests of a flow run on the urls a real
deployment serves.
"""
from urllib.parse import urlsplit

import httpx2
import pytest

from alasio.config.entry.const import ModEntryInfo
from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.simple_pip import pip_pack
from alasio.deploy.simple_pip.install_base import InstallBase
from alasio.deploy_dev.pack.pack_wheel import PackWheel
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import file_read_bytes
from alasio.ext.path.iter import iter_files
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack.conftest import MockServerFile, app_folder  # noqa: F401
from tests.deploy_dev.simple_pip.conftest import abs_path, build_wheel, create_dist, site_packages

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


class ChannelServer(MockServerFile):
    """
    MockServerFile serving a pack channel whose base url carries a path
    prefix.

    The channel base of a deployment is
    {mirror}/{Author}_{Repo}_{Branch}/packdep/{name} while MockServerFile
    serves the packs of the path right after the host: the prefix of the
    base url is stripped from every request before the packs are served, so
    the requests of a flow run on the urls a deployment serves. The version
    registered last is the latest one, served by latest.pack like the
    deployment serves the packs of its target version.
    """

    def __init__(self, base_url):
        """
        Args:
            base_url (str): Base url of the channel, the path prefix included
        """
        super().__init__(base_url)
        # the urls of the requests served, in order
        self.requests = []
        self._prefix = urlsplit(base_url).path.rstrip('/')

    def _handle(self, request):
        """
        Serve one request of the channel, the prefix of the base url stripped.

        Args:
            request (httpx2.Request): The request

        Returns:
            httpx2.Response: The response
        """
        self.requests.append(str(request.url))
        path = request.url.path
        if path.startswith(self._prefix):
            path = path[len(self._prefix):] or '/'
        # MockServerFile serves the packs of the stripped path
        return super()._handle(httpx2.Request(
            request.method, request.url.copy_with(path=path), headers=request.headers))


def patch_servers(monkeypatch, expected_scope, *dists):
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
        expected_scope (str): Scope the mirror record of every channel is
            expected with, the mod name: the record is shared with the
            channel of the mod tree
        *dists (str): Dist-keys of the channels to serve

    Returns:
        dict[str, ChannelServer]: Server of the channel of each
            distribution, the requests of its install are served by it
    """
    servers = {name: ChannelServer(dep_channel(name)) for name in dists}
    channels = {dep_channel(name): server for name, server in servers.items()}

    def make_server(mirrors, scope='', client=None):
        urls = mirrors.urls
        assert len(urls) == 1, 'a dependency channel derives a single url from the mod mirrors'
        url = next(iter(urls.values()))
        assert url in channels, f'the channel of a dependency was not served: {url}'
        assert scope == expected_scope, 'the mirror record of a channel must be scoped to the mod'
        return channels[url]

    monkeypatch.setattr(pip_pack, 'ServerFile', make_server)
    return servers


class TestDictUpdate:
    """The local check: the pins of the requirements file against the folder."""

    @pytest.mark.trio
    async def test_out_of_date_pins(self, fs):
        """A pin that is missing and a pin at another version are out of date,
        a satisfied pin is not."""
        site = site_packages(fs)
        create_dist(fs, {}, name='httpx', version='0.28.1')
        create_dist(fs, {
            'ruamel_yaml-0.18.6.dist-info/METADATA':
                b'Metadata-Version: 2.1\nName: ruamel.yaml\nVersion: 0.18.6\n\n',
        }, name='ruamel_yaml', version='0.18.6')
        create_dist(fs, {}, name='demo', version='1.0')
        mod = make_mod(fs, content="""\
# the dependencies of the demo
demo==2.0
httpx==0.28.1
ruamel.yaml==0.18.6
missing==3.1
nopin>=1.0
""")
        install = InstallBase(mod, site)
        # httpx and ruamel.yaml are at their versions (the names compare
        # normalized), demo is at another one and missing is not installed;
        # the declaration without a pin is not a dependency
        assert await install.get_dict_update() == {'demo': '2.0', 'missing': '3.1'}

    @pytest.mark.trio
    async def test_the_file_of_the_entry(self, fs):
        """The pins are read from {mod.root}/{entry.requirements_file}."""
        site = site_packages(fs)
        root = abs_path(fs, '/mod')
        fs.create_file(f'{root}/requirements.txt', contents='demo==1.0\n')
        fs.create_file(f'{root}/deploy/requirements-lock.txt', contents='demo==2.0\n')
        mod = FakeMod(root=root, requirements_file='deploy/requirements-lock.txt')
        install = InstallBase(mod, site)
        assert install.path_requirements == f'{root}/deploy/requirements-lock.txt'
        assert await install.get_dict_update() == {'demo': '2.0'}

    @pytest.mark.trio
    async def test_missing_file(self, fs):
        """A mod without a requirements file has nothing to install."""
        with logger.mock_capture_writer() as capture:
            install = InstallBase(make_mod(fs), site_packages(fs))
            assert await install.get_dict_update() == {}
        assert capture.fd.any_contains('no requirements file')

    @pytest.mark.trio
    async def test_cached(self, fs):
        """The pins are read once and cached on the instance."""
        site = site_packages(fs)
        install = InstallBase(make_mod(fs, content='demo==1.0\n'), site)
        assert await install.get_dict_update() == {'demo': '1.0'}
        # the folder reaches the version afterwards: the cached read does
        # not change, a new instance reads the new state
        create_dist(fs, {}, name='demo', version='1.0')
        assert await install.get_dict_update() == {'demo': '1.0'}
        assert await InstallBase(install.mod, site).get_dict_update() == {}

    @pytest.mark.trio
    async def test_first_metadata_entry_wins(self, fs):
        """A name with two installed entries checks the first of the listing,
        the entry list() keeps and reports with a warning: the check is not a
        file-level verification, a stale entry does not hide the version of
        the pin."""
        site = site_packages(fs)
        create_dist(fs, {}, name='demo', version='1.0')
        create_dist(fs, {}, name='demo', version='2.0')
        with logger.mock_capture_writer() as capture:
            install_a = InstallBase(make_mod(fs, content='demo==1.0\n', root='/mod-a'), site)
            assert await install_a.get_dict_update() == {}
            assert capture.fd.any_contains('Skipped a duplicate installation of "demo"')
            install_b = InstallBase(make_mod(fs, content='demo==2.0\n', root='/mod-b'), site)
            assert await install_b.get_dict_update() == {
                'demo': '2.0',
            }

    @pytest.mark.trio
    async def test_the_pack_ledger_wins(self, fs):
        """The check reads the version of the pack ledger of a declared
        distribution: the metadata an interrupted update already shows does
        not satisfy a pin of the new version."""
        site = site_packages(fs)
        # the state of an interrupted update: the metadata of the new
        # version landed, the ledger still records the old one
        create_dist(fs, {}, name='demo', version='2.0')
        _, index = build_dist('demo', '1.0')
        fs.create_file(f'{site}/.pack/demo/index.pack', contents=index)

        mod = make_mod(fs, content='demo==2.0\n', pack_update_deps=['demo'])
        assert await InstallBase(mod, site).get_dict_update() == {'demo': '2.0'}
        # the version the ledger records is satisfied, whatever the metadata
        mod = make_mod(fs, content='demo==1.0\n', root='/mod-b', pack_update_deps=['demo'])
        assert await InstallBase(mod, site).get_dict_update() == {}

    @pytest.mark.trio
    async def test_only_the_declared_ledgers_are_read(self, fs):
        """A ledger of a distribution the mod does not declare is not read."""
        site = site_packages(fs)
        create_dist(fs, {}, name='demo', version='2.0')
        _, index = build_dist('demo', '1.0')
        fs.create_file(f'{site}/.pack/demo/index.pack', contents=index)

        assert await InstallBase(make_mod(fs, content='demo==2.0\n'), site).get_dict_update() == {}


class TestInstallPack:
    """The pack install of the declared and out-of-date pins, served from memory."""

    @pytest.mark.trio
    async def test_only_declared_out_of_date_pins(self, fs, app_folder, monkeypatch):
        """The out-of-date pins declared in pack_update_deps are installed from
        the channels of the mod, one channel per distribution; a satisfied pin
        and an undeclared one are not touched."""
        site = site_packages(fs)
        fs.create_dir(site)
        create_dist(fs, {}, name='other', version='1.0')
        mod = make_mod(fs, content="""\
demo==2.0
other==1.0
plain==1.0
""", pack_update_deps=['Demo', 'other'])
        full, index = build_dist('demo', '2.0')
        servers = patch_servers(monkeypatch, 'alas', 'demo')
        servers['demo'].register_version('2.0', full, index)

        install = InstallBase(mod, site)
        assert await install.get_dict_update() == {'demo': '2.0', 'plain': '1.0'}
        assert await install.install_pack() == {'demo': True}

        # the files and the metadata of the channel landed, at the version
        tree = read_tree(site)
        assert {path: content for path, content in tree.items() if path.startswith('demo')} == tree_of(full)
        assert file_read_bytes(f'{site}/.pack/demo/index.pack') == index
        # the satisfied declared pin and the undeclared pin are not installed
        assert not [path for path in tree if path.startswith('plain')]
        # the install reads the version first, the channel must publish it
        assert servers['demo'].requests[0] == f'{dep_channel("demo")}/latest.pack'

    @pytest.mark.trio
    async def test_nothing_out_of_date(self, fs, monkeypatch):
        """Nothing is requested when every declared pin is satisfied: the
        check needs no channel at all."""
        site = site_packages(fs)
        create_dist(fs, {}, name='demo', version='1.0')
        mod = make_mod(fs, content='demo==1.0\n', pack_update_deps=['demo'])

        def make_server(mirrors, scope='', client=None):
            raise AssertionError('no channel may be requested')

        monkeypatch.setattr(pip_pack, 'ServerFile', make_server)
        assert await InstallBase(mod, site).install_pack() == {}

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

        async def install_raising(self, name, version):
            if name == 'demo':
                raise httpx2.ConnectError('mock network failure')
            return await install_original(self, name, version)

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
