"""
Tests for the manifest of a workspace: the split of the files of a run and the
file that records it.

The manifest is the record of what a generation publishes and what it keeps on
the disk of the generator (the wheel cache). The split is the one the cleanup
of the run uses too, so the tests here are about one rule with two readers: the
published half and the private half of a workspace.
"""

import pytest

from alasio.deploy.pack.server_file import LatestInfo
from alasio.deploy_dev.pack_server.manifest import (
    MANIFEST_FILE, MANIFEST_SCHEMA, Manifest, is_private_path, split_paths
)
from alasio.deploy_dev.pack_server.repo_manifest import RepoManifest
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import file_read_bytes
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401

# a tmp file of an interrupted atomic write, the shape is_tmp_file() accepts
TMP_FILE = 'packrepo/latest.pack.sTD2kF.tmp'


class TestIsPrivatePath:
    """The path segments that mark a path as private."""

    @pytest.mark.parametrize('path, expected', [
        # the published folders of a run
        ('packrepo/latest.pack', False),
        ('packrepo/3f2b9c1d/full.pack', False),
        ('packdep/httpx/0.28.1/full.pack', False),
        ('wheel/anyio/4.5.2/anyio-4.5.2-py3-none-any.whl', False),
        (MANIFEST_FILE, False),
        # the cache folders, the dot of the current layout and the underscore
        # of the layout an older run wrote
        ('.wheel_cache', True),
        ('.wheel_cache/httpx/0.28.1/index.html', True),
        ('_wheel_cache/httpx/0.28.1/index.html', True),
        # a private segment at any depth, not only the first one
        ('packrepo/.hidden/full.pack', True),
        ('packrepo/3f2b9c1d/_tmp.pack', True),
    ])
    def test_private_path(self, path, expected):
        assert is_private_path(path) is expected


class TestSplitPaths:
    """The two halves of the files of a run."""

    def test_split_keeps_every_entry_sorted(self):
        """A path is in exactly one half, and both halves are sorted."""
        paths = [
            'wheel/anyio/4.5.2/anyio-4.5.2-py3-none-any.whl',
            '.wheel_cache/httpx/0.28.1/index.html',
            'packrepo/latest.pack',
            '_wheel_cache/legacy/1.0/legacy-1.0-py3-none-any.whl',
            'packrepo/3f2b9c1d/full.pack',
        ]
        files, private = split_paths(paths)
        assert files == [
            'packrepo/3f2b9c1d/full.pack',
            'packrepo/latest.pack',
            'wheel/anyio/4.5.2/anyio-4.5.2-py3-none-any.whl',
        ]
        assert private == [
            '.wheel_cache/httpx/0.28.1/index.html',
            '_wheel_cache/legacy/1.0/legacy-1.0-py3-none-any.whl',
        ]

    def test_tmp_and_manifest_are_not_entries(self):
        """The manifest and the tmp file of an interrupted write are in neither half."""
        paths = [MANIFEST_FILE, TMP_FILE, 'packrepo/latest.pack']
        files, private = split_paths(paths)
        assert files == ['packrepo/latest.pack']
        assert private == []

    def test_empty(self):
        """A run that touched nothing has two empty halves."""
        assert split_paths([]) == ([], [])


class TestManifestDump:
    """The bytes of the manifest file."""

    def test_dump_is_the_stable_json_of_the_manifest(self):
        """The file is the indented json of the struct, the lists sorted."""
        manifest = Manifest.build(
            channel='Author_Repo_master',
            version='3f2b9c1d',
            checksum='0a1b2c3d4e5f60718293a4b5c6d7e8f901234567',
            files=['packrepo/latest.pack', 'packrepo/3f2b9c1d/full.pack'],
            private=['.wheel_cache/httpx/0.28.1/index.html'],
            generated_at=1761470000,
        )
        assert manifest.dump() == b"""\
{
  "schema": 1,
  "channel": "Author_Repo_master",
  "version": "3f2b9c1d",
  "checksum": "0a1b2c3d4e5f60718293a4b5c6d7e8f901234567",
  "generated_at": 1761470000,
  "files": [
    "packrepo/3f2b9c1d/full.pack",
    "packrepo/latest.pack"
  ],
  "private": [
    ".wheel_cache/httpx/0.28.1/index.html"
  ]
}"""

    def test_build_sorts_and_records_the_schema(self):
        """The lists are sorted, so the bytes do not depend on the order."""
        manifest = Manifest.build(
            channel='ch', version='v1', checksum='c1',
            files=['b', 'a'], private=['d', 'c'], generated_at=1)
        assert manifest.schema == MANIFEST_SCHEMA
        assert manifest.files == ['a', 'b']
        assert manifest.private == ['c', 'd']


class TestManifestWrite:
    """Write the manifest to the workspace, unless it is already there."""

    def _write(self, file, version='v1', checksum='c1', generated_at=100):
        manifest = Manifest.build(
            channel='ch', version=version, checksum=checksum,
            files=['packrepo/latest.pack'], private=['.wheel_cache/a'],
            generated_at=generated_at)
        return manifest.write(file)

    def test_write_and_read_back(self, fs):
        """The written file decodes to the manifest that wrote it."""
        file = '/run/manifest.json'
        manifest = Manifest.build(
            channel='ch', version='v1', checksum='c1',
            files=['packrepo/latest.pack'], private=['.wheel_cache/a'],
            generated_at=1761470000)
        assert manifest.write(file) is True
        assert Manifest.from_file(file) == manifest
        assert file_read_bytes(file) == manifest.dump()

    def test_second_write_is_skipped(self, fs):
        """The same generation writes nothing the second time."""
        file = '/run/manifest.json'
        assert self._write(file) is True
        content = file_read_bytes(file)
        assert self._write(file) is False
        assert file_read_bytes(file) == content

    def test_generated_at_of_the_generation_is_kept(self, fs):
        """A rewrite of the same generation keeps the time the generation started."""
        file = '/run/manifest.json'
        self._write(file, generated_at=100)
        manifest = Manifest.build(
            channel='ch', version='v1', checksum='c1',
            files=['packrepo/latest.pack'], private=['.wheel_cache/a'],
            generated_at=200)
        assert manifest.write(file) is False
        assert manifest.generated_at == 100
        assert Manifest.from_file(file).generated_at == 100

    def test_generated_at_moves_with_the_version(self, fs):
        """Another generation starts at the time of its own write."""
        file = '/run/manifest.json'
        self._write(file, version='v1', generated_at=100)
        assert self._write(file, version='v2', generated_at=200) is True
        assert Manifest.from_file(file).generated_at == 200

    def test_broken_file_is_replaced(self, fs):
        """A file that is not a manifest is not a base, the run writes its own."""
        file = '/run/manifest.json'
        fs.create_file(file, contents=b'{not json')
        assert self._write(file) is True
        assert Manifest.from_file(file).version == 'v1'

    def test_manifest_of_another_schema_is_replaced(self, fs):
        """A manifest of a format version this code does not read is replaced."""
        file = '/run/manifest.json'
        fs.create_file(file, contents=b"""\
{
  "schema": 2,
  "channel": "ch",
  "version": "v1",
  "checksum": "c1",
  "generated_at": 100,
  "files": [],
  "private": []
}""")
        assert self._write(file) is True
        assert Manifest.from_file(file).schema == MANIFEST_SCHEMA


class _FakeBase:
    """The part of RepoBase the manifest step reads."""

    def __init__(self, workspace):
        self.workspace = PathStr.new(workspace)
        self.touched = []

    def repo_manifest(self):
        """The split of the run, a fixed answer of the test."""
        return ['packrepo/latest.pack'], ['.wheel_cache/a']

    def touch(self, file):
        """Record the file, see RepoBase.touch."""
        self.touched.append(file)


class _FakeLatest:
    """The part of RepoLatest the manifest step reads."""

    def __init__(self, info):
        self.info = info


class TestRepoManifest:
    """The step that writes the manifest of a run."""

    def test_run_takes_the_barrier_of_the_step(self, fs):
        """The version and the checksum are the ones RepoLatest published."""
        base = _FakeBase('/run/pack/Author_Repo_master')
        latest = _FakeLatest(LatestInfo(version='3f2b9c1d', checksum=bytes(range(20)).hex()))
        manifest = RepoManifest(base, latest).run()
        assert manifest.version == '3f2b9c1d'
        assert manifest.checksum == bytes(range(20)).hex()
        assert manifest.channel == 'Author_Repo_master'
        assert manifest.files == ['packrepo/latest.pack']
        assert manifest.private == ['.wheel_cache/a']
        # the file is written and touched, so the cleanup of the run keeps it
        assert base.workspace.joinpath(MANIFEST_FILE).isfile()
        assert base.touched == [base.workspace.joinpath(MANIFEST_FILE)]

    def test_run_does_not_read_the_barrier_file(self, fs):
        """The step needs no latest.pack on the disk: the info is an input."""
        base = _FakeBase('/run/pack/Author_Repo_master')
        latest = _FakeLatest(LatestInfo(version='v1', checksum='00' * 20))
        manifest = RepoManifest(base, latest).run()
        assert manifest.version == 'v1'
        # the workspace holds the manifest only, no packrepo folder was needed
        assert not base.workspace.joinpath('packrepo').exists()

    def test_run_without_the_barrier_step_fails(self, fs):
        """The manifest never names a version the workspace does not serve."""
        base = _FakeBase('/run/pack/Author_Repo_master')
        with pytest.raises(RuntimeError):
            RepoManifest(base, _FakeLatest(None)).run()

    def test_run_twice_writes_once(self, fs):
        """A run that changes nothing does not rewrite the file."""
        base = _FakeBase('/run/pack/Author_Repo_master')
        latest = _FakeLatest(LatestInfo(version='3f2b9c1d', checksum=bytes(range(20)).hex()))
        first = RepoManifest(base, latest).run()
        with logger.mock_capture_writer() as capture:
            second = RepoManifest(base, latest).run()
        assert 'Manifest exists, skipped' in capture.concat()
        assert second.dump() == first.dump()
