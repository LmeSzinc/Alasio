"""
Tests for named deploy targets: DeployJob(name=...).

A named target shares its root with other targets (e.g. the python
dists of a site-packages folder) and keeps its ledger folder in
{root}/.pack/{name}; the paths of the canonical pack area .pack of the
packs are mapped into that folder (JobBase.local_path / localize), so
the same pack applies to the project tree target (name='') and to any
named target, and no .pack path of the pack lands outside the ledger
folder.

The packs are module level singletons, built before the fake
filesystem is active: MockGitRepo reads the real .gitattributes file,
which the fake filesystem does not provide.
"""
import os

import pytest

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job import DeployJob
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy_dev.pack.pack_full import PackFull
from alasio.deploy_dev.pack.pack_update import PackUpdate
from alasio.ext import env
from alasio.ext.path.atomic import file_read_bytes
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack.conftest import WEBSITE_FILES, WEBSITE_FULL_PACK, MockServerFile, make_repo

# a two version scenario with a modified file, an added file and the
# generated .pack/history.pack extra, shared by the tests below
NAMED_REPO = make_repo({
    'old': {
        'module/__init__.py': b'',
        'module/core.py': b'VERSION = 1\n' * 40,
        'docs/readme.md': b'# readme\n',
    },
    'new': {
        'module/__init__.py': b'',
        'module/core.py': b'VERSION = 2\n' * 40,
        'module/extra.py': b'EXTRA = True\n',
        'docs/readme.md': b'# readme\n',
    },
})
NAMED_OLD_PACK = b''.join(PackFull(NAMED_REPO, commit='old').iter_pack_data())
NAMED_NEW_PACK = b''.join(PackFull(NAMED_REPO, commit='new').iter_pack_data())
NAMED_OLD_INDEX = bytes(PackDecodeBase(NAMED_OLD_PACK).extract_index_pack())
NAMED_NEW_INDEX = bytes(PackDecodeBase(NAMED_NEW_PACK).extract_index_pack())
NAMED_UPDATE = b''.join(PackUpdate(PackFull(NAMED_REPO, commit='new'), 'old').iter_pack_data())
NAMED_NEW_DECODER = PackDecodeBase(NAMED_NEW_PACK)
NAMED_TREE = {
    path: bytes(NAMED_NEW_DECODER.catfile(info))
    for path, info in NAMED_NEW_DECODER.fileinfo.items()
    if info.edit != 2 and not path.startswith('.pack/')
}
NAMED_SERVER = MockServerFile()
NAMED_SERVER.register_version('old', NAMED_OLD_PACK, NAMED_OLD_INDEX)
NAMED_SERVER.register_version('new', NAMED_NEW_PACK, NAMED_NEW_INDEX)
NAMED_SERVER.register_update('old', 'new', NAMED_UPDATE)


class TestNamedUnpack:
    """The full pack flow on a named target."""

    def test_unpack(self, app_folder):
        """The ledger and the pack area paths land under .pack/{name}."""
        with logger.mock_capture_writer():
            DeployJob(name='httpx').unpack(WEBSITE_FULL_PACK)
        for path, (content, _) in WEBSITE_FILES.items():
            assert file_read_bytes(env.PROJECT_ROOT / path) == content, path
        # the ledger of the target, and the history of the pack mapped
        # from the canonical .pack/history.pack into the ledger folder
        decoder = PackDecodeBase(file_read_bytes(env.PROJECT_ROOT / '.pack/httpx/index.pack'))
        assert decoder.current_version == 'c1'
        assert os.path.exists(env.PROJECT_ROOT / '.pack/httpx/history.pack')
        # nothing lands in the flat pack area nor under a second .pack layer
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/index.pack')
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/history.pack')
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/httpx/.pack')
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/httpx/workspace')

    def test_ledger_and_workspace_are_per_name(self, app_folder):
        """A named target has its own job file and workspace."""
        UnpackJob(WEBSITE_FULL_PACK, name='a').write()
        assert DeployJob(name='b').get_unfinished_job() is None
        job = DeployJob(name='a').get_unfinished_job()
        assert job is not None
        assert isinstance(job, UnpackJob)
        with logger.mock_capture_writer():
            job.run()
        assert file_read_bytes(env.PROJECT_ROOT / 'backend/main.py') == \
            WEBSITE_FILES['backend/main.py'][0]
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/a/workspace')

    def test_localize_rewrites_every_view(self, app_folder):
        """localize() rewrites idx_info, fileinfo and refinfo together,
        the cached dicts built or not before the rewrite."""
        job = UnpackJob(NAMED_UPDATE, name='httpx')
        # the lazy path: the dict caches are built after the rewrite
        decoder = PackDecodeBase(NAMED_UPDATE)
        decoder.validate()
        job.localize(decoder)
        self._assert_localized(decoder)
        # the eager path: the dict caches are built before the rewrite
        decoder = PackDecodeBase(NAMED_UPDATE)
        decoder.validate()
        assert '.pack/index.pack' in decoder.fileinfo
        assert all(not path.startswith('.pack/httpx/') for path in decoder.refinfo)
        job.localize(decoder)
        self._assert_localized(decoder)

    def test_local_path_rejects_the_pack_area_itself(self, app_folder):
        """.pack is the pack area folder, it is never mapped as a file."""
        job = UnpackJob(WEBSITE_FULL_PACK, name='httpx')
        assert job.local_path('.pack/index.pack') == '.pack/httpx/index.pack'
        assert job.local_path('backend/main.py') == 'backend/main.py'
        with pytest.raises(ValueError, match='pack area itself'):
            job.local_path('.pack')
        # the folder is rejected whatever the target name is
        with pytest.raises(ValueError, match='pack area itself'):
            UnpackJob(WEBSITE_FULL_PACK).local_path('.pack')

    @staticmethod
    def _assert_localized(decoder):
        """The three views of the decoder hold the local paths only."""
        # the index record and the history record of the pack are
        # mapped into the ledger folder of the target
        pack_paths = [info.path for info in decoder.idx_info if info.path.startswith('.pack/')]
        assert pack_paths, 'the update pack should carry .pack paths'
        assert all(path.startswith('.pack/httpx/') for path in pack_paths)
        assert '.pack/httpx/index.pack' in decoder.fileinfo
        # the dict views share the records with idx_info, keys included
        assert all(path == info.path for path, info in decoder.fileinfo.items())
        assert all(path == info.path for path, info in decoder.refinfo.items())


class TestNamedUpdate:
    """The incremental flow on a named target."""

    def test_update(self, app_folder, monkeypatch):
        """Everything the update needs comes from the local files: a
        download attempt means the mapping of a source path missed the
        ledger folder of the target."""
        with logger.mock_capture_writer():
            UnpackJob(NAMED_OLD_PACK, name='httpx').run()

        def _fail(*args, **kwargs):
            raise AssertionError('no download expected for a named target')
        monkeypatch.setattr(NAMED_SERVER, 'get_file_content', _fail)

        with logger.mock_capture_writer():
            assert DeployJob(name='httpx').update(NAMED_SERVER)
        for path, content in NAMED_TREE.items():
            assert file_read_bytes(env.PROJECT_ROOT / path) == content, path
        # the generated history record follows the mapping too
        history = bytes(NAMED_NEW_DECODER.catfile(NAMED_NEW_DECODER.fileinfo['.pack/history.pack']))
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/httpx/history.pack') == history
        decoder = PackDecodeBase(file_read_bytes(env.PROJECT_ROOT / '.pack/httpx/index.pack'))
        assert decoder.current_version == 'new'
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/index.pack')
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/httpx/workspace')

    def test_reset(self, app_folder):
        """The validation flow reads the ledger of the named target."""
        with logger.mock_capture_writer():
            UnpackJob(NAMED_NEW_PACK, name='httpx').run()
            assert DeployJob(name='httpx').update(NAMED_SERVER)
        decoder = PackDecodeBase(file_read_bytes(env.PROJECT_ROOT / '.pack/httpx/index.pack'))
        assert decoder.current_version == 'new'
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/httpx/workspace')

    def test_rebuild_when_ledger_missing(self, app_folder):
        """A missing ledger of a named target is rebuilt from the
        server, the new ledger included."""
        with logger.mock_capture_writer():
            UnpackJob(NAMED_OLD_PACK, name='httpx').run()
        os.remove(env.PROJECT_ROOT / '.pack/httpx/index.pack')
        with logger.mock_capture_writer() as capture:
            assert DeployJob(name='httpx').update(NAMED_SERVER)
        assert capture.backend.any_contains('Failed to read the local version')
        for path, content in NAMED_TREE.items():
            assert file_read_bytes(env.PROJECT_ROOT / path) == content, path
        decoder = PackDecodeBase(file_read_bytes(env.PROJECT_ROOT / '.pack/httpx/index.pack'))
        assert decoder.current_version == 'new'
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/httpx/workspace')

    def test_name_change_between_versions(self, app_folder, monkeypatch):
        """Renaming the ledger folder between two versions keeps the
        incremental update working: the pack paths are mapped at run
        time, nothing in the pack knows the name."""
        with logger.mock_capture_writer():
            UnpackJob(NAMED_OLD_PACK, name='a').run()
        os.rename(env.PROJECT_ROOT / '.pack/a', env.PROJECT_ROOT / '.pack/b')

        def _fail(*args, **kwargs):
            raise AssertionError('no download expected after a rename')
        monkeypatch.setattr(NAMED_SERVER, 'get_file_content', _fail)

        with logger.mock_capture_writer():
            assert DeployJob(name='b').update(NAMED_SERVER)
        decoder = PackDecodeBase(file_read_bytes(env.PROJECT_ROOT / '.pack/b/index.pack'))
        assert decoder.current_version == 'new'
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/a')
