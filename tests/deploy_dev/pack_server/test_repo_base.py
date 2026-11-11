"""
Tests for the state the steps of a repo share: repo_base.RepoBase.

The proxy of the config (PackRepoModel.Env) routes the downloads of the pack
server: the wheels come from the index a pin declares and never from a mirror
of the client, so a machine that cannot reach those indexes downloads them
through the proxy the operator configured, see RepoBase.fetch_client. One client
serves every fetcher of a step, with or without the proxy.

The removal of one stale entry (repo_base._remove_stale_entry) has a class of
its own: it is the boundary between the cleanup and the filesystem, the only
place of the flow where an error is answered with a log line instead of an
exception.
"""
import os

import pytest
import trio

from alasio.deploy_dev.pack_server import repo_base
from alasio.deploy_dev.pack_server.repo_base import RepoBase, _is_missing, _read_tree, _remove_stale_entry
from alasio.ext.path import PathStr
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack_server.conftest import join_path
from tests.deploy_dev.pack_server.test_repo import PROXY, make_config, make_proxy_config, make_repo


pytestmark = [
    pytest.mark.parallel,
]


class FakeClient:
    """
    A client of the tests: it sends nothing and records the close of the step.
    """

    def __init__(self):
        self.closed = False

    async def aclose(self):
        """Close the client, the end of the step that owned it."""
        self.closed = True


class TestFetchClient:
    """The client the wheel fetches of one step are sent with."""

    def test_direct_client_of_the_step(self, fs, run_dir, monkeypatch):
        """Without a proxy the step still gets one client of its own, closed at the end."""
        created = []

        def create_client(proxy):
            client = FakeClient()
            created.append((proxy, client))
            return client

        monkeypatch.setattr(repo_base, 'create_client', create_client)
        base = RepoBase(make_repo(), make_config())

        async def open():
            async with base.fetch_client() as client:
                assert client is created[0][1]
                assert client.closed is False
                return client

        with logger.mock_capture_writer() as capture:
            client = trio.run(open)

        # the step creates the client once, without a proxy, and closes it
        assert created == [('', client)]
        assert client.closed is True
        assert capture.concat() == ''

    def test_proxy(self, fs, run_dir, monkeypatch):
        """The proxy of the config builds the client of the step, closed with the step."""
        created = []

        def create_client(proxy):
            client = FakeClient()
            created.append((proxy, client))
            return client

        monkeypatch.setattr(repo_base, 'create_client', create_client)
        base = RepoBase(make_repo(), make_proxy_config())

        async def open():
            async with base.fetch_client() as client:
                # the client is the one of the proxy, open for the whole step
                assert client is created[0][1]
                assert client.closed is False
                return client

        with logger.mock_capture_writer() as capture:
            client = trio.run(open)

        assert created == [(PROXY, client)]
        assert client.closed is True
        assert capture.concat() == f"""\
INFO | Downloading the wheels through the proxy "{PROXY}"
"""

    def test_injected_client(self, fs, run_dir, monkeypatch):
        """An injected client is used as it is, the proxy of the config is not applied."""
        def create_client(proxy):
            raise AssertionError('The proxy of the config must not be used with an injected client')

        monkeypatch.setattr(repo_base, 'create_client', create_client)
        client = FakeClient()
        base = RepoBase(make_repo(), make_proxy_config(), client)

        async def open():
            async with base.fetch_client() as held:
                return held

        with logger.mock_capture_writer() as capture:
            assert trio.run(open) is client

        # the client of the caller is not closed by the step, the route of it is
        # the one the caller set up
        assert client.closed is False
        assert capture.concat() == ''


class TestIsMissing:
    """The look up that tells "nothing was removed" from "the path is still there"."""

    def test_missing_file(self, fs):
        assert _is_missing('/data/missing.pack') is True

    def test_existing_file(self, fs):
        fs.create_file('/data/a.pack', contents=b'x')
        assert _is_missing('/data/a.pack') is False

    def test_existing_folder(self, fs):
        fs.create_dir('/data/folder')
        assert _is_missing('/data/folder') is False

    def test_missing_folder(self, fs):
        assert _is_missing('/data/folder') is True

    def test_not_a_folder(self, fs):
        """A path whose parent is a file has no entry either."""
        fs.create_file('/data/a.pack', contents=b'x')
        assert _is_missing('/data/a.pack/inner.pack') is True

    def test_unreadable_path_is_reported_present(self, fs, monkeypatch):
        """A path that cannot be looked up is left for the next run to retry."""
        fs.create_file('/data/a.pack', contents=b'x')

        def denied(path):
            raise PermissionError(13, 'Permission denied', path)

        monkeypatch.setattr(os, 'lstat', denied)
        assert _is_missing('/data/a.pack') is False


class TestRemoveStaleEntry:
    """The removal of one entry of the cleanup plan, the boundary of the flow."""

    def test_file_removed(self, fs):
        fs.create_file('/data/stale.pack', contents=b'x')
        assert _remove_stale_entry('/data/stale.pack', is_dir=False) is True
        assert not os.path.exists('/data/stale.pack')

    def test_folder_removed(self, fs):
        fs.create_dir('/data/folder')
        assert _remove_stale_entry('/data/folder', is_dir=True) is True
        assert not os.path.exists('/data/folder')

    def test_file_gone_before_the_removal(self, fs):
        """A file another process removed first is not a failure."""
        assert _remove_stale_entry('/data/stale.pack', is_dir=False) is True

    def test_folder_gone_before_the_removal(self, fs):
        assert _remove_stale_entry('/data/folder', is_dir=True) is True

    def test_file_still_there(self, fs, monkeypatch):
        """A removal that answers False with the path still there is a failure."""
        fs.create_file('/data/stale.pack', contents=b'x')
        monkeypatch.setattr(repo_base, 'file_remove', lambda file: False)
        with logger.mock_capture_writer() as capture:
            assert _remove_stale_entry('/data/stale.pack', is_dir=False) is False
        assert capture.concat() == f"""\
WARNING | Failed to remove stale file "/data/stale.pack"
"""

    def test_folder_not_empty(self, fs):
        """A folder a concurrent writer filled after the scan is kept, with a warning."""
        fs.create_file('/data/folder/inner.pack', contents=b'x')
        with logger.mock_capture_writer() as capture:
            assert _remove_stale_entry('/data/folder', is_dir=True) is False
        assert capture.concat() == f"""\
WARNING | Failed to remove stale folder "/data/folder"
"""
        assert os.path.isdir('/data/folder')

    def test_file_locked(self, fs, monkeypatch):
        """The PermissionError of a file a process still holds open is logged, not raised."""
        fs.create_file('/data/stale.pack', contents=b'x')

        def locked(file):
            raise PermissionError(13, 'The process cannot access the file', file)

        monkeypatch.setattr(repo_base, 'file_remove', locked)
        with logger.mock_capture_writer() as capture:
            assert _remove_stale_entry('/data/stale.pack', is_dir=False) is False
        assert capture.concat() == f"""\
WARNING | Failed to remove stale file "/data/stale.pack": \
[Errno 13] The process cannot access the file: '/data/stale.pack'
"""
        assert os.path.exists('/data/stale.pack')

    def test_folder_locked(self, fs, monkeypatch):
        """The failure of a folder removal is logged too, never raised."""
        fs.create_dir('/data/folder')

        def locked(folder):
            raise OSError(5, 'Input/output error', folder)

        monkeypatch.setattr(repo_base, 'folder_rmtree_empty', locked)
        with logger.mock_capture_writer() as capture:
            assert _remove_stale_entry('/data/folder', is_dir=True) is False
        assert capture.concat() == f"""\
WARNING | Failed to remove stale folder "/data/folder": \
[Errno 5] Input/output error: '/data/folder'
"""

    def test_file_not_found_of_the_removal(self, fs, monkeypatch):
        """An entry that vanishes between the lookup and the unlink is gone, not failed."""
        fs.create_file('/data/stale.pack', contents=b'x')

        def vanished(file):
            raise FileNotFoundError(2, 'No such file or directory', file)

        monkeypatch.setattr(repo_base, 'file_remove', vanished)
        with logger.mock_capture_writer() as capture:
            assert _remove_stale_entry('/data/stale.pack', is_dir=False) is True
        assert capture.concat() == ''


class TestTouch:
    """The record of the files a run uses: only the files of the workspace."""

    @pytest.fixture
    def base(self, fs, run_dir):
        """Workspace of the tests, the folder the record is relative to."""
        return RepoBase(make_repo(), make_config())

    def test_file_of_the_workspace(self, fs, base):
        """A file below the workspace is recorded relative to it."""
        file = base.packrepo_folder.joinpath('latest.pack')
        fs.create_file(file, contents=b'x')
        base.touch(file)
        assert base.repo_manifest() == (['packrepo/latest.pack'], [])

    def test_private_file_of_the_workspace(self, fs, base):
        """A private folder of the workspace is recorded like any other file."""
        file = base.wheel_cache_of('httpx', '0.28.1').joinpath('index.html')
        fs.create_file(file, contents=b'x')
        base.touch(file)
        assert base.repo_manifest() == ([], ['.wheel_cache/httpx/0.28.1/index.html'])

    def test_record_is_a_set(self, fs, base):
        """Touching the same file twice records it once."""
        file = base.packrepo_folder.joinpath('latest.pack')
        fs.create_file(file, contents=b'x')
        base.touch(file)
        base.touch(file)
        assert base.repo_manifest() == (['packrepo/latest.pack'], [])

    def test_path_outside_the_workspace(self, fs, base):
        """A path of another tree is a broken step, not a file of the run."""
        with pytest.raises(ValueError) as e:
            base.touch('/run/elsewhere/latest.pack')
        assert str(e.value) == (
            f'Touched path is not a file of the workspace of "Author_Repo_master": '
            f'"/run/elsewhere/latest.pack"')
        assert base.repo_manifest() == ([], [])

    def test_workspace_itself(self, fs, base):
        """The workspace is not a file of the run: it would preserve the whole tree."""
        with pytest.raises(ValueError) as e:
            base.touch(base.workspace)
        assert str(e.value) == (
            f'Touched path is not a file of the workspace of "Author_Repo_master": '
            f'"{base.workspace}"')
        assert base.repo_manifest() == ([], [])

    def test_sibling_folder_sharing_the_name(self, fs, base):
        """A folder whose name starts with the workspace name is another tree."""
        with pytest.raises(ValueError):
            base.touch(join_path(str(base.workspace) + '_old', 'packrepo', 'latest.pack'))

    def test_relative_path(self, fs, base):
        """A relative path is not a file of the workspace, the steps pass absolute ones."""
        with pytest.raises(ValueError):
            base.touch('packrepo/latest.pack')

    def test_traversal_out_of_the_workspace(self, fs, base):
        """A path that leaves the workspace through '..' is refused by the path rules."""
        path = join_path(join_path(base.workspace, '..'), 'other', 'latest.pack')
        with pytest.raises(ValueError) as e:
            base.touch(path)
        assert str(e.value) == (
            f'Touched path is not a file of the workspace of "Author_Repo_master": '
            f'"{path}": Path component cannot be directory pointer: ".."')
        assert base.repo_manifest() == ([], [])

    @pytest.mark.parametrize('name, reason', [
        ('latest.pack:', 'Path component should not contain character: ":"'),
        ('latest.pack.', 'Path component cannot end with a <dot>'),
        ('latest.pack ', 'Path component cannot end with a <space>'),
        ('CON.pack', 'Path component cannot be reserved system name: CON'),
    ])
    def test_name_the_path_rules_refuse(self, fs, base, name, reason):
        """A name a filesystem or a mirror cannot carry has no entry of its own."""
        path = base.packrepo_folder.joinpath(name)
        with pytest.raises(ValueError) as e:
            base.touch(path)
        assert str(e.value).endswith(f'"{path}": {reason}')
        assert base.repo_manifest() == ([], [])


class TestReadTree:
    """The walk of the workspace the cleanup removes against."""

    @pytest.fixture
    def data(self, fs):
        """Root folder of the tree of the tests, as the walk answers it."""
        root = PathStr.new(fs.root_dir.path).joinpath('data')
        fs.create_dir(root)
        return root

    def test_files_and_folders(self, fs, data):
        """Every file and folder of the tree is read, the root is not an entry."""
        fs.create_file(data.joinpath('a.txt'), contents=b'x')
        fs.create_file(data.joinpath('sub').joinpath('b.txt'), contents=b'x')
        fs.create_file(data.joinpath('sub').joinpath('deep').joinpath('c.txt'), contents=b'x')
        files, folders, blocked = _read_tree(data)
        assert sorted(files) == sorted([
            data.joinpath('a.txt'),
            data.joinpath('sub').joinpath('b.txt'),
            data.joinpath('sub').joinpath('deep').joinpath('c.txt'),
        ])
        assert sorted(folders) == sorted([
            data.joinpath('sub'),
            data.joinpath('sub').joinpath('deep'),
        ])
        assert blocked == []

    def test_empty_tree(self, fs, data):
        assert _read_tree(data) == ([], [], [])

    def test_missing_root(self, fs, data):
        """A root that is not there is an empty tree, not an error."""
        assert _read_tree(data.joinpath('missing')) == ([], [], [])

    def test_root_is_a_file(self, fs, data):
        """A root that is not a folder has no entry, and is not a blocked one."""
        fs.create_file(data.joinpath('a.zip'), contents=b'x')
        assert _read_tree(data.joinpath('a.zip')) == ([], [], [])

    def test_unlistable_folder_is_blocked(self, fs, data):
        """A folder that cannot be listed is reported, its siblings are still read.

        The file system denies the listing of one folder, the way an access
        control list or a scanner holding it does. The walk must not raise: the
        cleanup runs after the version is published.
        """
        locked = data.joinpath('locked')
        fs.create_file(data.joinpath('a.txt'), contents=b'x')
        fs.create_file(locked.joinpath('inner.txt'), contents=b'x')
        fs.create_file(data.joinpath('other').joinpath('b.txt'), contents=b'x')
        original = os.scandir

        def denied(folder):
            if str(folder).replace('\\', '/') == str(locked):
                raise PermissionError(13, 'Access is denied', str(folder))
            return original(folder)

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(os, 'scandir', denied)
        try:
            with logger.mock_capture_writer() as capture:
                files, folders, blocked = _read_tree(data)
        finally:
            monkeypatch.undo()

        # the content of the locked folder is unknown, the rest of the tree is
        # read as usual
        assert sorted(files) == sorted([
            data.joinpath('a.txt'),
            data.joinpath('other').joinpath('b.txt'),
        ])
        assert sorted(folders) == sorted([locked, data.joinpath('other')])
        assert blocked == [locked]
        assert capture.concat() == f"""\
WARNING | Failed to list the folder of the workspace "{locked}": \
[Errno 13] Access is denied: '{locked}'
"""
