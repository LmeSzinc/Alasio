"""
Tests for alasio.ext.file.watcher.

The watcher is synchronous, so these tests do not need trio. All filesystem
operations go through the in-memory filesystem fixture.
"""
import inspect
import os
import stat
import time
from typing import Literal, get_type_hints

import pytest
from msgspec import Struct

import alasio.ext.file.watcher as watcher_module
from alasio.ext.file.watcher import (
    _SCAN_INCOMPLETE, _SCAN_MISSING, _SCAN_NOT_DIRECTORY, _SCAN_OK, PathWatcher, WatchEvent, WatchRecord, _covers,
    _list_dir, _normalize_path, _preserve, _Watch
)
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from alasio.testing.filesystem.entry import FakeDirEntry


def event_shape(events):
    """
    Reduce events to their stable, filesystem-independent fields.

    Args:
        events (list[WatchEvent]): Events to reduce

    Returns:
        list[tuple]: (event, path, type) of every event
    """
    return [(event.event, event.path, event.type) for event in events]


def record_shape(records):
    """
    Reduce records to their stable, filesystem-independent fields.

    Args:
        records (list[WatchRecord]): Records to reduce

    Returns:
        list[tuple]: (path, type) of every record
    """
    return [(record.path, record.type) for record in records]


class TestContract:
    def test_public_types_are_literals(self):
        event_hints = get_type_hints(WatchEvent)
        record_hints = get_type_hints(WatchRecord)
        assert event_hints['event'] == Literal['A', 'M', 'D']
        assert event_hints['type'] == Literal['', 'F', 'D', 'S', 'O']
        assert record_hints['type'] == Literal['', 'F', 'D', 'S', 'O']

    def test_structs_are_frozen(self):
        record = WatchRecord('/a', 'F', 1, 2)
        event = WatchEvent('A', '/a', 'F', 1, 2)
        assert isinstance(record, Struct)
        assert isinstance(event, Struct)
        with pytest.raises(AttributeError):
            record.path = '/b'
        with pytest.raises(AttributeError):
            event.event = 'M'

    def test_watch_event_from_record(self):
        record = WatchRecord('/a', 'F', 1, 2, 3)
        event = WatchEvent.from_record('M', record)
        assert (event.event, event.path, event.type) == ('M', '/a', 'F')
        assert (event.mtime_ns, event.size) == (1, 2)
        event = WatchEvent.from_record('D', record, '/b')
        assert event.path == '/b'

    def test_subscribe_and_unsubscribe_do_not_touch_the_filesystem(
            self, monkeypatch):
        def fail(*args, **kwargs):
            raise AssertionError('subscription must not scan the filesystem')

        monkeypatch.setattr(os, 'stat', fail)
        monkeypatch.setattr(os, 'scandir', fail)
        watcher = PathWatcher()
        watcher.subscribe('/watch', recursive=True)
        watcher.subscribe('/watch')
        watcher.unsubscribe('/watch')
        watcher.unsubscribe('/missing')

    def test_relative_paths_are_rejected(self):
        watcher = PathWatcher()
        with pytest.raises(ValueError):
            watcher.subscribe('relative')
        with pytest.raises(ValueError):
            watcher.unsubscribe('relative')
        with pytest.raises(ValueError):
            watcher.mark_modified('relative')

    def test_mark_modified_is_memory_only(self, monkeypatch):
        def fail(*args, **kwargs):
            raise AssertionError('mark_modified must not do filesystem IO')

        monkeypatch.setattr(os, 'stat', fail)
        monkeypatch.setattr(os, 'scandir', fail)
        watcher = PathWatcher()
        watcher.mark_modified('/watch/a.txt')

    def test_scanning_has_no_cache_layer(self):
        assert not hasattr(watcher_module, '_ScanCache')
        assert not hasattr(watcher_module, '_ScanSession')
        assert not hasattr(watcher_module, '_TreeScan')
        assert inspect.isgeneratorfunction(watcher_module._change_events)
        assert inspect.isgeneratorfunction(PathWatcher._update_watch)
        assert not hasattr(PathWatcher, '_scan')
        watch = _Watch('/a', False)
        assert not hasattr(watch, 'retry')
        assert not hasattr(watch, 'snapshot')
        assert not hasattr(watch, 'seen')
        assert watch.initialized is False

    def test_list_dir_returns_struct(self, fs):
        fs.create_file('/watch/a.txt')
        assert isinstance(_list_dir('/watch'), Struct)

    def test_records_are_stored_without_copying(self, fs):
        fs.create_file('/watch/a.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch', recursive=True)
        watcher.update()

        first = {record.path: record for record in watcher.list()}
        second = {record.path: record for record in watcher.list()}
        assert first['/watch/a.txt'] is second['/watch/a.txt']


class TestDriveRoot:
    def test_drive_root_covers_its_children(self):
        assert _covers(_Watch('E:/', False), 'E:/a.txt')
        assert not _covers(_Watch('E:/', False), 'E:/sub/a.txt')
        assert _covers(_Watch('E:/', True), 'E:/sub/a.txt')

    def test_preserve_keeps_the_drive_root_subtree(self):
        old = {
            'E:/a.txt': WatchRecord('E:/a.txt', 'F', 1, 2),
            'E:/sub/b.txt': WatchRecord('E:/sub/b.txt', 'F', 3, 4),
            'F:/other.txt': WatchRecord('F:/other.txt', 'F', 5, 6),
        }
        new = {}
        _preserve(old, new, 'E:/')
        assert new == {
            'E:/a.txt': old['E:/a.txt'],
            'E:/sub/b.txt': old['E:/sub/b.txt'],
        }

    @pytest.mark.skipif(os.name != 'nt', reason='Windows drive roots only')
    def test_drive_root_stays_absolute(self):
        assert _normalize_path('E:\\') == 'E:/'
        assert _normalize_path('E:/') == 'E:/'

    @pytest.mark.skipif(os.name != 'nt', reason='Windows drive roots only')
    def test_watching_a_drive_root_reads_its_children(self, fs):
        root = fs.root_dir.path
        fs.create_file(f'{root}/a.txt')
        watcher = PathWatcher()
        watcher.subscribe(root + '\\')
        assert event_shape(watcher.update()) == [('A', root + '/', 'D')]
        fs.create_file(f'{root}/b.txt')
        assert event_shape(watcher.update()) == [('A', f'{root}/b.txt', 'F')]


class TestCovers:
    def test_recursive_needs_a_separator_boundary(self):
        watch = _Watch('/watch', True)
        assert _covers(watch, '/watch')
        assert _covers(watch, '/watch/a.txt')
        assert _covers(watch, '/watch/sub/a.txt')
        assert not _covers(watch, '/watcher/a.txt')

    def test_non_recursive_covers_direct_children_only(self):
        watch = _Watch('/watch', False)
        assert _covers(watch, '/watch')
        assert _covers(watch, '/watch/a.txt')
        assert not _covers(watch, '/watch/sub/a.txt')
        assert not _covers(watch, '/watcher/a.txt')


class TestDirectoryListing:
    def test_list_dir_is_type_only(self, fs, monkeypatch):
        fs.create_file('/watch/a/b/f.txt')
        fs.create_file('/watch/a/g.txt', contents='abc')

        def fail(*args, **kwargs):
            raise AssertionError('listing must not stat entries')

        monkeypatch.setattr(os, 'stat', fail)
        monkeypatch.setattr(os, 'lstat', fail)
        monkeypatch.setattr(FakeDirEntry, 'stat', fail)
        scan = _list_dir('/watch/a')
        assert scan.status == _SCAN_OK
        assert {(record.path, record.type) for record in scan.records} == {
            ('/watch/a/b', 'D'),
            ('/watch/a/g.txt', 'F'),
        }
        records = {record.path: record for record in scan.records}
        assert records['/watch/a/g.txt'].mtime_ns == 0
        assert list(scan.entries) == ['/watch/a/g.txt']

    def test_list_dir_statuses(self, fs, monkeypatch):
        fs.create_dir('/watch/empty')
        scan = _list_dir('/watch/empty')
        assert scan.status == _SCAN_OK
        assert scan.records == []

        scan = _list_dir('/watch/missing')
        assert scan.status == _SCAN_MISSING
        assert scan.records == []

        fs.create_file('/watch/file.txt')
        scan = _list_dir('/watch/file.txt')
        assert scan.status == _SCAN_NOT_DIRECTORY
        assert scan.records == []

        fs.create_dir('/watch/denied')

        def fail_scandir(path):
            raise PermissionError('denied')

        monkeypatch.setattr(os, 'scandir', fail_scandir)
        scan = _list_dir('/watch/denied')
        assert scan.status == _SCAN_INCOMPLETE
        assert isinstance(scan.error, PermissionError)


class TestSpecialEntry:
    @pytest.mark.parametrize('mode', [
        stat.S_IFIFO | 0o644,
        stat.S_IFSOCK | 0o644,
        stat.S_IFCHR | 0o644,
        stat.S_IFBLK | 0o644,
    ])
    def test_from_stat_other_mode(self, mode):
        st = os.stat_result((mode, 1, 0, 1, 0, 0, 0, 0, 0, 0))
        record = WatchRecord.from_stat('/watch/special', st)
        assert record.type == 'O'
        assert record.size == 0

    def test_list_dir_marks_special_entry(self, fs, monkeypatch):
        fs.create_file('/watch/special')
        fs.create_file('/watch/normal.txt')
        real_is_file = FakeDirEntry.is_file

        def is_file(self, follow_symlinks=True):
            if self.name == 'special':
                return False
            return real_is_file(self, follow_symlinks=follow_symlinks)

        monkeypatch.setattr(FakeDirEntry, 'is_file', is_file)
        scan = _list_dir('/watch')
        assert {(record.path, record.type) for record in scan.records} == {
            ('/watch/special', 'O'),
            ('/watch/normal.txt', 'F'),
        }
        assert list(scan.entries) == ['/watch/normal.txt']

    def test_special_entry_is_not_a_file(self, fs, monkeypatch):
        fs.create_file('/watch/special', contents='a')
        real_is_file = FakeDirEntry.is_file

        def is_file(self, follow_symlinks=True):
            if self.name == 'special':
                return False
            return real_is_file(self, follow_symlinks=follow_symlinks)

        monkeypatch.setattr(FakeDirEntry, 'is_file', is_file)
        watcher = PathWatcher()
        watcher.subscribe('/watch')
        watcher.update()
        assert record_shape(watcher.list()) == [
            ('/watch', 'D'),
            ('/watch/special', 'O'),
        ]

        # A special entry has no file signature to compare.
        with open('/watch/special', 'w') as file:
            file.write('changed')
        assert watcher.update() == []

        monkeypatch.setattr(FakeDirEntry, 'is_file', real_is_file)
        assert event_shape(watcher.update()) == [
            ('D', '/watch/special', 'O'),
            ('A', '/watch/special', 'F'),
        ]

    def test_special_root_is_a_single_entry(self, fs, monkeypatch):
        fs.create_file('/watch/special')
        real_lstat = os.lstat
        special = os.stat_result((stat.S_IFIFO | 0o644, 1, 0, 1, 0, 0, 0, 0, 0, 0))

        def lstat(path):
            if path == '/watch/special':
                return special
            return real_lstat(path)

        monkeypatch.setattr(os, 'lstat', lstat)
        watcher = PathWatcher()
        watcher.subscribe('/watch/special')
        assert event_shape(watcher.update()) == [
            ('A', '/watch/special', 'O'),
        ]
        assert record_shape(watcher.list()) == [
            ('/watch/special', 'O'),
        ]


class TestBaseline:
    def test_first_update_returns_root_only_and_list_exposes_entries(
            self, fs):
        fs.create_file('/watch/a.txt', contents='a')
        fs.create_file('/watch/sub/b.txt', contents='bb')
        watcher = PathWatcher()
        watcher.subscribe('/watch', recursive=True)

        assert event_shape(watcher.update()) == [
            ('A', '/watch', 'D'),
        ]

        records = watcher.list()
        assert record_shape(records) == [
            ('/watch', 'D'),
            ('/watch/a.txt', 'F'),
            ('/watch/sub', 'D'),
            ('/watch/sub/b.txt', 'F'),
        ]
        by_path = {record.path: record for record in records}
        assert by_path['/watch'].mtime_ns == 0
        assert by_path['/watch/a.txt'].mtime_ns > 0
        assert by_path['/watch/a.txt'].size == 1
        assert by_path['/watch/sub'].mtime_ns == 0
        assert by_path['/watch/sub/b.txt'].size == 2

    def test_first_update_of_missing_root(self, fs):
        watcher = PathWatcher()
        watcher.subscribe('/missing')
        assert event_shape(watcher.update()) == [
            ('D', '/missing', ''),
        ]
        assert watcher.list() == []
        assert watcher.update() == []

    def test_missing_to_exists_and_back(self, fs):
        watcher = PathWatcher()
        watcher.subscribe('/missing', recursive=True)
        assert event_shape(watcher.update()) == [
            ('D', '/missing', ''),
        ]

        fs.create_file('/missing/a.txt', contents='a')
        assert event_shape(watcher.update()) == [
            ('A', '/missing', 'D'),
            ('A', '/missing/a.txt', 'F'),
        ]
        assert record_shape(watcher.list()) == [
            ('/missing', 'D'),
            ('/missing/a.txt', 'F'),
        ]

        fs.rmtree('/missing')
        assert event_shape(watcher.update()) == [
            ('D', '/missing', 'D'),
            ('D', '/missing/a.txt', 'F'),
        ]
        assert watcher.list() == []

    def test_recursive_mode_change_starts_a_new_baseline(self, fs):
        fs.create_file('/watch/sub/b.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch')
        watcher.update()

        watcher.subscribe('/watch', recursive=True)
        assert event_shape(watcher.update()) == [
            ('A', '/watch', 'D'),
        ]
        assert ('/watch/sub/b.txt', 'F') in record_shape(watcher.list())

    def test_dropping_recursion_drops_tracked_descendants(self, fs):
        fs.create_file('/watch/sub/b.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch', recursive=True)
        watcher.update()

        watcher.subscribe('/watch')
        assert event_shape(watcher.update()) == [
            ('A', '/watch', 'D'),
        ]
        assert record_shape(watcher.list()) == [
            ('/watch', 'D'),
            ('/watch/sub', 'D'),
        ]


class TestNonRecursive:
    def test_direct_child_file_signature_is_stored(self, fs):
        fs.create_file('/watch/a.txt', contents='abc')
        fs.create_dir('/watch/sub')
        watcher = PathWatcher()
        watcher.subscribe('/watch')
        watcher.update()

        records = {record.path: record for record in watcher.list()}
        assert records['/watch/a.txt'].type == 'F'
        assert records['/watch/a.txt'].mtime_ns > 0
        assert records['/watch/a.txt'].size == 3
        assert records['/watch/sub'].mtime_ns == 0

    def test_direct_child_modification_is_reported(self, fs):
        fs.create_file('/watch/a.txt', contents='a')
        watcher = PathWatcher()
        watcher.subscribe('/watch')
        watcher.update()

        with open('/watch/a.txt', 'w') as file:
            file.write('b')
        fs.utime('/watch/a.txt', ns=(0, 1000000000))
        assert event_shape(watcher.update()) == [
            ('M', '/watch/a.txt', 'F'),
        ]

    def test_stats_only_file_children(self, fs, monkeypatch):
        fs.create_file('/watch/a.txt')
        fs.create_dir('/watch/sub')
        fs.create_symlink('/watch/link', '/watch/a.txt')

        watcher = PathWatcher()
        watcher.subscribe('/watch')
        real_stat = FakeDirEntry.stat
        stats = []

        def counting_stat(self, follow_symlinks=True):
            stats.append(self.name)
            return real_stat(self, follow_symlinks=follow_symlinks)

        monkeypatch.setattr(FakeDirEntry, 'stat', counting_stat)
        watcher.update()
        assert stats == ['a.txt']

    def test_direct_children_only(self, fs):
        fs.create_file('/watch/a.txt', contents='a')
        fs.create_file('/watch/sub/b.txt', contents='b')
        watcher = PathWatcher()
        watcher.subscribe('/watch')
        assert event_shape(watcher.update()) == [
            ('A', '/watch', 'D'),
        ]
        assert record_shape(watcher.list()) == [
            ('/watch', 'D'),
            ('/watch/a.txt', 'F'),
            ('/watch/sub', 'D'),
        ]

        with open('/watch/sub/b.txt', 'w') as file:
            file.write('changed')
        assert watcher.update() == []

        fs.create_file('/watch/c.txt')
        assert event_shape(watcher.update()) == [
            ('A', '/watch/c.txt', 'F'),
        ]
        fs.remove('/watch/c.txt')
        assert event_shape(watcher.update()) == [
            ('D', '/watch/c.txt', 'F'),
        ]

        fs.remove('/watch/a.txt')
        fs.create_dir('/watch/a.txt')
        assert event_shape(watcher.update()) == [
            ('D', '/watch/a.txt', 'F'),
            ('A', '/watch/a.txt', 'D'),
        ]

    def test_every_update_lists_the_directory(self, fs, monkeypatch):
        fs.create_dir('/watch')
        watcher = PathWatcher()
        watcher.subscribe('/watch')
        watcher.update()

        real_scandir = os.scandir
        scans = []

        def counting_scandir(path):
            scans.append(path)
            return real_scandir(path)

        monkeypatch.setattr(os, 'scandir', counting_scandir)
        assert watcher.update() == []
        assert scans == ['/watch']

        fs.create_file('/watch/a.txt')
        assert event_shape(watcher.update()) == [
            ('A', '/watch/a.txt', 'F'),
        ]
        assert scans == ['/watch', '/watch']

    def test_directory_replacement_is_visible(self, fs):
        fs.create_dir('/watch')
        watcher = PathWatcher()
        watcher.subscribe('/watch')
        watcher.update()

        fs.rmtree('/watch')
        fs.create_dir('/watch')
        fs.create_file('/watch/new.txt')
        assert event_shape(watcher.update()) == [
            ('A', '/watch/new.txt', 'F'),
        ]


class TestRecursive:
    def test_file_and_subtree_changes(self, fs):
        fs.create_file('/watch/a.txt', contents='a')
        fs.create_file('/watch/sub/b.txt', contents='b')
        fs.create_file('/watch/sub/deep/c.txt', contents='c')
        watcher = PathWatcher()
        watcher.subscribe('/watch', recursive=True)
        watcher.update()

        fs.utime('/watch/a.txt', ns=(0, 1000000000))
        assert event_shape(watcher.update()) == [
            ('M', '/watch/a.txt', 'F'),
        ]

        fs.create_file('/watch/new.txt')
        assert event_shape(watcher.update()) == [
            ('A', '/watch/new.txt', 'F'),
        ]

        fs.rmtree('/watch/sub')
        assert event_shape(watcher.update()) == [
            ('D', '/watch/sub', 'D'),
            ('D', '/watch/sub/b.txt', 'F'),
            ('D', '/watch/sub/deep', 'D'),
            ('D', '/watch/sub/deep/c.txt', 'F'),
        ]

        fs.create_file('/watch/newdir/x.txt')
        assert event_shape(watcher.update()) == [
            ('A', '/watch/newdir', 'D'),
            ('A', '/watch/newdir/x.txt', 'F'),
        ]

    def test_symlink_is_not_followed(self, fs):
        fs.create_file('/watch/a.txt')
        fs.create_file('/outside/target/inside.txt')
        fs.create_symlink('/watch/link', '/outside/target')
        watcher = PathWatcher()
        watcher.subscribe('/watch', recursive=True)
        watcher.update()

        fs.create_file('/outside/target/new.txt')
        assert watcher.update() == []

        assert record_shape(watcher.list()) == [
            ('/watch', 'D'),
            ('/watch/a.txt', 'F'),
            ('/watch/link', 'S'),
        ]

    def test_root_deletion_reports_every_entry(self, fs):
        fs.create_file('/watch/a.txt')
        fs.create_file('/watch/sub/b.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch', recursive=True)
        watcher.update()

        fs.rmtree('/watch')
        events = watcher.update()
        assert event_shape(events) == [
            ('D', '/watch', 'D'),
            ('D', '/watch/a.txt', 'F'),
            ('D', '/watch/sub', 'D'),
            ('D', '/watch/sub/b.txt', 'F'),
        ]

    def test_recursive_scan_stats_only_files(self, fs, monkeypatch):
        fs.create_file('/watch/a/b/f.txt', contents='abc')
        fs.create_file('/watch/a/g.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch/a', recursive=True)
        real_stat = FakeDirEntry.stat
        stats = []

        def counting_stat(self, follow_symlinks=True):
            stats.append(self.name)
            return real_stat(self, follow_symlinks=follow_symlinks)

        monkeypatch.setattr(FakeDirEntry, 'stat', counting_stat)
        watcher.update()
        assert set(stats) == {'f.txt', 'g.txt'}


class TestFileRoot:
    def test_modify_delete_and_recreate(self, fs):
        fs.create_file('/watch/file.txt', contents='a')
        watcher = PathWatcher()
        watcher.subscribe('/watch/file.txt')
        assert event_shape(watcher.update()) == [
            ('A', '/watch/file.txt', 'F'),
        ]

        fs.utime('/watch/file.txt', ns=(0, 1000000000))
        assert event_shape(watcher.update()) == [
            ('M', '/watch/file.txt', 'F'),
        ]

        fs.remove('/watch/file.txt')
        assert event_shape(watcher.update()) == [
            ('D', '/watch/file.txt', 'F'),
        ]

        fs.create_file('/watch/file.txt', contents='b')
        assert event_shape(watcher.update()) == [
            ('A', '/watch/file.txt', 'F'),
        ]

    def test_existing_file_root_only_stats(self, fs, monkeypatch):
        fs.create_file('/watch/file.txt', contents='a')
        watcher = PathWatcher()
        watcher.subscribe('/watch/file.txt')
        watcher.update()

        real_scandir = os.scandir
        scans = []

        def counting_scandir(path):
            scans.append(path)
            return real_scandir(path)

        monkeypatch.setattr(os, 'scandir', counting_scandir)
        assert watcher.update() == []
        assert scans == []


class TestFileRootFirstObservation:
    def test_file_root_skips_first_scandir(self, fs, monkeypatch):
        fs.create_file('/watch/file.txt', contents='a')
        watcher = PathWatcher()
        watcher.subscribe('/watch/file.txt')

        real_scandir = os.scandir
        scans = []

        def counting_scandir(path):
            scans.append(path)
            return real_scandir(path)

        monkeypatch.setattr(os, 'scandir', counting_scandir)
        assert event_shape(watcher.update()) == [
            ('A', '/watch/file.txt', 'F'),
        ]
        assert scans == []

    def test_directory_root_is_scanned(self, fs):
        fs.create_file('/watch/sub/a.txt')
        fs.create_file('/watch/sub/deep/b.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch/sub')
        assert event_shape(watcher.update()) == [
            ('A', '/watch/sub', 'D'),
        ]
        records = record_shape(watcher.list())
        assert ('/watch/sub/a.txt', 'F') in records
        assert ('/watch/sub/deep/b.txt', 'F') not in records
        assert watcher.update() == []

    def test_directory_root_is_scanned_recursively(self, fs):
        fs.create_file('/watch/sub/deep/a.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch/sub', recursive=True)
        watcher.update()
        assert ('/watch/sub/deep/a.txt', 'F') in record_shape(watcher.list())

    def test_missing_root_then_file(self, fs, monkeypatch):
        watcher = PathWatcher()
        watcher.subscribe('/watch/file.txt')

        real_scandir = os.scandir
        scans = []

        def counting_scandir(path):
            scans.append(path)
            return real_scandir(path)

        monkeypatch.setattr(os, 'scandir', counting_scandir)
        assert event_shape(watcher.update()) == [
            ('D', '/watch/file.txt', ''),
        ]
        assert scans == []

        fs.create_file('/watch/file.txt', contents='a')
        assert event_shape(watcher.update()) == [
            ('A', '/watch/file.txt', 'F'),
        ]
        assert scans == []

    def test_modify_after_file_root(self, fs):
        fs.create_file('/watch/file.txt', contents='a')
        watcher = PathWatcher()
        watcher.subscribe('/watch/file.txt')
        watcher.update()

        with open('/watch/file.txt', 'w') as file:
            file.write('b')
        fs.utime('/watch/file.txt', ns=(0, 1000000000))
        assert event_shape(watcher.update()) == [
            ('M', '/watch/file.txt', 'F'),
        ]

    def test_symlink_root_is_not_followed(self, fs, monkeypatch):
        fs.create_file('/watch/target.txt')
        fs.create_symlink('/watch/link.txt', '/watch/target.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch/link.txt')

        def fail(*args, **kwargs):
            raise AssertionError('a symlink root must not be scanned')

        monkeypatch.setattr(os, 'scandir', fail)
        assert event_shape(watcher.update()) == [
            ('A', '/watch/link.txt', 'S'),
        ]
        assert record_shape(watcher.list()) == [('/watch/link.txt', 'S')]

    def test_repeated_subscribe_is_not_reset(self, fs):
        fs.create_file('/watch/file.txt', contents='a')
        watcher = PathWatcher()
        watcher.subscribe('/watch/file.txt')
        watcher.update()

        watcher.subscribe('/watch/file.txt')
        assert watcher.update() == []
        assert ('/watch/file.txt', 'F') in record_shape(watcher.list())


class TestRootTypeChange:
    def test_file_directory_file(self, fs):
        fs.create_file('/watch/root', contents='a')
        watcher = PathWatcher()
        watcher.subscribe('/watch/root')
        assert event_shape(watcher.update()) == [
            ('A', '/watch/root', 'F'),
        ]

        fs.remove('/watch/root')
        fs.create_file('/watch/root/child.txt')
        assert event_shape(watcher.update()) == [
            ('D', '/watch/root', 'F'),
            ('A', '/watch/root', 'D'),
            ('A', '/watch/root/child.txt', 'F'),
        ]

        fs.rmtree('/watch/root')
        fs.create_file('/watch/root', contents='b')
        assert event_shape(watcher.update()) == [
            ('D', '/watch/root', 'D'),
            ('A', '/watch/root', 'F'),
            ('D', '/watch/root/child.txt', 'F'),
        ]


class TestSymlinkRoot:
    def test_file_symlink_is_the_link_only(self, fs):
        fs.create_file('/outside/target.txt', contents='a')
        fs.create_symlink('/watch/link.txt', '/outside/target.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch/link.txt')
        assert event_shape(watcher.update()) == [('A', '/watch/link.txt', 'S')]
        assert record_shape(watcher.list()) == [('/watch/link.txt', 'S')]

        # Changing the target does not change the link entry.
        with open('/outside/target.txt', 'w') as file:
            file.write('b')
        fs.utime('/outside/target.txt', ns=(0, 1000000000))
        assert watcher.update() == []

        # Retargeting replaces the link entry itself.
        fs.remove('/watch/link.txt')
        fs.create_symlink('/watch/link.txt', '/outside/other.txt')
        assert event_shape(watcher.update()) == [('M', '/watch/link.txt', 'S')]

        fs.remove('/watch/link.txt')
        assert event_shape(watcher.update()) == [('D', '/watch/link.txt', 'S')]

    def test_dangling_symlink_is_a_link(self, fs):
        fs.create_symlink('/watch/link.txt', '/outside/missing.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch/link.txt')
        assert event_shape(watcher.update()) == [('A', '/watch/link.txt', 'S')]
        assert record_shape(watcher.list()) == [('/watch/link.txt', 'S')]

    def test_directory_symlink_is_not_traversed(self, fs, monkeypatch):
        fs.create_file('/outside/target/inside.txt')
        fs.create_symlink('/watch/link', '/outside/target')
        watcher = PathWatcher()
        watcher.subscribe('/watch/link', recursive=True)

        def fail(*args, **kwargs):
            raise AssertionError('a symlink root must not be scanned')

        monkeypatch.setattr(os, 'scandir', fail)
        assert event_shape(watcher.update()) == [('A', '/watch/link', 'S')]
        assert record_shape(watcher.list()) == [('/watch/link', 'S')]

    def test_directory_root_replaced_by_symlink(self, fs):
        fs.create_file('/watch/root/a.txt')
        fs.create_file('/outside/target/b.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch/root', recursive=True)
        watcher.update()

        fs.rmtree('/watch/root')
        fs.create_symlink('/watch/root', '/outside/target')
        assert event_shape(watcher.update()) == [
            ('D', '/watch/root', 'D'),
            ('A', '/watch/root', 'S'),
            ('D', '/watch/root/a.txt', 'F'),
        ]
        assert record_shape(watcher.list()) == [('/watch/root', 'S')]

        # The target stays outside of the watched tree.
        fs.create_file('/outside/target/c.txt')
        assert watcher.update() == []

    def test_symlink_root_replaced_by_file(self, fs):
        fs.create_symlink('/watch/root', '/outside/target')
        watcher = PathWatcher()
        watcher.subscribe('/watch/root')
        assert event_shape(watcher.update()) == [('A', '/watch/root', 'S')]

        fs.remove('/watch/root')
        fs.create_file('/watch/root', contents='a')
        assert event_shape(watcher.update()) == [
            ('D', '/watch/root', 'S'),
            ('A', '/watch/root', 'F'),
        ]


class TestOverlap:
    def test_steady_state_does_not_check_coverage(self, fs, monkeypatch):
        fs.create_file('/watch/a.txt')
        fs.create_dir('/watch/sub')
        watcher = PathWatcher()
        watcher.subscribe('/watch', recursive=True)
        watcher.subscribe('/watch/sub', recursive=True)
        watcher.update()

        def fail(*args, **kwargs):
            raise AssertionError('steady state must not check coverage')

        monkeypatch.setattr(watcher_module, '_covers', fail)
        fs.create_file('/watch/sub/b.txt')
        assert event_shape(watcher.update()) == [('A', '/watch/sub/b.txt', 'F')]

    def test_events_are_deduplicated(self, fs):
        fs.create_file('/watch/a/b/f.txt')
        fs.create_file('/watch/a/g.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch/a', recursive=True)
        watcher.subscribe('/watch/a/b', recursive=True)
        watcher.update()

        fs.create_file('/watch/a/b/h.txt')
        assert event_shape(watcher.update()) == [
            ('A', '/watch/a/b/h.txt', 'F'),
        ]

        fs.create_file('/watch/a/b/deep/i.txt')
        assert event_shape(watcher.update()) == [
            ('A', '/watch/a/b/deep', 'D'),
            ('A', '/watch/a/b/deep/i.txt', 'F'),
        ]

    def test_new_child_baseline_does_not_duplicate_parent_add(self, fs):
        fs.create_dir('/watch')
        watcher = PathWatcher()
        watcher.subscribe('/watch', recursive=True)
        watcher.update()

        fs.create_file('/watch/new/a.txt')
        watcher.subscribe('/watch/new')
        assert event_shape(watcher.update()) == [
            ('A', '/watch/new', 'D'),
            ('A', '/watch/new/a.txt', 'F'),
        ]

    def test_new_recursive_child_keeps_parent_modify(self, fs):
        fs.create_file('/watch/sub/a.txt', contents='a')
        watcher = PathWatcher()
        watcher.subscribe('/watch', recursive=True)
        watcher.update()

        with open('/watch/sub/a.txt', 'w') as file:
            file.write('b')
        fs.utime('/watch/sub/a.txt', ns=(0, 1000000000))
        watcher.subscribe('/watch/sub', recursive=True)
        assert event_shape(watcher.update()) == [
            ('A', '/watch/sub', 'D'),
            ('M', '/watch/sub/a.txt', 'F'),
        ]

    def test_new_recursive_child_keeps_parent_add(self, fs):
        fs.create_dir('/watch/sub')
        watcher = PathWatcher()
        watcher.subscribe('/watch', recursive=True)
        watcher.update()

        fs.create_file('/watch/sub/new.txt')
        watcher.subscribe('/watch/sub', recursive=True)
        assert event_shape(watcher.update()) == [
            ('A', '/watch/sub', 'D'),
            ('A', '/watch/sub/new.txt', 'F'),
        ]

    def test_new_recursive_child_baseline_has_no_extra_changes(self, fs):
        fs.create_file('/watch/sub/a.txt', contents='a')
        watcher = PathWatcher()
        watcher.subscribe('/watch', recursive=True)
        watcher.update()

        watcher.subscribe('/watch/sub', recursive=True)
        assert event_shape(watcher.update()) == [
            ('A', '/watch/sub', 'D'),
        ]

    def test_new_recursive_child_keeps_initialized_file_root_change(self, fs):
        fs.create_file('/watch/sub/a.txt', contents='a')
        watcher = PathWatcher()
        watcher.subscribe('/watch/sub/a.txt')
        watcher.update()

        with open('/watch/sub/a.txt', 'w') as file:
            file.write('b')
        fs.utime('/watch/sub/a.txt', ns=(0, 1000000000))
        watcher.subscribe('/watch/sub', recursive=True)
        assert ('M', '/watch/sub/a.txt', 'F') in event_shape(watcher.update())

    def test_new_recursive_child_under_nonrecursive_parent(self, fs):
        fs.create_file('/watch/sub/deep/a.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch')
        watcher.update()

        watcher.subscribe('/watch/sub', recursive=True)
        watcher.update()
        assert ('/watch/sub/deep/a.txt', 'F') in record_shape(watcher.list())

    def test_new_file_root_keeps_nonrecursive_parent_change(self, fs):
        fs.create_file('/watch/a.txt', contents='a')
        watcher = PathWatcher()
        watcher.subscribe('/watch')
        watcher.update()

        with open('/watch/a.txt', 'w') as file:
            file.write('b')
        fs.utime('/watch/a.txt', ns=(0, 1000000000))
        watcher.subscribe('/watch/a.txt')
        assert ('M', '/watch/a.txt', 'F') in event_shape(watcher.update())

    def test_new_recursive_parent_keeps_existing_child_change(self, fs):
        fs.create_file('/watch/sub/a.txt', contents='a')
        watcher = PathWatcher()
        watcher.subscribe('/watch/sub')
        watcher.update()

        with open('/watch/sub/a.txt', 'w') as file:
            file.write('b')
        fs.utime('/watch/sub/a.txt', ns=(0, 1000000000))
        watcher.subscribe('/watch', recursive=True)
        assert ('M', '/watch/sub/a.txt', 'F') in event_shape(watcher.update())

    def test_new_recursive_child_keeps_parent_type_change(self, fs):
        fs.create_file('/watch/sub/a', contents='a')
        watcher = PathWatcher()
        watcher.subscribe('/watch', recursive=True)
        watcher.update()

        fs.remove('/watch/sub/a')
        fs.create_file('/watch/sub/a/b.txt')
        watcher.subscribe('/watch/sub', recursive=True)
        assert event_shape(watcher.update()) == [
            ('A', '/watch/sub', 'D'),
            ('D', '/watch/sub/a', 'F'),
            ('A', '/watch/sub/a', 'D'),
            ('A', '/watch/sub/a/b.txt', 'F'),
        ]

    def test_nested_recursive_scans_each_directory_once(
            self, fs, monkeypatch):
        fs.create_file('/watch/sub/a.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch', recursive=True)
        watcher.subscribe('/watch/sub', recursive=True)
        watcher.update()

        real_scandir = os.scandir
        scans = []

        def counting_scandir(path):
            scans.append(path)
            return real_scandir(path)

        monkeypatch.setattr(os, 'scandir', counting_scandir)
        real_stat = FakeDirEntry.stat
        stats = []

        def counting_stat(self, follow_symlinks=True):
            stats.append(self.name)
            return real_stat(self, follow_symlinks=follow_symlinks)

        monkeypatch.setattr(FakeDirEntry, 'stat', counting_stat)
        fs.utime('/watch/sub/a.txt', ns=(0, 1000000000))
        assert event_shape(watcher.update()) == [
            ('M', '/watch/sub/a.txt', 'F'),
        ]
        assert scans.count('/watch') == 1
        assert scans.count('/watch/sub') == 1
        # The deeper subscription stat'ed the file, the ancestor skips it.
        assert stats == ['a.txt']

    def test_recursive_parent_nonrecursive_child_scans_once(
            self, fs, monkeypatch):
        fs.create_file('/watch/sub/a.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch', recursive=True)
        watcher.subscribe('/watch/sub')
        watcher.update()

        real_scandir = os.scandir
        scans = []

        def counting_scandir(path):
            scans.append(path)
            return real_scandir(path)

        monkeypatch.setattr(os, 'scandir', counting_scandir)
        fs.utime('/watch/sub/a.txt', ns=(0, 1000000000))
        assert event_shape(watcher.update()) == [
            ('M', '/watch/sub/a.txt', 'F'),
        ]
        assert scans.count('/watch') == 1
        assert scans.count('/watch/sub') == 1

    def test_nested_watch_keeps_file_signature(self, fs):
        fs.create_file('/watch/sub/b.txt', contents='b')
        watcher = PathWatcher()
        watcher.subscribe('/watch', recursive=True)
        watcher.subscribe('/watch/sub')
        watcher.update()

        records = {record.path: record for record in watcher.list()}
        assert records['/watch/sub/b.txt'].mtime_ns > 0

        fs.utime('/watch/sub/b.txt', ns=(0, 1000000000))
        assert event_shape(watcher.update()) == [
            ('M', '/watch/sub/b.txt', 'F'),
        ]

    def test_one_record_per_path(self, fs):
        fs.create_file('/watch/a.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch')
        watcher.subscribe('/watch/a.txt')
        watcher.update()

        paths = [record.path for record in watcher.list()]
        assert paths == sorted(set(paths))
        by_path = {record.path: record for record in watcher.list()}
        assert by_path['/watch/a.txt'].mtime_ns > 0

        fs.utime('/watch/a.txt', ns=(0, 1000000000))
        assert event_shape(watcher.update()) == [
            ('M', '/watch/a.txt', 'F'),
        ]


class TestFailureRecovery:
    def test_ancestor_failure_does_not_rollback_fresher_records(
            self, fs, monkeypatch):
        fs.create_file('/watch/sub/a.txt', contents='a')
        watcher = PathWatcher()
        watcher.subscribe('/watch', recursive=True)
        watcher.subscribe('/watch/sub', recursive=True)
        watcher.update()

        with open('/watch/sub/a.txt', 'w') as file:
            file.write('bb')
        fs.utime('/watch/sub/a.txt', ns=(0, 1000000000))

        real_scandir = os.scandir

        def selective_scandir(path):
            if path == '/watch':
                raise PermissionError('denied')
            return real_scandir(path)

        monkeypatch.setattr(os, 'scandir', selective_scandir)
        assert event_shape(watcher.update()) == [
            ('M', '/watch/sub/a.txt', 'F'),
        ]
        # The ancestor still cannot be read, but the fresh child record
        # must not be rolled back and the event must not repeat.
        assert watcher.update() == []
        records = {record.path: record for record in watcher.list()}
        assert records['/watch/sub/a.txt'].size == 2

        monkeypatch.setattr(os, 'scandir', real_scandir)
        assert watcher.update() == []

    def test_rescan_after_directory_failure(self, fs, monkeypatch):
        fs.create_dir('/watch')
        watcher = PathWatcher()
        watcher.subscribe('/watch')
        watcher.update()

        fs.create_file('/watch/a.txt')
        real_scandir = os.scandir

        def fail_scandir(path):
            raise PermissionError('denied')

        monkeypatch.setattr(os, 'scandir', fail_scandir)
        assert watcher.update() == []

        monkeypatch.setattr(os, 'scandir', real_scandir)
        assert event_shape(watcher.update()) == [
            ('A', '/watch/a.txt', 'F'),
        ]

    def test_file_root_failure_does_not_emit_a_change(
            self, fs, monkeypatch):
        fs.create_file('/watch/file.txt', contents='a')
        watcher = PathWatcher()
        watcher.subscribe('/watch/file.txt')
        watcher.update()

        real_lstat = os.lstat

        def fail_lstat(path):
            if path == '/watch/file.txt':
                raise PermissionError('denied')
            return real_lstat(path)

        monkeypatch.setattr(os, 'lstat', fail_lstat)
        assert watcher.update() == []

        monkeypatch.setattr(os, 'lstat', real_lstat)
        assert watcher.update() == []
        assert record_shape(watcher.list()) == [
            ('/watch/file.txt', 'F'),
        ]

    def test_incomplete_directory_is_not_retried_in_same_update(
            self, fs, monkeypatch):
        fs.create_file('/watch/sub/a.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch', recursive=True)
        watcher.subscribe('/watch/sub')
        watcher.update()

        real_scandir = os.scandir
        calls = []

        def selective_scandir(path):
            calls.append(path)
            if path == '/watch/sub':
                raise PermissionError('denied')
            return real_scandir(path)

        monkeypatch.setattr(os, 'scandir', selective_scandir)
        assert watcher.update() == []
        # The recursive parent fails once; the non-recursive child sees
        # the preserved root in `new` and does not retry it.
        assert calls.count('/watch/sub') == 1
        assert ('/watch/sub/a.txt', 'F') in record_shape(watcher.list())

    def test_file_to_unreadable_directory_keeps_observed_type(
            self, fs, monkeypatch):
        fs.create_file('/watch/root', contents='a')
        watcher = PathWatcher()
        watcher.subscribe('/watch/root')
        watcher.update()

        fs.remove('/watch/root')
        fs.create_file('/watch/root/child.txt')
        real_scandir = os.scandir

        def fail_scandir(path):
            if path == '/watch/root':
                raise PermissionError('denied')
            return real_scandir(path)

        monkeypatch.setattr(os, 'scandir', fail_scandir)
        assert event_shape(watcher.update()) == [
            ('D', '/watch/root', 'F'),
            ('A', '/watch/root', 'D'),
        ]
        assert record_shape(watcher.list()) == [
            ('/watch/root', 'D'),
        ]

    def test_child_directory_failure_keeps_old_subtree(
            self, fs, monkeypatch):
        fs.create_file('/watch/sub/a.txt')
        fs.create_file('/watch/other.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch', recursive=True)
        watcher.update()

        real_scandir = os.scandir

        def selective_scandir(path):
            if path == '/watch/sub':
                raise PermissionError('denied')
            return real_scandir(path)

        monkeypatch.setattr(os, 'scandir', selective_scandir)
        fs.create_file('/watch/sub/b.txt')
        fs.create_file('/watch/sibling.txt')
        assert event_shape(watcher.update()) == [
            ('A', '/watch/sibling.txt', 'F'),
        ]
        assert ('/watch/sub/a.txt', 'F') in record_shape(watcher.list())
        assert ('/watch/sub/b.txt', 'F') not in record_shape(watcher.list())

        monkeypatch.setattr(os, 'scandir', real_scandir)
        assert event_shape(watcher.update()) == [
            ('A', '/watch/sub/b.txt', 'F'),
        ]

    def test_single_file_error_keeps_other_changes(
            self, fs, monkeypatch):
        fs.create_file('/watch/a.txt', contents='a')
        fs.create_file('/watch/b.txt', contents='b')
        watcher = PathWatcher()
        watcher.subscribe('/watch', recursive=True)
        watcher.update()

        real_stat = FakeDirEntry.stat

        def selective_stat(self, follow_symlinks=True):
            if self.name == 'a.txt':
                raise PermissionError('denied')
            return real_stat(self, follow_symlinks=follow_symlinks)

        monkeypatch.setattr(FakeDirEntry, 'stat', selective_stat)
        fs.create_file('/watch/c.txt', contents='c')
        assert event_shape(watcher.update()) == [
            ('A', '/watch/c.txt', 'F'),
        ]
        records = {record.path: record for record in watcher.list()}
        assert records['/watch/a.txt'].size == 1
        assert '/watch/c.txt' in records

        monkeypatch.setattr(FakeDirEntry, 'stat', real_stat)
        assert watcher.update() == []


class TestErrorLogging:
    def test_directory_scan_error(self, fs, monkeypatch):
        fs.create_dir('/watch')
        watcher = PathWatcher()
        watcher.subscribe('/watch')
        watcher.update()

        def fail_scandir(path):
            raise PermissionError('denied')

        monkeypatch.setattr(os, 'scandir', fail_scandir)
        with logger.mock_capture_writer() as capture:
            assert watcher.update() == []
        assert capture.concat() == (
            'ERROR | PathWatcher: failed to scan directory /watch: '
            'PermissionError: denied\n'
        )

    def test_child_directory_scan_error(self, fs, monkeypatch):
        fs.create_file('/watch/sub/a.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch', recursive=True)
        watcher.update()

        real_scandir = os.scandir

        def selective_scandir(path):
            if path == '/watch/sub':
                raise PermissionError('denied')
            return real_scandir(path)

        monkeypatch.setattr(os, 'scandir', selective_scandir)
        with logger.mock_capture_writer() as capture:
            assert watcher.update() == []
        assert capture.concat() == (
            'ERROR | PathWatcher: failed to scan directory /watch/sub: '
            'PermissionError: denied\n'
        )

    def test_file_root_stat_error(self, fs, monkeypatch):
        fs.create_file('/watch/file.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch/file.txt')
        watcher.update()

        real_lstat = os.lstat

        def fail_lstat(path):
            if path == '/watch/file.txt':
                raise PermissionError('denied')
            return real_lstat(path)

        monkeypatch.setattr(os, 'lstat', fail_lstat)
        with logger.mock_capture_writer() as capture:
            assert watcher.update() == []
        assert capture.concat() == (
            'ERROR | PathWatcher: failed to stat path /watch/file.txt: '
            'PermissionError: denied\n'
        )

    def test_error_without_message(self, fs, monkeypatch):
        fs.create_file('/watch/file.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch/file.txt')
        watcher.update()

        real_lstat = os.lstat

        def fail_lstat(path):
            if path == '/watch/file.txt':
                raise PermissionError()
            return real_lstat(path)

        monkeypatch.setattr(os, 'lstat', fail_lstat)
        with logger.mock_capture_writer() as capture:
            assert watcher.update() == []
        assert capture.concat() == (
            'ERROR | PathWatcher: failed to stat path /watch/file.txt: '
            'PermissionError\n'
        )

    def test_entry_type_error(self, fs, monkeypatch):
        fs.create_file('/watch/a.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch')
        watcher.update()

        real_is_dir = FakeDirEntry.is_dir

        def fail_is_dir(self, follow_symlinks=True):
            if self.name == 'a.txt':
                raise PermissionError('denied')
            return real_is_dir(self, follow_symlinks=follow_symlinks)

        monkeypatch.setattr(FakeDirEntry, 'is_dir', fail_is_dir)
        with logger.mock_capture_writer() as capture:
            assert watcher.update() == []
        assert capture.concat() == (
            'ERROR | PathWatcher: failed to read directory entry /watch/a.txt: '
            'PermissionError: denied\n'
        )

    def test_entry_stat_error(self, fs, monkeypatch):
        fs.create_file('/watch/a.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch')
        watcher.update()

        real_stat = FakeDirEntry.stat

        def fail_stat(self, follow_symlinks=True):
            if self.name == 'a.txt':
                raise PermissionError('denied')
            return real_stat(self, follow_symlinks=follow_symlinks)

        monkeypatch.setattr(FakeDirEntry, 'stat', fail_stat)
        with logger.mock_capture_writer() as capture:
            assert watcher.update() == []
        assert capture.concat() == (
            'ERROR | PathWatcher: failed to stat file /watch/a.txt: '
            'PermissionError: denied\n'
        )

    def test_missing_and_file_roots_do_not_log(self, fs):
        fs.create_file('/watch/file.txt')
        watcher = PathWatcher()
        watcher.subscribe('/missing')
        watcher.subscribe('/watch/file.txt')
        with logger.mock_capture_writer() as capture:
            assert event_shape(watcher.update()) == [
                ('D', '/missing', ''),
                ('A', '/watch/file.txt', 'F'),
            ]
        assert capture.concat() == ''


class TestMarkModified:
    def test_recursive_ancestor(self, fs):
        fs.create_file('/watch/a.txt', contents='a')
        watcher = PathWatcher()
        watcher.subscribe('/watch', recursive=True)
        watcher.update()

        with open('/watch/a.txt', 'w') as file:
            file.write('changed')
        watcher.mark_modified('/watch/a.txt')

        assert watcher.update() == []
        record = next(
            record for record in watcher.list()
            if record.path == '/watch/a.txt'
        )
        assert record.size == len('changed')

    def test_external_change_after_mark_is_reported(self, fs):
        fs.create_file('/watch/a.txt', contents='a')
        watcher = PathWatcher()
        watcher.subscribe('/watch', recursive=True)
        watcher.update()

        watcher.mark_modified('/watch/a.txt')
        with open('/watch/a.txt', 'w') as file:
            file.write('external')
        fs.utime('/watch/a.txt', ns=(0, time.time_ns() + 1000000000))

        assert event_shape(watcher.update()) == [
            ('M', '/watch/a.txt', 'F'),
        ]

    def test_recursive_ancestor_adds_new_entry(self, fs):
        fs.create_dir('/watch')
        watcher = PathWatcher()
        watcher.subscribe('/watch', recursive=True)
        watcher.update()

        fs.create_file('/watch/new.txt')
        watcher.mark_modified('/watch/new.txt')

        assert watcher.update() == []
        assert ('/watch/new.txt', 'F') in record_shape(watcher.list())

    def test_non_recursive_parent(self, fs):
        fs.create_dir('/watch')
        watcher = PathWatcher()
        watcher.subscribe('/watch')
        watcher.update()

        fs.create_file('/watch/new.txt')
        watcher.mark_modified('/watch/new.txt')

        assert watcher.update() == []
        assert watcher.update() == []
        assert ('/watch/new.txt', 'F') in record_shape(watcher.list())

    def test_file_root(self, fs):
        fs.create_file('/watch/file.txt', contents='a')
        watcher = PathWatcher()
        watcher.subscribe('/watch/file.txt')
        watcher.update()

        with open('/watch/file.txt', 'w') as file:
            file.write('changed')
        watcher.mark_modified('/watch/file.txt')

        assert watcher.update() == []
        records = watcher.list()
        assert len(records) == 1
        assert records[0].size == len('changed')

    def test_suppresses_delete(self, fs):
        fs.create_file('/watch/a.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch')
        watcher.update()

        fs.remove('/watch/a.txt')
        watcher.mark_modified('/watch/a.txt')
        assert watcher.update() == []
        assert ('/watch/a.txt', 'F') not in record_shape(watcher.list())

    def test_type_change_after_mark_is_reported(self, fs):
        fs.create_file('/watch/a.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch')
        watcher.update()

        watcher.mark_modified('/watch/a.txt')
        fs.remove('/watch/a.txt')
        fs.create_dir('/watch/a.txt')
        assert event_shape(watcher.update()) == [
            ('D', '/watch/a.txt', 'F'),
            ('A', '/watch/a.txt', 'D'),
        ]

    def test_mark_expires_after_one_scan(self, fs):
        fs.create_file('/watch/a.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch')
        watcher.update()

        watcher.mark_modified('/watch/a.txt')
        watcher.mark_modified('/not/watched.txt')
        assert watcher.update() == []
        assert watcher._marked == {}

    def test_mark_only_silences_its_path(self, fs):
        fs.create_file('/watch/a.txt', contents='a')
        fs.create_file('/watch/b.txt', contents='b')
        watcher = PathWatcher()
        watcher.subscribe('/watch')
        watcher.update()

        with open('/watch/a.txt', 'w') as file:
            file.write('aa')
        with open('/watch/b.txt', 'w') as file:
            file.write('bb')
        fs.utime('/watch/a.txt', ns=(0, 1000000000))
        fs.utime('/watch/b.txt', ns=(0, 1000000000))
        watcher.mark_modified('/watch/a.txt')
        assert event_shape(watcher.update()) == [
            ('M', '/watch/b.txt', 'F'),
        ]

    def test_batch_generated_files_do_not_emit_events(self, fs):
        fs.create_dir('/watch')
        watcher = PathWatcher()
        watcher.subscribe('/watch', recursive=True)
        watcher.update()

        paths = [f'/watch/{index}.txt' for index in range(20)]
        for path in paths:
            fs.create_file(path, contents='x')
            watcher.mark_modified(path)
        assert watcher.update() == []
        assert watcher._marked == {}
        assert watcher.update() == []
        assert len(watcher.list()) == len(paths) + 1

    def test_mark_during_scan_suppresses_event(self, fs, monkeypatch):
        fs.create_dir('/watch')
        watcher = PathWatcher()
        watcher.subscribe('/watch')
        watcher.update()

        real_list_dir = watcher_module._list_dir
        state = {'marked': False}

        def hooking_list_dir(path):
            if path == '/watch' and not state['marked']:
                state['marked'] = True
                fs.create_file('/watch/new.txt')
                watcher.mark_modified('/watch/new.txt')
            return real_list_dir(path)

        monkeypatch.setattr(watcher_module, '_list_dir', hooking_list_dir)
        assert watcher.update() == []
        assert ('/watch/new.txt', 'F') in record_shape(watcher.list())
        assert watcher._marked != {}

        assert watcher.update() == []
        assert watcher._marked == {}

    def test_mark_during_scan_is_kept_for_next_scan(self, fs, monkeypatch):
        fs.create_dir('/watch')
        watcher = PathWatcher()
        watcher.subscribe('/watch')
        watcher.update()

        real_list_dir = watcher_module._list_dir
        state = {'marked': False}

        def hooking_list_dir(path):
            scan = real_list_dir(path)
            if path == '/watch' and not state['marked']:
                state['marked'] = True
                fs.create_file('/watch/new.txt')
                watcher.mark_modified('/watch/new.txt')
            return scan

        monkeypatch.setattr(watcher_module, '_list_dir', hooking_list_dir)
        assert watcher.update() == []
        assert ('/watch/new.txt', 'F') not in record_shape(watcher.list())
        assert watcher._marked != {}

        assert watcher.update() == []
        assert ('/watch/new.txt', 'F') in record_shape(watcher.list())
        assert watcher._marked == {}


class TestList:
    def test_before_baseline_is_empty(self):
        watcher = PathWatcher()
        watcher.subscribe('/watch')
        assert watcher.list() == []

    def test_records_are_global_across_watches(self, fs):
        fs.create_file('/watch/a/b.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch/a/b.txt')
        watcher.subscribe('/watch/a', recursive=True)
        assert event_shape(watcher.update()) == [
            ('A', '/watch/a', 'D'),
            ('A', '/watch/a/b.txt', 'F'),
        ]
        assert record_shape(watcher.list()) == [
            ('/watch/a', 'D'),
            ('/watch/a/b.txt', 'F'),
        ]
        assert watcher.update() == []

    def test_unsubscribe_keeps_records_of_other_watches(self, fs):
        fs.create_file('/watch/a/b/f.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch/a', recursive=True)
        watcher.subscribe('/watch/a/b', recursive=True)
        watcher.update()

        watcher.unsubscribe('/watch/a')
        assert record_shape(watcher.list()) == [
            ('/watch/a/b', 'D'),
            ('/watch/a/b/f.txt', 'F'),
        ]
        assert watcher.update() == []

    def test_unsubscribe_respects_non_recursive_coverage(self, fs):
        fs.create_file('/watch/sub/a.txt')
        fs.create_file('/watch/direct.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch')
        watcher.subscribe('/watch/sub', recursive=True)
        watcher.update()

        watcher.unsubscribe('/watch/sub')
        assert record_shape(watcher.list()) == [
            ('/watch', 'D'),
            ('/watch/direct.txt', 'F'),
            ('/watch/sub', 'D'),
        ]
        assert watcher.update() == []

    def test_missing_child_baseline_does_not_duplicate_delete(self, fs):
        fs.create_file('/watch/a.txt')
        fs.create_file('/watch/child/b.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch', recursive=True)
        watcher.update()

        fs.rmtree('/watch/child')
        watcher.subscribe('/watch/child', recursive=True)
        assert event_shape(watcher.update()) == [
            ('D', '/watch/child', 'D'),
            ('D', '/watch/child/b.txt', 'F'),
        ]

    def test_unsubscribe_produces_no_delete_event(self, fs):
        fs.create_file('/watch/a.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch')
        watcher.update()

        watcher.unsubscribe('/watch')
        assert watcher.update() == []
        assert watcher.list() == []

    def test_does_not_scan(self, fs, monkeypatch):
        fs.create_file('/watch/a.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch', recursive=True)
        watcher.update()

        def fail(*args, **kwargs):
            raise AssertionError('list must not scan the filesystem')

        monkeypatch.setattr(os, 'stat', fail)
        monkeypatch.setattr(os, 'scandir', fail)
        assert record_shape(watcher.list()) == [
            ('/watch', 'D'),
            ('/watch/a.txt', 'F'),
        ]
