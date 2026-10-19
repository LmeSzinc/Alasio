import io
import tarfile
import zipfile

import pytest

from alasio.deploy_dev.wheel.build_wheel import BuildWheel
from alasio.deploy_dev.wheel.parse_metadata import RepoStandardMetadata
from alasio.ext.path.atomic import atomic_read_bytes
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401

# 2021-01-01 00:00:00 UTC
COMMIT_TIME = 1609459200

FILES = {
    'pyproject.toml': b'[project]\nname = "demo-pkg"\nversion = "1.0.0"\nreadme = "README.md"\n',
    'README.md': b'# demo\n',
    'demo_pkg/__init__.py': b'',
    'demo_pkg/core.py': b'a = 1\n',
    'demo_pkg/empty.py': b'',
}


@pytest.fixture
def repo(make_repo):
    """
    The commit being built.

    Built before the fake filesystem is activated: MockGitRepo reads the
    .gitattributes of the project when files are registered, which happens
    while the real filesystem is still active.

    Returns:
        MockGitRepo:
    """
    return make_repo(FILES, time=COMMIT_TIME)


@pytest.fixture
def wheel(repo, fs):
    """
    A BuildWheel whose repo and head are in memory.

    The fs fixture is requested here so every test that builds an artifact
    writes to the in-memory filesystem, never to the real disk.

    Returns:
        BuildWheel:
    """
    build = BuildWheel('/repo', 'demo_pkg')
    # Replace the git repo and the head of the working tree, the properties
    # of the build read them back through the instance cache
    build.head = 'c1'
    build.repo = repo
    build.repo_metadata = RepoStandardMetadata(repo, 'demo_pkg', commit='c1')
    # What RepoWithStatics.list_files gives, without the repo of a working tree
    build.package_files = {
        path: entry
        for path, entry in repo.list_files('c1').items()
        if path.startswith('demo_pkg/')
    }
    return build


class TestCommitTime:
    def test_commit_time(self, make_repo):
        """The committer time of the head is the timestamp of the build."""
        repo = make_repo({'demo_pkg/__init__.py': b''}, time=COMMIT_TIME)
        build = BuildWheel('/repo', 'demo_pkg')
        build.head = 'c1'
        build.repo = repo
        assert build.commit_time == COMMIT_TIME

    def test_annotated_tag(self, make_repo):
        """A head that points to a tag object is resolved to its commit."""
        repo = make_repo({'demo_pkg/__init__.py': b''}, time=COMMIT_TIME)
        repo.register_tag_object('tag1', object='c1', tag='v1.0.0')
        build = BuildWheel('/repo', 'demo_pkg')
        build.head = 'tag1'
        build.repo = repo
        assert build.commit_time == COMMIT_TIME

    def test_head_is_not_a_commit(self, make_repo):
        """A head that points to a file raises ValueError."""
        repo = make_repo({'demo_pkg/__init__.py': b''}, time=COMMIT_TIME)
        build = BuildWheel('/repo', 'demo_pkg')
        build.head = repo.list_files('c1')['demo_pkg/__init__.py'].sha1
        build.repo = repo
        with pytest.raises(ValueError):
            build.commit_time


class TestWheel:
    def test_members(self, wheel):
        """Every wheel member is written with the fixed metadata."""
        with zipfile.ZipFile(wheel._build_wheel()) as zf:
            assert zf.testzip() is None
            assert zf.namelist() == [
                'demo_pkg/__init__.py',
                'demo_pkg/core.py',
                'demo_pkg/empty.py',
                'demo_pkg-1.0.0.dist-info/METADATA',
                'demo_pkg-1.0.0.dist-info/WHEEL',
                'demo_pkg-1.0.0.dist-info/top_level.txt',
                'demo_pkg-1.0.0.dist-info/RECORD',
            ]
            for info in zf.infolist():
                assert info.date_time == (2021, 1, 1, 0, 0, 0)
                assert info.external_attr == 0o644 << 16
                assert info.create_system == 3
                assert info.compress_type == zipfile.ZIP_DEFLATED

    def test_rebuild_is_identical(self, wheel):
        """Building the same commit twice produces the same bytes."""
        first = atomic_read_bytes(wheel._build_wheel())
        second = atomic_read_bytes(wheel._build_wheel())
        assert second == first


class TestSdist:
    def test_members(self, wheel):
        """Every sdist member is written with the fixed metadata."""
        data = atomic_read_bytes(wheel._build_targz())
        # tarfile opens files with the builtin open captured on import, it
        # does not read the fake filesystem, so read the archive from memory
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tar:
            names = tar.getnames()
            assert 'demo-pkg-1.0.0/pyproject.toml' in names
            assert 'demo-pkg-1.0.0/README.md' in names
            assert 'demo-pkg-1.0.0/demo_pkg/core.py' in names
            for info in tar.getmembers():
                assert info.mtime == COMMIT_TIME
                assert info.mode == 0o644
                assert info.uid == 0
                assert info.gid == 0

    def test_gzip_header(self, wheel):
        """The gzip header carries the commit time and no file name."""
        data = atomic_read_bytes(wheel._build_targz())
        # magic, deflate, FNAME flag unset
        assert data[:4] == b'\x1f\x8b\x08\x00'
        assert int.from_bytes(data[4:8], 'little') == COMMIT_TIME

    def test_rebuild_is_identical(self, wheel):
        """Building the same commit twice produces the same bytes."""
        first = atomic_read_bytes(wheel._build_targz())
        second = atomic_read_bytes(wheel._build_targz())
        assert second == first


class TestBuild:
    def test_build(self, wheel):
        """build() succeeds and stamps the commit time."""
        with logger.mock_capture_writer() as capture:
            assert wheel.build() is True
        assert capture.fd.any_contains('Building demo_pkg==1.0.0 commit=c1 time=1609459200')
