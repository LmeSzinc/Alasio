"""
Tests for ServerUrl: the mirror set of an update server and the
selected mirror recorded in gui.db.

The behavior is tested with the tables of conftest: FakeMirrorTable
runs the real PackMirrorTable SQL on the in-memory sqlite database
(BrokenMirrorTable fails every operation), both are injected with
make_server_url(); one class exercises the real PackMirrorTable on a
temporary gui.db of a real directory.
"""
import os
import shutil

import pytest

from alasio.db.conn import SQLITE_POOL
from alasio.deploy.pack.server_url import PackMirrorRow, PackMirrorTable, ServerUrl
from alasio.ext import env
from alasio.ext.env import ALASIO_ROOT
from alasio.logger import logger
from tests.deploy_dev.pack.conftest import BrokenMirrorTable, FakeMirrorTable, make_server_url


class TestMirrorSet:
    """The input model: the three forms, the structure and the single
    mirror shortcut."""

    def test_str_is_a_single_mirror(self):
        """A str input is one mirror named 'default', trailing slashes
        are stripped."""
        server_url = ServerUrl('http://only/')
        assert server_url.mirrors.groups == {'default': {'default': 'http://only'}}
        assert server_url.single == 'default'
        assert server_url.url_of('default') == 'http://only'
        assert server_url.url_of('other') == ''

    def test_flat_dict_entries_are_their_own_group(self):
        """In the flat form every key is a mirror name of a group of
        its own."""
        server_url = ServerUrl({'cn': 'http://cn', 'global': 'http://global'})
        assert server_url.mirrors.groups == {
            'cn': {'cn': 'http://cn'},
            'global': {'global': 'http://global'},
        }
        assert server_url.single == ''
        assert server_url.url_of('cn') == 'http://cn'
        assert server_url.url_of('global') == 'http://global'

    def test_full_structure_keeps_the_order(self):
        """The members of a group keep the declaration order, it is
        their priority order."""
        server_url = ServerUrl({
            'cn': {'123pan': 'http://pan', 'tencent-cos': 'http://cos'},
            'global': {'global': 'http://global'},
        })
        assert list(server_url.mirrors.groups) == ['cn', 'global']
        assert list(server_url.mirrors.groups['cn']) == ['123pan', 'tencent-cos']
        assert server_url.url_of('tencent-cos') == 'http://cos'
        assert server_url.single == ''

    def test_set_key_covers_the_structure(self):
        """The fingerprint is of the group boundaries and the names in
        their order; the urls and the group names are not part of it."""
        a = ServerUrl({'cn': {'a': 'http://1', 'b': 'http://2'}, 'global': {'g': 'http://3'}})
        b = ServerUrl({'cn': {'a': 'http://1', 'b': 'http://2'}, 'global': {'g': 'http://3'}})
        # a member reorder changes the priority order
        c = ServerUrl({'cn': {'b': 'http://2', 'a': 'http://1'}, 'global': {'g': 'http://3'}})
        # a url change keeps the identity of the mirrors
        d = ServerUrl({'cn': {'a': 'http://9', 'b': 'http://2'}, 'global': {'g': 'http://3'}})
        # a group name is a label only
        e = ServerUrl({'cn-main': {'a': 'http://1', 'b': 'http://2'}, 'global': {'g': 'http://3'}})
        # moving a member to another group changes the semantics
        f = ServerUrl({'cn': {'a': 'http://1'}, 'global': {'g': 'http://3', 'b': 'http://2'}})
        assert a.set_key == b.set_key
        assert a.set_key == d.set_key
        assert a.set_key == e.set_key
        assert a.set_key != c.set_key
        assert a.set_key != f.set_key

    def test_invalid_mirrors_are_rejected(self):
        """The input validation of populate_mirrors() applies."""
        with pytest.raises(ValueError):
            ServerUrl({})
        with pytest.raises(TypeError):
            ServerUrl(['http://a'])
        with pytest.raises(ValueError):
            ServerUrl({'cn': {'a b': 'http://a'}})
        with pytest.raises(ValueError, match='Duplicate mirror name'):
            ServerUrl({'cn': {'a': 'http://a'}, 'global': {'a': 'http://b'}})


class TestPersistence:
    """The gui.db record: read, write, staleness and failure."""

    MIRRORS = {
        'cn': {'123pan': 'http://pan', 'tencent': 'http://cos'},
        'global': {'global': 'http://global'},
    }

    def test_no_record(self):
        """Without a record the name is empty, the database was read."""
        table = FakeMirrorTable()
        server_url = make_server_url(self.MIRRORS, table=table)
        assert server_url.name == ''
        assert table.selects == 1

    def test_record_round_trip(self):
        """A written name is read back, by the same and by a new
        instance; the same instance never reads the database again."""
        table = FakeMirrorTable()
        server_url = make_server_url(self.MIRRORS, table=table)
        server_url.set_name('tencent')
        assert table.upserts == 1
        assert server_url.name == 'tencent'
        assert table.selects == 0
        other = make_server_url(self.MIRRORS, table=table)
        assert other.name == 'tencent'
        assert table.selects == 1

    def test_same_name_skips_the_write(self):
        """Writing the recorded name again makes no IO."""
        table = FakeMirrorTable()
        server_url = make_server_url(self.MIRRORS, table=table)
        server_url.set_name('123pan')
        server_url.set_name('123pan')
        assert table.upserts == 1

    def test_stale_set_key_is_ignored(self):
        """A record of another mirror structure is ignored."""
        table = FakeMirrorTable()
        table.seed(set_key='stale', name='123pan')
        server_url = make_server_url(self.MIRRORS, table=table)
        assert server_url.name == ''

    def test_unknown_name_is_ignored(self):
        """A record of a mirror the set no longer has is ignored."""
        table = FakeMirrorTable()
        server_url = make_server_url(self.MIRRORS, table=table)
        table.seed(set_key=server_url.set_key, name='removed')
        assert server_url.name == ''

    def test_scope_isolates_the_record(self):
        """Independent update servers do not overwrite each other."""
        table = FakeMirrorTable()
        a = make_server_url(self.MIRRORS, scope='a', table=table)
        b = make_server_url(self.MIRRORS, scope='b', table=table)
        a.set_name('123pan')
        assert b.name == ''
        assert a.name == '123pan'

    def test_unknown_write_is_rejected(self):
        """Writing a name that is not in the set is a caller bug."""
        server_url = make_server_url(self.MIRRORS, table=FakeMirrorTable())
        with pytest.raises(ValueError):
            server_url.set_name('nope')

    def test_single_mirror_never_touches_the_table(self):
        """A single mirror set has no selection: no read, no write."""
        server_url = make_server_url('http://only', table=BrokenMirrorTable())
        assert server_url.single == 'default'
        assert server_url.name == ''
        server_url.set_name('default')

    def test_broken_table_degrades(self):
        """A broken database only warns, the selection still works."""
        server_url = make_server_url(self.MIRRORS, table=BrokenMirrorTable())
        with logger.mock_capture_writer() as capture:
            assert server_url.name == ''
            server_url.set_name('123pan')
        assert capture.backend.any_contains('Failed to read the recorded mirror')
        assert capture.backend.any_contains('Failed to record the mirror')


@pytest.fixture(scope='module')
def mirror_db_dir():
    """
    Real directory for the real table tests.

    SQLite opens its database file in the C layer, the in-memory fake
    filesystem cannot intercept it: the PackMirrorTable tests need a
    real directory (the same reason as the table tests of tests/db).
    """
    path = ALASIO_ROOT.joinpath('temp/pack_mirror_table')
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path.joinpath('config'))
    yield path
    # release the pooled connection so the files can be removed on
    # Windows
    SQLITE_POOL.delete_file(path.joinpath('config/gui.db'))
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def real_table(mirror_db_dir, monkeypatch):
    """A real PackMirrorTable on a fresh temporary gui.db."""
    monkeypatch.setattr(env, 'PROJECT_ROOT', mirror_db_dir)
    # a fresh database for every test, and drop the instance of a
    # previous test (the table is a singleton)
    SQLITE_POOL.delete_file(mirror_db_dir.joinpath('config/gui.db'))
    PackMirrorTable.singleton_clear()
    yield PackMirrorTable()
    PackMirrorTable.singleton_clear()


class TestPackMirrorTable:
    """The real table on a real gui.db."""

    def test_round_trip(self, real_table):
        """The table is created on first use, a row is written and read."""
        assert real_table.select_one(scope='') is None
        real_table.upsert_row(
            PackMirrorRow(scope='', set_key='k1', name='123pan'),
            conflicts='scope', updates=('set_key', 'name'),
        )
        row = real_table.select_one(scope='')
        assert (row.scope, row.set_key, row.name) == ('', 'k1', '123pan')

    def test_upsert_replaces_the_scope(self, real_table):
        """A second upsert of the scope replaces the record."""
        real_table.upsert_row(
            PackMirrorRow(scope='', set_key='k1', name='a'),
            conflicts='scope', updates=('set_key', 'name'),
        )
        real_table.upsert_row(
            PackMirrorRow(scope='', set_key='k2', name='b'),
            conflicts='scope', updates=('set_key', 'name'),
        )
        row = real_table.select_one(scope='')
        assert (row.set_key, row.name) == ('k2', 'b')
