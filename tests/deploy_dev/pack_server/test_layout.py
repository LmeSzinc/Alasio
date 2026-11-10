"""
The layout of a workspace, the one name every step of the flow has to agree on.

Three programs are built on these names: the generator writes the files, the
uploader mirrors them, the server publishes them under a url, and a client
downloads from that url. The names are therefore a wire format, not
implementation detail: a step that keeps its own copy of one of them can be
renamed with the others and nothing fails until a deployed client asks for a
path that no longer exists.

The tests pin the literals and the agreement between the steps.
"""

import pytest

from alasio.deploy_dev.pack_server import serve
from alasio.deploy_dev.pack_server.publish.common import BARRIER_KEY, MANIFEST_KEY
from alasio.deploy_dev.pack_server.publish.upload import UploadRun
from alasio.deploy_dev.pack_server.repo_base import PACK_FOLDER, PACKREPO_FOLDER
from alasio.deploy_dev.pack_server.repo_latest import LATEST_FILE
from alasio.ext import env
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack_server.publish.conftest import CHANNEL, write_config

pytestmark = [
    pytest.mark.parallel,
]


class TestWireFormat:
    """The names a deployed client already uses, which no rename may change."""

    def test_the_folders_of_the_run_directory(self):
        """pack/ holds the workspaces, packrepo/ the packs of one of them."""
        assert PACK_FOLDER == 'pack'
        assert PACKREPO_FOLDER == 'packrepo'
        assert LATEST_FILE == 'latest.pack'

    def test_the_keys_of_a_workspace(self):
        """A client fetches the barrier and the contract by these keys."""
        assert BARRIER_KEY == 'packrepo/latest.pack'
        assert MANIFEST_KEY == 'manifest.json'


class TestTheStepsAgree:
    """Every step derives its paths from the same constants."""

    def test_the_uploader_mirrors_the_workspace_the_generator_writes(self, run_dir):
        """The folder the uploader reads is the folder the generator fills.

        Both derive it from the same constants: a step that built
        ``pack/{workspace}`` from its own copy would mirror an empty folder if
        the generator's constant changed.
        """
        expected = env.PROJECT_ROOT.joinpath(PACK_FOLDER).joinpath(CHANNEL)
        assert UploadRun(write_config(run_dir)).workspace == expected

    def test_the_server_serves_the_folder_the_generator_writes(self):
        """The url prefix of the files served is the folder name itself."""
        assert serve.PACK_URL == f'/{PACK_FOLDER}'

    def test_the_barrier_is_the_key_the_server_prints(self):
        """The url the server logs names the same barrier a client fetches."""
        url = f'{serve.PACK_URL}/{CHANNEL}/{BARRIER_KEY}'
        assert url.endswith(f'/{CHANNEL}/packrepo/latest.pack')
