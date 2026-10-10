"""
Tests for PipPack: the dependency install entry of a mod.

The channel of a dependency is derived from the mirrors of the mod (the
packrepo folder of every url becomes the packdep folder of the
distribution) and installed with the deploy job on the site-packages
folder, driven by the version the caller names: the install requires the
channel to publish exactly that version as its latest one, a channel that
publishes another version provides no valid update and the install is
refused. The packs of a test are built from wheels on the in-memory
filesystem and served by an in-memory channel server whose base url
carries the path prefix of a deployment, so the requests of a flow run on
the urls a real channel is served at.

Every test that performs requests is async (pytest-trio): the install
awaits its network phases on the test event loop.
"""
import os
from urllib.parse import urlsplit

import httpx2
import pytest

from alasio.config.entry.const import ModEntryInfo
from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy.pack.server_url import ServerUrl
from alasio.deploy.simple_pip import pip_pack
from alasio.deploy.simple_pip.pip_pack import PipPack
from alasio.deploy_dev.pack.pack_wheel import PackWheel
from alasio.deploy_dev.pack.pack_wheel_update import PackWheelUpdate
from alasio.ext.path.atomic import file_read_bytes, file_write
from alasio.ext.path.iter import iter_files
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack.conftest import MockServerFile, app_folder  # noqa: F401
from tests.deploy_dev.simple_pip.conftest import build_wheel, site_packages

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


def dist_files(version):
    """
    Files of the demo distribution of a version.

    Args:
        version (str): Version of the distribution

    Returns:
        dict[str, bytes]: Member of the wheel -> content
    """
    return {
        'demo/__init__.py': b'',
        'demo/core.py': f'VERSION = {version!r}\n'.encode(),
        'demo/data/table.txt': b'name,value\nalpha,1\nbeta,2\n',
    }


def build_dist(version):
    """
    Build the wheel of the demo distribution and encode its install tree.

    Args:
        version (str): Version of the distribution

    Returns:
        tuple[PackWheel, bytes, bytes]: The wheel pack, its full pack data
            and its index pack (the ledger of the version)
    """
    wheel = build_wheel(
        f'/w/demo-{version}-py3-none-any.whl', dist_files(version), name='demo', version=version)
    pack = PackWheel(wheel)
    return pack, b''.join(pack.iter_pack_data()), bytes(pack.index_pack)


def tree_of(pack_data):
    """
    {path: content} of the install tree of a full pack.

    The ledger area (.pack/**) and the deleted markers of the pack are not
    files of the site-packages folder.

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
    """Mod stand-in: a name and an entry carrying the update mirrors."""

    def __init__(self, name='alas', mirrors=MIRROR):
        self.name = name
        self.entry = ModEntryInfo(name=name, mirrors=mirrors)


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


def patch_server(monkeypatch, server, channel, expected_scope):
    """
    Patch pip_pack.ServerFile with the in-memory server of a channel.

    The factory is the seam of the test: it asserts the channel PipPack
    derived from the mirrors of the mod and the scope of its mirror record
    before it hands the served channel over, so a derivation other than the
    expected one fails the test before any request.

    Args:
        monkeypatch (MonkeyPatch): The active monkeypatch
        server (ChannelServer): Server of the channel, the requests of the
            install are served by it in memory
        channel (str): Channel base url the install is expected to derive
        expected_scope (str): Scope the mirror record of the channel is
            expected with, the mod name: the record is shared with the
            channel of the mod tree
    """

    def make_server(mirrors, scope='', client=None):
        assert mirrors.urls == {'default': channel}, 'PipPack must derive the channel of the mod mirrors'
        assert scope == expected_scope, 'the mirror record of a channel must be scoped to it'
        return server

    monkeypatch.setattr(pip_pack, 'ServerFile', make_server)


class TestChannelMirrors:
    """The derivation of a dependency channel from the mirrors of the mod."""

    def test_single_mirror(self):
        """The packrepo folder of the url becomes the packdep folder."""
        pip = PipPack(FakeMod(), '/env/Lib/site-packages')
        mirrors = pip._channel_mirrors('httpx')
        assert mirrors.groups == {'default': {'default': dep_channel('httpx')}}
        assert mirrors.urls == {'default': dep_channel('httpx')}

    def test_groups_names_and_order_are_kept(self):
        """The shape of the mod mirrors is kept, only the urls change."""
        pip = PipPack(FakeMod(mirrors={
            'cn': {
                '123pan': 'https://123pan.example.com/pack/alas/packrepo',
                'tencent-cos': 'https://cos.example.com/pack/alas/packrepo',
            },
            'global': {'global': 'https://global.example.com/alas/packrepo'},
        }), '/env/Lib/site-packages')
        mirrors = pip._channel_mirrors('ruamel-yaml')
        assert mirrors.groups == {
            'cn': {
                '123pan': 'https://123pan.example.com/pack/alas/packdep/ruamel-yaml',
                'tencent-cos': 'https://cos.example.com/pack/alas/packdep/ruamel-yaml',
            },
            'global': {'global': 'https://global.example.com/alas/packdep/ruamel-yaml'},
        }
        # the members of a group are tried in the declared order, the
        # groups race in parallel: the derivation must not reorder them
        assert list(mirrors.groups) == ['cn', 'global']
        assert list(mirrors.groups['cn']) == ['123pan', 'tencent-cos']

    def test_trailing_slash_is_normalized(self):
        """A trailing slash of a mirror url does not break the derivation."""
        pip = PipPack(FakeMod(mirrors=MIRROR + '/'), '/env/Lib/site-packages')
        assert pip._channel_mirrors('httpx').urls == {'default': dep_channel('httpx')}

    def test_mirror_record_is_shared_with_the_mod(self):
        """The mirror names and the group boundaries are kept: the derived
        structure has the fingerprint of the mod mirrors and the record is
        scoped to the mod, so the probe result of the mod channel (or of
        another dependency) is reused."""
        pip = PipPack(FakeMod(), '/env/Lib/site-packages')
        assert ServerUrl(pip._channel_mirrors('httpx')).set_key == ServerUrl(pip.mirrors).set_key

    def test_url_without_the_channel_folder(self):
        """A url that does not point at a packrepo channel is refused."""
        pip = PipPack(FakeMod(mirrors={'cn': 'https://cn.example.com/alas'}), '/env/Lib/site-packages')
        with pytest.raises(ValueError, match='does not point at a packrepo channel'):
            pip._channel_mirrors('httpx')


class TestConstruction:
    """The inputs of PipPack: the mod mirrors, the folder and the client."""

    def test_mirrors_are_read_and_the_client_kept(self):
        client = object()
        pip = PipPack(FakeMod(), '/env/Lib/site-packages', client=client)
        assert pip.mirrors.urls == {'default': MIRROR}
        assert pip.client is client

    def test_no_update_source(self):
        """A mod without mirrors cannot install anything."""
        with pytest.raises(ValueError, match='declares no update source'):
            PipPack(FakeMod(mirrors=''), '/env/Lib/site-packages')

    def test_malformed_mirrors(self):
        """A broken update source is refused when the entry is read."""
        with pytest.raises(ValueError, match='Invalid mirror url'):
            PipPack(FakeMod(mirrors='not-an-url'), '/env/Lib/site-packages')


class TestInstall:
    """The install flow of a distribution, served from memory."""

    @pytest.mark.trio
    async def test_fresh(self, fs, app_folder, monkeypatch):
        """A distribution that is not installed is rebuilt from the channel:
        the missing ledger of the unknown local version falls back to a
        rebuild, every file (and the ledger) comes from the channel."""
        site = site_packages(fs)
        fs.create_dir(site)
        _, full, index = build_dist('1.0')
        server = ChannelServer(dep_channel('demo'))
        server.register_version('1.0', full, index)
        patch_server(monkeypatch, server, dep_channel('demo'), 'alas')

        # the name is normalized to the dist-key of the channel and the
        # ledger, any case and separator style is accepted
        with logger.mock_capture_writer() as capture:
            assert await PipPack(FakeMod(), site).install('Demo', '1.0')

        assert capture.fd.any_contains('Installed "Demo==1.0"')
        assert read_tree(site) == tree_of(full)
        # the ledger of the distribution is the index pack of the channel
        assert file_read_bytes(f'{site}/.pack/demo/index.pack') == index
        # the install first verifies that the channel publishes the version
        # (latest.pack), the missing ledger then falls back to a rebuild that
        # reads the index pack of the version and every missing file from
        # its full pack
        assert server.requests[0] == f'{dep_channel("demo")}/latest.pack'
        assert set(server.requests[1:]) == {f'{dep_channel("demo")}/1.0/full.pack'}
        assert len(server.requests) > 3

    @pytest.mark.trio
    async def test_upgrade(self, fs, app_folder, monkeypatch):
        """A version mismatch applies the update pack of the channel: the
        local files satisfy its sources, nothing is downloaded from the
        full pack."""
        site = site_packages(fs)
        fs.create_dir(site)
        pack1, full1, _ = build_dist('1.0')
        pack2, full2, index2 = build_dist('2.0')
        update = b''.join(PackWheelUpdate(pack2, pack1).iter_pack_data())
        with logger.mock_capture_writer():
            await UnpackJob(full1, root=site, name='demo').run()

        server = ChannelServer(dep_channel('demo'))
        server.register_version('2.0', full2, index2)
        server.register_update('1.0', '2.0', update)
        patch_server(monkeypatch, server, dep_channel('demo'), 'alas')

        with logger.mock_capture_writer():
            assert await PipPack(FakeMod(), site).install('demo', '2.0')

        assert read_tree(site) == tree_of(full2)
        assert file_read_bytes(f'{site}/.pack/demo/index.pack') == index2
        # the version check, then the update pack alone: the local files
        # satisfy its sources
        assert server.requests == [
            f'{dep_channel("demo")}/latest.pack',
            f'{dep_channel("demo")}/2.0/from_1.0.pack',
        ]

    @pytest.mark.trio
    async def test_same_version_repairs(self, fs, app_folder, monkeypatch):
        """The same version verifies the local files and repairs a damaged
        one from the full pack of the channel."""
        site = site_packages(fs)
        fs.create_dir(site)
        _, full, index = build_dist('1.0')
        with logger.mock_capture_writer():
            await UnpackJob(full, root=site, name='demo').run()
        file_write(f'{site}/demo/core.py', b'damaged\n')

        server = ChannelServer(dep_channel('demo'))
        server.register_version('1.0', full, index)
        patch_server(monkeypatch, server, dep_channel('demo'), 'alas')

        with logger.mock_capture_writer():
            assert await PipPack(FakeMod(), site).install('demo', '1.0')

        assert file_read_bytes(f'{site}/demo/core.py') == b"VERSION = '1.0'\n"
        assert read_tree(site) == tree_of(full)
        # the version check, then the damaged file alone: the index and the
        # other files passed their checks
        assert server.requests == [
            f'{dep_channel("demo")}/latest.pack',
            f'{dep_channel("demo")}/1.0/full.pack',
        ]

    @pytest.mark.trio
    async def test_version_not_published(self, fs, app_folder, monkeypatch):
        """A channel that publishes another version provides no valid update:
        the install is refused by the version check before anything runs."""
        site = site_packages(fs)
        fs.create_dir(site)
        _, full, index = build_dist('2.0')
        server = ChannelServer(dep_channel('demo'))
        server.register_version('2.0', full, index)
        patch_server(monkeypatch, server, dep_channel('demo'), 'alas')

        with logger.mock_capture_writer(), pytest.raises(ValueError, match='no valid update'):
            await PipPack(FakeMod(), site).install('demo', '1.0')

        # the refusal is the version check alone: only latest.pack was read
        # and nothing was written
        assert server.requests == [f'{dep_channel("demo")}/latest.pack']
        assert not os.path.exists(f'{site}/.pack')
        assert read_tree(site) == {}

    @pytest.mark.trio
    async def test_version_must_be_a_path_component(self):
        """The version names the pack folder of the channel and is embedded
        in the urls of the flow: a version that is not a single safe path
        component is refused before any request."""
        pip = PipPack(FakeMod(), '/env/Lib/site-packages')
        with pytest.raises(ValueError, match='the version is empty'):
            await pip.install('demo', '')
        with pytest.raises(ValueError, match='should not contain character'):
            await pip.install('demo', '../1.0')
