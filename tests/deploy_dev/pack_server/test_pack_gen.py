"""
Tests for PackRepoGen: generate the packs of a repo to the run directory.

The output layout is fixed by PackRepoModel: the full pack of the latest
commit, an update pack from every lookback commit to it, latest.pack, and
only the folder of the latest commit is kept.

Every output is verified with the decode side of the library
(alasio/deploy/pack): the packs are decoded and their checksums validated,
the update packs are applied to an unpacked old version with UpdateJob and
the result is compared to the tree of the generated full pack, so the test
covers the path from the git repo to the client.

The mock repos are built inside the tests, when the fake filesystem is
active: MockGitRepo and the pack encoders read the builtin .gitattributes
of the library (env.ALASIO_ROOT), which the run_dir fixture provides in
the fake filesystem.
"""
import os

import pytest

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy.pack.job_update import UpdateJob
from alasio.deploy.pack.server_file import LatestInfo
from alasio.deploy_dev.pack.pack_full import PackFull
from alasio.deploy_dev.pack_server import pack_gen
from alasio.deploy_dev.pack_server.gate import RunDirError, check_run_dir
from alasio.deploy_dev.pack_server.model import LookbackConfig, PackRepoModel, RepoConfig
from alasio.deploy_dev.pack_server.pack_gen import PackRepoGen
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_write, file_read_bytes
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401

# the builtin .gitattributes of the library, copied into the fake
# filesystem by the run_dir fixture, see GitAttributes
BUILTIN_GITATTRIBUTES = env.ALASIO_ROOT.joinpath('.gitattributes').atomic_read_bytes()

# rules of the mock repos: text files are lf, notes and guides are crlf
GITATTRIBUTES = (
    b'*.py text eol=lf\n'
    b'*.txt text eol=crlf\n'
    b'*.png binary\n'
)

# versions of the mock repo, a chain of commits, the oldest commit first.
# Every version carries the record types a pack can hold: A (added), M
# (modified), D (deleted), R (renamed), and the eol cases lf / crlf /
# binary, so the full pack and the update packs of the version cover them
VERSIONS = {
    'c1': {
        '.gitattributes': GITATTRIBUTES,
        'backend/__init__.py': b'',
        'backend/main.py': b'print("hello")\n',
        'docs/notes.txt': b'line 1\r\nline 2\r\n',
        'assets/logo.png': bytes(range(256)) * 4,
    },
    'c2': {
        '.gitattributes': GITATTRIBUTES,
        'backend/__init__.py': b'',
        'backend/main.py': b'print("hello")\nprint("world")\n',
        'backend/tools/helper.py': b'def helper():\n    return 1\n',
        'docs/notes.txt': b'line 1\r\nline 2\r\nline 3\r\n',
        'docs/draft.md': b'# Draft\n',
        'assets/logo.png': bytes(range(256)) * 4,
    },
    'c3': {
        '.gitattributes': GITATTRIBUTES,
        'backend/__init__.py': b'',
        'backend/main.py': b'print("hello")\nprint("world")\nprint("bye")\n',
        'backend/tools/helper.py': b'def helper():\n    return 2\n',
        'docs/guide.txt': b'line 1\r\nline 2\r\nline 3\r\n',
        'assets/logo.png': bytes(range(256)) * 8,
    },
}

# the config of the tests, the lookback day limit is off so the synthetic
# commit times of the mock repos are all inside the window
CONFIG = PackRepoModel(
    Repo=RepoConfig(
        Remote='https://github.com/Author/Repo',
        Author='Author',
        Repo='Repo',
        Branch='master',
    ),
    Lookback=LookbackConfig(MaxCommitDay=0),
)

# folder of the packs of CONFIG in the run directory, the packrepo folder
# of the repo folder
PACK_ROOT = 'pack/Author_Repo_master/packrepo'


@pytest.fixture
def run_dir(fs, monkeypatch):
    """
    A run directory of the pack server in the fake filesystem.

    env.ALASIO_ROOT points at a folder of its own with the builtin
    .gitattributes of the library, so the pack encoders resolve the eol of
    the files in the fake filesystem.

    Returns:
        PathStr: Absolute path of the run directory
    """
    root = PathStr.new(fs.root_dir.path)
    library = root.joinpath('alasio')
    run = root.joinpath('pack_server')
    fs.create_dir(library)
    fs.create_dir(run)
    fs.create_file(library.joinpath('.gitattributes'), contents=BUILTIN_GITATTRIBUTES)
    monkeypatch.setattr(env, 'ALASIO_ROOT', library)
    monkeypatch.setattr(env, 'PROJECT_ROOT', run)
    return run


def join_path(root, *parts):
    """
    Join path parts one by one, PathStr.joinpath takes one part only.

    Args:
        root (str): Root path
        *parts (str): Parts to join

    Returns:
        PathStr: Joined path
    """
    path = PathStr.new(root)
    for part in parts:
        path = path.joinpath(part)
    return path


def make_repo(versions, branch='master'):
    """
    Build a mock repo of a linear commit chain.

    Args:
        versions (dict): {commit: {path: content | (content, mode)}}, the
            oldest commit first, the parent of a commit is the one before it
        branch (str): Branch of the head. Defaults to 'master'.

    Returns:
        MockGitRepo: Repo, the head of the branch is the newest commit
    """
    repo = MockGitRepo()
    parent = None
    for index, (commit, files) in enumerate(versions.items()):
        for path, value in files.items():
            if isinstance(value, tuple):
                content, mode = value
            else:
                content, mode = value, 644
            repo.register_file(commit, path, content, mode=mode)
        repo.register_commit(
            commit, parents=[parent] if parent else None,
            author_name='Author', author_time=index, message=f'commit {commit}')
        parent = commit
    repo.register_branch(branch, parent)
    repo.register_head(parent)
    return repo


def push_commit(repo, commit, files, parents, branch='master', author_time=0):
    """
    Add a commit to a mock repo and move its branch to it.

    Args:
        repo (MockGitRepo): Repo to extend
        commit (str): Commit sha1 of the new commit
        files (dict): {path: content | (content, mode)} of the commit
        parents (list[str]): Parent commit sha1s
        branch (str): Branch to move. Defaults to 'master'.
        author_time (int): Author / committer time. Defaults to 0.
    """
    for path, value in files.items():
        if isinstance(value, tuple):
            content, mode = value
        else:
            content, mode = value, 644
        repo.register_file(commit, path, content, mode=mode)
    repo.register_commit(commit, parents=parents, author_name='Author', author_time=author_time,
                         message=f'commit {commit}')
    repo.register_branch(branch, commit)
    repo.register_head(commit)


def read_tree(root):
    """
    Read the working tree under a root as {path: content}.

    The log folder of the logger is not part of the tree.

    Args:
        root (str): Root folder

    Returns:
        dict[str, bytes]: {filepath: content}
    """
    tree = {}
    for folder, folders, filenames in os.walk(root):
        folders[:] = [name for name in folders if name != 'log']
        for name in filenames:
            path = os.path.join(folder, name)
            key = os.path.relpath(path, root).replace(os.sep, '/')
            tree[key] = file_read_bytes(path)
    return tree


def make_client(monkeypatch, fs, name='client'):
    """
    Move env.PROJECT_ROOT to a client folder of the fake filesystem.

    The working tree of the client is unpacked to a folder of its own: the
    packs generated by the tests live in the run directory, which is not
    part of the client tree.

    Args:
        monkeypatch (MonkeyPatch): Patcher of the test
        fs (FakeFilesystem): Fake filesystem of the test
        name (str): Name of the folder. Defaults to 'client'.

    Returns:
        PathStr: Path of the client folder
    """
    client = PathStr.new(fs.root_dir.path).joinpath(name)
    fs.create_dir(client)
    monkeypatch.setattr(env, 'PROJECT_ROOT', client)
    return client


def expected_tree(full):
    """
    The working tree of a full pack, as the client unpacks it.

    Args:
        full (PackDecodeBase): Decoder of the full pack

    Returns:
        dict[str, bytes]: {filepath: content}, the index pack included
    """
    tree = {
        path: bytes(full.catfile(info))
        for path, info in full.fileinfo.items()
        if info.edit != 2
    }
    tree['.pack/index.pack'] = bytes(full.extract_index_pack())
    return tree


class TestConfigAndFolders:
    """The run directory paths of the generator.

    The empty Author / Repo / Branch of a config are checked by the config
    reader, see TestPackRepoConfig in test_model.py, the generator does not
    check them again.
    """

    def test_branch_with_path_separator(self, fs, run_dir):
        """A Branch that would nest the pack folder inside another config is refused."""
        config = PackRepoModel(Repo=RepoConfig(Author='Author', Repo='Repo', Branch='feature/x'))
        with pytest.raises(ValueError):
            PackRepoGen(make_repo(VERSIONS), config)

    def test_pack_folder(self, fs, run_dir):
        """The folder of the packs and of the latest version are named by the config."""
        gen = PackRepoGen(make_repo(VERSIONS), CONFIG)
        assert gen.repo_folder == join_path(run_dir, 'pack', 'Author_Repo_master')
        assert gen.pack_folder == join_path(run_dir, 'pack', 'Author_Repo_master', 'packrepo')
        assert gen.version_folder == join_path(
            run_dir, 'pack', 'Author_Repo_master', 'packrepo', 'c3')

    def test_run_dir_is_a_mod(self, fs, run_dir, monkeypatch):
        """The generator refuses to run in a mod, like the config reader."""
        fs.create_file(join_path(run_dir, 'module', 'main.py'), contents='')
        # check_run_dir runs once per process (init_once), the bare check runs
        # the gate for the run directory of this test
        monkeypatch.setattr(pack_gen, 'check_run_dir', check_run_dir.__wrapped__)
        with pytest.raises(RunDirError):
            PackRepoGen(make_repo(VERSIONS), CONFIG).run()


class TestRun:
    """Generation of the packs of a repo."""

    def test_full_pack(self, fs, run_dir):
        """The full pack of the latest commit is written and decodes to the version."""
        repo = make_repo(VERSIONS)
        PackRepoGen(repo, CONFIG).run()

        file = join_path(run_dir, PACK_ROOT, 'c3', 'full_c3.pack')
        assert file.isfile()
        decoder = PackDecodeBase(file_read_bytes(file))
        decoder.validate()
        assert decoder.current_version == 'c3'
        assert decoder.old_version == ''

        # every file of the version is recorded and decodes back to the
        # git blob of the repo, the eol rule of the version applied
        for path, entry in repo.list_files('c3').items():
            info = decoder.fileinfo[path]
            assert info.edit != 2
            assert bytes(decoder.catfile(info)) == PackDecodeBase.apply_eol(
                repo.cat(entry.sha1).decoded, info.eol)
        # the commit history is packed as an extra file
        assert '.pack/history.pack' in decoder.fileinfo
        # backend/tools has no __init__.py in the version, the deleted
        # marker of the folder is added
        assert decoder.fileinfo['backend/tools/__init__.py'].edit == 2

    def test_update_packs(self, fs, run_dir):
        """An update pack from every lookback commit to the latest one is written."""
        repo = make_repo(VERSIONS)
        with logger.mock_capture_writer() as capture:
            PackRepoGen(repo, CONFIG).run()

        for old in ('c1', 'c2'):
            file = join_path(run_dir, PACK_ROOT, 'c3', f'update_{old}.pack')
            assert file.isfile()
            decoder = PackDecodeBase(file_read_bytes(file))
            decoder.validate()
            assert decoder.current_version == 'c3'
            assert decoder.old_version == old
        # every pack of the version lives in the folder of the latest commit,
        # the lookback versions have no folder of their own
        assert list(join_path(run_dir, PACK_ROOT).iter_foldernames()) == ['c3']
        # every update pack is logged with its position in the lookback window,
        # the lookback commits are the newest first
        assert capture.fd.any_contains('[1/2] Packing update pack from c2')
        assert capture.fd.any_contains('[2/2] Packing update pack from c1')

    def test_update_pack_records(self, fs, run_dir):
        """The update pack records the changes from the old version to the latest one."""
        repo = make_repo(VERSIONS)
        PackRepoGen(repo, CONFIG).run()

        decoder = PackDecodeBase(file_read_bytes(
            join_path(run_dir, PACK_ROOT, 'c3', 'update_c2.pack')))
        # docs/notes.txt was renamed to docs/guide.txt, the content is identical
        renamed = decoder.fileinfo['docs/guide.txt']
        assert renamed.edit == 3
        assert renamed.source_path == 'docs/notes.txt'
        # docs/draft.md was deleted
        assert decoder.fileinfo['docs/draft.md'].edit == 2
        # modified files carry data, the old content is the patch dictionary
        modified = decoder.fileinfo['backend/main.py']
        assert modified.edit == 1
        assert modified.data_size > 0
        # the index pack is a normal record of the update
        assert '.pack/index.pack' in decoder.fileinfo
        assert '.pack/index.pack' in decoder.refinfo

    def test_latest_pack(self, fs, run_dir):
        """latest.pack is written with the latest version and its index checksum."""
        repo = make_repo(VERSIONS)
        PackRepoGen(repo, CONFIG).run()

        data = file_read_bytes(join_path(run_dir, PACK_ROOT, 'latest.pack'))
        info = LatestInfo.parse(data)
        assert info.version == 'c3'
        full = PackDecodeBase(file_read_bytes(
            join_path(run_dir, PACK_ROOT, 'c3', 'full_c3.pack')))
        assert info.checksum == full.index_checksum
        # the checksum of the index pack, the bytes the clients hold in
        # .pack/index.pack after unpacking the full pack
        assert data == b'c3' + bytes(full.extract_index_pack())[-20:]

    def test_single_commit(self, fs, run_dir):
        """A repo of a single commit has the full pack only, no update pack."""
        repo = make_repo({'c1': VERSIONS['c1']})
        PackRepoGen(repo, CONFIG).run()

        assert join_path(run_dir, PACK_ROOT, 'latest.pack').isfile()
        assert list(join_path(run_dir, PACK_ROOT, 'c1').iter_filenames()) == ['full_c1.pack']

    def test_lookback_window(self, fs, run_dir):
        """Only the commits of the lookback window have an update pack."""
        repo = make_repo(VERSIONS)
        config = PackRepoModel(
            Repo=CONFIG.Repo, Lookback=LookbackConfig(MaxCommitCount=2, MaxCommitDay=0))
        PackRepoGen(repo, config).run()

        # the latest commit counts into MaxCommitCount, so c2 is the only
        # lookback version and c1 has no update pack
        assert join_path(run_dir, PACK_ROOT, 'c3', 'update_c2.pack').isfile()
        assert not join_path(run_dir, PACK_ROOT, 'c3', 'update_c1.pack').isfile()

    def test_stale_folders_removed(self, fs, run_dir):
        """A new latest commit keeps its folder only, the older folders are removed."""
        repo = make_repo(VERSIONS)
        PackRepoGen(repo, CONFIG).run()
        assert join_path(run_dir, PACK_ROOT, 'c3').isdir()
        # a folder of an earlier run that no latest.pack links anymore
        fs.create_file(join_path(run_dir, PACK_ROOT, 'stale', 'full_stale.pack'), contents=b'x')

        # a new commit is pushed to the branch
        push_commit(repo, 'c4', VERSIONS['c3'], parents=['c3'], author_time=4)
        PackRepoGen(repo, CONFIG).run()

        # only the folder of the latest commit is kept
        assert list(join_path(run_dir, PACK_ROOT).iter_foldernames()) == ['c4']
        assert join_path(run_dir, PACK_ROOT, 'c4', 'full_c4.pack').isfile()
        assert join_path(run_dir, PACK_ROOT, 'c4', 'update_c3.pack').isfile()
        assert not join_path(run_dir, PACK_ROOT, 'c3').isdir()
        assert not join_path(run_dir, PACK_ROOT, 'stale').isdir()
        # and latest.pack points at the new version
        info = LatestInfo.parse(file_read_bytes(join_path(run_dir, PACK_ROOT, 'latest.pack')))
        assert info.version == 'c4'

    def test_stale_folder_removal_is_best_effort(self, fs, run_dir, monkeypatch):
        """A stale folder that cannot be removed does not fail the generation."""
        repo = make_repo(VERSIONS)
        PackRepoGen(repo, CONFIG).run()
        push_commit(repo, 'c4', VERSIONS['c3'], parents=['c3'], author_time=4)

        def fail(folder):
            raise PermissionError(f'folder is held: {folder}')

        monkeypatch.setattr(pack_gen, 'atomic_rmtree', fail)
        with logger.mock_capture_writer() as capture:
            PackRepoGen(repo, CONFIG).run()
        assert capture.fd.any_contains('Failed to remove stale pack folder')
        # the new version is published, the stale folder is left to the next run
        assert join_path(run_dir, PACK_ROOT, 'c4', 'full_c4.pack').isfile()
        assert join_path(run_dir, PACK_ROOT, 'c3').isdir()
        info = LatestInfo.parse(file_read_bytes(join_path(run_dir, PACK_ROOT, 'latest.pack')))
        assert info.version == 'c4'

    def test_failed_run_keeps_latest_pack(self, fs, run_dir, monkeypatch):
        """A failed generation does not publish the partial version."""
        repo = make_repo(VERSIONS)
        PackRepoGen(repo, CONFIG).run()
        before = file_read_bytes(join_path(run_dir, PACK_ROOT, 'latest.pack'))

        # a new commit arrives, but building its update packs fails
        push_commit(repo, 'c4', VERSIONS['c3'], parents=['c3'], author_time=4)

        def fail(self, pack):
            raise RuntimeError('pack build failed')

        monkeypatch.setattr(PackRepoGen, '_write_update_packs', fail)
        with pytest.raises(RuntimeError):
            PackRepoGen(repo, CONFIG).run()

        # latest.pack still points at the old version and its folder is kept,
        # the clients are not switched to the partial version
        assert file_read_bytes(join_path(run_dir, PACK_ROOT, 'latest.pack')) == before
        assert join_path(run_dir, PACK_ROOT, 'c3', 'full_c3.pack').isfile()


class TestExistingPacks:
    """The packs of an earlier run are kept, latest.pack is written again."""

    def test_existing_packs_are_kept(self, fs, run_dir, monkeypatch):
        """A complete output folder is kept as it is: nothing is built again."""
        repo = make_repo(VERSIONS)
        PackRepoGen(repo, CONFIG).run()
        tree = read_tree(join_path(run_dir, PACK_ROOT))

        # no pack of the version may be built again: the encoder of the
        # second run fails the test when it is constructed, and the writes
        # are collected
        class NoPackUpdate:
            def __init__(self, *args, **kwargs):
                raise AssertionError('the pack of the version must not be built again')

        written = []
        monkeypatch.setattr(pack_gen, 'PackUpdate', NoPackUpdate)
        monkeypatch.setattr(
            pack_gen, 'atomic_write_stream', lambda file, data: written.append(str(file)))
        PackRepoGen(repo, CONFIG).run()

        assert written == []
        # latest.pack is written again, from the checksum of the kept full
        # pack: the file is the same as the one of the first run
        assert read_tree(join_path(run_dir, PACK_ROOT)) == tree

    def test_latest_pack_is_written_again(self, fs, run_dir):
        """latest.pack is written on every run, from the checksum of the kept full pack."""
        repo = make_repo(VERSIONS)
        PackRepoGen(repo, CONFIG).run()
        latest = join_path(run_dir, PACK_ROOT, 'latest.pack')
        data = file_read_bytes(latest)
        os.remove(latest)

        PackRepoGen(repo, CONFIG).run()

        assert file_read_bytes(latest) == data

    def test_missing_pack_is_built_again(self, fs, run_dir, monkeypatch):
        """Only the pack that is missing is built again."""
        repo = make_repo(VERSIONS)
        PackRepoGen(repo, CONFIG).run()
        target = join_path(run_dir, PACK_ROOT, 'c3', 'update_c1.pack')
        data = file_read_bytes(target)
        os.remove(target)

        # the lookback version of every pack that is built, in the order of the run
        built = []
        real_pack_update = pack_gen.PackUpdate

        class CountingPackUpdate:
            def __init__(self, pack, old):
                built.append(old)
                self.update = real_pack_update(pack, old)

            def iter_pack_data(self):
                return self.update.iter_pack_data()

        monkeypatch.setattr(pack_gen, 'PackUpdate', CountingPackUpdate)
        PackRepoGen(repo, CONFIG).run()

        assert built == ['c1']
        assert file_read_bytes(target) == data

    def test_pack_of_another_format_is_written_again(self, fs, run_dir):
        """A pack of another pack format is not kept: a version folder mixes no formats."""
        repo = make_repo(VERSIONS)
        PackRepoGen(repo, CONFIG).run()

        folder = join_path(run_dir, PACK_ROOT, 'c3')
        names = ('full_c3.pack', 'update_c1.pack', 'update_c2.pack')
        for name in names:
            file = folder.joinpath(name)
            data = bytearray(file_read_bytes(file))
            # the pack version is the single byte behind b'PACK'
            data[4] = 1
            atomic_write(file, bytes(data))

        PackRepoGen(repo, CONFIG).run()

        for name in names:
            assert file_read_bytes(folder.joinpath(name))[4] == 0
        # latest.pack is built from the full pack this run wrote
        info = LatestInfo.parse(file_read_bytes(join_path(run_dir, PACK_ROOT, 'latest.pack')))
        full = PackDecodeBase(file_read_bytes(folder.joinpath('full_c3.pack')))
        assert info.checksum == full.index_checksum

    def test_pack_that_is_not_a_pack_is_written_again(self, fs, run_dir):
        """A kept file that cannot be read as a pack is overwritten, the run goes on."""
        repo = make_repo(VERSIONS)
        PackRepoGen(repo, CONFIG).run()
        file = join_path(run_dir, PACK_ROOT, 'c3', 'update_c1.pack')
        atomic_write(file, b'NOPE' + b'\x00' * 60)

        with logger.mock_capture_writer() as capture:
            PackRepoGen(repo, CONFIG).run()

        assert capture.fd.any_contains('Failed to read the existing pack')
        decoder = PackDecodeBase(file_read_bytes(file))
        decoder.validate()
        assert decoder.old_version == 'c1'

    def test_pack_of_another_version_is_written_again(self, fs, run_dir):
        """A pack of another version pair is not kept, a version folder mixes no versions."""
        repo = make_repo(VERSIONS)
        PackRepoGen(repo, CONFIG).run()
        # the update pack of c2, put at the path of the update pack of c1
        source = join_path(run_dir, PACK_ROOT, 'c3', 'update_c2.pack')
        file = join_path(run_dir, PACK_ROOT, 'c3', 'update_c1.pack')
        atomic_write(file, file_read_bytes(source))

        with logger.mock_capture_writer() as capture:
            PackRepoGen(repo, CONFIG).run()

        assert capture.fd.any_contains('Existing pack is not the pack of this version')
        decoder = PackDecodeBase(file_read_bytes(file))
        decoder.validate()
        assert decoder.old_version == 'c1'


class TestUpdateApplies:
    """The generated update packs upgrade an unpacked old version."""

    @pytest.mark.trio
    async def test_update_applies(self, fs, run_dir, monkeypatch):
        """The update pack upgrades the old tree to the tree of the full pack."""
        repo = make_repo(VERSIONS)
        PackRepoGen(repo, CONFIG).run()

        # the tree of the generated full pack, the expected result of the update
        full = PackDecodeBase(file_read_bytes(
            join_path(run_dir, PACK_ROOT, 'c3', 'full_c3.pack')))
        expected = expected_tree(full)

        # the client that has been running the old version
        client = make_client(monkeypatch, fs)
        await UnpackJob(b''.join(PackFull(repo, 'c2').iter_pack_data())).run()
        old_tree = read_tree(client)
        assert 'docs/notes.txt' in old_tree
        assert 'docs/guide.txt' not in old_tree

        # apply the update pack generated by the server
        update = file_read_bytes(join_path(run_dir, PACK_ROOT, 'c3', 'update_c2.pack'))
        job = UpdateJob(update)
        with logger.mock_capture_writer():
            assert await job.run()
        assert job.error == []
        assert read_tree(client) == expected
        assert not client.joinpath('.pack/workspace').exists()

    @pytest.mark.trio
    async def test_update_from_every_lookback(self, fs, run_dir, monkeypatch):
        """Every lookback version can be updated to the latest one."""
        repo = make_repo(VERSIONS)
        PackRepoGen(repo, CONFIG).run()
        full = PackDecodeBase(file_read_bytes(
            join_path(run_dir, PACK_ROOT, 'c3', 'full_c3.pack')))
        expected = expected_tree(full)

        for index, old in enumerate(('c1', 'c2')):
            client = make_client(monkeypatch, fs, f'client_{index}')
            await UnpackJob(b''.join(PackFull(repo, old).iter_pack_data())).run()
            update = file_read_bytes(join_path(run_dir, PACK_ROOT, 'c3', f'update_{old}.pack'))
            job = UpdateJob(update)
            with logger.mock_capture_writer():
                assert await job.run()
            assert job.error == []
            assert read_tree(client) == expected
