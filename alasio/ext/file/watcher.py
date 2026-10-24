import os
import stat
import time
from threading import Lock
from typing import Dict, List, Literal

from msgspec import Struct, field

from ..path.calc import joinpath, normpath

__all__ = [
    'PathWatcher',
    'WatchEvent',
    'WatchRecord',
]

# Record and event types
_RootType = Literal['', 'F', 'D', 'S']
_EventType = Literal['A', 'M', 'D']
_MISSING = object()

_EVENT_ORDER: Dict[_EventType, int] = {
    'D': 0,
    'A': 1,
    'M': 2,
}

# Timestamp rounding tolerance for suppressing our own writes without IO.
_MARK_GRACE_NS = 1000

_SCAN_OK = 'ok'
_SCAN_MISSING = 'missing'
_SCAN_NOT_DIRECTORY = 'not_directory'
_SCAN_INCOMPLETE = 'incomplete'


class WatchRecord(Struct, frozen=True, omit_defaults=True):
    """
    One path observed by the watcher.

    Attributes:
        path (str): Absolute path, normalized to '/' separators
        type (str): 'F' file, 'D' directory, 'S' symlink
        mtime_ns (int): Modification time in nanoseconds; 0 for
            directory and symlink entries
        size (int): File size in bytes, 0 for directories and symlinks
        ino (int): Filesystem inode number, 0 when unavailable
    """
    path: str
    type: _RootType = ''
    mtime_ns: int = 0
    size: int = 0
    ino: int = 0

    @classmethod
    def from_stat(cls, path, st):
        """
        Build a record from one stat result.

        Args:
            path (str): Absolute path
            st (os.stat_result): Stat result to read

        Returns:
            WatchRecord:
        """
        mode = st.st_mode
        if stat.S_ISREG(mode):
            path_type = 'F'
        elif stat.S_ISDIR(mode):
            path_type = 'D'
        elif stat.S_ISLNK(mode):
            path_type = 'S'
        else:
            path_type = 'F'
        mtime_ns = getattr(st, 'st_mtime_ns', None)
        if mtime_ns is None:
            mtime_ns = int(st.st_mtime * 1000000000)
        size = int(st.st_size) if path_type == 'F' else 0
        ino = int(getattr(st, 'st_ino', 0) or 0)
        return cls(path, path_type, mtime_ns, size, ino)


class WatchEvent(Struct, frozen=True, omit_defaults=True):
    """
    One filesystem change returned by PathWatcher.update().

    Attributes:
        event (str): 'A' added, 'M' modified, 'D' deleted
        path (str): Absolute path, normalized to '/' separators
        type (str): 'F' file, 'D' directory, 'S' symlink,
            '' when the root is missing at baseline
        mtime_ns (int): Modification time in nanoseconds
        size (int): File size in bytes
    """
    event: _EventType
    path: str
    type: _RootType = ''
    mtime_ns: int = 0
    size: int = 0

    @classmethod
    def from_record(cls, event, record, path=None):
        """
        Build an event from a WatchRecord.

        Args:
            event (str): 'A', 'M', or 'D'
            record (WatchRecord): Record to read
            path (str): Optional event path, defaults to record.path

        Returns:
            WatchEvent:
        """
        if path is None:
            path = record.path
        return cls(
            event, path, record.type, record.mtime_ns, record.size
        )


class _Watch(Struct, omit_defaults=True):
    path: str
    recursive: bool
    # False until the first update that observed this subscription. The
    # first observation becomes the baseline and only reports the root.
    initialized: bool = False


class _DirScan(Struct, frozen=True, omit_defaults=True):
    """
    Result of reading one directory.

    Attributes:
        status (str): 'ok', 'missing', 'not_directory', or 'incomplete'
        records (list[WatchRecord]): Direct children read successfully
        failed (dict): path -> error for children whose type could not
            be read; callers preserve their old records
        error (object): First error, None when every entry was read
    """
    status: str
    records: List[WatchRecord] = field(default_factory=list)
    failed: Dict[str, object] = field(default_factory=dict)
    error: object = None


def _normalize_path(filepath):
    """
    Validate and normalize an absolute subscription path.

    Args:
        filepath (str): Absolute path to normalize

    Returns:
        str: Path with '/' separators

    Raises:
        TypeError: When filepath is not a string
        ValueError: When filepath is not absolute
    """
    filepath = os.fspath(filepath)
    if not isinstance(filepath, str):
        raise TypeError(f'Expected a str path, got: {type(filepath).__name__}')
    if not os.path.isabs(filepath):
        raise ValueError(f'Expected an absolute path, got: {filepath!r}')
    return normpath(filepath)


def _covers(watch, path):
    """
    Check whether one subscription covers a path.

    Args:
        watch (_Watch): Subscription to test
        path (str): Normalized absolute path

    Returns:
        bool: True when watch observes path
    """
    if path == watch.path:
        return True
    if watch.recursive:
        return path.startswith(
            '/' if watch.path == '/' else watch.path + '/'
        )
    parent, sep, _ = path.rpartition('/')
    return bool(sep) and (parent or '/') == watch.path


def _stat(path):
    """
    Stat a path, following symlinks.

    Args:
        path (str): Absolute path

    Returns:
        WatchRecord | object | None: Record, _MISSING, or None on an
            unreadable path
    """
    try:
        return WatchRecord.from_stat(path, os.stat(path))
    except (FileNotFoundError, NotADirectoryError):
        return _MISSING
    except OSError:
        return None


def _lstat(path):
    """
    Lstat a path without following symlinks.

    Args:
        path (str): Absolute path

    Returns:
        WatchRecord | object | None: Record, _MISSING, or None on an
            unreadable path
    """
    try:
        return WatchRecord.from_stat(path, os.lstat(path))
    except (FileNotFoundError, NotADirectoryError):
        return _MISSING
    except OSError:
        return None


def _list_dir(path):
    """
    Read one directory without caching.

    Args:
        path (str): Absolute directory path

    Returns:
        _DirScan: Complete or partial direct children, or a directory
            level status such as missing or permission denied
    """
    records = []
    failed = {}
    error = None
    status = _SCAN_OK
    try:
        with os.scandir(path) as scan:
            for entry in scan:
                full = joinpath(path, entry.name)
                try:
                    if entry.is_symlink():
                        path_type = 'S'
                    elif entry.is_dir(follow_symlinks=False):
                        path_type = 'D'
                    elif entry.is_file(follow_symlinks=False):
                        path_type = 'F'
                    else:
                        st = entry.stat(follow_symlinks=False)
                        path_type = WatchRecord.from_stat(full, st).type
                except FileNotFoundError:
                    continue
                except OSError as e:
                    failed[full] = e
                    if error is None:
                        error = e
                    continue
                records.append(WatchRecord(full, path_type))
    except FileNotFoundError:
        status = _SCAN_MISSING
    except NotADirectoryError:
        status = _SCAN_NOT_DIRECTORY
    except OSError as e:
        status = _SCAN_INCOMPLETE
        error = e
    return _DirScan(status, records, failed, error)


def _change_events(old, new):
    """
    Compare one old record with one new record.

    Args:
        old (WatchRecord | None): Previous record
        new (WatchRecord): Current record

    Yields:
        WatchEvent: Added, deleted, or modified events
    """
    if old is None:
        yield WatchEvent.from_record('A', new)
        return
    if old.type != new.type:
        yield WatchEvent.from_record('D', old)
        yield WatchEvent.from_record('A', new)
        return
    if (new.type == 'F' and new.mtime_ns
            and (old.mtime_ns, old.size, old.ino) != (
                new.mtime_ns, new.size, new.ino)):
        yield WatchEvent.from_record('M', new)


def _preserve(old, new, path, keep_root=False):
    """
    Copy the old records of one path from old to new.

    Args:
        old (dict): Previous path -> WatchRecord map
        new (dict): Current path -> WatchRecord map
        path (str): Root of the subtree to preserve
        keep_root (bool): True to keep a root already observed in new
    """
    prefix = path + '/' if path != '/' else '/'
    for key, record in old.items():
        if key == path and keep_root and path in new:
            continue
        if key == path or key.startswith(prefix):
            new[key] = record


def _event_sort_key(event):
    """
    Sort events by path, then by delete / add / modify.

    Args:
        event (WatchEvent): Event to sort

    Returns:
        tuple: Sort key
    """
    return event.path, _EVENT_ORDER[event.event], event.type


class PathWatcher:
    """
    Polling filesystem watcher with explicit subscriptions and updates.

    Subscriptions only change in-memory state. The caller decides when to
    scan by calling update(); this class never sleeps and never starts a
    background thread.
    """

    def __init__(self):
        self._watches: Dict[str, _Watch] = {}
        self._records: Dict[str, WatchRecord] = {}
        self._marked: Dict[str, int] = {}
        self._lock = Lock()

    def subscribe(self, filepath, recursive=False):
        """
        Track a file or directory. Sync, memory only, no filesystem IO.

        Args:
            filepath (str): Absolute path to track
            recursive (bool): Watch the whole subtree of a directory.
                Ignored for file paths.
        """
        filepath = _normalize_path(filepath)
        recursive = bool(recursive)
        with self._lock:
            watch = self._watches.get(filepath)
            if watch is not None and watch.recursive == recursive:
                return
            if watch is not None:
                self._watches.pop(filepath)
                self._release(watch)
            # A new subscription starts a new baseline.
            self._watches[filepath] = _Watch(filepath, recursive)

    def unsubscribe(self, filepath):
        """
        Stop tracking a path. Sync, memory only, no filesystem IO.
        Idempotent. Never produces an event by itself.

        Args:
            filepath (str): Absolute path to stop tracking
        """
        filepath = _normalize_path(filepath)
        with self._lock:
            watch = self._watches.pop(filepath, None)
            self._marked.pop(filepath, None)
            if watch is not None:
                self._release(watch)

    def _release(self, watch):
        """
        Drop records no longer covered by any subscription.

        Args:
            watch (_Watch): Subscription that was removed
        """
        remaining = list(self._watches.values())
        self._records = {
            path: record for path, record in self._records.items()
            if not _covers(watch, path)
            or any(
                _covers(other, path)
                for other in remaining
            )
        }

    def update(self, force=False):
        """
        Scan every subscription and diff against the previous records.

        Args:
            force (bool): Kept for API compatibility. Scans always run.

        Returns:
            list[WatchEvent]: Added / modified / deleted paths,
                sorted by path; 'D' before 'A' on the same path.
        """
        with self._lock:
            old = self._records
            new = {}
            events = []
            # Recursive subscriptions first, then the deepest roots, so
            # a file root contributes its full signature before an
            # ancestor's type-only entry. The `new` check skips paths
            # already handled by an earlier subscription.
            watches = sorted(
                self._watches.values(),
                key=lambda watch: (
                    not watch.recursive, -len(watch.path)
                ),
            )
            for watch in watches:
                events.extend(self._update_watch(old, new, watch))
            for path, record in old.items():
                if path not in new:
                    events.append(
                        WatchEvent.from_record('D', record)
                    )

            result = []
            consumed = set()
            events_map = {}
            for event in events:
                key = (event.event, event.path, event.type)
                previous = events_map.get(key)
                if (previous is None
                        or (event.mtime_ns, event.size)
                        > (previous.mtime_ns, previous.size)):
                    events_map[key] = event
            for event in events_map.values():
                mark_time_ns = self._marked.get(event.path)
                if mark_time_ns is not None:
                    if event.event in ('A', 'M'):
                        consumed.add(event.path)
                        if event.mtime_ns <= mark_time_ns + _MARK_GRACE_NS:
                            continue
                    elif event.event == 'D':
                        consumed.add(event.path)
                result.append(event)
            for path in consumed:
                self._marked.pop(path, None)
            self._records = new
        return sorted(result, key=_event_sort_key)

    def list(self):
        """
        Return the records known from the last update. Does not scan the
        filesystem.

        Returns:
            list[WatchRecord]: Every known record, sorted by path
        """
        with self._lock:
            records = list(self._records.values())
        return sorted(records, key=lambda record: record.path)

    def mark_modified(self, filepath):
        """
        Mark a path written by us so changes not newer than the mark
        are suppressed. Sync and memory only; no filesystem IO.

        Args:
            filepath (str): Absolute path that was written by us
        """
        filepath = _normalize_path(filepath)
        with self._lock:
            self._marked[filepath] = time.time_ns()

    def _update_watch(self, old, new, watch):
        """
        Scan one subscription and update the new record map.

        Args:
            old (dict): Previous path -> WatchRecord map
            new (dict): Current path -> WatchRecord map
            watch (_Watch): Subscription to scan

        Yields:
            WatchEvent: Changes observed by this subscription
        """
        root = watch.path
        first = not watch.initialized
        old_root = old.get(root)
        current = new.get(root)

        if current is not None:
            # Another subscription already scanned this root. Still
            # report the root on the first observation for baseline.
            if first:
                yield WatchEvent.from_record('A', current)
                watch.initialized = True
            return

        if old_root is not None and old_root.type != 'D':
            record = _stat(root)
            if record is _MISSING:
                if first and old_root is None:
                    yield WatchEvent('D', root, '', 0, 0)
                if first:
                    watch.initialized = True
                return
            if record is None:
                new[root] = old_root
                return
            if record.type != 'D':
                if not first:
                    yield from _change_events(old_root, record)
                new[root] = record
                watch.initialized = True
                if first:
                    yield WatchEvent.from_record('A', record)
                return
            if not first:
                yield from _change_events(old_root, record)
            new[root] = record

        # The root is a directory, or its current type is not known yet.
        stack = [root]
        root_state = None
        while stack:
            dir_path = stack.pop()
            scan = _list_dir(dir_path)
            if scan.status == _SCAN_INCOMPLETE:
                # Do not retry in the same update. The next update is
                # the retry point, after the caller has waited.
                _preserve(old, new, dir_path, keep_root=True)
                if dir_path == root:
                    return
                continue
            if scan.status == _SCAN_MISSING:
                new.pop(dir_path, None)
                if dir_path == root:
                    root_state = 'missing'
                    break
                continue
            if scan.status == _SCAN_NOT_DIRECTORY:
                record = _stat(dir_path)
                if record is _MISSING:
                    new.pop(dir_path, None)
                    if dir_path == root:
                        root_state = 'missing'
                        break
                    continue
                if record is None or record.type == 'D':
                    _preserve(old, new, dir_path, keep_root=True)
                    if dir_path == root:
                        return
                    continue
                old_record = old.get(dir_path)
                existing = new.get(dir_path)
                if existing is None or existing.type == 'D':
                    if not first:
                        yield from _change_events(old_record, record)
                new[dir_path] = record
                if dir_path == root:
                    root_state = 'record'
                continue

            if dir_path not in new:
                old_record = old.get(dir_path)
                if old_record is not None and old_record.type == 'D':
                    record = old_record
                else:
                    record = WatchRecord(dir_path, 'D')
                if not first:
                    yield from _change_events(old_record, record)
                new[dir_path] = record
            if dir_path == root:
                root_state = 'record'

            for child in scan.records:
                if child.path in new:
                    # Another subscription already handled this path
                    # and, if it is a directory, its subtree.
                    continue
                if watch.recursive and child.type == 'F':
                    record = _lstat(child.path)
                    if record is _MISSING:
                        continue
                    if record is None:
                        _preserve(old, new, child.path)
                        continue
                    child = record
                old_record = old.get(child.path)
                if not first:
                    yield from _change_events(old_record, child)
                new[child.path] = child
                if watch.recursive and child.type == 'D':
                    stack.append(child.path)
            for path in scan.failed:
                _preserve(old, new, path)

        if first:
            record = new.get(root)
            if record is not None:
                yield WatchEvent.from_record('A', record)
            elif root_state == 'missing' and old_root is None:
                yield WatchEvent('D', root, '', 0, 0)
            elif root_state != 'missing':
                return
            watch.initialized = True
        elif root_state is not None:
            watch.initialized = True
