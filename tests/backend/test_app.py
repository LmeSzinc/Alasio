"""
Tests for alasio.backend.app (create_app).

create_app() mounts the frontend page server of alasio.backend.frontend at "/":
the site (SITE, the singleton of the backend process) serves the files of the
delivered frontend manifest
(<ALASIO_ROOT>/alasio/deploy_data/frontend-manifest.pack) from memory.

create_app() runs inside the 5s startup window of the supervisor, so it must
read nothing: the manifest and the files are loaded by the warmup task of the
lifespan and by the requests themselves.

An unmatched path of the API namespace is answered by app.api_not_found and
never by the frontend mount: a typo in an endpoint URL must not come back as
the SPA page with status 200.

The manifest path is resolved from env.ALASIO_ROOT, which is built with PathStr
from the module __file__ and keeps the platform separators: uppath() on a
Windows __file__ (backslashes) returns '' and the mount would silently fall
back to a cwd-relative path, which only resolves while the cwd happens to be
the same tree.
"""
import os

import pytest
from starlette.requests import Request
from starlette.routing import Match, Route

from alasio.backend import frontend
from alasio.backend.app import api_not_found, create_app
from alasio.backend.frontend import FRONTEND_FOLDER, MANIFEST_FILE, FrontendSite
from alasio.ext.env import ALASIO_ROOT
from alasio.ext.starapi.param import HTTPExceptionJson


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


def _mounts(app):
    """
    The frontend mounts of an app.

    Args:
        app (StarAPI):

    Returns:
        list: The mounts serving a FrontendSite
    """
    return [route for route in app.routes
            if isinstance(getattr(route, 'app', None), FrontendSite)]


def make_scope(path, method='GET'):
    """
    Build a minimal http scope.

    Args:
        path (str): Request path
        method (str): Request method. Defaults to 'GET'.

    Returns:
        dict: ASGI scope
    """
    return {
        'type': 'http',
        'method': method,
        'path': path,
        'root_path': '',
        'headers': [],
        'query_string': b'',
        'scheme': 'http',
        'server': ('127.0.0.1', 22267),
        'client': ('127.0.0.1', 123),
    }


class TestFrontendMount:
    """
    create_app() mounts the frontend site, without touching the disk.
    """

    def test_mounts_the_singleton(self):
        app = create_app()
        mounts = _mounts(app)
        assert len(mounts) == 1, mounts
        assert mounts[0].app is frontend.SITE

    def test_create_app_reads_nothing(self, monkeypatch):
        """
        The app is built inside the 5s startup window of the supervisor: the
        manifest must not be read here (the warmup task and the requests do it).
        """
        def forbidden(*args, **kwargs):
            raise AssertionError('create_app() must not read the manifest')

        monkeypatch.setattr(frontend, '_read_manifest', forbidden)
        app = create_app()
        assert len(_mounts(app)) == 1

    def test_paths_are_relative_to_alasio_root(self):
        """
        The site resolves the manifest and the files from the alasio root, not
        from a cwd-relative path.
        """
        create_app()
        site = frontend.SITE
        assert _norm(site.root) == _norm(ALASIO_ROOT)
        assert site.manifest_file == MANIFEST_FILE
        assert site.manifest is None
        assert _norm(site.folder) == _norm(ALASIO_ROOT.joinpath(FRONTEND_FOLDER))


class TestUnknownApiRoute:
    """
    An unmatched path of the API namespace must not be answered with the SPA
    page: the frontend mount is the last route, so without the catch-all a
    typo in an endpoint URL returns index.html with status 200.
    """

    @pytest.mark.parametrize('path', ['/api', '/api/', '/api/nope', '/api/config/nope'])
    @pytest.mark.parametrize('method', ['GET', 'POST'])
    def test_catch_all_is_the_first_match(self, path, method):
        app = create_app()
        scope = make_scope(path, method=method)
        for route in app.routes:
            match, _ = route.matches(scope)
            if match is Match.FULL:
                assert isinstance(route, Route), f'{path} matched {route}'
                assert route.path in ('/api', '/api/{path:path}'), f'{path} matched {route.path}'
                return
        raise AssertionError(f'{path} matched no route')

    @pytest.mark.trio
    async def test_payload(self):
        """The client gets the API error format, with the unmatched path."""
        with pytest.raises(HTTPExceptionJson) as excinfo:
            await api_not_found(Request(make_scope('/api/nope')))
        assert excinfo.value.status_code == 404
        assert excinfo.value.detail == b'{"err":"API_NOT_FOUND","data":{"path":"/api/nope"}}'
