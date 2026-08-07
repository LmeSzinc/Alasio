"""
Tests for the command line entry of unpack.py.

Uses conftest.WEBSITE_FULL_PACK (mock modern full-stack website).
Every test runs in a pyfakefs in-memory filesystem, no real files are
written: the app_folder fixture points env.PROJECT_ROOT at the fake
filesystem.
"""
import os
import sys

import pytest
from conftest import WEBSITE_FILES, WEBSITE_FULL_PACK

from alasio.deploy.unpack import main
from alasio.ext import env
from alasio.ext.path.atomic import file_read_bytes


class TestUnpackCli:
    """The command line entry of unpack.py."""

    def test_main_unpacks(self, app_folder, fs, monkeypatch):
        """main() unpacks the pack into the working directory."""
        fs.create_file('/full.pack', contents=WEBSITE_FULL_PACK)
        monkeypatch.setattr(sys, 'argv', ['unpack', '/full.pack'])
        main()
        for path, (content, _) in WEBSITE_FILES.items():
            assert file_read_bytes(env.PROJECT_ROOT / path) == content, path
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_main_bad_args(self, app_folder, monkeypatch):
        """Invalid arguments raise SystemExit."""
        monkeypatch.setattr(sys, 'argv', ['unpack'])
        with pytest.raises(SystemExit):
            main()

    def test_main_missing_pack(self, app_folder, monkeypatch):
        """A missing pack file raises SystemExit."""
        monkeypatch.setattr(sys, 'argv', ['unpack', '/not/exist.pack'])
        with pytest.raises(SystemExit):
            main()
