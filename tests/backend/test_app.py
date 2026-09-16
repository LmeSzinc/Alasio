"""
Tests for alasio.backend.app (create_app).

The SPA is served from the frontend build of the alasio root
(<ALASIO_ROOT>/frontend/build, next to the package the backend was imported
from). Building that path from PathStr(__file__) instead keeps the platform
separators: uppath() on a Windows __file__ (backslashes) returns '' and the
mount silently falls back to the cwd-relative 'frontend/build', which only
resolves while the cwd happens to be the same tree.
"""
import os

import pytest

from alasio.backend.app import create_app
from alasio.backend.dev.assets import SPANoCacheStaticFiles
from alasio.ext.env import ALASIO_ROOT

FRONTEND_BUILD = ALASIO_ROOT.joinpath('frontend/build')


def _norm(path):
    """
    Normalize a path for comparison: paths from the app are PathStr (forward
    slashes) while the test builds them with os.path (platform separator).

    Args:
        path (str | None):

    Returns:
        str | None:
    """
    if path is None:
        return None
    return os.path.normcase(os.path.normpath(path))


class TestFrontendMount:
    """
    create_app() must serve the SPA from the frontend build of the alasio
    root.
    """

    def test_mounts_frontend_build_from_alasio_root(self):
        """
        The frontend mount resolves to <ALASIO_ROOT>/frontend/build, not to a
        cwd-relative path.
        """
        app = create_app()
        mounts = [route for route in app.routes
                  if isinstance(getattr(route, 'app', None), SPANoCacheStaticFiles)]

        # a checkout without a built frontend: mount() logs and skips it
        # (frontend/build is git-ignored, it exists after `pnpm build`)
        if not os.path.isdir(FRONTEND_BUILD):
            assert mounts == [], mounts
            pytest.skip('frontend/build is not built in this checkout')

        assert len(mounts) == 1, mounts
        assert _norm(mounts[0].app.directory) == _norm(FRONTEND_BUILD)
