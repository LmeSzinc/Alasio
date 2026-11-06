"""
Tests for the files of a target that are not the working tree file of the
same path, see alasio.deploy.pack.local_side.

A repository target hides nothing from its tree (LocalSideRepo answers the
empty value everywhere). A target a mod took over from pip is the exception:
its files are the ones the installer wrote, listed in the RECORD of the
distribution, and LocalSidePip supplies them. The flow that uses it end to
end is covered by tests/deploy/simple_pip/test_pip_pack.py.
"""
from hashlib import sha1

import pytest

from alasio.deploy.pack.job import DeployJob
from alasio.deploy.pack.job_base import JobBase
from alasio.deploy.pack.local_side import LocalSide, LocalSidePip, LocalSideRepo, create_local_side
from alasio.deploy.simple_pip.whl_record import RecordManager
from alasio.deploy_dev.pack.pack_full import PackFull
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack.conftest import app_folder  # noqa: F401

# path of the bytecode an installer compiles at install time, the row a pip
# RECORD carries and the pack of the same version does not
PYC_PATH = 'demo/__pycache__/core.cpython-38.pyc'
RECORD_PATH = 'demo-1.0.dist-info/RECORD'


def dist_tree():
    """
    The tree of a distribution, the way its pack records it.

    Returns:
        dict[str, bytes]: {path: content} of the whole tree, the RECORD
            included, in the shape the pack of the version records it
    """
    tree = {
        'demo/__init__.py': b'',
        'demo/core.py': b'VERSION = 1\n' * 40,
        'demo-1.0.dist-info/METADATA': (
            b'Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n'),
        'demo-1.0.dist-info/WHEEL': b'Wheel-Version: 1.0\nGenerator: alasio\n',
        'demo-1.0.dist-info/INSTALLER': b'pip\n',
    }
    record = RecordManager()
    for path, content in tree.items():
        record.add_content(path, content)
    record.add_content(RECORD_PATH, None)
    tree[RECORD_PATH] = record.dump_bytes()
    return tree


TREE = dist_tree()


def ledger_index(files=None, commit='c1'):
    """
    The index pack of a version, the ledger of a target.

    Args:
        files (dict[str, bytes], optional): Files of the version. Defaults to
            None, one file
        commit (str): Version of the pack. Defaults to 'c1'

    Returns:
        bytes: Index pack data
    """
    repo = MockGitRepo()
    for path, content in (files or {'a.py': b'x = 1\n'}).items():
        repo.register_file(commit, path, content)
    repo.register_commit(commit, author_name='A', message='')
    return b''.join(PackFull(repo, commit=commit).iter_packidx_data())


# module level, built before the fake filesystem is active: MockGitRepo reads
# the real .gitattributes file, which the fake filesystem does not provide
LEDGER_INDEX = ledger_index()


def install_pip(fs, site):
    """
    Install the distribution the way pip installs a wheel.

    Args:
        fs (FakeFilesystem): Filesystem of the test
        site (PathStr): Site-packages folder of the installation
    """
    for path, content in TREE.items():
        if path == RECORD_PATH:
            # the RECORD of the installation is written below
            continue
        fs.create_file(site.joinpath(path), contents=content)
    record = RecordManager()
    record.load_bytes(TREE[RECORD_PATH])
    record.add_content(PYC_PATH, None)
    fs.create_file(site.joinpath(RECORD_PATH), contents=record.dump_bytes())
    fs.create_file(site.joinpath(PYC_PATH), contents=b'\x00\x00\x00\x00')


@pytest.fixture
def site(fs, app_folder):
    """The site-packages folder of the test with the distribution installed."""
    site = PathStr.new(env.PROJECT_ROOT.joinpath('site'))
    install_pip(fs, site)
    return site


class TestRepo:
    """A target whose files are its own tree: nothing is supplied."""

    def test_every_member_is_empty(self):
        """The base supplies nothing, so the flow reads the tree."""
        side = LocalSideRepo()
        assert side.version == ''
        assert side.fileinfo == {}
        assert side.filedata == {}

    def test_the_properties_are_the_base_ones(self):
        """A repository side is a LocalSide with no overrides, so a job can
        ask it anything without a branch."""
        assert isinstance(LocalSideRepo(), LocalSide)


class TestCreate:
    """The scene of the caller names the side of the target."""

    def test_repo(self):
        """A mod tree is served from a repo channel."""
        assert type(create_local_side('repo', '/app', 'demo')) is LocalSideRepo

    def test_pypi(self):
        """A python distribution is served from a pypi channel."""
        side = create_local_side('pypi', '/app', 'demo')
        assert type(side) is LocalSidePip
        assert (side.root, side.name) == (PathStr.new('/app'), 'demo')

    def test_the_project_tree_is_always_the_repo_side(self):
        """The project tree target manages no distribution, whatever the
        caller declares: no path of a site-packages folder is ever touched."""
        assert type(create_local_side('pypi', '/app', '')) is LocalSideRepo

    def test_an_unknown_scene_is_refused(self):
        """A scene that names no side is a mistake of the caller, not a
        silent fallback."""
        with pytest.raises(ValueError, match='Unknown scene'):
            create_local_side('dns', '/app', 'demo')

    def test_the_job_carries_the_side(self, fs, app_folder):
        """A named target holds the side of its scene and nothing else."""
        site = PathStr.new('/app')
        install_pip(fs, site)
        assert type(DeployJob(root=site, name='demo').local_side) is LocalSideRepo
        assert type(DeployJob(root=site, name='demo', scene='pypi').local_side) is LocalSidePip
        # the project tree is the repo side even when the caller says pypi
        assert type(DeployJob(root=site, scene='pypi').local_side) is LocalSideRepo

    def test_no_io_in_the_constructor(self, fs, app_folder, monkeypatch):
        """The side of a flow reads nothing when it is created: a check that
        only compares versions never touches the installation."""
        import builtins
        opened = []
        real_open = builtins.open

        def open_record(file, *args, **kwargs):
            opened.append(file)
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr(builtins, 'open', open_record)
        install_pip(fs, PathStr.new('/app'))
        opened.clear()
        side = create_local_side('pypi', '/app', 'demo')
        # nothing is read when the side is created
        assert opened == []
        # the version is the first fact a flow asks for, and it reads the
        # metadata of the installation and the RECORD that says whether the
        # distribution can name its files at all
        assert side.version == '1.0'
        assert opened == [
            '/app/demo-1.0.dist-info/METADATA',
            '/app/demo-1.0.dist-info/RECORD',
        ]


class TestPipVersion:
    """The version of the installation, the version of the target."""

    def test_version(self, site):
        """The version is the one the metadata of the installation records."""
        assert LocalSidePip(site, 'demo').version == '1.0'

    @pytest.mark.parametrize("name", ['demo', 'Demo', 'DEMO'])
    def test_name_is_normalized(self, site, name):
        """The lookup takes the name any way the caller writes it, the
        ledger key of a target is the PEP 503 normalized name."""
        assert LocalSidePip(site, name).version == '1.0'

    def test_no_installation(self, fs, app_folder):
        """A folder without the distribution supplies nothing, like a repo
        side: the flow behaves the same way."""
        site = PathStr.new(env.PROJECT_ROOT.joinpath('site'))
        fs.create_dir(site)
        side = LocalSidePip(site, 'demo')
        assert side.version == ''
        assert side.fileinfo == {}
        assert side.filedata == {}

    def test_without_record(self, fs, app_folder):
        """An installation without a RECORD can not name its files, so it
        does not name a version either: the flow would ask for an update
        pack it could not apply."""
        site = PathStr.new(env.PROJECT_ROOT.joinpath('site'))
        fs.create_file(
            site.joinpath('demo-1.0.dist-info/METADATA'),
            contents=b'Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n')
        side = LocalSidePip(site, 'demo')
        assert side.version == ''
        assert side.fileinfo == {}
        assert side.filedata == {}

    def test_version_of_the_entry_name(self, fs, app_folder):
        """A metadata that records no version falls back to the version of
        the entry name, the same fallback as a listing."""
        site = PathStr.new(env.PROJECT_ROOT.joinpath('site'))
        fs.create_file(
            site.joinpath('demo-1.0.dist-info/METADATA'),
            contents=b'Metadata-Version: 2.1\nName: demo\n')
        fs.create_file(
            site.joinpath('demo-1.0.dist-info/RECORD'), contents=b'')
        assert LocalSidePip(site, 'demo').version == '1.0'

    def test_a_legacy_installation_supplies_nothing(self, fs, app_folder):
        """An .egg-info installation is a metadata entry, not a file list."""
        site = PathStr.new(env.PROJECT_ROOT.joinpath('site'))
        fs.create_file(
            site.joinpath('demo.egg-info/PKG-INFO'),
            contents=b'Metadata-Version: 1.2\nName: demo\nVersion: 1.0\n')
        side = LocalSidePip(site, 'demo')
        assert side.version == ''
        assert side.fileinfo == {}


class TestPipFiles:
    """The RECORD of the installation, the files a job can not read."""

    def test_fileinfo(self, site):
        """Every file of the installation is a record of the old version,
        the bytecode an installer wrote included: it is a file of the folder
        the flow removes when it is not in the index."""
        fileinfo = LocalSidePip(site, 'demo').fileinfo
        assert set(fileinfo) == set(TREE) | {PYC_PATH}
        assert fileinfo['demo/core.py'].size == len(TREE['demo/core.py'])
        # the RECORD records no sha1: the job reads the file that uses one
        assert fileinfo['demo/core.py'].sha1 == b''

    def test_filedata_is_the_pack_record(self, site):
        """The one file an installation writes differently from the pack is
        the RECORD, in the shape of the pack."""
        assert LocalSidePip(site, 'demo').filedata == {RECORD_PATH: TREE[RECORD_PATH]}

    def test_pack_record(self, site):
        """The RECORD of the installation is rebuilt without the rows the
        pack of the version does not record."""
        side = LocalSidePip(site, 'demo')
        assert side.pack_record(side.record) == TREE[RECORD_PATH]

    def test_a_file_outside_the_folder_is_no_record(self, fs, app_folder):
        """A RECORD row that leaves the site-packages folder, e.g. a console
        script, is not a file of the pack of the distribution."""
        site = PathStr.new(env.PROJECT_ROOT.joinpath('site'))
        fs.create_file(site.joinpath('demo/core.py'), contents=b'x = 1\n')
        fs.create_file(
            site.joinpath('demo-1.0.dist-info/METADATA'),
            contents=b'Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n')
        record = RecordManager()
        record.add_content('demo/core.py', b'x = 1\n')
        record.add_content('../../Scripts/demo.exe', b'MZ')
        record.add_content('demo-1.0.dist-info/RECORD', None)
        fs.create_file(site.joinpath('demo-1.0.dist-info/RECORD'), contents=record.dump_bytes())
        assert set(LocalSidePip(site, 'demo').fileinfo) == {
            'demo/core.py', 'demo-1.0.dist-info/RECORD'}

    def test_the_sha1_of_a_file_is_never_answered(self, site):
        """The local side supplies sizes and the one file that differs: the
        sha1 of a source is computed by the job that reads it, so a file
        edited after the installation is caught there."""
        side = LocalSidePip(site, 'demo')
        assert side.fileinfo['demo/core.py'].sha1 == b''
        assert sha1(TREE['demo/core.py']).digest() != side.fileinfo['demo/core.py'].sha1
        # the file the job reads is the tree one, unchanged
        assert side.filedata.keys() == {RECORD_PATH}


class TestSuppliesNothing:
    """The two sides differ in one thing: whether they name files."""

    def test_the_repo_side_names_no_file(self):
        """A tree target hides nothing, so the pack claims about it are
        checked against the ledger alone."""
        assert LocalSideRepo().fileinfo == {}

    def test_the_pip_side_names_files(self, site):
        """An installation names its files, so a claim of the pack about one
        of them is advisory: the two records of the test are covered end to
        end by test_an_unmatched_record_is_downloaded (advisory) and
        test_a_wrong_refinfo_is_refused (binding)."""
        assert LocalSidePip(site, 'demo').fileinfo

    def test_a_pip_side_without_a_record_names_nothing(self, fs, app_folder):
        """An installation that can not name its files behaves like a tree
        target: nothing to be advisory about."""
        site = PathStr.new(env.PROJECT_ROOT.joinpath('site'))
        fs.create_dir(site)
        side = LocalSidePip(site, 'demo')
        assert side.fileinfo == {}
        assert side.filedata == {}


class TestOldFileinfo:
    """The records a job reads as the old version: the ledger, then the side.

    It is a cached property next to residue_fileinfo(), so a flow merges the
    two the same way, and the flows warm it in a worker thread before use.
    """

    def test_the_ledger_wins(self, fs, app_folder):
        """A target with a ledger is read from it, the side is not asked."""
        job = JobBase(b'', root='/app', name='demo')
        job.local_side = LocalSidePip('/app', 'demo')
        fs.create_file(job.index_file, contents=LEDGER_INDEX)
        assert 'a.py' in job.old_fileinfo

    def test_the_side_answers_without_a_ledger(self, site):
        """A target without a ledger is read from the side of the caller."""
        job = JobBase(b'', root='/app', name='demo')
        job.local_side = LocalSidePip(site, 'demo')
        assert 'demo/core.py' in job.old_fileinfo

    def test_read_once_per_flow(self, fs, app_folder):
        """The property is cached on the job: the second access answers the
        same dict, without opening anything."""
        job = JobBase(b'', root='/app', name='demo')
        fs.create_file(job.index_file, contents=LEDGER_INDEX)
        first = job.old_fileinfo
        assert 'a.py' in first
        # the file is gone, the second access still answers: it was read
        fs.remove(job.index_file)
        assert job.old_fileinfo is first

    def test_the_warm_method_returns_the_records(self, fs, app_folder):
        """The flows hand _warm_old_fileinfo() to a worker thread: it is the
        only way to touch the property without blocking the event loop."""
        job = JobBase(b'', root='/app', name='demo')
        fs.create_file(job.index_file, contents=LEDGER_INDEX)
        assert job._warm_old_fileinfo() is job.old_fileinfo

    def test_neither_names_a_file(self, fs, app_folder):
        """A fresh target has no deletion base, and the reason is reported
        here: the version check runs before the job and does not log it."""
        job = JobBase(b'', root='/app', name='demo')
        with logger.mock_capture_writer() as capture:
            assert job.old_fileinfo == {}
        assert capture.fd.any_contains('Failed to read the old index pack')
