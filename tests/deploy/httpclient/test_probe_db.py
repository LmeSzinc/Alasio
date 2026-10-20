"""
Tests for probe_db: the selection record of the probes in gui.db.

The behavior is tested with the tables of the pack conftest: FakeMirrorTable
runs the real MirrorRecordTable SQL on the in-memory sqlite database
(BrokenMirrorTable fails every operation); one class exercises the real
MirrorRecordTable on a temporary gui.db of a real directory.
"""
import os
import shutil
from time import time

import pytest

from alasio.db.conn import SQLITE_POOL
from alasio.deploy.httpclient.probe_db import RECORD_TTL, MirrorRecord, MirrorRecordTable, load_result, save_result
from alasio.deploy.httpclient.probe_mirrors import Mirrors
from alasio.ext import env
from alasio.logger import logger
from tests.deploy_dev.pack.conftest import BrokenMirrorTable, FakeMirrorTable

MIRRORS = Mirrors({
    'cn': {'123pan': 'http://pan', 'tencent': 'http://cos'},
    'global': {'global': 'http://global'},
})


class TestRecordRead:
    """load_result(): the record, its staleness and its expiration."""

    def test_no_record(self):
        """Without a record the result is empty, the database was read."""
        table = FakeMirrorTable()
        assert load_result('repo', 'alas', MIRRORS, table=table) == ''
        assert table.selects == 1

    def test_record_round_trip(self):
        """A written result is read back by another reader."""
        table = FakeMirrorTable()
        save_result('repo', 'alas', MIRRORS, 'tencent', table=table)
        assert table.upserts == 1
        assert load_result('repo', 'alas', MIRRORS, table=table) == 'tencent'
        assert table.selects == 1

    def test_scope_isolates_the_records(self):
        """The key is (scene, name): independent sets never read the record
        of another one, even with the same mirror structure."""
        table = FakeMirrorTable()
        save_result('repo', 'alas', MIRRORS, '123pan', table=table)
        assert load_result('repo', 'alas', MIRRORS, table=table) == '123pan'
        assert load_result('repo', 'other', MIRRORS, table=table) == ''
        assert load_result('pypi', 'alas', MIRRORS, table=table) == ''

    def test_stale_digest_is_ignored(self):
        """A record of another mirror structure is ignored: the flow probes
        again and the reason is logged."""
        table = FakeMirrorTable()
        table.seed(scene='repo', name='alas', digest='stale', result='123pan')
        with logger.mock_capture_writer() as capture:
            assert load_result('repo', 'alas', MIRRORS, table=table) == ''
        assert capture.concat() == """\
INFO | Mirror record of `repo/alas` is of another mirror structure, selecting again: 123pan
"""

    def test_unknown_name_is_ignored(self):
        """A record of a mirror the set no longer has is ignored."""
        table = FakeMirrorTable()
        table.seed(scene='repo', name='alas', digest=MIRRORS.digest, result='removed')
        assert load_result('repo', 'alas', MIRRORS, table=table) == ''

    def test_expired_record_is_ignored(self):
        """A record past its expiration is treated as no record, so the next
        flow probes again and follows the network environment."""
        table = FakeMirrorTable()
        table.seed(
            scene='repo', name='alas', digest=MIRRORS.digest,
            result='123pan', expire=int(time()) - 1)
        with logger.mock_capture_writer() as capture:
            assert load_result('repo', 'alas', MIRRORS, table=table) == ''
        assert capture.concat() == """\
INFO | Mirror record of `repo/alas` is expired, selecting again: 123pan
"""

    def test_record_without_expire_is_expired(self):
        """expire=0 (a row without the column, an old development database)
        is expired by definition."""
        table = FakeMirrorTable()
        table.seed(scene='repo', name='alas', digest=MIRRORS.digest, result='123pan', expire=0)
        assert load_result('repo', 'alas', MIRRORS, table=table) == ''

    def test_record_expiring_too_far_is_ignored(self):
        """No version of this code writes a record expiring further than the
        max of RECORD_TTL in the future: a tampered record or an abnormal
        clock must not pin the machine."""
        table = FakeMirrorTable()
        expire = int(time()) + RECORD_TTL[1] + 1
        table.seed(
            scene='repo', name='alas', digest=MIRRORS.digest, result='123pan', expire=expire)
        with logger.mock_capture_writer() as capture:
            assert load_result('repo', 'alas', MIRRORS, table=table) == ''
        assert capture.concat() == f"""\
WARNING | Mirror record of `repo/alas` expires too far in the future, ignored: 123pan, expire {expire}
"""

    def test_record_at_the_ttl_boundary_is_valid(self):
        """A record expiring exactly the max of RECORD_TTL in the future is
        still a hint, only a moment further is impossible."""
        table = FakeMirrorTable()
        table.seed(
            scene='repo', name='alas', digest=MIRRORS.digest, result='tencent',
            expire=int(time()) + RECORD_TTL[1])
        assert load_result('repo', 'alas', MIRRORS, table=table) == 'tencent'

    def test_single_mirror_never_touches_the_table(self):
        """A single mirror set has no selection to remember: no read."""
        assert load_result('repo', '', Mirrors('http://only'), table=BrokenMirrorTable()) == ''

    def test_broken_table_degrades(self):
        """A broken database only warns, the selection still works."""
        with logger.mock_capture_writer() as capture:
            assert load_result('repo', 'alas', MIRRORS, table=BrokenMirrorTable()) == ''
        assert capture.concat() == """\
WARNING | Failed to read the mirror record of `repo/alas`: unable to open database file
"""


class TestRecordWrite:
    """save_result(): the expiration, the failures and the last writer."""

    def test_expiration_is_written(self):
        """save_result records a random expiration 1~1.5 days from now: the
        random moment spreads the re-probes of a client fleet."""
        table = FakeMirrorTable()
        ttl_min, ttl_max = RECORD_TTL
        start = int(time())
        save_result('repo', 'alas', MIRRORS, '123pan', table=table)
        end = int(time())
        row = table.select_one(scene='repo', name='alas')
        assert row.result == '123pan'
        assert row.digest == MIRRORS.digest
        assert row.expire - start >= ttl_min
        assert row.expire - end <= ttl_max

    def test_unknown_write_is_rejected(self):
        """Writing a name that is not in the set is a caller bug."""
        with pytest.raises(ValueError):
            save_result('repo', 'alas', MIRRORS, 'nope', table=FakeMirrorTable())

    def test_single_mirror_never_touches_the_table(self):
        """A single mirror set has no selection to remember: no write."""
        save_result('repo', '', Mirrors('http://only'), 'default', table=BrokenMirrorTable())

    def test_broken_table_degrades(self):
        """A broken database only warns, the update works without the
        record."""
        with logger.mock_capture_writer() as capture:
            save_result('repo', 'alas', MIRRORS, '123pan', table=BrokenMirrorTable())
        assert capture.concat() == """\
WARNING | Failed to record the mirror of `repo/alas`: unable to open database file
"""

    def test_last_write_wins(self):
        """The write is a single UPSERT without a lock: two writers of the
        same key end with the last winner (both were usable)."""
        table = FakeMirrorTable()
        save_result('repo', 'alas', MIRRORS, '123pan', table=table)
        save_result('repo', 'alas', MIRRORS, 'tencent', table=table)
        assert table.selects == 0
        assert load_result('repo', 'alas', MIRRORS, table=table) == 'tencent'


@pytest.fixture(scope='module')
def mirror_db_dir():
    """
    Real directory for the real table tests.

    SQLite opens its database file in the C layer, the in-memory fake
    filesystem cannot intercept it: the MirrorRecordTable tests need a
    real directory (the same reason as the table tests of tests/db).
    """
    path = env.ALASIO_TEMP.joinpath('mirror_record_table')
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path.joinpath('config'))
    yield path
    # release the pooled connection so the files can be removed on
    # Windows
    SQLITE_POOL.delete_file(path.joinpath('config/gui.db'))
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def real_table(mirror_db_dir, monkeypatch):
    """A real MirrorRecordTable on a fresh temporary gui.db."""
    from alasio.ext import env
    monkeypatch.setattr(env, 'PROJECT_ROOT', mirror_db_dir)
    # a fresh database for every test, and drop the instance of a
    # previous test (the table is a singleton)
    SQLITE_POOL.delete_file(mirror_db_dir.joinpath('config/gui.db'))
    MirrorRecordTable.singleton_clear()
    yield MirrorRecordTable()
    MirrorRecordTable.singleton_clear()


class TestMirrorRecordTable:
    """The real table on a real gui.db."""

    def test_round_trip(self, real_table):
        """The table is created on first use, a row is written and read."""
        assert real_table.select_one(scene='repo', name='') is None
        real_table.upsert_row(
            MirrorRecord(scene='repo', name='', digest='k1', result='123pan', expire=1700000000),
            conflicts=('scene', 'name'), updates=('digest', 'result', 'expire'),
        )
        row = real_table.select_one(scene='repo', name='')
        assert (row.scene, row.name, row.digest, row.result, row.expire) == (
            'repo', '', 'k1', '123pan', 1700000000)

    def test_the_record_flow_round_trip(self, real_table):
        """The real table serves the flow of the probes: a selection is
        recorded and read back, and the flow is silent as long as the
        record is usable."""
        with logger.mock_capture_writer() as capture:
            save_result('repo', 'alas', MIRRORS, '123pan', table=real_table)
            assert load_result('repo', 'alas', MIRRORS, table=real_table) == '123pan'
        # the record is a hint: a usable one is used without a log
        assert capture.concat() == ''

    def test_the_record_flow_logs_an_expired_record(self, real_table):
        """The expiration of a real record is read from its column."""
        real_table.upsert_row(
            MirrorRecord(
                scene='repo', name='alas', digest=MIRRORS.digest, result='tencent',
                expire=int(time()) - 1),
            conflicts=('scene', 'name'), updates=('digest', 'result', 'expire'),
        )
        with logger.mock_capture_writer() as capture:
            assert load_result('repo', 'alas', MIRRORS, table=real_table) == ''
        assert capture.concat() == """\
INFO | Mirror record of `repo/alas` is expired, selecting again: tencent
"""

    def test_upsert_replaces_the_key(self, real_table):
        """A second upsert of the (scene, name) key replaces the record."""
        real_table.upsert_row(
            MirrorRecord(scene='repo', name='alas', digest='k1', result='a', expire=1700000000),
            conflicts=('scene', 'name'), updates=('digest', 'result', 'expire'),
        )
        real_table.upsert_row(
            MirrorRecord(scene='repo', name='alas', digest='k2', result='b', expire=1700000001),
            conflicts=('scene', 'name'), updates=('digest', 'result', 'expire'),
        )
        row = real_table.select_one(scene='repo', name='alas')
        assert (row.digest, row.result, row.expire) == ('k2', 'b', 1700000001)

    def test_scene_and_name_are_one_key(self, real_table):
        """The rows of two names do not overwrite each other."""
        real_table.upsert_row(
            MirrorRecord(scene='repo', name='a', digest='k1', result='m', expire=1700000000),
            conflicts=('scene', 'name'), updates=('digest', 'result', 'expire'),
        )
        real_table.upsert_row(
            MirrorRecord(scene='repo', name='b', digest='k2', result='m', expire=1700000000),
            conflicts=('scene', 'name'), updates=('digest', 'result', 'expire'),
        )
        assert real_table.select_one(scene='repo', name='a').digest == 'k1'
        assert real_table.select_one(scene='repo', name='b').digest == 'k2'
