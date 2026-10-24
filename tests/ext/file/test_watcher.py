"""
Tests for alasio.ext.file.watcher.

The watcher is synchronous, so these tests do not need trio. All filesystem
operations go through the in-memory filesystem fixture.
"""
import inspect
import os
import time
from typing import Literal, get_type_hints

import pytest
from msgspec import Struct

import alasio.ext.file.watcher as watcher_module
from alasio.ext.file.watcher import (
    _SCAN_INCOMPLETE, _SCAN_MISSING, _SCAN_NOT_DIRECTORY, _SCAN_OK, PathWatcher, WatchEvent, WatchRecord, _list_dir,
    _Watch
)
from alasio.testing.filesystem import fs  # noqa: F401


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
        assert event_hints['type'] == Literal['', 'F', 'D', 'S']
        assert record_hints['type'] == Literal['', 'F', 'D', 'S']

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


class TestDirectoryListing:
    def test_non_recursive_listing_does_not_stat_entries(
            self, fs, monkeypatch):
        fs.create_file('/watch/a/b/f.txt')
        fs.create_file('/watch/a/g.txt', contents='abc')

        def fail(*args, **kwargs):
            raise AssertionError('listing must not stat entries')

        monkeypatch.setattr(os, 'stat', fail)
        monkeypatch.setattr(os, 'lstat', fail)
        scan = _list_dir('/watch/a')
        assert scan.status == _SCAN_OK
        assert {(record.path, record.type) for record in scan.records} == {
            ('/watch/a/b', 'D'),
            ('/watch/a/g.txt', 'F'),
        }

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

    def test_recursive_scan_lstats_only_files(self, fs, monkeypatch):
        fs.create_file('/watch/a/b/f.txt', contents='abc')
        fs.create_file('/watch/a/g.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch/a', recursive=True)
        real_lstat = os.lstat
        stats = []

        def counting_lstat(path):
            stats.append(path)
            return real_lstat(path)

        monkeypatch.setattr(os, 'lstat', counting_lstat)
        watcher.update()
        assert set(stats) == {'/watch/a/b/f.txt', '/watch/a/g.txt'}


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


class TestOverlap:
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
        fs.utime('/watch/sub/a.txt', ns=(0, 1000000000))
        assert event_shape(watcher.update()) == [
            ('M', '/watch/sub/a.txt', 'F'),
        ]
        assert scans.count('/watch') == 1
        assert scans.count('/watch/sub') == 1

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

    def test_full_record_wins_over_type_only(self, fs):
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

        real_stat = os.stat

        def fail_stat(path):
            if path == '/watch/file.txt':
                raise PermissionError('denied')
            return real_stat(path)

        monkeypatch.setattr(os, 'stat', fail_stat)
        assert watcher.update() == []

        monkeypatch.setattr(os, 'stat', real_stat)
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

        real_lstat = os.lstat

        def selective_lstat(path):
            if path == '/watch/a.txt':
                raise PermissionError('denied')
            return real_lstat(path)

        monkeypatch.setattr(os, 'lstat', selective_lstat)
        fs.create_file('/watch/c.txt', contents='c')
        assert event_shape(watcher.update()) == [
            ('A', '/watch/c.txt', 'F'),
        ]
        records = {record.path: record for record in watcher.list()}
        assert records['/watch/a.txt'].size == 1
        assert '/watch/c.txt' in records

        monkeypatch.setattr(os, 'lstat', real_lstat)
        assert watcher.update() == []


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

    def test_does_not_suppress_delete(self, fs):
        fs.create_file('/watch/a.txt')
        watcher = PathWatcher()
        watcher.subscribe('/watch')
        watcher.update()

        fs.remove('/watch/a.txt')
        watcher.mark_modified('/watch/a.txt')
        assert event_shape(watcher.update()) == [
            ('D', '/watch/a.txt', 'F'),
        ]


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
