"""
Tests for the golden test review topics (alasio/backend/topic/golden_test.py).

The page browses the tests of a mod lazily: the tree reads the folders the page
subscribed, one level each, and the file topic parses one test file. Both views
are process-wide singletons, because the review page serves the one desktop
client of the backend: switching the mod or the file changes the subscription of
that instance. The two sources share the one PathWatcher of the page, and the
poll loop of the module dispatches one scan to both of them.
"""
from types import SimpleNamespace

import pytest
import trio
from trio.testing import wait_all_tasks_blocked

import alasio.backend.topic.golden_test as golden_test_module
from alasio.backend.reactive.event import RpcValueError
from alasio.backend.topic.golden_test import (
    WATCH_INTERVAL, GoldenTestEntry, GoldenTestFile, GoldenTestFileData, GoldenTestFileSource, GoldenTestPathError,
    GoldenTestPathWatcher, GoldenTestTree, GoldenTestTreeData, GoldenTestTreeSource, GoldenTestWatchSource,
    resolve_collection_image, resolve_test_file, snapshot_dirs
)
from alasio.config.entry.loader import MOD_LOADER
from alasio.ext.path import PathStr
from alasio.testing.filesystem import fs  # noqa: F401
from tests.backend.reactive.test_source_base import MockTopic, decode as decode_payload

MOD_NAME = 'test_mod'
OTHER_MOD = 'other_mod'
CONN_ID = 'conn_golden_test'

TEST_SOURCE = """\
from alasio.testing.screenshot import image


class TestFleetBarNotExpanded:
    @image("map/fleet_bar/not_expanded.webp")
    def test_not_expanded(self):
        assert 1 == 1
"""


def decode(payload):
    """
    The last event of one payload

    A source merges the events of one critical section into a single array
    message, so a payload is one event or a list of them.

    Args:
        payload (bytes): Encoded payload of the subscriber

    Returns:
        ResponseEvent | None: The last event of the payload
    """
    if payload is None:
        return None
    decoded = decode_payload(payload)
    if isinstance(decoded, list):
        return decoded[-1]
    return decoded


def events_of(payload):
    """
    Every event of one payload

    Args:
        payload (bytes): Encoded payload of the subscriber

    Returns:
        list[ResponseEvent]: The events of the payload
    """
    if payload is None:
        return []
    decoded = decode_payload(payload)
    if isinstance(decoded, list):
        return decoded
    return [decoded]


class RecordingServer:
    """
    The send surface of a websocket connection, enough for a real BaseTopic

    The topic layer writes the full events and the increments of a source to its
    server: recording them is what a test asserts on.
    """

    def __init__(self):
        self.sent = []

    def send_nowait(self, event):
        self.sent.append(event)

    async def send(self, event):
        self.sent.append(event)

    def send_lossy(self, event):
        self.sent.append(event)


@pytest.fixture
def project(fs, monkeypatch):
    """
    A fake project with two mods, pointing MOD_LOADER at it

    Both mods have the same root, so their tests folder is the same directory.

    Returns:
        PathStr: Root of the mods
    """
    root = PathStr.new(fs.root_dir.path).joinpath('project')
    monkeypatch.setattr(MOD_LOADER, 'dict_mod', {
        MOD_NAME: SimpleNamespace(root=root, name=MOD_NAME),
        OTHER_MOD: SimpleNamespace(root=root, name=OTHER_MOD),
    })
    return root


@pytest.fixture
def tests_root(project, fs):
    """The tests folder of the fake mods"""
    fs.create_dir(project.joinpath('tests'))
    return project.joinpath('tests')


@pytest.fixture(autouse=True)
def cleanup():
    """Every test starts and ends with no singleton and an empty watcher"""
    classes = (GoldenTestTreeSource, GoldenTestFileSource, GoldenTestTree, GoldenTestFile)
    for cls in classes:
        cls.singleton_clear()
    GoldenTestWatchSource.WATCHER = GoldenTestPathWatcher()
    yield
    for cls in classes:
        cls.singleton_clear()
    GoldenTestWatchSource.WATCHER = GoldenTestPathWatcher()


def watched():
    """The paths currently subscribed on the shared watcher"""
    return set(GoldenTestWatchSource.WATCHER._watches)


def entry_shape(entries):
    """(path, is_dir) of the entries, for a compact assertion"""
    return [(entry.path, entry.is_dir) for entry in entries]


async def settle():
    """
    Let the doorbell of the source run

    A push is queued with run_sync_soon, the subscriber receives it on the next
    checkpoint, exactly like a real connection.
    """
    await wait_all_tasks_blocked()


async def subscribe_tree():
    """
    Subscribe the tree source the way the topic does

    Returns:
        tuple: The source and its subscriber, its full event on `sub.full`
    """
    source = GoldenTestTreeSource()
    sub = MockTopic(topic_name=GoldenTestTreeSource.TOPIC_NAME)
    sub.full = decode(await source.subscribe(sub))
    return source, sub


async def subscribe_file():
    """
    Subscribe the file source the way the topic does

    Returns:
        tuple: The source and its subscriber, its full event on `sub.full`
    """
    source = GoldenTestFileSource()
    sub = MockTopic(topic_name=GoldenTestFileSource.TOPIC_NAME)
    sub.full = decode(await source.subscribe(sub))
    return source, sub


def scan_dirs(folders, mod_name=MOD_NAME):
    """The children of the folders of a mod, from a fresh scan"""
    source = GoldenTestTreeSource()
    source.mod_name = mod_name
    source.dirs = {rel: [] for rel in folders}
    source.watch_paths()
    source.WATCHER.update()
    return snapshot_dirs(source.WATCHER.list(), source._abs_dirs)


class TestSnapshotDirs:
    """snapshot_dirs() reads the children of every subscribed folder"""

    @pytest.mark.trio
    async def test_one_level_only(self, fs, tests_root):
        fs.create_file(tests_root.joinpath('map/test_a.py'))
        fs.create_file(tests_root.joinpath('map/sub/test_c.py'))
        fs.create_file(tests_root.joinpath('map/notes.txt'))
        fs.create_file(tests_root.joinpath('test_b.py'))
        fs.create_file(tests_root.joinpath('conftest.py'))
        GoldenTestWatchSource.WATCHER.subscribe(str(tests_root))

        entries, missing = scan_dirs([''])

        assert missing == set()
        # the nested folder and file belong to the level below
        assert entry_shape(entries['']) == [('map', True), ('test_b.py', False)]

    @pytest.mark.trio
    async def test_directories_first_then_files_sorted(self, fs, tests_root):
        for name in ('beta/test_b.py', 'Zebra/test_c.py', 'apple/test_a.py', 'test_z.py', 'Test_y.py', 'test_a.py'):
            fs.create_file(tests_root.joinpath(name))
        GoldenTestWatchSource.WATCHER.subscribe(str(tests_root))

        entries, missing = scan_dirs([''])

        # case insensitive order first, the exact name breaks the tie; a file
        # named Test_y.py is not a test file, pytest collects test_*.py only
        assert entry_shape(entries['']) == [
            ('apple', True),
            ('beta', True),
            ('Zebra', True),
            ('test_a.py', False),
            ('test_z.py', False),
        ]

    @pytest.mark.trio
    async def test_skipped_folders(self, fs, tests_root):
        for name in ('__pycache__/test_x.py', '.git/test_y.py', '.pytest_cache/test_z.py', '.hidden/test_a.py'):
            fs.create_file(tests_root.joinpath(name))
        fs.create_file(tests_root.joinpath('test_ok.py'))
        GoldenTestWatchSource.WATCHER.subscribe(str(tests_root))

        entries, missing = scan_dirs([''])

        assert entry_shape(entries['']) == [('test_ok.py', False)]

    @pytest.mark.trio
    async def test_symlink_is_not_followed(self, fs, tests_root, project):
        fs.create_file(project.joinpath('outside/test_a.py'))
        fs.create_symlink(tests_root.joinpath('link'), project.joinpath('outside'))
        fs.create_symlink(tests_root.joinpath('test_link.py'), project.joinpath('outside/test_a.py'))
        fs.create_file(tests_root.joinpath('test_ok.py'))
        GoldenTestWatchSource.WATCHER.subscribe(str(tests_root))

        entries, missing = scan_dirs([''])

        assert entry_shape(entries['']) == [('test_ok.py', False)]

    @pytest.mark.trio
    async def test_missing_folder(self, fs, tests_root):
        GoldenTestWatchSource.WATCHER.subscribe(str(tests_root.joinpath('no_such_folder')))

        entries, missing = scan_dirs(['no_such_folder'])

        assert missing == {'no_such_folder'}
        assert entries['no_such_folder'] == []

    @pytest.mark.trio
    async def test_nested_folders(self, fs, tests_root):
        fs.create_file(tests_root.joinpath('map/fleet/test_a.py'))
        fs.create_file(tests_root.joinpath('map/test_b.py'))
        GoldenTestWatchSource.WATCHER.subscribe(str(tests_root))
        GoldenTestWatchSource.WATCHER.subscribe(str(tests_root.joinpath('map')))

        entries, missing = scan_dirs(['', 'map'])

        assert entry_shape(entries['']) == [('map', True)]
        assert entry_shape(entries['map']) == [('map/fleet', True), ('map/test_b.py', False)]


class TestResolveTestFile:
    """resolve_test_file() keeps every request inside the tests folder"""

    def test_resolves_the_file(self, fs, tests_root):
        file = tests_root.joinpath('map/test_a.py')
        fs.create_file(file)

        assert resolve_test_file(MOD_NAME, 'map/test_a.py') == str(file)

    def test_file_that_does_not_exist(self, tests_root):
        """A missing file still resolves, the watcher reports it when it appears"""
        assert resolve_test_file(MOD_NAME, 'map/test_a.py') == str(tests_root.joinpath('map/test_a.py'))

    @pytest.mark.parametrize("file", [
        '../test_a.py',
        'map/../../test_a.py',
        '/etc/test_a.py',
        'C:/project/tests/test_a.py',
        'map\\test_a.py',
        'map/test_a.txt',
        'map/notes.py',
        '',
    ])
    def test_rejected_paths(self, tests_root, file):
        with pytest.raises(GoldenTestPathError):
            resolve_test_file(MOD_NAME, file)

    def test_unknown_mod(self, tests_root):
        with pytest.raises(GoldenTestPathError) as e:
            resolve_test_file('no_such_mod', 'test_a.py')
        assert 'No such mod' in e.value.message

    def test_symlink_escape(self, fs, tests_root, project):
        """A file of the mod that links out of the tests folder is refused"""
        outside = project.joinpath('outside/test_a.py')
        fs.create_file(outside)
        fs.create_symlink(tests_root.joinpath('test_link.py'), outside)

        with pytest.raises(GoldenTestPathError) as e:
            resolve_test_file(MOD_NAME, 'test_link.py')
        assert 'outside the allowed directory' in e.value.message


class TestResolveCollectionImage:
    """resolve_collection_image() keeps every request inside the collection folder"""

    def test_resolves_the_image(self, fs, project):
        image = project.joinpath('collection/map/fleet_bar/not_expanded.webp')
        fs.create_file(image)

        assert resolve_collection_image(MOD_NAME, 'map/fleet_bar/not_expanded.webp') == str(image)

    def test_image_that_does_not_exist(self, project):
        """A missing image still resolves, the route reports the missing file"""
        assert resolve_collection_image(MOD_NAME, 'missing.webp') == str(
            project.joinpath('collection/missing.webp'))

    def test_the_folder_is_next_to_the_tests(self, project):
        """The collection folder of a test is `<mod root>/collection`"""
        assert resolve_collection_image(MOD_NAME, 'a.webp') == str(project.joinpath('collection/a.webp'))

    @pytest.mark.parametrize("image_path", [
        '../tests/test_a.py',
        'map/../../secret.webp',
        '/etc/passwd.webp',
        'C:/project/collection/a.webp',
        '',
    ])
    def test_rejected_paths(self, project, image_path):
        with pytest.raises(GoldenTestPathError):
            resolve_collection_image(MOD_NAME, image_path)

    def test_unknown_mod(self, project):
        with pytest.raises(GoldenTestPathError) as e:
            resolve_collection_image('no_such_mod', 'a.webp')
        assert 'No such mod' in e.value.message

    def test_symlink_escape(self, fs, project):
        """A screenshot of the collection that links out of the folder is refused"""
        outside = project.joinpath('outside/secret.webp')
        fs.create_file(outside)
        fs.create_symlink(project.joinpath('collection/secret.webp'), outside)

        with pytest.raises(GoldenTestPathError) as e:
            resolve_collection_image(MOD_NAME, 'secret.webp')
        assert 'outside the allowed directory' in e.value.message


class TestSharedWatcher:
    """The two sources share one watcher, and one poll serves both"""

    @pytest.mark.trio
    async def test_a_source_takes_its_paths(self, fs, tests_root):
        source, sub = await subscribe_tree()

        await source.set_mod(MOD_NAME)

        assert source._paths == {str(tests_root)}
        assert watched() == {str(tests_root)}

    @pytest.mark.trio
    async def test_the_tree_and_the_file_share_the_scan(self, fs, tests_root):
        """
        The tree and the file of the same folder are watched by one watcher, and
        one poll serves both of them
        """
        file = tests_root.joinpath('map/test_a.py')
        fs.create_file(file, contents=TEST_SOURCE)
        tree, tree_sub = await subscribe_tree()
        await tree.set_mod(MOD_NAME)
        await tree.sub_folder('map')
        selected, file_sub = await subscribe_file()
        await selected.set_file(MOD_NAME, 'map/test_a.py')
        await settle()
        file_sub.sent.clear()

        # the root, the expanded folder, and the file: three paths, subscribed
        # once each, the file although a folder also reports it
        assert watched() == {str(tests_root), str(tests_root.joinpath('map')), str(file)}

        fs.create_file(tests_root.joinpath('map/test_new.py'))
        await golden_test_module.poll_once()
        await settle()

        # the tree got the new file of the folder ...
        assert [(row['path'], row.get('is_dir', False)) for row in decode(tree_sub.sent[-1]).v] == [
            ('map/test_a.py', False), ('map/test_new.py', False),
        ]
        # ... and the unrelated file did not reparse the selection
        assert file_sub.sent == []

    @pytest.mark.trio
    async def test_a_path_is_released_with_its_last_source(self, fs, tests_root):
        file = tests_root.joinpath('map/test_a.py')
        fs.create_file(file, contents=TEST_SOURCE)
        tree, _ = await subscribe_tree()
        await tree.set_mod(MOD_NAME)
        await tree.sub_folder('map')
        selected, _ = await subscribe_file()
        await selected.set_file(MOD_NAME, 'map/test_a.py')

        await tree.unsub_folder('map')

        # the file is still watched by the file source
        assert str(file) in watched()
        assert str(tests_root.joinpath('map')) not in watched()

        await selected.set_file('', '')
        assert str(file) not in watched()

    @pytest.mark.trio
    async def test_the_last_subscriber_leaves_the_watch(self, fs, tests_root):
        fs.create_file(tests_root.joinpath('map/test_a.py'))
        source, sub = await subscribe_tree()
        await source.set_mod(MOD_NAME)

        source.unsubscribe(sub)

        # nothing is scanned any more, and the view is kept for the next
        # subscriber
        assert source._paths == frozenset()
        assert watched() == set()
        assert GoldenTestTreeSource() is source
        assert source.mod_name == MOD_NAME

    @pytest.mark.trio
    async def test_a_closed_page_stops_the_scanning(self, fs, tests_root):
        """Unsubscribing the topics releases every path of both sources"""
        fs.create_file(tests_root.joinpath('map/test_a.py'), contents=TEST_SOURCE)
        server = RecordingServer()
        tree = GoldenTestTree(CONN_ID, server)
        file = GoldenTestFile(CONN_ID, server)
        await tree.op_sub()
        await file.op_sub()
        await GoldenTestTree.rpc_methods['set_mod'].call_async(tree, {'mod_name': MOD_NAME})
        await GoldenTestFile.rpc_methods['set_file'].call_async(
            file, {'mod_name': MOD_NAME, 'file': 'map/test_a.py'},
        )
        assert watched()

        await tree.op_unsub()
        await file.op_unsub()

        assert GoldenTestTreeSource()._paths == frozenset()
        assert GoldenTestFileSource()._paths == frozenset()
        assert watched() == set()

    @pytest.mark.trio
    async def test_a_poll_without_a_subscriber_does_not_scan(self, fs, tests_root, monkeypatch):
        """No subscriber, no scan: the worker thread is not started at all"""

        def fail(*args, **kwargs):
            pytest.fail('a poll without a subscriber must not scan')

        fs.create_file(tests_root.joinpath('map/test_a.py'))
        source, sub = await subscribe_tree()
        await source.set_mod(MOD_NAME)
        source.unsubscribe(sub)
        monkeypatch.setattr(GoldenTestWatchSource.WATCHER, 'update', fail)

        await golden_test_module.poll_once()

    @pytest.mark.trio
    async def test_a_reconnect_resumes_the_view(self, fs, tests_root):
        """The state is kept: the next subscriber gets the open view back"""
        fs.create_file(tests_root.joinpath('map/test_a.py'))
        source, sub = await subscribe_tree()
        await source.set_mod(MOD_NAME)
        source.unsubscribe(sub)
        assert watched() == set()

        again, again_sub = await subscribe_tree()

        assert again is source
        assert again_sub.full.v['mod_name'] == MOD_NAME
        # the paths are taken again by the full build of the new subscription
        assert str(tests_root) in watched()

    @pytest.mark.trio
    async def test_an_empty_view_takes_no_path(self):
        """A source with nothing to watch is not polled"""
        tree, _ = await subscribe_tree()
        selected, _ = await subscribe_file()

        assert tree._paths == frozenset()
        assert selected._paths == frozenset()
        assert watched() == set()

    @pytest.mark.trio
    async def test_one_failing_source_does_not_stop_the_other(self, fs, tests_root):
        """A source that raises on apply never blocks the other one"""
        file = tests_root.joinpath('map/test_a.py')
        fs.create_file(file, contents=TEST_SOURCE)
        tree, tree_sub = await subscribe_tree()
        await tree.set_mod(MOD_NAME)
        await tree.sub_folder('map')
        selected, file_sub = await subscribe_file()
        await selected.set_file(MOD_NAME, 'map/test_a.py')
        await settle()
        file_sub.sent.clear()

        async def failing(events):
            raise OSError('boom')

        tree.watch_apply = failing
        fs.remove(file)
        fs.create_file(file, contents='assert 1\n')
        await golden_test_module.poll_once()
        await settle()

        # the file source still reparsed its file
        assert decode(file_sub.sent[-1]).v['source'] == 'assert 1\n'

    @pytest.mark.trio
    async def test_watch_interval(self):
        """The dev tool polls every 2 seconds"""
        assert WATCH_INTERVAL == 2.0

    @pytest.mark.trio
    async def test_a_scan_opens_a_new_window(self, fs, tests_root):
        """A scan made by an RPC counts as the poll of that window"""
        fs.create_file(tests_root.joinpath('map/test_a.py'))
        source, sub = await subscribe_tree()
        await source.set_mod(MOD_NAME)

        # the last scan was just now: the loop waits for the rest of the window
        delay = GoldenTestWatchSource.WATCHER.scan_delay(WATCH_INTERVAL)
        assert 0 < delay <= WATCH_INTERVAL

    @pytest.mark.trio
    async def test_an_old_scan_does_not_delay_the_next_one(self):
        """Without a recent scan the loop scans at once"""
        GoldenTestWatchSource.WATCHER.last_update = 0.0

        assert GoldenTestWatchSource.WATCHER.scan_delay(WATCH_INTERVAL) == 0.0

    @pytest.mark.trio
    async def test_a_scan_in_the_middle_of_the_window_delays_by_its_rest(self, fs, tests_root):
        fs.create_file(tests_root.joinpath('map/test_a.py'))
        source, sub = await subscribe_tree()
        await source.set_mod(MOD_NAME)
        GoldenTestWatchSource.WATCHER.last_update = trio.current_time() - WATCH_INTERVAL / 2

        delay = GoldenTestWatchSource.WATCHER.scan_delay(WATCH_INTERVAL)

        assert delay == pytest.approx(WATCH_INTERVAL / 2, abs=0.2)

    @pytest.mark.trio
    async def test_a_poll_reports_whether_it_scanned(self, fs, tests_root):
        """No subscriber, no scan: the loop schedules itself, not a busy loop"""
        assert await golden_test_module.poll_once() is False

        fs.create_file(tests_root.joinpath('map/test_a.py'))
        source, sub = await subscribe_tree()
        await source.set_mod(MOD_NAME)

        assert await golden_test_module.poll_once() is True


class TestSingleton:
    """Each view is one instance for the whole process"""

    @pytest.mark.trio
    async def test_one_source_per_view(self):
        assert GoldenTestTreeSource() is GoldenTestTreeSource()
        assert GoldenTestFileSource() is GoldenTestFileSource()

    @pytest.mark.trio
    async def test_both_topics_resolve_the_same_source(self):
        first = await GoldenTestTree(CONN_ID, None).get_source()
        second = await GoldenTestTree('another_connection', None).get_source()
        selected = await GoldenTestFile(CONN_ID, None).get_source()

        assert first is second is GoldenTestTreeSource()
        assert selected is GoldenTestFileSource()

    @pytest.mark.trio
    async def test_a_second_subscriber_sees_the_open_view(self, fs, tests_root):
        """A reconnect subscribes the view that is already open, no rebuild"""
        fs.create_file(tests_root.joinpath('map/test_a.py'))
        first, first_sub = await subscribe_tree()
        await first.set_mod(MOD_NAME)
        await first.sub_folder('map')

        second, second_sub = await subscribe_tree()

        # the newcomer received the whole view of the shared source
        assert second_sub.full.v['mod_name'] == MOD_NAME
        assert sorted(second_sub.full.v['dirs']) == ['', 'map']
        assert second is first


class TestTreeSource:
    """GoldenTestTreeSource reads the folders the page subscribed"""

    @pytest.mark.trio
    async def test_empty_view_reads_nothing(self, monkeypatch):
        """The initial view receives an empty view, no scan at all"""

        def fail(*args, **kwargs):
            pytest.fail('the empty view must not touch the filesystem')

        monkeypatch.setattr(golden_test_module, '_mod_root', fail)
        source = GoldenTestTreeSource()

        assert await source._build_full() == GoldenTestTreeData()
        assert source.watch_paths() == []

    @pytest.mark.trio
    async def test_empty_view_is_sent(self):
        """An empty view is a Struct, so it is not filtered as falsy data"""
        source, sub = await subscribe_tree()

        assert sub.full.o == 'full'
        assert sub.full.v == {}
        assert source._subscribers == {sub}

    @pytest.mark.trio
    async def test_set_mod_reads_the_root(self, fs, tests_root):
        fs.create_file(tests_root.joinpath('map/test_a.py'))
        fs.create_file(tests_root.joinpath('test_b.py'))
        source, sub = await subscribe_tree()

        await source.set_mod(MOD_NAME)
        await settle()

        # the root is subscribed by set_mod, so the page has a tree at once
        assert entry_shape(source.dirs['']) == [('map', True), ('test_b.py', False)]
        decoded = decode(sub.sent[-1])
        assert decoded.o == 'full'
        assert decoded.v['mod_name'] == MOD_NAME
        assert [row['path'] for row in decoded.v['dirs']['']] == ['map', 'test_b.py']

    @pytest.mark.trio
    async def test_set_mod_clears_the_previous_tree(self, fs, tests_root):
        fs.create_file(tests_root.joinpath('map/test_a.py'))
        fs.create_file(tests_root.joinpath('island/test_b.py'))
        source, sub = await subscribe_tree()
        await source.set_mod(MOD_NAME)
        await source.sub_folder('map')
        await settle()

        await source.set_mod(OTHER_MOD)
        await settle()

        # the folders of the mod that is no longer browsed are dropped
        assert list(source.dirs) == ['']
        assert entry_shape(source.dirs['']) == [('island', True), ('map', True)]
        assert source._paths == {str(tests_root)}

    @pytest.mark.trio
    async def test_set_mod_resolves_the_root_of_the_new_mod(self, fs, project, monkeypatch):
        """The tests root is a cached property, and the mod switch drops it"""
        other_root = project.joinpath('other_project')
        fs.create_dir(other_root.joinpath('tests/deep'))
        fs.create_file(other_root.joinpath('tests/deep/test_c.py'))
        monkeypatch.setattr(MOD_LOADER, 'dict_mod', {
            MOD_NAME: SimpleNamespace(root=project, name=MOD_NAME),
            OTHER_MOD: SimpleNamespace(root=other_root, name=OTHER_MOD),
        })
        source, sub = await subscribe_tree()

        await source.set_mod(MOD_NAME)
        await settle()
        first = source.tests_root
        await source.set_mod(OTHER_MOD)
        await settle()

        assert first == str(project.joinpath('tests'))
        assert source.tests_root == str(other_root.joinpath('tests'))
        assert entry_shape(source.dirs['']) == [('deep', True)]
        assert watched() == {str(other_root.joinpath('tests'))}

    @pytest.mark.trio
    async def test_set_mod_clears_the_selected_file(self, fs, tests_root):
        """The selected file belongs to the mod that is no longer browsed"""
        fs.create_file(tests_root.joinpath('map/test_a.py'), contents=TEST_SOURCE)
        tree = GoldenTestTreeSource()
        selected = GoldenTestFileSource()
        await tree.set_mod(MOD_NAME)
        await selected.set_file(MOD_NAME, 'map/test_a.py')

        await tree.set_mod(OTHER_MOD)

        assert selected.file == ''
        assert selected.abs_file == ''
        assert selected.watch_paths() == []

    @pytest.mark.trio
    async def test_set_mod_unknown(self, tests_root):
        source, sub = await subscribe_tree()
        await source.set_mod(MOD_NAME)

        with pytest.raises(RpcValueError) as e:
            await source.set_mod('no_such_mod')
        assert 'No such mod' in str(e.value)
        # the state is untouched
        assert source.mod_name == MOD_NAME

    @pytest.mark.trio
    async def test_set_mod_clears_the_selection(self, fs, tests_root):
        fs.create_file(tests_root.joinpath('map/test_a.py'))
        source, sub = await subscribe_tree()
        await source.set_mod(MOD_NAME)

        await source.set_mod('')
        await settle()

        assert source.mod_name == ''
        assert source.dirs == {}
        assert watched() == set()

    @pytest.mark.trio
    async def test_set_mod_same_mod_reloads(self, fs, tests_root):
        fs.create_file(tests_root.joinpath('map/test_a.py'))
        source, sub = await subscribe_tree()
        await source.set_mod(MOD_NAME)
        await source.sub_folder('map')
        fs.create_file(tests_root.joinpath('map/test_new.py'))

        await source.set_mod(MOD_NAME)

        assert entry_shape(source.dirs['map']) == [('map/test_a.py', False), ('map/test_new.py', False)]

    @pytest.mark.trio
    async def test_sub_folder_of_a_child_folder(self, fs, tests_root):
        fs.create_file(tests_root.joinpath('map/fleet/test_a.py'))
        source, sub = await subscribe_tree()
        await source.set_mod(MOD_NAME)
        await settle()

        await source.sub_folder('map')
        await settle()

        assert entry_shape(source.dirs['map']) == [('map/fleet', True)]
        assert decode(sub.sent[-1]).k == ('dirs', 'map')

    @pytest.mark.trio
    async def test_sub_folder_pushes_an_empty_folder(self, fs, tests_root):
        """An empty folder is pushed as well, the page is waiting for it"""
        fs.create_dir(tests_root.joinpath('map'))
        source, sub = await subscribe_tree()
        await source.set_mod(MOD_NAME)
        await settle()

        await source.sub_folder('map')
        await settle()

        assert source.dirs['map'] == []
        decoded = decode(sub.sent[-1])
        assert decoded.o == 'set'
        assert decoded.v == []

    @pytest.mark.trio
    async def test_sub_folder_requires_the_subscribed_parent(self, fs, tests_root):
        fs.create_file(tests_root.joinpath('map/sub/test_b.py'))
        source, sub = await subscribe_tree()
        await source.set_mod(MOD_NAME)

        with pytest.raises(RpcValueError) as e:
            # map is not subscribed yet, its child can not be subscribed either
            await source.sub_folder('map/sub')
        assert 'is not subscribed' in str(e.value)

    @pytest.mark.trio
    async def test_sub_folder_requires_a_known_child(self, fs, tests_root):
        fs.create_file(tests_root.joinpath('map/test_a.py'))
        source, sub = await subscribe_tree()
        await source.set_mod(MOD_NAME)
        await source.sub_folder('map')
        await settle()

        with pytest.raises(RpcValueError) as e:
            await source.sub_folder('map/no_such')
        assert 'No such folder' in str(e.value)
        with pytest.raises(RpcValueError) as e:
            # a file is not a folder of the tree
            await source.sub_folder('map/test_a.py')
        assert 'No such folder' in str(e.value)

    @pytest.mark.trio
    async def test_sub_folder_rejects_an_invalid_path(self, fs, tests_root):
        source, sub = await subscribe_tree()
        await source.set_mod(MOD_NAME)

        with pytest.raises(RpcValueError):
            await source.sub_folder('../project')

    @pytest.mark.trio
    async def test_set_mod_of_a_mod_without_tests(self, fs, project):
        """A mod without a tests folder: the root folder does not exist"""
        source, sub = await subscribe_tree()

        await source.set_mod(MOD_NAME)
        await settle()

        assert source.dirs == {}
        assert watched() == set()

    @pytest.mark.trio
    async def test_unsub_folder(self, fs, tests_root):
        fs.create_file(tests_root.joinpath('map/fleet/test_a.py'))
        source, sub = await subscribe_tree()
        await source.set_mod(MOD_NAME)
        await source.sub_folder('map')
        await settle()

        await source.unsub_folder('map')
        await settle()

        # the folder and the folders below it are dropped
        assert list(source.dirs) == ['']
        decoded = decode(sub.sent[-1])
        assert decoded.o == 'del'
        assert decoded.k == ('dirs', 'map')
        assert str(tests_root.joinpath('map')) not in watched()

    @pytest.mark.trio
    async def test_unsub_folder_is_idempotent(self, fs, tests_root):
        source, sub = await subscribe_tree()
        await source.set_mod(MOD_NAME)

        await source.unsub_folder('no_such_folder')

        assert list(source.dirs) == ['']

    @pytest.mark.trio
    async def test_unsub_the_root_drops_everything(self, fs, tests_root):
        fs.create_file(tests_root.joinpath('map/test_a.py'))
        source, sub = await subscribe_tree()
        await source.set_mod(MOD_NAME)
        await source.sub_folder('map')
        await settle()

        await source.unsub_folder('')
        await settle()

        # the mod is still browsed, the page just collapsed everything
        assert source.dirs == {}
        assert decode(sub.sent[-1]).o == 'del'
        assert watched() == set()

    @pytest.mark.trio
    async def test_no_mod_selected(self, tests_root):
        source, sub = await subscribe_tree()

        with pytest.raises(RpcValueError) as e:
            await source.sub_folder('')
        assert 'No mod selected' in str(e.value)
        with pytest.raises(RpcValueError) as e:
            await source.unsub_folder('')
        assert 'No mod selected' in str(e.value)


class TestTreeWatch:
    """The poll loop pushes outside changes of the subscribed folders"""

    @pytest.mark.trio
    async def test_new_test_file_is_pushed(self, fs, tests_root):
        fs.create_file(tests_root.joinpath('map/test_a.py'))
        source, sub = await subscribe_tree()
        await source.set_mod(MOD_NAME)
        await source.sub_folder('map')
        await settle()

        fs.create_file(tests_root.joinpath('map/test_new.py'))
        await golden_test_module.poll_once()
        await settle()

        assert entry_shape(source.dirs['map']) == [('map/test_a.py', False), ('map/test_new.py', False)]
        decoded = decode(sub.sent[-1])
        assert decoded.o == 'set'
        assert decoded.k == ('dirs', 'map')

    @pytest.mark.trio
    async def test_deleted_test_file_is_pushed(self, fs, tests_root):
        fs.create_file(tests_root.joinpath('map/test_a.py'))
        fs.create_file(tests_root.joinpath('map/test_b.py'))
        source, sub = await subscribe_tree()
        await source.set_mod(MOD_NAME)
        await source.sub_folder('map')
        await settle()

        fs.remove(tests_root.joinpath('map/test_a.py'))
        await golden_test_module.poll_once()
        await settle()

        assert entry_shape(source.dirs['map']) == [('map/test_b.py', False)]

    @pytest.mark.trio
    async def test_new_child_folder_is_pushed_to_the_parent(self, fs, tests_root):
        fs.create_file(tests_root.joinpath('map/test_a.py'))
        source, sub = await subscribe_tree()
        await source.set_mod(MOD_NAME)
        await settle()

        fs.create_dir(tests_root.joinpath('island'))
        await golden_test_module.poll_once()
        await settle()

        assert entry_shape(source.dirs['']) == [('island', True), ('map', True)]
        # a new folder is not read until the page subscribes it
        assert 'island' not in source.dirs

    @pytest.mark.trio
    async def test_deleted_folder_is_delivered(self, fs, tests_root):
        fs.create_file(tests_root.joinpath('map/test_a.py'))
        source, sub = await subscribe_tree()
        await source.set_mod(MOD_NAME)
        await source.sub_folder('map')
        await settle()

        fs.remove(tests_root.joinpath('map/test_a.py'))
        fs.remove(tests_root.joinpath('map'))
        await golden_test_module.poll_once()
        await settle()

        assert 'map' not in source.dirs
        assert str(tests_root.joinpath('map')) not in watched()
        events = [event for payload in sub.sent for event in events_of(payload)]
        assert any(event.o == 'del' and event.k == ('dirs', 'map') for event in events)

    @pytest.mark.trio
    async def test_a_file_that_is_not_a_test_is_not_shown(self, fs, tests_root):
        """Only one kind of entry is pushed: the events are converted, not re-read"""
        source, sub = await subscribe_tree()
        await source.set_mod(MOD_NAME)

        fs.create_file(tests_root.joinpath('notes.txt'))
        fs.create_file(tests_root.joinpath('test_ok.py'))
        await golden_test_module.poll_once()
        await settle()

        assert entry_shape(source.dirs['']) == [('test_ok.py', False)]

    @pytest.mark.trio
    async def test_a_test_file_renamed_away_is_removed(self, fs, tests_root):
        fs.create_file(tests_root.joinpath('test_a.py'))
        source, sub = await subscribe_tree()
        await source.set_mod(MOD_NAME)
        await settle()

        fs.remove(tests_root.joinpath('test_a.py'))
        fs.create_file(tests_root.joinpath('notes.txt'))
        await golden_test_module.poll_once()
        await settle()

        assert source.dirs[''] == []

    @pytest.mark.trio
    async def test_unwatched_folder_is_not_pushed(self, fs, tests_root):
        """Only what the page subscribed is read and pushed"""
        fs.create_file(tests_root.joinpath('map/test_a.py'))
        source, sub = await subscribe_tree()
        await source.set_mod(MOD_NAME)
        await settle()
        sent = len(sub.sent)

        fs.create_file(tests_root.joinpath('map/test_new.py'))
        await golden_test_module.poll_once()
        await settle()

        assert len(sub.sent) == sent
        assert 'map' not in source.dirs

    @pytest.mark.trio
    async def test_one_push_per_folder_per_poll(self, fs, tests_root):
        """Several changes of one folder are one `set`, in the final order"""
        source, sub = await subscribe_tree()
        await source.set_mod(MOD_NAME)
        await settle()
        sub.sent.clear()

        fs.create_file(tests_root.joinpath('test_c.py'))
        fs.create_file(tests_root.joinpath('test_a.py'))
        fs.create_dir(tests_root.joinpath('map'))
        await golden_test_module.poll_once()
        await settle()

        events = [event for payload in sub.sent for event in events_of(payload)]
        assert len(events) == 1
        assert [(row['path'], row.get('is_dir', False)) for row in events[0].v] == [
            ('map', True), ('test_a.py', False), ('test_c.py', False),
        ]


class TestFileSource:
    """GoldenTestFileSource parses and watches the selected file"""

    @pytest.mark.trio
    async def test_empty_view_reads_nothing(self, monkeypatch):
        def fail(*args, **kwargs):
            pytest.fail('the empty view must not read the tests')

        monkeypatch.setattr(golden_test_module, 'parse_test_file', fail)
        monkeypatch.setattr(golden_test_module, 'resolve_test_file', fail)
        source = GoldenTestFileSource()

        assert await source._build_full() == GoldenTestFileData()
        assert source.watch_paths() == []

    @pytest.mark.trio
    async def test_parses_the_file(self, fs, tests_root):
        fs.create_file(tests_root.joinpath('map/test_fleet_bar.py'), contents=TEST_SOURCE)
        source, sub = await subscribe_file()

        await source.set_file(MOD_NAME, 'map/test_fleet_bar.py')
        await settle()

        assert source.abs_file == str(tests_root.joinpath('map/test_fleet_bar.py'))
        assert source._paths == {source.abs_file}
        decoded = decode(sub.sent[-1])
        assert decoded.o == 'full'
        assert decoded.v['source'] == TEST_SOURCE
        image = decoded.v['images'][0]
        assert (image['class_name'], image['func_name'], image['start_line'], image['image']) == (
            'TestFleetBarNotExpanded', 'test_not_expanded', 5, 'map/fleet_bar/not_expanded.webp',
        )

    @pytest.mark.trio
    async def test_missing_file_is_watched(self, tests_root):
        """A missing file is subscribed as well, the page shows it appearing"""
        source, sub = await subscribe_file()

        await source.set_file(MOD_NAME, 'map/test_a.py')
        await settle()

        decoded = decode(sub.sent[-1])
        assert decoded.v.get('source', '') == ''
        assert decoded.v['errors']
        assert source.abs_file in source._paths

    @pytest.mark.trio
    async def test_invalid_path_is_rejected(self, tests_root):
        source, sub = await subscribe_file()

        with pytest.raises(RpcValueError):
            await source.set_file(MOD_NAME, '../test_a.py')

        # the selection is untouched, and nothing was subscribed
        assert source.file == ''
        assert source._paths == frozenset()

    @pytest.mark.trio
    async def test_outside_edit_is_pushed(self, fs, tests_root):
        file = tests_root.joinpath('map/test_a.py')
        fs.create_file(file, contents=TEST_SOURCE)
        source, sub = await subscribe_file()
        await source.set_file(MOD_NAME, 'map/test_a.py')
        await settle()
        sub.sent.clear()

        fs.remove(file)
        fs.create_file(file, contents='assert 1\n')
        await golden_test_module.poll_once()
        await settle()

        decoded = decode(sub.sent[-1])
        assert decoded.o == 'full'
        assert decoded.v['source'] == 'assert 1\n'

    @pytest.mark.trio
    async def test_deleted_file_is_pushed(self, fs, tests_root):
        file = tests_root.joinpath('map/test_a.py')
        fs.create_file(file, contents=TEST_SOURCE)
        source, sub = await subscribe_file()
        await source.set_file(MOD_NAME, 'map/test_a.py')
        await settle()

        fs.remove(file)
        await golden_test_module.poll_once()
        await settle()

        # the page shows the error instead of the stale source
        last = decode(sub.sent[-1])
        assert last.o == 'full'
        assert last.v.get('source', '') == ''
        assert last.v['errors']

    @pytest.mark.trio
    async def test_other_files_are_ignored(self, fs, tests_root):
        fs.create_file(tests_root.joinpath('map/test_a.py'), contents=TEST_SOURCE)
        source, sub = await subscribe_file()
        await source.set_file(MOD_NAME, 'map/test_a.py')
        await settle()
        sent = len(sub.sent)

        fs.create_file(tests_root.joinpath('map/test_b.py'), contents='assert 2\n')
        await golden_test_module.poll_once()
        await settle()

        assert len(sub.sent) == sent

    @pytest.mark.trio
    async def test_set_file_again_reloads(self, fs, tests_root):
        """Clicking the selected file again reparses it, there is no refresh RPC"""
        file = tests_root.joinpath('map/test_a.py')
        fs.create_file(file, contents=TEST_SOURCE)
        source, sub = await subscribe_file()
        await source.set_file(MOD_NAME, 'map/test_a.py')
        await settle()
        sub.sent.clear()

        fs.remove(file)
        fs.create_file(file, contents='assert 1\n')
        await source.set_file(MOD_NAME, 'map/test_a.py')
        await settle()

        assert decode(sub.sent[-1]).v['source'] == 'assert 1\n'

    @pytest.mark.trio
    async def test_set_file_clears_the_selection(self, fs, tests_root):
        fs.create_file(tests_root.joinpath('map/test_a.py'), contents=TEST_SOURCE)
        source, sub = await subscribe_file()
        await source.set_file(MOD_NAME, 'map/test_a.py')

        await source.set_file('', '')
        await settle()

        assert source.file == ''
        assert source._paths == frozenset()
        assert decode(sub.sent[-1]).v == {}

    @pytest.mark.trio
    async def test_set_file_rejects_an_escaping_path(self, tests_root):
        source, sub = await subscribe_file()

        with pytest.raises(RpcValueError) as e:
            await source.set_file(MOD_NAME, '../test_a.py')
        assert 'directory pointer' in str(e.value)
        assert source.file == ''

    @pytest.mark.trio
    async def test_set_file_rejects_an_unknown_mod(self, tests_root):
        source, sub = await subscribe_file()

        with pytest.raises(RpcValueError) as e:
            await source.set_file('no_such_mod', 'test_a.py')
        assert 'No such mod' in str(e.value)

    @pytest.mark.trio
    async def test_set_file_requires_a_mod(self, tests_root):
        source, sub = await subscribe_file()

        with pytest.raises(RpcValueError) as e:
            await source.set_file('', 'map/test_a.py')
        assert 'No mod selected' in str(e.value)


class TestTopics:
    """The topics of the review page and their electron gate"""

    @pytest.mark.trio
    async def test_tree_rpcs(self):
        assert sorted(GoldenTestTree.rpc_methods) == ['set_mod', 'sub_folder', 'unsub_folder']
        # the view is process wide: the whole topic is electron only, the
        # subscription included
        assert GoldenTestTree.REQUIRE_ELECTRON is True
        # no refresh RPC and no "load everything" RPC: the watcher is the only
        # refresh path, the folders are subscribed one by one
        assert 'refresh' not in GoldenTestTree.rpc_methods

    @pytest.mark.trio
    async def test_file_rpcs(self):
        assert sorted(GoldenTestFile.rpc_methods) == ['set_file']
        assert GoldenTestFile.REQUIRE_ELECTRON is True
        # a refresh RPC would be a second path besides the watcher
        assert 'refresh' not in GoldenTestFile.rpc_methods

    @pytest.mark.trio
    async def test_initial_view_is_empty(self):
        """Both topics start on the state of a source that reads nothing"""
        tree_source = await GoldenTestTree(CONN_ID, None).get_source()
        file_source = await GoldenTestFile(CONN_ID, None).get_source()

        assert tree_source is GoldenTestTreeSource()
        assert file_source is GoldenTestFileSource()
        assert tree_source.mod_name == ''
        assert file_source.mod_name == ''
        assert file_source.file == ''

    @pytest.mark.trio
    async def test_set_mod_rpc(self, fs, tests_root):
        fs.create_file(tests_root.joinpath('map/test_a.py'))
        topic = GoldenTestTree(CONN_ID, None)

        await GoldenTestTree.rpc_methods['set_mod'].call_async(topic, {'mod_name': MOD_NAME})

        assert GoldenTestTreeSource().mod_name == MOD_NAME
        assert list(GoldenTestTreeSource().dirs) == ['']

    @pytest.mark.trio
    async def test_set_mod_rpc_unknown(self, tests_root):
        topic = GoldenTestTree(CONN_ID, None)

        with pytest.raises(RpcValueError) as e:
            await GoldenTestTree.rpc_methods['set_mod'].call_async(topic, {'mod_name': 'no_such_mod'})
        assert 'No such mod' in str(e.value)
        assert GoldenTestTreeSource().mod_name == ''

    @pytest.mark.trio
    async def test_sub_and_unsub_folder_rpc(self, fs, tests_root):
        fs.create_file(tests_root.joinpath('map/test_a.py'))
        topic = GoldenTestTree(CONN_ID, None)
        call = GoldenTestTree.rpc_methods
        source = await topic.get_source()
        sub = MockTopic(topic_name=GoldenTestTreeSource.TOPIC_NAME)
        await source.subscribe(sub)

        await call['set_mod'].call_async(topic, {'mod_name': MOD_NAME})
        await call['sub_folder'].call_async(topic, {'path': 'map'})
        await settle()
        assert entry_shape(source.dirs['map']) == [('map/test_a.py', False)]

        await call['unsub_folder'].call_async(topic, {'path': 'map'})
        await settle()
        assert list(source.dirs) == ['']

    @pytest.mark.trio
    async def test_set_file_rpc(self, fs, tests_root):
        fs.create_file(tests_root.joinpath('map/test_a.py'), contents=TEST_SOURCE)
        topic = GoldenTestFile(CONN_ID, None)
        source = await topic.get_source()
        sub = MockTopic(topic_name=GoldenTestFileSource.TOPIC_NAME)
        await source.subscribe(sub)

        await GoldenTestFile.rpc_methods['set_file'].call_async(
            topic, {'mod_name': MOD_NAME, 'file': 'map/test_a.py'},
        )
        await settle()

        assert source.abs_file == str(tests_root.joinpath('map/test_a.py'))
        assert decode(sub.sent[-1]).v['source'] == TEST_SOURCE

    @pytest.mark.trio
    async def test_set_mod_rpc_clears_the_file(self, fs, tests_root):
        """The topics stay consistent: a mod switch clears the file view"""
        fs.create_file(tests_root.joinpath('map/test_a.py'), contents=TEST_SOURCE)
        tree = GoldenTestTree(CONN_ID, None)
        file = GoldenTestFile(CONN_ID, None)

        await GoldenTestFile.rpc_methods['set_file'].call_async(
            file, {'mod_name': MOD_NAME, 'file': 'map/test_a.py'},
        )
        await GoldenTestTree.rpc_methods['set_mod'].call_async(tree, {'mod_name': OTHER_MOD})

        assert GoldenTestFileSource().file == ''
        assert GoldenTestFileSource().watch_paths() == []


class TestRegistered:
    """The topics are served by the websocket server"""

    def test_registered_topics(self):
        from alasio.backend.ws.topic import WebsocketServer

        assert WebsocketServer.ALL_TOPIC_CLASS['GoldenTestTree'] is GoldenTestTree
        assert WebsocketServer.ALL_TOPIC_CLASS['GoldenTestFile'] is GoldenTestFile


def test_entry_shape_of_a_file():
    """A file entry has is_dir False, a directory entry True"""
    assert GoldenTestEntry(name='test_a.py', path='test_a.py').is_dir is False
    assert GoldenTestEntry(name='map', path='map', is_dir=True).is_dir is True
