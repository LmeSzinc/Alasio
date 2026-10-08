"""
Tests for the wheel full pack (PackWheel).

The tree a pack of a wheel carries is the tree of an installation: the
.dist-info of the version with the RECORD written by the installer, the
INSTALLER, the .data mapping and the executable bit. The tests build
wheels on the in-memory filesystem with the helpers of the simple_pip
tests and compare the decoded pack against them; the conformance tests
install the same wheel with SimplePip, the installer that runs on a
device, and compare every file of the environment.
"""
import os
import zipfile
from hashlib import sha1

import pytest

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy.pack.server_file import LatestInfo
from alasio.deploy_dev.pack import _pack_cache, pack_wheel
from alasio.deploy_dev.pack._pack_cache import PackCache
from alasio.deploy_dev.pack.pack_wheel import PackWheel
from alasio.deploy_dev.simple_pip.simple_pip import SimplePip
from alasio.ext.path.atomic import file_read_bytes
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.simple_pip.conftest import (  # noqa: F401
    build_wheel, list_files, pyc_compile, sha256_record, site_packages
)

# the member built with the executable permission
DEMO_EXECUTABLE = 'demo/scripts/run.py'


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    """
    Fresh cache of the wheel pipeline for one test.

    The module level WHEEL_CACHE is shared by every build of the process
    (that is the point of it: the versions of a run reuse the encodings of
    the contents they share), so a test that reads its counters replaces it
    with a cache of its own, like the tests of the git pipeline swap
    PACK_CACHE.
    """
    monkeypatch.setattr(pack_wheel, 'WHEEL_CACHE', PackCache())


def demo_files(version='1.0'):
    """
    Files of the demo wheel: a package, a duplicated content, a .data member.

    Args:
        version (str): Version of the wheel, the name of its .data folder

    Returns:
        dict[str, bytes]: {member of the wheel: content}
    """
    return {
        'demo/__init__.py': b'',
        'demo/core.py': b'def add(a, b):\n    return a + b\n',
        # the same content as core.py: stored once, the other file is a
        # C (copied) record
        'demo/util.py': b'def add(a, b):\n    return a + b\n',
        'demo/data/table.txt': b'name,value\nalpha,1\nbeta,2\n',
        DEMO_EXECUTABLE: b'print("run")\n',
        # installed below site-packages with the category prefix stripped
        f'demo-{version}.data/purelib/demo/extra.py': b'EXTRA = 1\n',
    }


def build_demo(version='1.0', files=None, **kwargs):
    """
    Build the demo wheel on the fake filesystem, see build_wheel.

    Args:
        version (str): Version of the wheel. Defaults to '1.0'.
        files (dict | None): Files of the wheel. Defaults to None, the
            demo files of the version
        **kwargs: Arguments of build_wheel, e.g. executable

    Returns:
        str: Path of the wheels
    """
    return build_wheel(
        f'/w/demo-{version}-py3-none-any.whl',
        demo_files(version) if files is None else files,
        name='demo', version=version, **kwargs)


class TestInstallTree:
    """The members install by the rules of the installer, SimplePip."""

    def test_install_tree(self, fs):
        """Every member installs, .data/purelib loses its prefix, and an
        installation writes INSTALLER and a RECORD of its own."""
        pack = PackWheel(build_demo())
        assert set(pack.tree) == {
            'demo/__init__.py',
            'demo/core.py',
            'demo/util.py',
            'demo/data/table.txt',
            'demo/scripts/run.py',
            'demo/extra.py',
            'demo-1.0.dist-info/INSTALLER',
            'demo-1.0.dist-info/METADATA',
            'demo-1.0.dist-info/WHEEL',
            'demo-1.0.dist-info/top_level.txt',
            'demo-1.0.dist-info/RECORD',
        }
        assert pack.tree['demo/extra.py'].content == b'EXTRA = 1\n'
        assert pack.tree['demo-1.0.dist-info/INSTALLER'].content == b'pip\n'
        # nothing of the .data folder keeps its category prefix
        assert not any(path.startswith('demo-1.0.data/') for path in pack.tree)

    def test_tree_is_in_dfs_order(self, fs):
        """The tree follows the DFS path order of the pack records."""
        assert list(PackWheel(build_demo()).tree) == [
            'demo/__init__.py',
            'demo/core.py',
            'demo/extra.py',
            'demo/util.py',
            'demo/data/table.txt',
            'demo/scripts/run.py',
            'demo-1.0.dist-info/INSTALLER',
            'demo-1.0.dist-info/METADATA',
            'demo-1.0.dist-info/RECORD',
            'demo-1.0.dist-info/WHEEL',
            'demo-1.0.dist-info/top_level.txt',
        ]

    def test_record_of_the_installation(self, fs):
        """The RECORD lists the installed paths, not the members of the wheel."""
        pack = PackWheel(build_demo())
        # the rows in the order RecordManager sorts them (the path split on
        # '/', unlike the DFS order of the records), the checksums of an
        # installation
        rows = [
            ('demo/__init__.py', b''),
            ('demo/core.py', b'def add(a, b):\n    return a + b\n'),
            ('demo/data/table.txt', b'name,value\nalpha,1\nbeta,2\n'),
            ('demo/extra.py', b'EXTRA = 1\n'),
            ('demo/scripts/run.py', b'print("run")\n'),
            ('demo/util.py', b'def add(a, b):\n    return a + b\n'),
            ('demo-1.0.dist-info/INSTALLER', b'pip\n'),
            ('demo-1.0.dist-info/METADATA', b'Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n\n'),
            # the RECORD lists itself without a hash and a size, pip does the same
            ('demo-1.0.dist-info/RECORD', None),
            ('demo-1.0.dist-info/WHEEL',
             b'Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\n'),
            ('demo-1.0.dist-info/top_level.txt', b'demo\n'),
        ]
        expected = ''.join(
            f'{path},{sha256_record(content) if content is not None else ""},'
            f'{len(content) if content is not None else ""}\n'
            for path, content in rows
        ).encode('utf-8')
        assert pack.tree['demo-1.0.dist-info/RECORD'].content == expected

    def test_executable_member(self, fs):
        """The executable permission of a member is kept by the pack."""
        pack = PackWheel(build_demo(executable=(DEMO_EXECUTABLE,)))
        assert pack.tree[DEMO_EXECUTABLE].mode == 1
        assert pack.tree['demo/core.py'].mode == 0

    def test_identity(self, fs):
        """The version of the pack is the display version of the METADATA."""
        pack = PackWheel(build_demo(version='2.1'))
        assert pack.current_version == '2.1'
        assert pack.old_version == ''
        assert pack.dist_info == 'demo-2.1.dist-info'
        assert pack.name == 'demo'
        assert pack.pack_version == 0

    def test_name_of_the_metadata(self, fs):
        """The Name header of the METADATA is the name of the distribution."""
        files = {
            'demo/__init__.py': b'',
            'demo-1.0.dist-info/METADATA': b'Metadata-Version: 2.1\nName: My.Demo\nVersion: 1.0\n\n',
        }
        assert PackWheel(build_demo(files=files)).name == 'My.Demo'

    def test_name_falls_back_to_the_folder(self, fs):
        """A METADATA without a Name gives the name of the .dist-info folder."""
        files = {
            'demo/__init__.py': b'',
            'demo-1.0.dist-info/METADATA': b'Metadata-Version: 2.1\nVersion: 1.0\n\n',
        }
        assert PackWheel(build_demo(files=files)).name == 'demo'

    def test_file_under_data_is_not_installed(self, fs):
        """A file directly under .data is skipped, the categories are folders."""
        files = demo_files()
        files['demo-1.0.data/readme.txt'] = b'not a category\n'
        pack = PackWheel(build_demo(files=files))
        assert not any('readme' in path for path in pack.tree)


class TestInitMarkers:
    """A folder of python modules without an __init__.py is not a package."""

    def test_marker_of_a_folder_without_init(self, fs):
        """The folders a .py file is imported from are marked as not packages."""
        pack = PackWheel(build_demo())
        # the wheel ships demo/scripts/run.py but no demo/scripts/__init__.py
        info = pack.fileinfo['demo/scripts/__init__.py']
        assert info.edit == 2
        assert info.data == b''
        assert info.data_size == 0
        assert info.size == 0
        # the folder the wheel gave an __init__.py is a package, not marked
        assert 'demo/__init__.py' in pack.tree
        # a folder that holds no .py file is not claimed: no import of the
        # distribution runs through it
        assert 'demo/data/__init__.py' not in pack.fileinfo

    def test_nested_markers(self, fs):
        """Every parent folder of a module is marked, a namespace root above
        a regular package included."""
        pack = PackWheel(build_demo(files={
            'ns/sub/__init__.py': b'',
            'ns/sub/mod.py': b'x = 1\n',
        }))
        # the folder of the module ships an __init__.py: it is a package
        assert pack.fileinfo['ns/sub/__init__.py'].edit == 0
        # the folder above it does not: it is a namespace package
        assert pack.fileinfo['ns/__init__.py'].edit == 2

    def test_root_module_has_no_marker(self, fs):
        """A module at the root of the tree has no parent folder to mark."""
        pack = PackWheel(build_demo(files={'app.py': b'print(1)\n'}))
        assert '__init__.py' not in pack.fileinfo

    def test_marker_is_encoded(self, fs):
        """The marker is a record of the pack, with no data at all."""
        pack = PackWheel(build_demo())
        decoder = PackDecodeBase(b''.join(pack.iter_pack_data()))
        decoder.validate()
        info = decoder.fileinfo['demo/scripts/__init__.py']
        assert info.edit == 2
        assert info.size == 0
        assert info.sha1 == b''
        assert bytes(decoder.catfile(info)) == b''

    @pytest.mark.trio
    async def test_unpack_removes_a_stray_init(self, app_folder, fs):
        """An __init__.py that appeared in a folder of the tree is removed."""
        pack = PackWheel(build_demo())
        full = b''.join(pack.iter_pack_data())
        site = site_packages(fs)
        stray = f'{site}/demo/scripts/__init__.py'
        fs.create_file(stray, contents='raise RuntimeError\n')
        with logger.mock_capture_writer():
            assert await UnpackJob(full, root=site, name='demo').run()
        assert not os.path.exists(stray)
        assert file_read_bytes(f'{site}/demo/scripts/run.py') == \
            pack.tree[DEMO_EXECUTABLE].content


class TestInvalidWheel:
    """A wheel that is not a pack target is refused, before anything is encoded."""

    @pytest.mark.parametrize('member', [
        'demo-1.0.data/scripts/demo-tool',
        'demo-1.0.data/headers/demo.h',
        'demo-1.0.data/data/share/demo/notes.txt',
    ])
    def test_category_outside_site_packages(self, fs, member):
        """A .data category that installs outside of site-packages is refused."""
        files = {'demo/__init__.py': b'x\n', member: b'x\n'}
        with pytest.raises(ValueError, match='outside of site-packages'):
            PackWheel(build_demo(files=files))

    def test_unknown_data_category(self, fs):
        """An unknown .data category is refused like the installer refuses it."""
        files = {'demo/__init__.py': b'x\n', 'demo-1.0.data/bogus/x.txt': b'x\n'}
        with pytest.raises(ValueError, match='unsupported .data directory'):
            PackWheel(build_demo(files=files))

    @pytest.mark.parametrize('member', [
        'demo/__pycache__/core.cpython-38.pyc',
        'demo/legacy.pyc',
    ])
    def test_bytecode(self, fs, member):
        """A wheel that ships bytecode is not a pack target."""
        with pytest.raises(ValueError, match='is a bytecode file'):
            PackWheel(build_demo(files={member: b'\x00\x01'}))

    @pytest.mark.parametrize('member', [
        '../evil.py',
        'demo/../../evil.py',
        '/evil.py',
        'demo/CON.py',
    ])
    def test_unsafe_path(self, fs, member):
        """A member that would install outside of the tree is refused."""
        with pytest.raises(ValueError, match='installs to'):
            PackWheel(build_demo(files={member: b'evil\n'}))

    @pytest.mark.skipif(os.name == 'nt', reason='zipfile normalizes the separator of the platform in a member name')
    def test_backslash_path(self, fs):
        """A member path with a backslash is refused: a wheel path is posix.

        On Windows zipfile replaces the backslash of a member name with '/' ,
        a wheel of a platform with another separator can still carry it.
        """
        os.makedirs('/w', exist_ok=True)
        with zipfile.ZipFile('/w/demo-1.0-py3-none-any.whl', 'w') as zf:
            zf.writestr('demo/__init__.py', b'x\n')
            zf.writestr('demo-1.0.dist-info/METADATA',
                        b'Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n\n')
            info = zipfile.ZipInfo('demo/evil.py')
            info.filename = 'demo\\evil.py'
            zf.writestr(info, b'evil\n')
        with pytest.raises(ValueError, match='carries a backslash'):
            PackWheel('/w/demo-1.0-py3-none-any.whl')

    @pytest.mark.parametrize('member', ['.pack/index.pack', '.pack/anything'])
    def test_pack_area(self, fs, member):
        """A member in the pack area of the pack format is refused."""
        with pytest.raises(ValueError, match='pack area'):
            PackWheel(build_demo(files={'demo/__init__.py': b'x\n', member: b'x\n'}))

    def test_two_members_one_path(self, fs):
        """Two members that install to the same path are refused."""
        os.makedirs('/w', exist_ok=True)
        with zipfile.ZipFile('/w/demo-1.0-py3-none-any.whl', 'w') as zf:
            zf.writestr('demo/a.py', b'a = 1\n')
            zf.writestr('demo-1.0.data/purelib/demo/a.py', b'a = 2\n')
            zf.writestr('demo-1.0.dist-info/METADATA',
                        b'Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n\n')
        with pytest.raises(ValueError, match='two members install to the same path'):
            PackWheel('/w/demo-1.0-py3-none-any.whl')

    def test_no_dist_info(self, fs):
        """A wheel without a .dist-info is not a distribution."""
        wheel = build_wheel('/w/demo-1.0-py3-none-any.whl', {'demo/__init__.py': b''}, dist_info=False)
        with pytest.raises(ValueError, match='expected 1 .dist-info directory'):
            PackWheel(wheel)

    def test_no_version(self, fs):
        """A METADATA without a Version cannot name the version of the pack."""
        files = {
            'demo/__init__.py': b'',
            'demo-1.0.dist-info/METADATA': b'Metadata-Version: 2.1\nName: demo\n\n',
        }
        with pytest.raises(ValueError, match='no Version in the METADATA'):
            PackWheel(build_demo(files=files))

    def test_metadata_not_utf8(self, fs):
        """A METADATA that is not UTF-8 is refused."""
        files = {
            'demo/__init__.py': b'',
            'demo-1.0.dist-info/METADATA': b'Metadata-Version: 2.1\nName: demo\nVersion: \xff\xfe\n',
        }
        with pytest.raises(ValueError, match='is not UTF-8'):
            PackWheel(build_demo(files=files))


class TestPathValidateCache:
    """The cross platform path rules are validated once per path.

    The rules of validate_filepath (relative, no traversal, no name a
    platform rejects, lengths every filesystem takes) keep a pack from
    carrying a path that cannot be unpacked somewhere; the same rules are
    applied to the install paths of a wheel and to the markers it adds, and
    the verdict is shared by the two sides of the build: the materialization
    of the wheel and the encoder that walks the records at the assembly look
    up the same cache of the encoder.
    """

    @staticmethod
    def _counting_validate(monkeypatch):
        """
        Count the validate_filepath calls of the pack encoders.

        Args:
            monkeypatch (pytest.MonkeyPatch): Monkeypatch fixture

        Returns:
            list[str]: Paths validated so far
        """
        from alasio.deploy_dev.pack import _pack_cache

        checked = []
        original = _pack_cache.validate_filepath

        def counting(path):
            checked.append(path)
            return original(path)

        monkeypatch.setattr(_pack_cache, 'validate_filepath', counting)
        return checked

    def test_path_validated_once(self, fs, monkeypatch):
        """A path the process validated before costs a set lookup."""
        wheel = build_demo(files={
            'cache_case/mod.py': b'x = 1\n',
            'cache_case/deep/other.py': b'y = 2\n',
        })
        checked = self._counting_validate(monkeypatch)
        b''.join(PackWheel(wheel).iter_pack_data())
        # the materialization validates the install paths and the markers
        assert checked.count('cache_case/mod.py') == 1
        assert checked.count('cache_case/__init__.py') == 1
        assert checked.count('cache_case/deep/__init__.py') == 1
        assert 'cache_case/deep/other.py' in checked

        checked.clear()
        b''.join(PackWheel(wheel).iter_pack_data())
        # every path of the wheel passed validation before: the
        # materialization and the encoder at the assembly look them up
        assert checked == []

    def test_the_encoder_reuses_the_validation(self, fs, monkeypatch):
        """The members of the wheel are not validated again at the assembly."""
        pack = PackWheel(build_demo(files={'cache_case_2/mod.py': b'x = 1\n'}))
        # the records are built (the install paths and the markers validated)
        _ = pack.fileinfo
        checked = self._counting_validate(monkeypatch)
        b''.join(pack.iter_pack_data())
        # the files the installation generates are not members of the archive:
        # they pass the gate for the first time here, every other path of the
        # tree is a lookup
        assert checked == ['demo-1.0.dist-info/INSTALLER', 'demo-1.0.dist-info/RECORD']

    def test_invalid_path_still_rejected(self, fs, monkeypatch):
        """A path that failed is not cached, it keeps failing."""
        self._counting_validate(monkeypatch)
        wheel = build_demo(files={'cache_case_3/CON.py': b'x = 1\n'})
        for _ in range(2):
            with pytest.raises(ValueError, match='reserved system name'):
                PackWheel(wheel)


class TestFullPack:
    """The encoded full pack: records, ledger, determinism."""

    def test_decode_round_trip(self, fs):
        """Every record decodes to the content of the install tree."""
        pack = PackWheel(build_demo(executable=(DEMO_EXECUTABLE,)))
        decoder = PackDecodeBase(b''.join(pack.iter_pack_data()))
        decoder.validate()
        assert decoder.current_version == '1.0'
        assert decoder.old_version == ''
        assert set(decoder.fileinfo) == set(pack.fileinfo)
        for path, info in decoder.fileinfo.items():
            if info.edit == 2:
                # a deleted marker: the file is not in the tree at all
                assert path not in pack.tree
                assert info.size == 0
                assert info.sha1 == b''
                assert bytes(decoder.catfile(info)) == b''
                continue
            file = pack.tree[path]
            # the footprint is binary: no line ending rule rewrites a file
            assert info.eol == 2
            assert info.mode == file.mode
            assert info.size == len(file.content)
            # an empty content is recorded without a sha1, like the empty
            # files of a git pack (FileInfo.sha1 is empty then)
            assert info.sha1 == (sha1(file.content).digest() if file.content else b'')
            assert bytes(decoder.catfile(info)) == file.content

    def test_copied_record(self, fs):
        """A content that appears twice is stored once and copied."""
        pack = PackWheel(build_demo())
        info = pack.fileinfo['demo/util.py']
        assert info.edit == 0
        # demo/core.py is the nearest earlier record of the content in the DFS
        # order (demo/__init__.py, demo/core.py, demo/extra.py, demo/util.py)
        assert info.source_lookback == 2
        assert info.data == b''
        assert info.data_size == 0
        # the record carries the info of its source, a decoder restores it
        assert info.size == pack.fileinfo['demo/core.py'].size
        assert info.sha1 == pack.fileinfo['demo/core.py'].sha1

        decoder = PackDecodeBase(b''.join(pack.iter_pack_data()))
        util = decoder.fileinfo['demo/util.py']
        assert util.source_path == 'demo/core.py'
        assert bytes(decoder.catfile(util)) == pack.tree['demo/util.py'].content

    def test_compressed_record(self, fs):
        """A compressible content is stored compressed."""
        content = b'line of the table\n' * 5000
        pack = PackWheel(build_demo(files={'demo/__init__.py': b'', 'demo/data.txt': content}))
        info = pack.fileinfo['demo/data.txt']
        assert info.algo == 1
        assert info.data_size < info.size

        decoder = PackDecodeBase(b''.join(pack.iter_pack_data()))
        assert bytes(decoder.catfile(decoder.fileinfo['demo/data.txt'])) == content

    def test_index_pack_is_the_ledger(self, fs):
        """index_pack is the prefix of the full pack, the payload of latest.pack."""
        pack = PackWheel(build_demo())
        data = b''.join(pack.iter_pack_data())
        index = pack.index_pack
        assert data.startswith(index)
        decoder = PackDecodeBase(index)
        decoder.validate_index()
        assert decoder.current_version == '1.0'
        assert decoder.old_version == ''
        assert set(decoder.fileinfo) == set(pack.fileinfo)

        info = LatestInfo.parse(pack.latest_pack())
        assert info.version == '1.0'
        assert info.checksum == PackDecodeBase(data).index_checksum

    def test_deterministic_bytes(self, fs):
        """Two builds of the same wheel give the same pack bytes."""
        wheel = build_demo()
        first = b''.join(PackWheel(wheel).iter_pack_data())
        second = b''.join(PackWheel(wheel).iter_pack_data())
        assert first == second


class TestWheelCache:
    """The wheel pipeline encodes through a cache of its own, WHEEL_CACHE."""

    def test_own_cache_instance(self, fs):
        """The build reads and fills WHEEL_CACHE by default."""
        pack = PackWheel(build_demo())
        assert pack.cache is pack_wheel.WHEEL_CACHE
        b''.join(pack.iter_pack_data())
        assert pack.cache.stat['content'][1] > 0

    def test_injected_cache(self, fs):
        """A caller can pass a cache of its own, e.g. one per run."""
        own = PackCache()
        pack = PackWheel(build_demo(), cache=own)
        assert pack.cache is own
        b''.join(pack.iter_pack_data())
        assert own.stat['content'][1] > 0
        assert pack_wheel.WHEEL_CACHE.stat['content'] == [0, 0]

    def test_the_git_cache_is_not_touched(self, fs, monkeypatch):
        """The wheel pipeline never reads nor fills the cache of the git pipeline."""
        git_cache = PackCache()
        monkeypatch.setattr(_pack_cache, 'PACK_CACHE', git_cache)
        b''.join(PackWheel(build_demo()).iter_pack_data())
        assert git_cache.stat == {
            'content': [0, 0], 'patch': [0, 0], 'extra': [0, 0],
            'rename': [0, 0], 'eol': [0, 0],
        }

    def test_content_is_encoded_once(self, fs):
        """A content the cache already holds is not compressed again."""
        wheel = build_demo()
        first = PackWheel(wheel)
        b''.join(first.iter_pack_data())
        cache = first.cache
        misses = cache.stat['content'][1]
        assert misses > 0

        second = PackWheel(wheel)
        second_data = b''.join(second.iter_pack_data())
        # the second build found every content in the cache: no new entry was
        # computed and the pack bytes are the ones of the first build
        assert cache.stat['content'][1] == misses
        assert cache.stat['content'][0] > 0

    def test_cached_encoding_does_not_change_the_bytes(self, fs):
        """A warm cache gives the pack bytes of a cold build."""
        wheel = build_demo()
        cold = b''.join(PackWheel(wheel, cache=PackCache()).iter_pack_data())
        b''.join(PackWheel(wheel).iter_pack_data())
        warm = b''.join(PackWheel(wheel).iter_pack_data())
        assert cold == warm


class TestPipConformance:
    """The tree of a pack is the tree of an installation of the same wheel."""

    @staticmethod
    def read_tree(root):
        """
        Read the files of a directory into {relative path: content}.

        Args:
            root (str): Directory to read

        Returns:
            dict[str, bytes]: {relative posix path: content}
        """
        out = {}
        for path in list_files(root):
            with open(f'{root}/{path}', 'rb') as f:
                out[path] = f.read()
        return out

    def test_tree_matches_simple_pip(self, fs, pyc_compile):
        """Every file of an installation is in the pack, the pyc excluded."""
        wheel = build_demo(executable=(DEMO_EXECUTABLE,))
        pack = PackWheel(wheel)
        site = site_packages(fs)
        SimplePip(site).install(wheel)

        installed = self.read_tree(site)
        record = 'demo-1.0.dist-info/RECORD'
        assert {path for path in installed if not path.endswith('.pyc')} == set(pack.tree)
        for path, content in installed.items():
            # the pyc of an installation is a product of the install
            # environment, a pack never carries it (see the module docstring)
            if path.endswith('.pyc') or path == record:
                continue
            assert content == pack.tree[path].content, path

        # the RECORD of an installation lists the pyc it compiled at install
        # time, the RECORD of the pack does not carry bytecode: the other rows
        # are the same, see test_record_matches_simple_pip for the bytes
        installed_rows = installed[record].decode('utf-8').splitlines()
        pyc_rows = [row for row in installed_rows if row.partition(',')[0].endswith('.pyc')]
        assert len(pyc_rows) == 5, 'one pyc row for each .py file of the wheel'
        assert [row for row in installed_rows if row not in pyc_rows] == \
            pack.tree[record].content.decode('utf-8').splitlines()

    def test_record_matches_simple_pip(self, fs):
        """The RECORD of the pack is the RECORD of an installation, byte for byte.

        The wheel carries no .py file: an installation compiles nothing, so
        the RECORD of SimplePip has no pyc row to differ with.
        """
        files = {
            'demo/assets/style.css': b'.btn { color: red; }\n',
            'demo/data/table.json': b'{"a": 1}\n',
            'demo-1.0.data/purelib/demo/extra.bin': bytes(range(64)),
        }
        wheel = build_demo(files=files)
        pack = PackWheel(wheel)
        site = site_packages(fs)
        SimplePip(site).install(wheel)

        installed = self.read_tree(site)
        assert set(installed) == set(pack.tree)
        record = 'demo-1.0.dist-info/RECORD'
        assert installed[record] == pack.tree[record].content
