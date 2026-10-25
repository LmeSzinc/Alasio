import os
import stat
import time
from threading import Lock
from typing import Dict, List, Literal

from msgspec import Struct, field

from alasio.ext.path.calc import normpath, subpath_to

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

# Filesystem timestamp rounding tolerance for marks. A mark is taken
# after our write, so a slightly later mtime can still be ours.
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
        mtime_ns (int): Modification time in nanoseconds
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
        return cls(event, path, record.type, record.mtime_ns, record.size)


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
        entries (dict): path -> os.DirEntry for non-directory entries,
            used to stat the file after the caller decides the path is
            not already handled by another subscription
    """
    status: str
    records: List[WatchRecord] = field(default_factory=list)
    failed: Dict[str, object] = field(default_factory=dict)
    error: object = None
    entries: Dict[str, object] = field(default_factory=dict)


def _normalize_path(filepath):
    """
    Validate and normalize an absolute subscription path.

    Args:
        filepath (str): Absolute path to normalize

    Returns:
        str: Path with '/' separators. A drive root keeps its trailing
            '/', so the result stays absolute.

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


def _subtree_prefix(path):
    """
    Get the prefix shared by every descendant of a path.

    Args:
        path (str): Normalized absolute directory path

    Returns:
        str: Path ending with '/', also for the POSIX root and drive roots
    """
    return path if path.endswith('/') else path + '/'


def _covers(watch, path):
    """
    Check whether one subscription covers a path.

    Args:
        watch (_Watch): Subscription to test
        path (str): Normalized absolute path

    Returns:
        bool: True when watch observes path
    """
    rest = subpath_to(path, watch.path)
    if rest is None:
        return False
    if watch.recursive:
        return True
    # A non-recursive root covers itself and its direct children only.
    return '/' not in rest


def _lstat(path):
    """
    Stat a path without following symlinks.

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

    Regular file and special entries are type-only; their DirEntry is
    kept in `entries`, and the caller stats it after deciding whether
    the path is already handled by another subscription. Directory and
    symlink entries are type-only and have no entry.

    Args:
        path (str): Absolute directory path

    Returns:
        _DirScan: Complete or partial direct children, or a directory
            level status such as missing or permission denied
    """
    records = []
    entries = {}
    failed = {}
    error = None
    status = _SCAN_OK
    prefix = _subtree_prefix(path)
    try:
        with os.scandir(path) as scan:
            for entry in scan:
                full = prefix + entry.name
                try:
                    if entry.is_symlink():
                        record = WatchRecord(full, 'S')
                    elif entry.is_dir(follow_symlinks=False):
                        record = WatchRecord(full, 'D')
                    else:
                        # Type-only here, the caller stats the entry after
                        # checking whether another subscription handled it.
                        record = WatchRecord(full, 'F')
                        entries[full] = entry
                except FileNotFoundError:
                    continue
                except OSError as e:
                    failed[full] = e
                    if error is None:
                        error = e
                    continue
                records.append(record)
    except FileNotFoundError:
        status = _SCAN_MISSING
    except NotADirectoryError:
        status = _SCAN_NOT_DIRECTORY
    except OSError as e:
        status = _SCAN_INCOMPLETE
        error = e
    return _DirScan(status, records, failed, error, entries)


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
    if new.type == 'D' or not new.mtime_ns:
        return
    if old.mtime_ns == new.mtime_ns and old.size == new.size and old.ino == new.ino:
        return
    yield WatchEvent.from_record('M', new)


def _preserve(old, new, path):
    """
    Fill records missing from new with the old records of one path.

    Existing new records are kept because they were read later and are
    fresher than the preserved ones.

    Args:
        old (dict): Previous path -> WatchRecord map
        new (dict): Current path -> WatchRecord map
        path (str): Root of the subtree to preserve
    """
    prefix = _subtree_prefix(path)
    for key, record in old.items():
        if key == path or key.startswith(prefix):
            new.setdefault(key, record)


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
        self._marks_lock = Lock()
        self._lock = Lock()

    def subscribe(self, filepath, recursive=False):
        """
        Track a file or directory. Sync, memory only, no filesystem IO.

        File entries are compared by modification time, size and inode,
        so a modified file is reported in both modes. A symlink is an
        entry of its own and is never followed. Directory entries are
        compared by type only. The first observation uses lstat, so a
        file or symlink root does not pay for a directory scan.

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
            if not _covers(watch, path) or any(_covers(other, path) for other in remaining)
        }

    def update(self):
        """
        Scan every subscription and diff against the previous records.

        Returns:
            list[WatchEvent]: Added / modified / deleted paths,
                sorted by path; 'D' before 'A' on the same path.
        """
        with self._lock:
            old = self._records
            new = {}
            events = []
            with self._marks_lock:
                start_marks = self._marked.copy()
            # Subscriptions that already had a baseline before this update.
            # A path covered by one of them still needs its diff even when a
            # new subscription recorded that path first.
            initialized = [
                watch for watch in self._watches.values() if watch.initialized
            ]
            # Recursive subscriptions first, then the deepest roots, so a
            # recursive scan covers whole subtrees before shallower roots,
            # and a deeper non-recursive root contributes its full file
            # signature before an ancestor scans the same path. The `new`
            # check skips paths already handled by an earlier subscription.
            watches = sorted(
                self._watches.values(),
                key=lambda w: (not w.recursive, -len(w.path)),
            )
            for watch in watches:
                events.extend(self._update_watch(old, new, watch))
            # Diff every path covered by a subscription that was already
            # initialized before this update. Paths recorded only by a new
            # subscription are its baseline and stay silent.
            if initialized:
                # In the steady state every subscription is initialized,
                # so every record in new was produced by one of them and
                # needs no coverage check.
                check_coverage = len(initialized) < len(watches)
                for path, record in new.items():
                    if check_coverage and not any(_covers(watch, path) for watch in initialized):
                        continue
                    old_record = old.get(path)
                    if old_record == record:
                        continue
                    events.extend(_change_events(old_record, record))
            for path, record in old.items():
                if path not in new:
                    events.append(WatchEvent.from_record('D', record))

            events_map = {}
            for event in events:
                key = (event.event, event.path, event.type)
                previous = events_map.get(key)
                if previous is None or (event.mtime_ns, event.size) > (previous.mtime_ns, previous.size):
                    events_map[key] = event
            with self._marks_lock:
                end_marks = self._marked
                self._marked = {}
            if end_marks:
                event_paths = {event.path for event in events_map.values()}
                suppressed = set()
                for path, mark in end_marks.items():
                    record = new.get(path)
                    if record is None:
                        # A path deleted by us has no record to compare.
                        silence = True
                    elif record.type == 'F':
                        silence = record.mtime_ns <= mark + _MARK_GRACE_NS
                    else:
                        # A type change is not ours to hide.
                        silence = False
                    if silence and path in event_paths:
                        suppressed.add(path)
                if suppressed:
                    events_map = {
                        key: event for key, event in events_map.items()
                        if event.path not in suppressed
                    }
                # Equal snapshots mean no mark was created during the
                # scan; every mark is a one-shot pre-scan mark.
                if start_marks != end_marks:
                    # A mark created while this update was scanning stays
                    # for one more update: the scan may have passed the path
                    # before the write became visible.
                    kept = {
                        path: mark for path, mark in end_marks.items()
                        if start_marks.get(path) != mark
                    }
                    if kept:
                        with self._marks_lock:
                            for path, mark in kept.items():
                                self._marked.setdefault(path, mark)
            result = list(events_map.values())
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
        Mark a path written, created, or deleted by us.

        Call it after the operation completes and before the next
        update(). The next update() silences every event for the path
        when the path is missing, or when it is still a file whose mtime
        is not newer than this mark. A path that is now a directory or
        symlink is reported. Sync and memory only; no filesystem IO.

        Args:
            filepath (str): Absolute path that was written by us
        """
        filepath = _normalize_path(filepath)
        with self._marks_lock:
            self._marked[filepath] = time.time_ns()

    @staticmethod
    def _update_children(old, new, watch, scan, stack):
        """
        Apply one successfully listed directory to the new records.

        Change events are not produced here; update() diffs the finished
        new record map against the old one.

        Args:
            old (dict): Previous path -> WatchRecord map
            new (dict): Current path -> WatchRecord map
            watch (_Watch): Subscription being scanned
            scan (_DirScan): Successful directory listing
            stack (list[str]): Pending child directories
        """
        for child in scan.records:
            if child.path in new:
                # Another subscription already handled this path
                # and, if it is a directory, its subtree.
                continue
            if child.type == 'F' and child.path in scan.entries:
                # The entry is not handled yet, stat it now so its
                # mtime/size are available for the diff.
                entry = scan.entries[child.path]
                try:
                    st = entry.stat(follow_symlinks=False)
                    child = WatchRecord.from_stat(child.path, st)
                except FileNotFoundError:
                    continue
                except OSError:
                    _preserve(old, new, child.path)
                    continue
            new[child.path] = child
            if watch.recursive and child.type == 'D':
                stack.append(child.path)
        for path in scan.failed:
            _preserve(old, new, path)

    def _update_watch(self, old, new, watch):
        """
        Scan one subscription and update the new record map.

        Args:
            old (dict): Previous path -> WatchRecord map
            new (dict): Current path -> WatchRecord map
            watch (_Watch): Subscription to scan

        Yields:
            WatchEvent: Baseline root events; change events are diffed
                by update()
        """
        root = watch.path
        first = not watch.initialized
        old_root = old.get(root)
        current = new.get(root)

        if current is not None:
            # Another subscription already scanned this root. Only the
            # baseline event belongs to this subscription; the diff is
            # done centrally in update().
            if first:
                yield WatchEvent.from_record('A', current)
                watch.initialized = True
            return

        # Resolve the root without following symlinks. A symlink is a
        # single record of its own, even when it points to a directory.
        record = _lstat(root)
        if record is _MISSING:
            if first:
                if old_root is None:
                    yield WatchEvent('D', root, '', 0, 0)
                watch.initialized = True
            else:
                new.pop(root, None)
            return
        if record is None:
            if old_root is not None:
                _preserve(old, new, root)
            return
        if record.type != 'D':
            new[root] = record
            watch.initialized = True
            if first:
                yield WatchEvent.from_record('A', record)
            return

        # Directory root: keep the old directory payload when there is
        # one, then scan the children. The root entry itself is already
        # resolved, so a child scan never has to compare with it.
        if old_root is None:
            new[root] = WatchRecord(root, 'D')
        elif old_root.type == 'D':
            new[root] = old_root
        else:
            new[root] = record

        # Root step: resolve root-level errors before traversing any
        # child directory. Child errors no longer need to compare with
        # the subscription root.
        stack = []
        root_state = None
        scan = _list_dir(root)
        if scan.status == _SCAN_INCOMPLETE:
            # Do not retry in the same update. The next update is
            # the retry point, after the caller has waited.
            _preserve(old, new, root)
            return
        if scan.status == _SCAN_MISSING:
            new.pop(root, None)
            root_state = 'missing'
        elif scan.status == _SCAN_NOT_DIRECTORY:
            record = _lstat(root)
            if record is _MISSING:
                new.pop(root, None)
                root_state = 'missing'
            elif record is None or record.type == 'D':
                _preserve(old, new, root)
                return
            else:
                new[root] = record
                root_state = 'record'
        else:
            root_state = 'record'
            self._update_children(old, new, watch, scan, stack)

        while stack:
            dir_path = stack.pop()
            scan = _list_dir(dir_path)
            if scan.status == _SCAN_INCOMPLETE:
                _preserve(old, new, dir_path)
                continue
            if scan.status == _SCAN_MISSING:
                new.pop(dir_path, None)
                continue
            if scan.status == _SCAN_NOT_DIRECTORY:
                record = _lstat(dir_path)
                if record is _MISSING:
                    new.pop(dir_path, None)
                    continue
                if record is None or record.type == 'D':
                    _preserve(old, new, dir_path)
                    continue
                new[dir_path] = record
                continue
            self._update_children(old, new, watch, scan, stack)

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
