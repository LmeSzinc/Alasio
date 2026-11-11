"""
Tests for the record of a workspace: the split of the files of a run and the
two files that hold it.

The record has two readers, so it is two files: the published contract
(manifest.json) names the files a mirror serves, the local record
(.manifest.json) names the files that stay on the disk of the generator (the
wheel cache). The split is the one the cleanup of the run uses too, so the
tests here are about one rule with three readers: the published half, the
private half, and the cleanup that keeps both.
"""

import pytest

from alasio.deploy.pack.server_file import LatestInfo
from alasio.deploy_dev.pack_server.manifest import (
    LOCAL_MANIFEST_FILE, LOCAL_MANIFEST_SCHEMA, MANIFEST_FILE, MANIFEST_SCHEMA, LocalManifest, Manifest,
    is_private_path, split_paths
)
from alasio.deploy_dev.pack_server.repo_manifest import RepoManifest
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import file_read_bytes
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401

pytestmark = [
    pytest.mark.parallel,
]

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
        # the local record, next to the published contract
        (LOCAL_MANIFEST_FILE, True),
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

    def test_tmp_and_record_are_not_entries(self):
        """The two files of the record and a tmp file are in neither half."""
        paths = [MANIFEST_FILE, LOCAL_MANIFEST_FILE, TMP_FILE, 'packrepo/latest.pack']
        files, private = split_paths(paths)
        assert files == ['packrepo/latest.pack']
        assert private == []

    def test_empty(self):
        """A run that touched nothing has two empty halves."""
        assert split_paths([]) == ([], [])


class TestManifestDump:
    """The bytes of the published contract."""

    def test_dump_is_the_stable_json_of_the_manifest(self):
        """The file is the indented json of the struct, the lists sorted."""
        manifest = Manifest.build(
            channel='Author_Repo_master',
            version='3f2b9c1d',
            checksum='0a1b2c3d4e5f60718293a4b5c6d7e8f901234567',
            files=['packrepo/latest.pack', 'packrepo/3f2b9c1d/full.pack'],
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
  ]
}"""

    def test_build_sorts_and_records_the_schema(self):
        """The list is sorted, so the bytes do not depend on the order."""
        manifest = Manifest.build(
            channel='ch', version='v1', checksum='c1',
            files=['b', 'a'], generated_at=1)
        assert manifest.schema == MANIFEST_SCHEMA
        assert manifest.files == ['a', 'b']


class TestManifestWrite:
    """Write the published contract, unless it is already there."""

    def _write(self, file, version='v1', checksum='c1', generated_at=100):
        manifest = Manifest.build(
            channel='ch', version=version, checksum=checksum,
            files=['packrepo/latest.pack'], generated_at=generated_at)
        return manifest.write(file)

    def test_write_and_read_back(self, fs):
        """The written file decodes to the manifest that wrote it."""
        file = '/run/manifest.json'
        manifest = Manifest.build(
            channel='ch', version='v1', checksum='c1',
            files=['packrepo/latest.pack'], generated_at=1761470000)
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
            files=['packrepo/latest.pack'], generated_at=200)
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

    def test_file_of_the_two_list_layout_is_rewritten(self, fs):
        """The manifest of the layout that held both lists is read and split.

        The private entries used to live in the same file as the published
        ones. A reader of that file still takes its published half (the field
        it does not know is ignored), and the next write drops the field and
        keeps the time the generation started.
        """
        file = '/run/manifest.json'
        fs.create_file(file, contents=b"""\
{
  "schema": 1,
  "channel": "ch",
  "version": "v1",
  "checksum": "c1",
  "generated_at": 100,
  "files": [
    "packrepo/latest.pack"
  ],
  "private": [
    ".wheel_cache/a"
  ]
}""")
        assert Manifest.from_file(file).files == ['packrepo/latest.pack']
        manifest = Manifest.build(
            channel='ch', version='v1', checksum='c1',
            files=['packrepo/latest.pack'], generated_at=200)
        assert manifest.write(file) is True
        assert manifest.generated_at == 100
        assert b'"private"' not in file_read_bytes(file)
        assert Manifest.from_file(file) == manifest


class TestLocalManifestDump:
    """The bytes of the local record."""

    def test_dump_is_the_stable_json_of_the_record(self):
        """The file is the indented json of the struct, the list sorted."""
        local = LocalManifest.build(
            channel='Author_Repo_master',
            version='3f2b9c1d',
            checksum='0a1b2c3d4e5f60718293a4b5c6d7e8f901234567',
            private=[
                '.wheel_cache/httpx/0.28.1/index.html',
                '.wheel_cache/anyio/4.5.2/index.html',
            ],
        )
        assert local.dump() == b"""\
{
  "schema": 1,
  "channel": "Author_Repo_master",
  "version": "3f2b9c1d",
  "checksum": "0a1b2c3d4e5f60718293a4b5c6d7e8f901234567",
  "private": [
    ".wheel_cache/anyio/4.5.2/index.html",
    ".wheel_cache/httpx/0.28.1/index.html"
  ]
}"""

    def test_build_sorts_and_records_the_schema(self):
        """The list is sorted, the schema is the one of the local record."""
        local = LocalManifest.build(
            channel='ch', version='v1', checksum='c1', private=['d', 'c'])
        assert local.schema == LOCAL_MANIFEST_SCHEMA
        assert local.private == ['c', 'd']

    def test_the_two_files_share_the_identity(self):
        """A published contract and a local record of a run name the same generation."""
        manifest = Manifest.build(
            channel='ch', version='v1', checksum='c1', files=['a'], generated_at=1)
        local = LocalManifest.build(
            channel='ch', version='v1', checksum='c1', private=['b'])
        assert (local.channel, local.version, local.checksum) == (
            manifest.channel, manifest.version, manifest.checksum)


class TestLocalManifestWrite:
    """Write the local record, unless it is already there."""

    def _write(self, file, version='v1', private=('.wheel_cache/a',)):
        local = LocalManifest.build(
            channel='ch', version=version, checksum='c1', private=private)
        return local.write(file)

    def test_write_and_read_back(self, fs):
        """The written file decodes to the record that wrote it."""
        file = '/run/.manifest.json'
        local = LocalManifest.build(
            channel='ch', version='v1', checksum='c1',
            private=['.wheel_cache/a'])
        assert local.write(file) is True
        assert LocalManifest.from_file(file) == local
        assert file_read_bytes(file) == local.dump()

    def test_second_write_is_skipped(self, fs):
        """A record that changes nothing is not rewritten."""
        file = '/run/.manifest.json'
        assert self._write(file) is True
        content = file_read_bytes(file)
        assert self._write(file) is False
        assert file_read_bytes(file) == content

    def test_another_generation_is_written(self, fs):
        """A record of another version replaces the file."""
        file = '/run/.manifest.json'
        self._write(file, version='v1')
        assert self._write(file, version='v2') is True
        assert LocalManifest.from_file(file).version == 'v2'

    def test_broken_file_is_replaced(self, fs):
        """A file that is not a record is not a base, the run writes its own."""
        file = '/run/.manifest.json'
        fs.create_file(file, contents=b'{not json')
        assert self._write(file) is True
        assert LocalManifest.from_file(file).version == 'v1'

    def test_record_of_another_schema_is_replaced(self, fs):
        """A record of a format version this code does not read is replaced."""
        file = '/run/.manifest.json'
        fs.create_file(file, contents=b"""\
{
  "schema": 2,
  "channel": "ch",
  "version": "v1",
  "checksum": "c1",
  "private": []
}""")
        assert self._write(file) is True
        assert LocalManifest.from_file(file).schema == LOCAL_MANIFEST_SCHEMA


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
    """The step that writes the record of a run."""

    def test_run_takes_the_barrier_of_the_step(self, fs):
        """The version and the checksum are the ones RepoLatest published."""
        base = _FakeBase('/run/pack/Author_Repo_master')
        info = LatestInfo(version='3f2b9c1d', checksum=bytes(range(20)).hex())
        step = RepoManifest(base, _FakeLatest(info))
        assert step.manifest is None
        assert step.local_manifest is None
        manifest = step.run()
        assert manifest.version == '3f2b9c1d'
        assert manifest.checksum == bytes(range(20)).hex()
        assert manifest.channel == 'Author_Repo_master'
        assert manifest.files == ['packrepo/latest.pack']
        # the private half is in the local record, the two files name one
        # generation
        local = LocalManifest.from_file(base.workspace.joinpath(LOCAL_MANIFEST_FILE))
        assert local.private == ['.wheel_cache/a']
        assert (local.channel, local.version, local.checksum) == (
            manifest.channel, manifest.version, manifest.checksum)
        assert base.workspace.joinpath(MANIFEST_FILE).isfile()
        # the files are written and touched, so the cleanup of the run keeps them
        assert base.touched == [
            base.workspace.joinpath(LOCAL_MANIFEST_FILE),
            base.workspace.joinpath(MANIFEST_FILE),
        ]

    def test_run_records_the_private_half(self, fs):
        """The local record names the private files, the contract does not."""
        base = _FakeBase('/run/pack/Author_Repo_master')
        latest = _FakeLatest(LatestInfo(version='v1', checksum='00' * 20))
        step = RepoManifest(base, latest)
        manifest = step.run()
        assert manifest.files == ['packrepo/latest.pack']
        assert step.local_manifest.private == ['.wheel_cache/a']
        assert (step.local_manifest.version, step.local_manifest.checksum) == (
            manifest.version, manifest.checksum)
        assert b'"private"' not in base.workspace.joinpath(MANIFEST_FILE).atomic_read_bytes()

    def test_run_does_not_read_the_barrier_file(self, fs):
        """The step needs no latest.pack on the disk: the info is an input."""
        base = _FakeBase('/run/pack/Author_Repo_master')
        latest = _FakeLatest(LatestInfo(version='v1', checksum='00' * 20))
        manifest = RepoManifest(base, latest).run()
        assert manifest.version == 'v1'
        # the workspace holds the record only, no packrepo folder was needed
        assert not base.workspace.joinpath('packrepo').exists()

    def test_run_without_the_barrier_step_fails(self, fs):
        """The record never names a version the workspace does not serve."""
        base = _FakeBase('/run/pack/Author_Repo_master')
        with pytest.raises(RuntimeError):
            RepoManifest(base, _FakeLatest(None)).run()
        assert not base.workspace.joinpath(MANIFEST_FILE).exists()
        assert not base.workspace.joinpath(LOCAL_MANIFEST_FILE).exists()

    def test_run_twice_writes_once(self, fs):
        """A run that changes nothing does not rewrite the files."""
        base = _FakeBase('/run/pack/Author_Repo_master')
        latest = _FakeLatest(LatestInfo(version='3f2b9c1d', checksum=bytes(range(20)).hex()))
        first = RepoManifest(base, latest).run()
        with logger.mock_capture_writer() as capture:
            second = RepoManifest(base, latest).run()
        log = capture.concat()
        assert 'Local manifest exists, skipped' in log
        assert 'Manifest exists, skipped' in log
        assert second.dump() == first.dump()
