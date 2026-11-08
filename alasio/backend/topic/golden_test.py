"""
Golden test review: the test tree of a mod, and one parsed test file.

The review page of the dev tools shows the golden tests of a mod the way VS Code
shows a folder: a lazily loaded tree on the left, the full source of the
selected test file in the middle and the screenshots bound by `@image` on the
right.

Two topics carry the data, so a change of the tree and a change of the file are
independent updates:

- GoldenTestTree: the children of the folders the page subscribed. A folder is
  read when the page subscribes it, never as a recursive walk of `tests/`.
- GoldenTestFile: the full source of the selected test file and its image
  bindings, parsed by alasio.testing.preview.parse_unittest (an `ast` read, the
  test module is never imported).

Both topics belong to the one desktop client the backend serves, so each of them
has exactly ONE source instance for the whole process: a new subscriber -- a
reconnect, a second window -- subscribes the view that is already open instead of
rebuilding an equivalent one. Switching the mod or the file changes the
subscription of that instance, never the instance itself.

The filesystem watching belongs to the sources, the way the cache sources own
their full and incremental data: the two sources share the one `PathWatcher` of
the review page, each declares the paths it needs (`watch_paths`) and releases
them when it loses its last subscriber. The poll loop of the lifespan scans the
shared watcher once and hands every event to both sources, which convert the
events of their own paths into the responses of their topic.

The topics require an electron connection, at subscription time and for every
RPC: the state of the view is process wide, so a remote connection must never
read it.
"""
from typing import Dict, List

import trio
from msgspec import Struct

from alasio.backend.reactive.base_rpc import rpc
from alasio.backend.reactive.event import ResponseEvent, RpcValueError
from alasio.backend.reactive.source import GlobalSource, NoCachePush
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.entry.loader import MOD_LOADER
from alasio.ext.cache import InstanceCacheOperation, cached_property
from alasio.ext.file.watcher import PathWatcher, WatchEvent
from alasio.ext.path import PathStr
from alasio.ext.path.calc import joinnormpath
from alasio.ext.path.validate import validate_filepath, validate_resolve_filepath
from alasio.logger import logger
from alasio.testing.preview.parse_unittest import ParsedUnittest, UnittestImage, parse_test_file
from alasio.testing.project import COLLECTION

# Folder of the tests, relative to the mod root
TESTS = 'tests'
# Files of the tree: pytest collects test_*.py
TEST_FILE_PREFIX = 'test_'
TEST_FILE_SUFFIX = '.py'
# Folders that are never part of the tree. Every folder starting with '.' (the
# caches of the tools, '.git') is skipped as well
SKIP_DIR_NAME = frozenset((
    '__pycache__',
    'node_modules',
))
# Development tool: 2 seconds is responsive enough
WATCH_INTERVAL = 2.0


class GoldenTestEntry(Struct, omit_defaults=True):
    """
    One child of a folder of the tree

    Attributes:
        name (str): Name of the entry
        path (str): Path relative to `tests/`, POSIX separators
        is_dir (bool): True for a folder, False for a test file
    """
    name: str
    path: str
    is_dir: bool = False


class GoldenTestTreeData(Struct, omit_defaults=True):
    """
    The loaded part of the test tree

    Attributes:
        mod_name (str): Browsed mod, empty for the initial view
        dirs (Dict[str, List[GoldenTestEntry]]): Subscribed folders, key is the
            folder path relative to `tests/`, '' is the tests root. The value
            holds the immediate children only.
    """
    mod_name: str = ''
    dirs: Dict[str, List[GoldenTestEntry]] = {}


class GoldenTestFileData(Struct, omit_defaults=True):
    """
    The selected test file

    Attributes:
        mod_name (str): Mod of the file, empty for the initial view
        file (str): Path relative to `tests/`, empty when nothing is selected
        source (str): Full source of the file, highlighted by the frontend
        images (List[UnittestImage]): Image bindings in source order
        errors (List[str]): File level errors, e.g. a syntax error
    """
    mod_name: str = ''
    file: str = ''
    source: str = ''
    images: List[UnittestImage] = []
    errors: List[str] = []


class GoldenTestPathError(Exception):
    """
    Raised when a requested path is not a readable test file of the mod
    """

    def __init__(self, message):
        """
        Args:
            message (str): Reason, reported to the frontend as is
        """
        super().__init__(message)
        self.message = message


def is_test_file(name):
    """
    Check whether a file name is a test file of the tree

    Args:
        name (str): Name of the file

    Returns:
        bool: True for `test_*.py`
    """
    return name.startswith(TEST_FILE_PREFIX) and name.endswith(TEST_FILE_SUFFIX)


def is_skipped_dir(name):
    """
    Check whether a folder is left out of the tree

    Args:
        name (str): Name of the folder

    Returns:
        bool: True for the hidden and the cache folders
    """
    return name.startswith('.') or name in SKIP_DIR_NAME


def _entry_sort_key(entry):
    """
    Sort the children of a folder: folders first, then by name

    Args:
        entry (GoldenTestEntry): Entry to sort

    Returns:
        tuple: Sort key
    """
    return not entry.is_dir, entry.name.casefold(), entry.name


def snapshot_dirs(records, folders):
    """
    Build the children of every subscribed folder from a watcher snapshot

    Only the immediate children of a folder are reported: the watcher scans a
    folder one level deep, so a record belongs to the folder that is its parent.
    Symbolic links and other special entries are not part of the tree.

    Args:
        records (list[WatchRecord]): Records of the last PathWatcher.update()
        folders (Dict[str, str]): Absolute folder path -> path relative to
            `tests/`

    Returns:
        Tuple[Dict[str, List[GoldenTestEntry]], set[str]]: Children by relative
            folder path, and the relative paths of the folders that are missing
            from the snapshot.
    """
    entries = {rel: [] for rel in folders.values()}
    found = set()
    for record in records:
        path = record.path
        rel = folders.get(path)
        if rel is not None and record.type == 'D':
            # A subscribed folder that still is a folder. It may also be the
            # child of another subscribed folder, which is handled below
            found.add(rel)

        parent, _, name = path.rpartition('/')
        parent_rel = folders.get(parent)
        if parent_rel is None:
            # A grandchild, a subscribed root, or a path of another subscription
            continue
        if record.type == 'D':
            if is_skipped_dir(name):
                continue
            is_dir = True
        elif record.type == 'F':
            if not is_test_file(name):
                continue
            is_dir = False
        else:
            continue

        entries[parent_rel].append(GoldenTestEntry(
            name=name,
            path=f'{parent_rel}/{name}' if parent_rel else name,
            is_dir=is_dir,
        ))

    for rows in entries.values():
        rows.sort(key=_entry_sort_key)
    missing = {rel for rel in folders.values() if rel not in found}
    return entries, missing


def _mod_root(mod_name):
    """
    Resolve the root of a mod, which is the project root of its tests

    Args:
        mod_name (str): Name of the mod

    Returns:
        PathStr: Absolute path of the mod root

    Raises:
        GoldenTestPathError: If there is no such mod
    """
    try:
        mod = MOD_LOADER.dict_mod[mod_name]
    except KeyError:
        raise GoldenTestPathError(f'No such mod: "{mod_name}"') from None
    return PathStr.new(mod.root)


def resolve_test_file(mod_name, file):
    """
    Resolve a test file of a mod to its absolute path

    The path of the request is relative to `tests/`, and
    validate_resolve_filepath() rejects an absolute path, a `..` segment, a
    reserved name and a path that resolves outside the tests folder (through a
    symbolic link as well).

    Args:
        mod_name (str): Name of the mod
        file (str): Path relative to `tests/`

    Returns:
        str: Safe absolute path of the test file, symbolic links resolved

    Raises:
        GoldenTestPathError: If the path is invalid or is not a python test file
    """
    if not is_test_file(file.rpartition('/')[2]):
        raise GoldenTestPathError(f'Not a test file: "{file}"')
    try:
        return validate_resolve_filepath(_mod_root(mod_name).joinpath(TESTS), file)
    except ValueError as e:
        raise GoldenTestPathError(str(e)) from None


def resolve_collection_image(mod_name, image_path):
    """
    Resolve a screenshot of a mod to its absolute path

    The path of the request is relative to `<mod root>/collection`, the folder
    the `image` decorator of alasio.testing.screenshot reads a test screenshot
    from, and validate_resolve_filepath() rejects an absolute path, a `..`
    segment and a path that resolves outside the folder (through a symbolic
    link as well).

    Args:
        mod_name (str): Name of the mod
        image_path (str): Path relative to `<mod root>/collection`

    Returns:
        str: Safe absolute path of the screenshot, symbolic links resolved

    Raises:
        GoldenTestPathError: If the path is invalid or there is no such mod
    """
    if not image_path:
        raise GoldenTestPathError('Image path cannot be empty')
    collection = _mod_root(mod_name).joinpath(COLLECTION)
    try:
        return validate_resolve_filepath(collection, image_path)
    except ValueError as e:
        raise GoldenTestPathError(str(e)) from None


class GoldenTestPathWatcher(PathWatcher):
    """
    The watcher of the review page, remembering when it was last scanned.

    At most one scan per `WATCH_INTERVAL`: a scan made by an RPC (a folder
    subscribe, a file select) counts as the poll of that window, so the loop
    waits for the rest of it instead of scanning the same paths again.
    """

    def __init__(self):
        super().__init__()
        # Trio clock of the last scan, 0 before the first one
        self.last_update = 0.0

    def scan_delay(self, interval):
        """
        Seconds left before the next scan of this watcher is due

        Args:
            interval (float): Length of one scan window in seconds

        Returns:
            float: The rest of the window of the last scan, 0 when it is over
        """
        return max(0.0, self.last_update + interval - trio.current_time())

    def mark_scanned(self):
        """
        [Trio] Record that a scan just finished
        """
        self.last_update = trio.current_time()


class GoldenTestWatchSource(NoCachePush):
    """
    Base of the two sources of the review page: the filesystem watching.

    The two sources share the one `WATCHER` of the page. A source declares what
    it needs (`watch_paths`), takes the paths when its state changes
    (`_sync_paths`) and releases them when the last subscriber leaves
    (`_remove`), so a closed page stops the scanning.

    The view state stays on the source: removing it from the registry would drop
    the browsed mod and the expanded folders, and a reconnect would have to
    select them again.
    """

    # The watcher of the review page, shared by both sources
    WATCHER = GoldenTestPathWatcher()
    # No topic of its own: the name only keeps the class-definition check of
    # the source framework quiet, the concrete sources declare theirs
    TOPIC_NAME = 'GoldenTestWatch'

    def __init__(self):
        super().__init__()
        # Absolute paths this source has subscribed to the shared watcher
        self._paths = frozenset()

    def watch_paths(self):
        """
        The absolute paths this source needs watched

        Returns:
            list[str]: Paths to subscribe on the shared watcher
        """
        raise NotImplementedError

    def _sync_paths(self):
        """
        [Trio] Take the paths of the current state, release the ones it left
        """
        new = frozenset(self.watch_paths())
        for path in new - self._paths:
            self.WATCHER.subscribe(path)
        for path in self._paths - new:
            # The path of one source is never the path of the other one: a
            # folder and a test file are different paths
            self.WATCHER.unsubscribe(path)
        self._paths = new

    def _remove(self):
        """
        [Trio] The last subscriber left: release the paths, keep the view

        The scanning stops here; the state stays so the next subscriber -- a
        reconnect -- subscribes the view that was open.
        """
        for path in self._paths:
            self.WATCHER.unsubscribe(path)
        self._paths = frozenset()

    @classmethod
    async def scan(cls):
        """
        [Trio] Scan the shared watcher once, in a worker thread

        The scan time is recorded on the watcher, so every caller -- an RPC or
        the poll loop -- shares the one scan window.

        Returns:
            list[WatchEvent]: Added / modified / deleted paths
        """
        events = await trio.to_thread.run_sync(cls.WATCHER.update)
        cls.WATCHER.mark_scanned()
        return events


async def poll_once():
    """
    [Trio] One poll: scan the shared watcher once, hand every event to both
    sources.

    Without a subscriber there is nothing to push, and the scan is skipped: no
    worker thread is started for a page nobody is looking at.

    Returns:
        bool: True when the watcher was scanned
    """
    sources = [
        source for source in (
            GoldenTestTreeSource.singleton_instance(),
            GoldenTestFileSource.singleton_instance(),
        )
        if source is not None and source._subscribers
    ]
    if not sources:
        return False

    events = await GoldenTestWatchSource.scan()
    if events:
        for source in sources:
            try:
                await source.watch_apply(events)
            except Exception:
                logger.exception(f'GoldenTest: watch apply failed: {source!r}')
    return True


async def watch_loop():
    """
    [Trio] The lifespan loop of the review page, see app.py

    The next scan of the shared watcher belongs to a window of `WATCH_INTERVAL`
    after the last one, no matter who ran it: when an RPC already scanned while
    the loop was asleep (a folder subscribe, a file select), the loop waits for
    the rest of that window instead of scanning the same paths again.
    """
    while True:
        # A scan made by an RPC while the loop was asleep (a folder subscribe, a
        # file select) counts as the poll of that window: wait for its rest
        remaining = GoldenTestWatchSource.WATCHER.scan_delay(WATCH_INTERVAL)
        if remaining > 0:
            await trio.sleep(remaining)
        try:
            scanned = await poll_once()
        except Exception:
            # A dev tool must never take the backend lifespan down
            logger.exception('GoldenTest: watch poll failed')
            scanned = False
        if not scanned:
            # Nothing is watched: look again in one window, no busy loop
            await trio.sleep(WATCH_INTERVAL)


class GoldenTestTreeSource(GoldenTestWatchSource, GlobalSource):
    """
    The one test tree of the review page, shared by every subscriber.

    `mod_name` is the browsed mod and `dirs` the folders the page subscribed
    (`''` is the tests root); `_abs_dirs` maps their absolute path back to the
    relative one, so converting a watch event needs one dict lookup.

    The empty view (no mod name) reads nothing: it is the view before the page
    selects a mod, and the view after it clears the selection.
    """
    TOPIC_NAME = 'GoldenTestTree'

    def __init__(self):
        super().__init__()
        self.mod_name = ''
        # Subscribed folders: path relative to tests/ -> immediate children
        self.dirs: Dict[str, List[GoldenTestEntry]] = {}
        # Absolute folder path -> relative path, built by _sync_paths()
        self._abs_dirs: Dict[str, str] = {}

    @cached_property
    def tests_root(self):
        """
        Absolute path of the tests folder of the browsed mod, resolved once

        The cache is dropped by set_mod(): a source browses one mod at a time.

        Returns:
            str: Absolute path of `<mod root>/tests`

        Raises:
            GoldenTestPathError: If no mod is browsed, or there is no such mod
        """
        return str(_mod_root(self.mod_name).joinpath(TESTS))

    def _abs(self, rel):
        """
        Resolve one folder path relative to `tests/`

        Args:
            rel (str): Folder path relative to `tests/`, '' for the root

        Returns:
            str: Absolute path of the folder

        Raises:
            GoldenTestPathError: If no mod is browsed, or there is no such mod
        """
        root = self.tests_root
        return joinnormpath(root, rel) if rel else root

    def watch_paths(self):
        if not self.mod_name:
            return []
        try:
            root = self.tests_root
        except GoldenTestPathError:
            return []
        # The absolute path of a folder is a join of the root and its relative
        # path: building the whole map here keeps the poll loop free of it
        self._abs_dirs = {
            (joinnormpath(root, rel) if rel else root): rel
            for rel in self.dirs
        }
        return list(self._abs_dirs)

    async def _build_full(self):
        """
        [Trio] Build the full view from the folders subscribed so far

        Returns:
            GoldenTestTreeData:
        """
        if not self.mod_name:
            # No mod selected: the empty view, no reading
            return GoldenTestTreeData()
        folders = list(self.dirs)
        if not folders:
            # Nothing subscribed yet: no scan, the page asks for a folder next
            return GoldenTestTreeData(mod_name=self.mod_name)

        # The paths are taken before the scan: a subscription that resumed
        # after every path was released (a reconnect) would otherwise read an
        # empty watcher and see all its folders as missing
        self._sync_paths()
        await self.scan()
        entries, missing = snapshot_dirs(self.WATCHER.list(), self._abs_dirs)
        for rel in missing:
            entries.pop(rel, None)
        # A folder subscribed while the scan ran is kept: the build must not
        # drop the increment sub_folder() is about to push
        for rel, rows in self.dirs.items():
            if rel in missing:
                continue
            entries.setdefault(rel, rows)
        self.dirs = entries
        self._sync_paths()
        return GoldenTestTreeData(mod_name=self.mod_name, dirs=dict(entries))

    async def watch_apply(self, events):
        """
        [Trio] Convert the watch events of the subscribed folders into pushes

        Every event is applied to the rows of its own folder, and only the
        folders that really changed are pushed. The folder of a path is one
        dict lookup in `_abs_dirs`, so a batch of events costs one pass over
        the events plus one sort per touched folder -- no snapshot rescan.

        Args:
            events (list[WatchEvent]): The events of one scan
        """
        if not self._subscribers or not self._abs_dirs:
            return

        # rel -> {name: row}, seeded from the rows the page already has
        changed: Dict[str, Dict[str, GoldenTestEntry]] = {}
        vanished = set()

        for event in events:
            path = event.path
            rel = self._abs_dirs.get(path)
            if rel is not None:
                # The subscribed folder itself
                if event.event == 'A' and event.type == 'D':
                    changed.setdefault(rel, {entry.name: entry for entry in self.dirs.get(rel, [])})
                else:
                    # Deleted, or replaced with something that is not a folder
                    vanished.add(rel)
                continue

            parent, _, name = path.rpartition('/')
            rel = self._abs_dirs.get(parent)
            if rel is None:
                # Not a direct child of a subscribed folder, or another
                # subscription of the watcher: only its own source converts it
                continue
            rows = changed.get(rel)
            if rows is None:
                rows = changed[rel] = {entry.name: entry for entry in self.dirs.get(rel, [])}

            exists = event.event != 'D'
            if not exists:
                rows.pop(name, None)
            elif event.type == 'D' and not is_skipped_dir(name):
                rows[name] = GoldenTestEntry(name=name, path=f'{rel}/{name}' if rel else name, is_dir=True)
            elif event.type == 'F' and is_test_file(name):
                rows[name] = GoldenTestEntry(name=name, path=f'{rel}/{name}' if rel else name)
            else:
                # A file that is not a test file, a symlink, a special entry
                rows.pop(name, None)

        with self._lock:
            for rel in vanished:
                if self.dirs.pop(rel, None) is None:
                    continue
                self._push(ResponseEvent(t=self.TOPIC_NAME, o='del', k=('dirs', rel)))
            for rel, rows in changed.items():
                if rel in vanished:
                    continue
                new = sorted(rows.values(), key=_entry_sort_key)
                if self.dirs.get(rel) == new:
                    continue
                self.dirs[rel] = new
                self._push(ResponseEvent(t=self.TOPIC_NAME, o='set', k=('dirs', rel), v=new))
        if vanished:
            self._sync_paths()

    async def set_mod(self, mod_name):
        """
        [Trio] Browse the tests of a mod: reset the tree and read its root

        The folders of the previous mod are dropped and the root of the new one
        is subscribed, so the page has a tree to show as soon as the full event
        arrives. The selected file is cleared as well: it belongs to the mod
        that is no longer browsed.

        Args:
            mod_name (str): Name of the mod, empty to clear the tree

        Raises:
            RpcValueError: If there is no such mod
        """
        if mod_name and mod_name not in MOD_LOADER.dict_mod:
            raise RpcValueError(f'No such mod: "{mod_name}"')

        await GoldenTestFileSource().set_file('', '')
        if self.mod_name == mod_name:
            # Same mod: reload the tree the page expanded so far
            await self.reinit()
            return

        self.mod_name = mod_name
        # The root belongs to the mod that was just dropped
        InstanceCacheOperation.pop(self, 'tests_root')
        self.dirs = {'': []} if mod_name else {}
        self._sync_paths()
        await self.reinit()

    async def sub_folder(self, path):
        """
        [Trio] Read one folder level and push it to the subscribers

        The folder must be a child of a folder that is already subscribed, so a
        request can never walk the tree into a folder the page does not show.

        Args:
            path (str): Folder path relative to `tests/`, '' for the tests root

        Raises:
            RpcValueError: If the mod is not resolved, the parent folder is not
                subscribed, the path is invalid or the folder does not exist
        """
        if not self.mod_name:
            raise RpcValueError('No mod selected')
        if path:
            try:
                validate_filepath(path)
            except ValueError as e:
                raise RpcValueError(str(e)) from None
            parent, _, _ = path.rpartition('/')
            known = self.dirs.get(parent)
            if known is None:
                raise RpcValueError(f'Folder is not subscribed: "{parent or TESTS}"')
            if not any(entry.is_dir and entry.path == path for entry in known):
                raise RpcValueError(f'No such folder: "{path}"')

        try:
            abs_dir = self._abs(path)
        except GoldenTestPathError as e:
            raise RpcValueError(e.message) from None

        self.dirs[path] = []
        self._sync_paths()
        events = await self.scan()
        entries, missing = snapshot_dirs(self.WATCHER.list(), {abs_dir: path})
        if path in missing:
            self.dirs.pop(path, None)
            self._sync_paths()
            raise RpcValueError(f'No such folder: "{path or TESTS}"')

        rows = entries[path]
        with self._lock:
            self.dirs[path] = rows
            if self._subscribers:
                # Always pushed: the page is waiting for this folder, even when
                # it has no children
                self._push(ResponseEvent(t=self.TOPIC_NAME, o='set', k=('dirs', path), v=rows))
        if events and self._subscribers:
            # The scan consumed the events that were pending since the last
            # poll: hand them over, or the changes of the other folders would
            # be lost until something else changes
            await self.watch_apply(events)

    async def unsub_folder(self, path):
        """
        [Trio] Stop reading a folder, and the folders below it

        Idempotent: unsubscribing a folder that is not subscribed does nothing.

        Args:
            path (str): Folder path relative to `tests/`, '' for the root

        Raises:
            RpcValueError: If the mod is not resolved
        """
        if not self.mod_name:
            raise RpcValueError('No mod selected')
        with self._lock:
            removed = [
                rel for rel in self.dirs
                if rel == path or not path or rel.startswith(f'{path}/')
            ]
            for rel in removed:
                self.dirs.pop(rel, None)
                if self._subscribers:
                    self._push(ResponseEvent(t=self.TOPIC_NAME, o='del', k=('dirs', rel)))
        self._sync_paths()


class GoldenTestFileSource(GoldenTestWatchSource, GlobalSource):
    """
    The one selected test file of the review page, shared by every subscriber.

    The file is parsed by `parse_test_file`, an `ast` read of the source: the
    test module is never imported, the backend process has no pytest and no
    game module to run it with.

    The file is the one path of the shared watcher this source needs, so an
    outside write (an editor, an AI agent) is reparsed and pushed without any
    refresh from the page.

    The empty view (no file) reads nothing: it is the view before the page
    selects a file, and the view after it clears the selection.
    """
    TOPIC_NAME = 'GoldenTestFile'

    def __init__(self):
        super().__init__()
        self.mod_name = ''
        self.file = ''
        # Safe absolute path of the file, resolved by the full build
        self.abs_file = ''

    def watch_paths(self):
        return [self.abs_file] if self.abs_file else []

    async def _build_full(self):
        """
        [Trio] Resolve, watch and parse the selected file

        Returns:
            GoldenTestFileData:
        """
        if not self.file:
            return GoldenTestFileData()
        try:
            self.abs_file = await trio.to_thread.run_sync(self._resolve)
        except GoldenTestPathError as e:
            self.abs_file = ''
            self._sync_paths()
            return GoldenTestFileData(mod_name=self.mod_name, file=self.file, errors=[e.message])

        self._sync_paths()
        # The file as it is now is the baseline: a write after this point is
        # what the page is waiting for, the parse below already reads it
        await self.scan()
        return await trio.to_thread.run_sync(self._parse)

    def _resolve(self):
        """
        [thread] Resolve the file path

        Returns:
            str: Absolute path of the test file

        Raises:
            GoldenTestPathError: If the path is invalid or there is no such mod
        """
        return resolve_test_file(self.mod_name, self.file)

    def _parse(self):
        """
        [thread] Parse the file

        Returns:
            GoldenTestFileData: A read error is a view error, not an exception
        """
        parsed: ParsedUnittest = parse_test_file(self.abs_file)
        return GoldenTestFileData(
            mod_name=self.mod_name,
            file=self.file,
            source=parsed.source,
            images=parsed.images,
            errors=parsed.errors,
        )

    async def watch_apply(self, events: "List[WatchEvent]"):
        """
        [Trio] Reparse the file when the watched file changed

        The source watches exactly one file, so this is one comparison per
        event, and a reparse only when that file is in the batch.

        Args:
            events (list[WatchEvent]): The events of one scan
        """
        if not self._subscribers or not self.abs_file:
            return
        for event in events:
            if event.path == self.abs_file:
                await self.reinit()
                return

    async def set_file(self, mod_name, file):
        """
        [Trio] Select a test file, or clear the selection with an empty `file`

        Selecting the file that is already selected reparses it: without a
        refresh RPC, clicking the current file is the manual way to reload it.

        Args:
            mod_name (str): Mod of the file
            file (str): Path relative to `tests/`, empty to clear the selection

        Raises:
            RpcValueError: If the mod or the file does not exist
        """
        if mod_name and mod_name not in MOD_LOADER.dict_mod:
            raise RpcValueError(f'No such mod: "{mod_name}"')
        if file:
            if not mod_name:
                raise RpcValueError('No mod selected')
            try:
                await trio.to_thread.run_sync(resolve_test_file, mod_name, file)
            except GoldenTestPathError as e:
                raise RpcValueError(e.message) from None

        self.mod_name = mod_name
        self.file = file
        if not file:
            self.abs_file = ''
        self._sync_paths()
        # A new selection, or the same one again (a manual reload: there is no
        # refresh RPC), rebuilds the view and broadcasts it
        await self.reinit()


class GoldenTestTree(BaseTopic):
    """
    The test tree of the browsed mod.

    The view lives in the one `GoldenTestTreeSource` of the process, so the
    topic is electron only: a remote connection must never read the tests of the
    mod the desktop client is reviewing.
    """
    TOPIC_NAME = 'GoldenTestTree'
    REQUIRE_ELECTRON = True

    async def get_source(self):
        return GoldenTestTreeSource()

    @rpc
    async def set_mod(self, mod_name: str):
        """
        Browse the tests of a mod, and clear the selected file

        Args:
            mod_name (str): Name of the mod, empty to clear the tree

        Raises:
            RpcValueError: If there is no such mod
        """
        await GoldenTestTreeSource().set_mod(mod_name)

    @rpc
    async def sub_folder(self, path: str):
        """
        Subscribe a folder and push its children

        Args:
            path (str): Folder path relative to tests/, '' for the tests root

        Raises:
            RpcValueError: If the folder is unknown or unreadable
        """
        await GoldenTestTreeSource().sub_folder(path)

    @rpc
    async def unsub_folder(self, path: str):
        """
        Drop a folder and the folders below it

        Args:
            path (str): Folder path relative to tests/, '' for the root

        Raises:
            RpcValueError: If the mod is not resolved
        """
        await GoldenTestTreeSource().unsub_folder(path)


class GoldenTestFile(BaseTopic):
    """
    The selected test file.

    The view lives in the one `GoldenTestFileSource` of the process, so the
    topic is electron only, for the same reason as the tree topic.
    """
    TOPIC_NAME = 'GoldenTestFile'
    REQUIRE_ELECTRON = True

    async def get_source(self):
        return GoldenTestFileSource()

    @rpc
    async def set_file(self, mod_name: str, file: str):
        """
        Select a test file, or clear the selection with an empty `file`

        Args:
            mod_name (str): Mod of the file
            file (str): Path relative to tests/, empty to clear the selection

        Raises:
            RpcValueError: If the mod or the file does not exist
        """
        await GoldenTestFileSource().set_file(mod_name, file)
