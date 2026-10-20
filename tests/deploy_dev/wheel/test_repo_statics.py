"""Tests for the static files of ``RepoWithStatics``, the wheel's webapp."""
import json

import pytest

from alasio.codegen.asar import AsarArchive, AsarEntryNotFoundError
from alasio.deploy_dev.wheel.alasio_wheel import RepoWithStatics
from alasio.testing.filesystem import fs  # noqa: F401


class TestWebappAsar:
    def test_webapp_dist_keeps_the_dist_prefix(self, fs):
        """The wheel's app.asar keeps the layout package.json.main expects."""
        fs.create_file('/repo/webapp/dist/main/index.js', contents=b'MAIN')
        fs.create_file('/repo/webapp/dist/preload/index.js', contents=b'PRELOAD')
        fs.create_file('/repo/webapp/dist/renderer/index.html', contents=b'<html>')
        fs.create_file(
            '/repo/webapp/package.json',
            contents=b'{"main": "dist/main/index.js"}',
        )

        repo = RepoWithStatics('/repo')
        files = repo.static_files
        assert 'alasio/deploy_data/webapp/app.asar' in files

        with AsarArchive('/repo/webapp/release/app.asar') as asar:
            assert bytes(asar.read_file('dist/main/index.js')) == b'MAIN'
            assert bytes(asar.read_file('dist/preload/index.js')) == b'PRELOAD'
            assert bytes(asar.read_file('dist/renderer/index.html')) == b'<html>'

            package = json.loads(bytes(asar.read_file('package.json')))
            assert asar.entry(package['main'])
            with pytest.raises(AsarEntryNotFoundError):
                asar.entry('main/index.js')
