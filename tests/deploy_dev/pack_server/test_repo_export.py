"""
Tests for the export of the release trees: ReleaseExport.

The flow of a repo generates one workspace in the run directory, and the export
turns the version it published into the two trees a release is copied from:
`.repo` is the working tree of the version with its ledger, `.dep` is the
install tree of the dependency pack channels with their ledgers. The trees are
verified against the packs they are decoded from, the decode side of the
library: the working tree of a pack is what its records say, and the ledgers
are the index packs the client validates its files with.
"""
import pytest

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job_base import DeployTarget, JobBase
from alasio.deploy.pack.packdep import PACKDEP_PATH, decode_packdep
from alasio.deploy.pack.server_file import LatestInfo
from alasio.deploy_dev.pack_server import repo_export
from alasio.deploy_dev.pack_server.pack_check import PackChecksum
from alasio.deploy_dev.pack_server.repo import Repo
from alasio.deploy_dev.pack_server.repo_export import (
    APP_ASAR, BUNDLED_APP_ASAR, DEP_DIR, DEPLOY_CONFIG, DEPLOY_TEMPLATE, REPO_DIR, ReleaseExportError, ReleaseExportJob
)
from alasio.ext.path.atomic import file_read_bytes, file_write
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy.simple_pip.conftest import build_wheel
from tests.deploy_dev.pack_server.conftest import BUNDLED_APP_ASAR_CONTENT, join_path
from tests.deploy_dev.pack_server.test_repo import (
    DEP_FOLDER, DEPLOY_TEMPLATE_TEXT, LIBRARY_REQUIREMENT, WORKSPACE, entry_source, make_config, make_repo, read_tree,
    register_wheels, repo_file, requirement, run_commit
)

pytestmark = [
    pytest.mark.parallel,
]

# folder of the working tree of the release in the workspace
REPO_ROOT = f'{WORKSPACE}/{REPO_DIR}'

# folder of the site-packages tree of the release in the workspace
DEP_ROOT = f'{WORKSPACE}/{DEP_DIR}'

# the dependency of the tests, and the version its pack channel publishes
DIST = 'httpx'
DIST_VERSION = '0.28.1'

# the version of the library the tests pack, see TestAppAsar
ALASIO_VERSION = '2.0.3'

# the distribution name of the library, the pack dep whose wheel carries the
# electron archive of the client
LIBRARY = 'alasio'

# the content of the two files of the copied record test, the version of the
# repo that carries them
DUPLICATE_CONTENT = b"""\
VALUE = 1
OTHER = 2
"""
COMMIT_DUPLICATE = 'c1'


def repo_root(run_dir):
    """
    Folder of the working tree of the release.

    Returns:
        PathStr: {run}/pack/Author_Repo_master/.repo
    """
    return join_path(run_dir, REPO_ROOT)


def dep_root(run_dir):
    """
    Folder of the site-packages tree of the release.

    Returns:
        PathStr: {run}/pack/Author_Repo_master/.dep
    """
    return join_path(run_dir, DEP_ROOT)


def tree_pack(run_dir, commit='c3'):
    """
    The full pack of the tree of a version.

    Returns:
        PackDecodeBase: The decoder of the pack
    """
    decoder = PackDecodeBase(file_read_bytes(repo_file(run_dir, commit, 'full.pack')))
    decoder.validate()
    return decoder


def dep_pack(run_dir, version=DIST_VERSION):
    """
    The full pack of the dependency of the tests.

    Returns:
        PackDecodeBase: The decoder of the pack
    """
    decoder = PackDecodeBase(file_read_bytes(join_path(run_dir, DEP_FOLDER, version, 'full.pack')))
    decoder.validate()
    return decoder


def make_duplicate_repo(content=DUPLICATE_CONTENT):
    """
    A repo with two files of the same content.

    The second file of the content is a C (copied) record of the full pack: the
    pack carries the content once and the record references it.

    Args:
        content (bytes): Content of the two files

    Returns:
        MockGitRepo: Repo, the head of the branch is the only commit
    """
    repo = MockGitRepo()
    repo.register_file(
        COMMIT_DUPLICATE, 'requirements.txt',
        (requirement('httpx==0.28.1') + LIBRARY_REQUIREMENT).encode())
    repo.register_file(
        COMMIT_DUPLICATE, DEPLOY_TEMPLATE, DEPLOY_TEMPLATE_TEXT.encode('utf-8'))
    repo.register_file(COMMIT_DUPLICATE, 'module/dup_a.py', content)
    repo.register_file(COMMIT_DUPLICATE, 'module/dup_b.py', content)
    repo.register_commit(COMMIT_DUPLICATE, author_name='Author', message='')
    repo.register_branch('master', COMMIT_DUPLICATE)
    repo.register_head(COMMIT_DUPLICATE)
    return repo


def working_tree(decoder):
    """
    The working tree a full pack unpacks to.

    Returns:
        dict[str, bytes]: {path: content}, the deleted markers excluded
    """
    return {
        path: bytes(decoder.catfile(info))
        for path, info in decoder.fileinfo.items()
        if info.edit != 2
    }


class TestWrite:
    """The job of the export: the pack is not copied to the job file."""

    def test_write_is_a_no_op(self, fs, run_dir):
        """write() leaves no job file: the pack of the export is on the disk already."""
        job = ReleaseExportJob(b'', root=join_path(run_dir, 'target'))
        job.write()
        assert not job.job_file.exists()

    def test_no_job_file_in_the_workspace(self, fs, run_dir, mirror):
        """A full run of the flow leaves no job.pack in either exported tree."""
        register_wheels(mirror)
        run_commit(make_repo(), make_config(), mirror.client)
        for folder in (repo_root(run_dir), dep_root(run_dir)):
            assert not [
                path for path in folder.iter_files(recursive=True)
                if path.rsplit('/', 1)[-1] == 'job.pack'
            ]

    def test_workspace_is_cleaned(self, fs, run_dir, mirror):
        """The tmp folder of the unpack job is removed when the tree landed."""
        register_wheels(mirror)
        run_commit(make_repo(), make_config(), mirror.client)
        assert not repo_root(run_dir).joinpath('.pack/workspace').exists()
        assert not dep_root(run_dir).joinpath(f'.pack/{DIST}/workspace').exists()


class TestTree:
    """The working tree of the version."""

    def test_files_are_the_records(self, fs, run_dir, mirror):
        """Every record of the version is a file of the tree, byte for byte."""
        register_wheels(mirror)
        run_commit(make_repo(), make_config(), mirror.client)
        expected = working_tree(tree_pack(run_dir))
        tree = read_tree(repo_root(run_dir))
        # the files the export writes besides the records
        del tree['.pack/index.pack']
        del tree[DEPLOY_CONFIG]
        del tree[APP_ASAR]
        assert tree == expected

    def test_index_pack_is_the_version(self, fs, run_dir, mirror):
        """The ledger of the tree is the index pack of the version latest.pack names."""
        register_wheels(mirror)
        register = run_commit(make_repo(), make_config(), mirror.client)
        full = tree_pack(run_dir)
        ledger = repo_root(run_dir).joinpath('.pack/index.pack')
        assert file_read_bytes(ledger) == bytes(full.extract_index_pack())
        # the checksum the client compares its ledger with, from the barrier
        info = LatestInfo.parse(file_read_bytes(repo_file(run_dir, 'latest.pack')))
        assert info.version == full.current_version
        assert PackChecksum.from_file(ledger).index_checksum == bytes.fromhex(info.checksum)
        assert register.base.latest_commit == info.version

    def test_generated_files(self, fs, run_dir, mirror):
        """The history and the dependency snapshot are the generated files of the pack."""
        register_wheels(mirror)
        run_commit(make_repo(), make_config(), mirror.client)
        full = tree_pack(run_dir)
        tree = repo_root(run_dir)
        for path in ('.pack/history.pack', PACKDEP_PATH):
            assert file_read_bytes(tree.joinpath(path)) == bytes(full.catfile(full.fileinfo[path]))
        entries = decode_packdep(file_read_bytes(tree.joinpath(PACKDEP_PATH)))
        assert list(entries) == ['alasio', DIST]
        assert entries[DIST].version == DIST_VERSION

    def test_deploy_config(self, fs, run_dir, mirror):
        """The deploy config of the tree is a copy of the template of the version."""
        register_wheels(mirror)
        run_commit(make_repo(), make_config(), mirror.client)
        tree = repo_root(run_dir)
        template = file_read_bytes(tree.joinpath(DEPLOY_TEMPLATE))
        assert file_read_bytes(tree.joinpath(DEPLOY_CONFIG)) == template
        # the template is the file the webapp locates the project by, it stays
        assert template == DEPLOY_TEMPLATE_TEXT.encode('utf-8')

    def test_deploy_config_is_idempotent(self, fs, run_dir, mirror):
        """A second run over a tree whose copy is current writes nothing."""
        register_wheels(mirror)
        repo = make_repo()
        config = make_config()
        run_commit(repo, config, mirror.client)
        file = repo_root(run_dir).joinpath(DEPLOY_CONFIG)
        stat = file.stat()
        with logger.mock_capture_writer() as capture:
            run_commit(repo, config, mirror.client)
        assert f'Deploy config exists, skipped: "{file}"' in capture.concat()
        assert file.stat().st_mtime_ns == stat.st_mtime_ns

    def test_deploy_template_is_required(self, fs, run_dir, mirror):
        """A version without the template fails the run, no release can be made."""
        register_wheels(mirror)
        repo = make_repo()
        config = make_config()
        # a version that drops the template of the previous one
        repo.register_file(
            'c4', 'requirements.txt',
            (requirement('httpx==0.28.1') + LIBRARY_REQUIREMENT).encode())
        repo.register_commit('c4', parents=['c3'], author_name='Author', message='')
        repo.register_branch('master', 'c4')
        repo.register_head('c4')
        gen = Repo(repo, config, client=mirror.client)
        with pytest.raises(ReleaseExportError, match='has no config/deploy.template.yaml'):
            gen.run()
        assert not repo_file(run_dir, 'latest.pack').exists()

    def test_deploy_config_of_an_older_export_removed(self, fs, run_dir, mirror):
        """A deploy config a previous export wrote is replaced by the copy."""
        register_wheels(mirror)
        repo = make_repo()
        config = make_config()
        run_commit(repo, config, mirror.client)
        file = repo_root(run_dir).joinpath(DEPLOY_CONFIG)
        file_write(file, b'Python:\n  PythonExecutable: ./elsewhere.exe\n')
        run_commit(repo, config, mirror.client)
        assert file_read_bytes(file) == DEPLOY_TEMPLATE_TEXT.encode('utf-8')

    def test_file_of_an_older_version_removed(self, fs, run_dir, mirror):
        """A file the version does not have is removed, a changed file is written back."""
        register_wheels(mirror)
        repo = make_repo()
        config = make_config()
        run_commit(repo, config, mirror.client)
        tree = repo_root(run_dir)
        stale_indexed = tree.joinpath('module/config/_index/mod_entry.yaml')
        stale_unindexed = tree.joinpath('module/removed.py')
        file_write(stale_unindexed, b'VALUE = 1\n')
        # a file of the version, changed on the disk
        file_write(stale_indexed, b'Mod:\n  name: changed\n')

        run_commit(repo, config, mirror.client)
        assert not stale_unindexed.exists()
        # the record of the version is restored, not kept as it was found
        full = tree_pack(run_dir)
        assert file_read_bytes(stale_indexed) == bytes(
            full.catfile(full.fileinfo['module/config/_index/mod_entry.yaml']))


class TestCopiedRecord:
    """A record the pack stores once and references."""

    def test_copied_record_is_unpacked(self, fs, run_dir, mirror):
        """A C (copied) record of the encoder is written to its own path."""
        register_wheels(mirror)
        run_commit(make_duplicate_repo(), make_config(), mirror.client)
        root = repo_root(run_dir)
        assert file_read_bytes(root.joinpath('module/dup_a.py')) == DUPLICATE_CONTENT
        assert file_read_bytes(root.joinpath('module/dup_b.py')) == DUPLICATE_CONTENT
        # the second record references the first, it has no data in the pack
        full = tree_pack(run_dir, COMMIT_DUPLICATE)
        copied = full.fileinfo['module/dup_b.py']
        assert copied.source_lookback == 1
        assert copied.source_path == 'module/dup_a.py'


class TestAppAsar:
    """The client archive the exported tree ships."""

    def _library_wheel(self, mirror, content=BUNDLED_APP_ASAR_CONTENT, version=ALASIO_VERSION):
        """
        Build the wheel of the library and register it in the mirror.

        The wheel carries the electron archive at the path the wheel build of
        the library writes it, so the dependency channel of the library ships it
        like the real one does.

        Args:
            mirror (MockMirror): Mirror to register the wheel in
            content (bytes): Content of the archive
            version (str): Version of the library

        Returns:
            str: The pin of the library, e.g. 'alasio==2.0.3'
        """
        pin = f'{LIBRARY}=={version}'
        path = f'/w/{LIBRARY}-{version}-py3-none-any.whl'
        build_wheel(path, {
            f'{LIBRARY}/__init__.py': b'VERSION = "2.0.3"\n',
            BUNDLED_APP_ASAR: content,
        }, name=LIBRARY, version=version)
        mirror.register(LIBRARY, version, content=file_read_bytes(path))
        return pin

    def _repo(self, pin):
        """
        The repo of the tests, with the library as its pack dependency.

        Returns:
            MockGitRepo: Repo
        """
        return make_repo(
            {'c1': requirement(pin) + requirement('demo==1.0')},
            entry=entry_source(LIBRARY))

    def test_shipped(self, fs, run_dir, mirror):
        """The archive of the library channel is a file of the tree, at the release path."""
        pin = self._library_wheel(mirror)
        gen = run_commit(self._repo(pin), make_config(), mirror.client)
        file = repo_root(run_dir).joinpath(APP_ASAR)
        assert file_read_bytes(file) == BUNDLED_APP_ASAR_CONTENT
        # a private file of the run: the record names it, a mirror never gets it
        files, private = gen.base.repo_manifest()
        assert f'{REPO_DIR}/{APP_ASAR}' in private
        assert f'{REPO_DIR}/{APP_ASAR}' not in files

    def test_a_run_without_the_library_fails(self, fs, run_dir, mirror):
        """A run that does not pack the library has no archive, the export fails."""
        register_wheels(mirror)
        repo = make_repo(
            {'c1': requirement('httpx==0.28.1')}, entry=entry_source('httpx'))
        gen = Repo(repo, make_config(), client=mirror.client)
        with pytest.raises(ReleaseExportError, match='carry no app.asar'):
            gen.run()
        assert not repo_root(run_dir).joinpath(APP_ASAR).exists()
        assert not repo_file(run_dir, 'latest.pack').exists()

    def test_missing_archive_fails(self, fs, run_dir, mirror):
        """A library wheel without the archive fails the run, nothing is published."""
        # the wheel carries the library only, no archive
        pin = f'{LIBRARY}=={ALASIO_VERSION}'
        path = f'/w/{LIBRARY}-{ALASIO_VERSION}-py3-none-any.whl'
        build_wheel(
            path, {f'{LIBRARY}/__init__.py': b''},
            name=LIBRARY, version=ALASIO_VERSION)
        mirror.register(LIBRARY, ALASIO_VERSION, content=file_read_bytes(path))

        gen = Repo(self._repo(pin), make_config(), client=mirror.client)
        with pytest.raises(ReleaseExportError, match='carry no app.asar'):
            gen.run()
        assert not repo_root(run_dir).joinpath(APP_ASAR).exists()
        assert not repo_file(run_dir, 'latest.pack').exists()

    def test_skipped_when_current(self, fs, run_dir, mirror):
        """A second run over the archive it already shipped writes nothing."""
        pin = self._library_wheel(mirror)
        repo = self._repo(pin)
        config = make_config()
        run_commit(repo, config, mirror.client)
        file = repo_root(run_dir).joinpath(APP_ASAR)
        stat = file.stat()
        with logger.mock_capture_writer() as capture:
            run_commit(repo, config, mirror.client)
        assert f'App asar exists, skipped: "{file}"' in capture.concat()
        assert file.stat().st_mtime_ns == stat.st_mtime_ns

    def test_updated_when_the_library_changes(self, fs, run_dir, mirror):
        """A new version of the library replaces the archive of the tree."""
        pin = self._library_wheel(mirror)
        repo = self._repo(pin)
        config = make_config()
        run_commit(repo, config, mirror.client)
        file = repo_root(run_dir).joinpath(APP_ASAR)
        assert file_read_bytes(file) == BUNDLED_APP_ASAR_CONTENT

        # a second version of the library, with another archive
        new_pin = self._library_wheel(mirror, b'NEW-ASAR-CONTENT', version='2.0.4')
        repo.register_file(
            COMMIT_DUPLICATE, 'requirements.txt',
            (requirement(new_pin) + requirement('demo==1.0')).encode())
        repo.register_commit(
            COMMIT_DUPLICATE, parents=['c1'], author_name='Author', message='')
        repo.register_branch('master', COMMIT_DUPLICATE)
        repo.register_head(COMMIT_DUPLICATE)
        run_commit(repo, config, mirror.client)
        assert file_read_bytes(file) == b'NEW-ASAR-CONTENT'


class TestDep:
    """The site-packages tree of the dependency channels."""

    def test_files_are_the_records(self, fs, run_dir, mirror):
        """Every record of the dependency is a file of the tree, byte for byte."""
        register_wheels(mirror)
        run_commit(make_repo(), make_config(), mirror.client)
        expected = working_tree(dep_pack(run_dir))
        tree = read_tree(dep_root(run_dir))
        # the tree carries every dependency channel, the library among them
        del tree[f'.pack/{DIST}/index.pack']
        files = {
            path: content for path, content in tree.items()
            if path.split('/')[0] in (DIST, f'{DIST}-{DIST_VERSION}.dist-info')
        }
        assert files == expected

    def test_ledger_is_the_channel(self, fs, run_dir, mirror):
        """The ledger of a distribution is the index pack its channel published."""
        register_wheels(mirror)
        gen = run_commit(make_repo(), make_config(), mirror.client)
        version, checksum = gen.dep.published[DIST]
        assert version == DIST_VERSION
        ledger = dep_root(run_dir).joinpath(f'.pack/{DIST}/index.pack')
        assert PackChecksum.from_file(ledger).index_checksum == checksum
        assert file_read_bytes(ledger) == bytes(dep_pack(run_dir).extract_index_pack())

    def test_ledger_path_is_the_client_path(self, fs, run_dir, mirror):
        """The ledger is at the path the client reads of a distribution target."""
        register_wheels(mirror)
        run_commit(make_repo(), make_config(), mirror.client)
        root = dep_root(run_dir)
        # DeployTarget is the path layer of the client's own job
        client = DeployTarget(root=root, name=DIST)
        assert client.index_file == root.joinpath(f'.pack/{DIST}/index.pack')
        assert client.index_file.isfile()
        # the same key of the workspace ledger of the tree target
        assert DeployTarget(root=root).index_file == root.joinpath('.pack/index.pack')

    def test_dist_info(self, fs, run_dir, mirror):
        """The metadata of the distribution is unpacked, no bytecode."""
        register_wheels(mirror)
        run_commit(make_repo(), make_config(), mirror.client)
        root = dep_root(run_dir)
        assert root.joinpath(f'{DIST}-{DIST_VERSION}.dist-info/METADATA').isfile()
        assert root.joinpath(f'{DIST}-{DIST_VERSION}.dist-info/RECORD').isfile()
        assert list(root.iter_files(ext='.pyc', recursive=True)) == []


class TestIdempotent:
    """A second run over a complete workspace."""

    def test_no_data_file_is_written_again(self, fs, run_dir, mirror, monkeypatch):
        """The second run writes no file of either tree, the records all match."""
        register_wheels(mirror)
        repo = make_repo()
        config = make_config()
        run_commit(repo, config, mirror.client)
        writes = []
        replace_data = JobBase.replace_data

        def spy(job):
            writes.append([pending.info.path for pending in job.pending])
            return replace_data(job)

        monkeypatch.setattr(JobBase, 'replace_data', spy)
        run_commit(repo, config, mirror.client)
        # the job of the tree and the two jobs of the dependencies, neither of
        # them has a change to apply (the ledger is committed by
        # replace_index, not by the data pass)
        assert writes == [[], [], []]

    def test_content_is_unchanged(self, fs, run_dir, mirror):
        """The trees of the second run are the trees of the first one."""
        register_wheels(mirror)
        repo = make_repo()
        config = make_config()
        run_commit(repo, config, mirror.client)
        tree = read_tree(repo_root(run_dir))
        dependencies = read_tree(dep_root(run_dir))
        run_commit(repo, config, mirror.client)
        assert read_tree(repo_root(run_dir)) == tree
        assert read_tree(dep_root(run_dir)) == dependencies


class TestReuse:
    """The decoder of the tree: the encoder of this run, or the pack on the disk."""

    def test_written_pack_is_not_read_back(self, fs, run_dir, mirror, monkeypatch):
        """A pack this run wrote is decoded from the encoder, never read again."""
        register_wheels(mirror)
        reads = []
        atomic_read_bytes = repo_export.atomic_read_bytes
        atomic_open = repo_export.atomic_open

        def spy_read(path):
            reads.append(str(path))
            return atomic_read_bytes(path)

        def spy_open(file, *args, **kwargs):
            reads.append(str(file))
            return atomic_open(file, *args, **kwargs)

        monkeypatch.setattr(repo_export, 'atomic_read_bytes', spy_read)
        monkeypatch.setattr(repo_export, 'atomic_open', spy_open)
        Repo(make_repo(), make_config(), client=mirror.client).run()
        # the pack of the tree is in the memory of the encoder, so its file is
        # never opened: neither by the decoder nor by the digest of the files
        # the export copies
        tree_pack_file = str(repo_file(run_dir, 'c3', 'full.pack'))
        assert tree_pack_file not in reads
        # the files the export reads: the two dependency packs it decodes, the
        # template it copies and its target (not there yet), then the archive
        # of the client and the target of the tree. The pack of the tree is
        # never opened: its records are in the memory of the encoder
        assert reads == [
            str(join_path(run_dir, DEP_FOLDER, DIST_VERSION, 'full.pack')),
            str(join_path(
                run_dir, WORKSPACE, 'packdep', LIBRARY, ALASIO_VERSION, 'full.pack')),
            str(repo_root(run_dir).joinpath(DEPLOY_TEMPLATE)),
            str(repo_root(run_dir).joinpath(DEPLOY_CONFIG)),
            str(dep_root(run_dir).joinpath(BUNDLED_APP_ASAR)),
            str(repo_root(run_dir).joinpath(APP_ASAR)),
        ]

    def test_kept_pack_is_read_once(self, fs, run_dir, mirror, monkeypatch):
        """A pack kept from an earlier run was not encoded by this one and is read once."""
        register_wheels(mirror)
        repo = make_repo()
        config = make_config()
        run_commit(repo, config, mirror.client)
        reads = []
        atomic_read_bytes = repo_export.atomic_read_bytes

        def spy(path):
            reads.append(str(path))
            return atomic_read_bytes(path)

        monkeypatch.setattr(repo_export, 'atomic_read_bytes', spy)
        run_commit(repo, config, mirror.client)
        assert str(repo_file(run_dir, 'c3', 'full.pack')) in reads


class TestCleanup:
    """The files the export does not write are removed by the run."""

    def test_stale_files_removed(self, fs, run_dir, mirror):
        """A file an older export left is not touched, so the cleanup removes it."""
        register_wheels(mirror)
        repo = make_repo()
        config = make_config()
        run_commit(repo, config, mirror.client)
        root = repo_root(run_dir)
        stale = [
            root.joinpath('module/removed.py'),
            root.joinpath('notes.txt'),
        ]
        for file in stale:
            file_write(file, b'VALUE = 1\n')
        stale_dep = dep_root(run_dir).joinpath(f'{DIST}/removed.py')
        file_write(stale_dep, b'VALUE = 1\n')

        run_commit(repo, config, mirror.client)
        assert [file for file in stale if file.exists()] == []
        assert not stale_dep.exists()
        # the files of the version stay
        assert root.joinpath('requirements.txt').isfile()
        assert dep_root(run_dir).joinpath(f'{DIST}/__init__.py').isfile()

    def test_manifest_records_the_trees(self, fs, run_dir, mirror):
        """The trees are private entries of the record, never published files."""
        register_wheels(mirror)
        gen = Repo(make_repo(), make_config(), client=mirror.client)
        gen.run()
        files, private = gen.base.repo_manifest()
        assert [path for path in files if path.startswith((REPO_DIR, DEP_DIR))] == []
        touched = {
            path for path in private
            if path.startswith((f'{REPO_DIR}/', f'{DEP_DIR}/'))
        }
        assert f'{REPO_DIR}/.pack/index.pack' in touched
        assert f'{REPO_DIR}/{DEPLOY_CONFIG}' in touched
        assert f'{REPO_DIR}/requirements.txt' in touched
        assert f'{DEP_DIR}/.pack/{DIST}/index.pack' in touched
        assert f'{DEP_DIR}/{DIST}/__init__.py' in touched


class TestFailure:
    """A tree that cannot be exported fails the generation."""

    def test_failed_export_fails_the_run(self, fs, run_dir, mirror, monkeypatch):
        """The run fails before the barrier, the version is not published."""
        register_wheels(mirror)

        async def fail(self):
            return False

        monkeypatch.setattr(ReleaseExportJob, 'run', fail)
        gen = Repo(make_repo(), make_config(), client=mirror.client)
        with pytest.raises(ReleaseExportError, match='Failed to export the tree'):
            gen.run()
        assert not repo_file(run_dir, 'latest.pack').exists()
        with pytest.raises(RuntimeError, match=r'run\(\) must be called'):
            gen.commit()

    def test_broken_ledger_is_reported(self, fs, run_dir, mirror):
        """A ledger of another checksum is reported, not copied into a release."""
        register_wheels(mirror)
        gen = run_commit(make_repo(), make_config(), mirror.client)
        with pytest.raises(ReleaseExportError, match='not the one of the version'):
            gen.export._check_ledger(gen.export.repo_root, '', 'c3', b'\x00' * 20)
        with pytest.raises(ReleaseExportError, match='not the one of the version'):
            gen.export._check_ledger(gen.export.repo_root, '', 'c2', b'\x00' * 20)
