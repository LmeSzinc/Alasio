"""
Tests for the wheel update pack (PackWheelUpdate).

The scenario is two versions of the demo wheel with every kind of change:
an unchanged file, a modified file (a zstd patch from the old content), a
deleted file, an added file, a copy of an unchanged old file, and the
.dist-info folder of the version (the files of the old folder are deleted,
the ones of the new folder are added, no rename is guessed).

The pack is applied with the client jobs (UnpackJob / UpdateJob) on the
in-memory filesystem: the old version is unpacked into a named target of a
site-packages, the update pack is applied from the local files only, and
the tree, the ledger and the deleted files are compared against the new
version.
"""
import os
from hashlib import sha1

import pytest

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job import DeployJob
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy.pack.job_update import UpdateJob
from alasio.deploy_dev.pack import pack_wheel
from alasio.deploy_dev.pack._pack_cache import PackCache
from alasio.deploy_dev.pack.pack_wheel import PackWheel
from alasio.deploy_dev.pack.pack_wheel_update import PackWheelUpdate, WheelDiff
from alasio.ext.path.atomic import file_read_bytes
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack.conftest import MockServerFile
from tests.deploy_dev.simple_pip.conftest import build_wheel, site_packages

# a module big enough that a zstd patch from the old content wins the encoding
# of the modified record
CORE_V1 = b''.join(b'VALUE_%d = %d\n' % (index, index) for index in range(80))
CORE_V2 = CORE_V1 + b'VALUE_NEW = 99\n'
KEEP = b'keep me\n'


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    """
    Fresh cache of the wheel pipeline for one test, see test_pack_wheel.
    """
    monkeypatch.setattr(pack_wheel, 'WHEEL_CACHE', PackCache())


def v1_files():
    """
    Files of the old wheel: a package, a module, a member of the .data folder.

    Returns:
        dict[str, bytes]: {member of the wheel: content}
    """
    return {
        'demo/__init__.py': b'',
        'demo/core.py': CORE_V1,
        'demo/old.py': b'OLD = True\n',
        'demo/keep.txt': KEEP,
        # the folder ships no __init__.py: the pack marks it as not a package
        'demo/tools/helper.py': b'def tool():\n    return 1\n',
        'demo-1.0.data/purelib/demo/extra.py': b'EXTRA = 1\n',
    }


def v2_files():
    """
    Files of the new wheel: core.py changed, old.py gone, a new module and a
    copy of an unchanged file.

    Returns:
        dict[str, bytes]: {member of the wheel: content}
    """
    return {
        'demo/__init__.py': b'',
        'demo/core.py': CORE_V2,
        'demo/new.py': b'NEW = True\n',
        'demo/keep.txt': KEEP,
        # the same content as keep.txt: a copy of an unchanged old file
        'demo/copy.txt': KEEP,
        'demo/tools/helper.py': b'def tool():\n    return 1\n',
    }


def build_v1():
    """
    Build the old wheel on the fake filesystem.

    Returns:
        str: Path of the wheel
    """
    return build_wheel('/w/demo-1.0-py3-none-any.whl', v1_files(), name='demo', version='1.0')


def build_v2():
    """
    Build the new wheel on the fake filesystem.

    Returns:
        str: Path of the wheel
    """
    return build_wheel('/w/demo-2.0-py3-none-any.whl', v2_files(), name='demo', version='2.0')


def make_packs():
    """
    Build the two wheels and the packs of the scenario.

    Returns:
        tuple[PackWheel, PackWheel, bytes]: The pack of the old version, the
            pack of the new version, the data of the update pack
    """
    old = PackWheel(build_v1())
    new = PackWheel(build_v2())
    return old, new, b''.join(PackWheelUpdate(new, old).iter_pack_data())


class TestWheelDiff:
    """The records of the diff of the two install trees."""

    def test_records(self, fs):
        """Every kind of change produces its record."""
        old = PackWheel(build_v1())
        new = PackWheel(build_v2())
        info = WheelDiff(old, new).diff_info

        # modified: the zstd patch from the old content won, the record
        # references the old file
        assert info['demo/core.py'].edit == 1
        assert info['demo/core.py'].source_path == 'demo/core.py'
        # added
        assert info['demo/new.py'].edit == 0
        assert info['demo/new.py'].source_path == ''
        # copied from an unchanged old file
        assert info['demo/copy.txt'].edit == 0
        assert info['demo/copy.txt'].source_path == 'demo/keep.txt'
        # deleted: the module of the old version, the member installed from
        # its .data folder, and every file of the old .dist-info
        assert info['demo/old.py'].edit == 2
        assert info['demo/extra.py'].edit == 2
        assert info['demo-1.0.dist-info/METADATA'].edit == 2
        # the files of the new .dist-info are added, no rename is guessed
        assert info['demo-2.0.dist-info/METADATA'].edit == 0
        # the unchanged files are not in the diff at all
        assert 'demo/__init__.py' not in info
        assert 'demo/keep.txt' not in info

    def test_deleted_records_come_last(self, fs):
        """The changed files follow the DFS order of the new version, the
        deleted ones come last."""
        old = PackWheel(build_v1())
        new = PackWheel(build_v2())
        edits = [info.edit for info in WheelDiff(old, new).diff_info.values()]
        # every deleted record is behind every other record
        assert edits == sorted(edits, key=lambda edit: edit == 2)

    def test_refinfo(self, fs):
        """The refinfo holds the old files the records reference."""
        old = PackWheel(build_v1())
        new = PackWheel(build_v2())
        ref = WheelDiff(old, new).refinfo
        assert set(ref) == {'demo/core.py', 'demo/keep.txt'}
        assert ref['demo/core.py'].size == len(CORE_V1)
        assert ref['demo/core.py'].sha1 == old.tree['demo/core.py'].sha1
        assert ref['demo/keep.txt'].sha1 == old.tree['demo/keep.txt'].sha1

    def test_copy_chain(self, fs):
        """A copy references the nearest earlier record of the content."""
        old = PackWheel(build_wheel(
            '/w/demo-1.0-py3-none-any.whl', {'demo/__init__.py': b''}, name='demo', version='1.0'))
        payload = b'payload\n' * 20
        new = PackWheel(build_wheel('/w/demo-2.0-py3-none-any.whl', {
            'demo/__init__.py': b'',
            'demo/a.txt': payload,
            'demo/b.txt': payload,
            'demo/c.txt': payload,
        }, name='demo', version='2.0'))
        info = WheelDiff(old, new).diff_info
        assert info['demo/a.txt'].source_path == ''
        assert info['demo/b.txt'].source_path == 'demo/a.txt'
        assert info['demo/c.txt'].source_path == 'demo/b.txt'

    def test_unchanged(self, fs):
        """Two builds of the same wheel have no record at all."""
        wheel = build_v1()
        diff = WheelDiff(PackWheel(wheel), PackWheel(wheel))
        assert diff.diff_info == {}
        assert diff.refinfo == {}

    def test_arguments(self, fs):
        """Both sides must be wheels."""
        wheel = PackWheel(build_v1())
        with pytest.raises(ValueError, match='requires a PackWheel of the old version'):
            WheelDiff('old', wheel)
        with pytest.raises(ValueError, match='requires a PackWheel of the new version'):
            WheelDiff(wheel, 'new')

    def test_cache_of_the_new_version(self, fs):
        """The diff encodes through the cache of the new version."""
        old = PackWheel(build_v1())
        new = PackWheel(build_v2())
        assert WheelDiff(old, new).cache is new.cache


class TestUpdatePack:
    """The encoded update pack."""

    def test_version_part(self, fs):
        """The pack records the new version and the old one."""
        old, new, update = make_packs()
        decoder = PackDecodeBase(update)
        decoder.validate()
        assert decoder.current_version == '2.0'
        assert decoder.old_version == '1.0'
        assert decoder.current_version == new.current_version
        assert decoder.old_version == old.current_version

    def test_pack_version(self, fs):
        """The update pack is encoded with the format of the new side."""
        # an old side of an earlier format is rebuilt with the format its
        # published pack was encoded with, see PackWheelUpdate
        old = PackWheel(build_v1(), pack_version=1)
        new = PackWheel(build_v2(), pack_version=1)
        assert PackWheelUpdate(new, old).pack_version == 1
        assert PackDecodeBase(b''.join(PackWheelUpdate(new, old).iter_pack_data())).pack_version == 1

    def test_ledger_record(self, fs):
        """The ledger is a normal file of the update, verified through the refinfo."""
        old, new, update = make_packs()
        decoder = PackDecodeBase(update)
        decoder.validate()
        info = decoder.fileinfo['.pack/index.pack']
        assert info.edit == 1
        assert info.source_path == '.pack/index.pack'
        ref = decoder.refinfo['.pack/index.pack']
        assert ref.size == len(old.index_pack)
        assert ref.sha1 == sha1(old.index_pack).digest()
        # the record decodes to the ledger of the new version, the patch
        # source is the index pack of the old one
        content = PackDecodeBase.decode_content(
            info, decoder.catdata(info), source=old.index_pack)
        assert content == new.index_pack

    def test_file_records(self, fs):
        """The records of the changed files of the two versions."""
        old, new, update = make_packs()
        decoder = PackDecodeBase(update)
        decoder.validate()
        info = decoder.fileinfo

        # the modified module: an M record with a zstd patch
        assert info['demo/core.py'].edit == 1
        assert info['demo/core.py'].source_path == 'demo/core.py'
        assert info['demo/core.py'].algo == 2
        assert info['demo/core.py'].data_size < info['demo/core.py'].size
        # the added module and the new .dist-info files
        assert info['demo/new.py'].edit == 0
        assert info['demo/new.py'].source_lookback == 0
        assert info['demo-2.0.dist-info/RECORD'].edit == 0
        # the copy of the unchanged old file carries no data
        assert info['demo/copy.txt'].edit == 0
        assert info['demo/copy.txt'].source_path == 'demo/keep.txt'
        assert info['demo/copy.txt'].data_size == 0
        # the files the new version does not have are deleted, the old module
        # and the member installed from the .data folder included
        assert info['demo/old.py'].edit == 2
        assert info['demo/extra.py'].edit == 2
        assert not any(path in new.tree for path, record in info.items() if record.edit == 2)

    def test_unchanged(self, fs):
        """An update between two builds of the same wheel has no record."""
        wheel = build_v1()
        update = PackWheelUpdate(PackWheel(wheel), PackWheel(wheel))
        decoder = PackDecodeBase(b''.join(update.iter_pack_data()))
        decoder.validate()
        assert decoder.current_version == '1.0'
        assert decoder.old_version == '1.0'
        assert decoder.fileinfo == {}
        assert decoder.refinfo == {}

    def test_markers_are_not_records_of_the_update(self, fs):
        """The __init__.py markers live in the index, not in the update pack.

        A marker is a property of a version, not a change between two of
        them: the client gets it from the new index pack the update installs
        (the ledger), and the validation that follows the update enforces it,
        see TestClientRoundTrip.test_stray_init_removed.
        """
        old, new, update = make_packs()
        decoder = PackDecodeBase(update)
        decoder.validate()
        # demo/tools/ ships no __init__.py in either version
        assert 'demo/tools/__init__.py' not in decoder.fileinfo
        assert PackDecodeBase(new.index_pack).fileinfo['demo/tools/__init__.py'].edit == 2

    def test_deterministic_bytes(self, fs):
        """Two builds of the same update give the same bytes."""
        old, new, update = make_packs()
        # a cold build of the same pair: the encodings the first build left
        # on the cache must not change a byte
        cold_old = PackWheel(build_v1(), cache=PackCache())
        cold_new = PackWheel(build_v2(), cache=cold_old.cache)
        assert b''.join(PackWheelUpdate(cold_new, cold_old).iter_pack_data()) == update

    def test_arguments(self, fs):
        """Both sides must be wheels."""
        wheel = PackWheel(build_v1())
        with pytest.raises(ValueError, match='requires a PackWheel of the old version'):
            PackWheelUpdate(wheel, 'old')
        with pytest.raises(ValueError, match='requires a PackWheel of the new version'):
            PackWheelUpdate('new', wheel)


class TestClientRoundTrip:
    """The client jobs apply the update pack to the tree of the old version."""

    @pytest.mark.trio
    async def test_update(self, app_folder, fs, monkeypatch):
        """Unpack the old version, apply the update, compare with the new one."""
        old, new, update_data = make_packs()
        old_full = b''.join(old.iter_pack_data())
        new_full = b''.join(new.iter_pack_data())
        site = site_packages(fs)
        server = MockServerFile()
        server.register_version('1.0', old_full, old.index_pack)
        server.register_version('2.0', new_full, new.index_pack)
        server.register_update('1.0', '2.0', update_data)

        # the client that has been running the old version
        with logger.mock_capture_writer():
            assert await UnpackJob(old_full, root=site, name='demo').run()
        for path, file in old.tree.items():
            assert file_read_bytes(f'{site}/{path}') == file.content, path
        assert file_read_bytes(f'{site}/.pack/demo/index.pack') == old.index_pack

        # every source of the update is local: a download attempt means a
        # source was not found where the pack says it is
        async def _fail(*args, **kwargs):
            raise AssertionError('no download expected, every source is local')

        monkeypatch.setattr(server, 'get_file_content', _fail)
        monkeypatch.setattr(server, 'get_index_pack', _fail)

        with logger.mock_capture_writer():
            job = UpdateJob(update_data, server=server, root=site, name='demo')
            assert await job.run()
        assert job.error == []

        # the tree of the new version, the ledger included
        for path, file in new.tree.items():
            assert file_read_bytes(f'{site}/{path}') == file.content, path
        for path in old.tree:
            if path not in new.tree:
                assert not os.path.exists(f'{site}/{path}'), path
        assert file_read_bytes(f'{site}/.pack/demo/index.pack') == new.index_pack
        assert not os.path.exists(f'{site}/.pack/demo/workspace')
        # the folder of the old .dist-info is gone as a whole, and nothing
        # of the pack landed in the flat pack area of the site-packages
        assert not os.path.exists(f'{site}/demo-1.0.dist-info')
        assert not os.path.exists(f'{site}/.pack/index.pack')

    @pytest.mark.trio
    async def test_unpack_new(self, app_folder, fs):
        """The full pack of the new wheel unpacks to the same tree."""
        old, new, update_data = make_packs()
        new_full = b''.join(new.iter_pack_data())
        site = site_packages(fs)
        with logger.mock_capture_writer():
            assert await UnpackJob(new_full, root=site, name='demo').run()
        for path, file in new.tree.items():
            assert file_read_bytes(f'{site}/{path}') == file.content, path
        assert file_read_bytes(f'{site}/.pack/demo/index.pack') == new.index_pack

    @pytest.mark.trio
    async def test_stray_init_removed(self, app_folder, fs, monkeypatch):
        """An __init__.py that appeared in a folder of the tree is removed.

        The marker of the index (demo/tools/__init__.py) is not a record of
        any pack of the update: the validation the update flow runs against
        the new index removes the file, without a download.
        """
        old, new, update_data = make_packs()
        new_full = b''.join(new.iter_pack_data())
        site = site_packages(fs)
        server = MockServerFile()
        server.register_version('2.0', new_full, new.index_pack)

        with logger.mock_capture_writer():
            assert await UnpackJob(new_full, root=site, name='demo').run()
        # a file the tree does not have, e.g. written by a tool of the user
        stray = f'{site}/demo/tools/__init__.py'
        fs.create_file(stray, contents='raise RuntimeError\n')

        async def _fail(*args, **kwargs):
            raise AssertionError('no download expected, the marker needs no server')

        monkeypatch.setattr(server, 'get_file_content', _fail)
        monkeypatch.setattr(server, 'get_index_pack', _fail)

        with logger.mock_capture_writer():
            assert await DeployJob(root=site, name='demo', server=server).update()
        assert not os.path.exists(stray)
        assert file_read_bytes(f'{site}/demo/tools/helper.py') == \
            new.tree['demo/tools/helper.py'].content
