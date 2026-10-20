from pathlib import Path

import pytest

from alasio.deploy_dev.wheel.parse_metadata import RepoStandardMetadata
from alasio.deploy_dev.wheel.repo_path import RepoPath

PYPROJECT = b'''[project]
name = "demo-pkg"
version = "1.0.0"
description = "Demo package"
readme = "README.md"
license = "MIT"
license-files = ["LICENSE*", "licenses/*.txt"]
requires-python = ">=3.8"
'''

FILES = {
    'pyproject.toml': PYPROJECT,
    'README.md': b'# repo readme\n',
    'LICENSE': b'repo license\n',
    'licenses/A.txt': b'repo A\n',
    'demo_pkg/__init__.py': b'',
}


class TestRepoStandardMetadata:
    """Tests for metadata parsed from a MockGitRepo."""

    @pytest.fixture
    def metadata(self, make_repo):
        return RepoStandardMetadata(make_repo(FILES), 'demo_pkg')

    def test_project_data(self, metadata):
        """pyproject.toml is parsed from the commit."""
        project = metadata.project_data['project']
        assert project['name'] == 'demo-pkg'
        assert project['version'] == '1.0.0'
        assert project['license'] == 'MIT'

    def test_metaname_and_version(self, metadata):
        """The name is normalized and the static version is used."""
        assert metadata.canonical_name == 'demo-pkg'
        assert metadata.metaname == 'demo_pkg'
        assert metadata.version == '1.0.0'

    @pytest.mark.parametrize('name, canonical, metaname', [
        ('demo-pkg', 'demo-pkg', 'demo_pkg'),
        ('Demo.Pkg', 'demo-pkg', 'demo_pkg'),
        ('demo__pkg', 'demo-pkg', 'demo_pkg'),
        ('demo.-_pkg', 'demo-pkg', 'demo_pkg'),
        ('DEMO_PKG', 'demo-pkg', 'demo_pkg'),
    ])
    def test_name_normalization(self, make_repo, name, canonical, metaname):
        """Names are normalized by PEP 503 and PEP 427."""
        repo = make_repo({
            'pyproject.toml': f'[project]\nname = "{name}"\nversion = "1.0.0"\n'.encode(),
        })
        metadata = RepoStandardMetadata(repo, 'demo_pkg')
        assert metadata.canonical_name == canonical
        assert metadata.metaname == metaname

    @pytest.mark.parametrize('version, expected', [
        ('1.0.0', '1.0.0'),
        ('1.0.0-alpha', '1.0.0a0'),
        ('1.0.0-rc1', '1.0.0rc1'),
        ('1.0.0.post1', '1.0.0.post1'),
        ('1.0.0+Build.ABC', '1.0.0+build.abc'),
    ])
    def test_version_normalization(self, make_repo, version, expected):
        """Versions are normalized by PEP 440."""
        repo = make_repo({
            'pyproject.toml': f'[project]\nname = "demo-pkg"\nversion = "{version}"\n'.encode(),
        })
        metadata = RepoStandardMetadata(repo, 'demo_pkg')
        assert metadata.version == expected

    def test_invalid_version(self, make_repo):
        """An invalid version raises ValueError."""
        repo = make_repo({
            'pyproject.toml': b'[project]\nname = "demo-pkg"\nversion = "not a version"\n',
        })
        with pytest.raises(ValueError):
            _ = RepoStandardMetadata(repo, 'demo_pkg').version

    def test_project_dir_is_cached_repo_path(self, metadata):
        """project_dir is a cached RepoPath root."""
        assert isinstance(metadata.project_dir, RepoPath)
        assert metadata.project_dir is metadata.project_dir
        assert metadata.project_dir.as_posix() == ''

    def test_data_metadata(self, metadata):
        """METADATA contains the repo readme, license expression and files."""
        text = metadata.data_metadata.decode('utf-8')
        assert 'Metadata-Version: 2.4' in text
        assert 'Name: demo-pkg' in text
        assert 'Version: 1.0.0' in text
        assert 'License-Expression: MIT' in text
        assert 'License-File: LICENSE' in text
        assert 'License-File: licenses/A.txt' in text
        assert 'Description-Content-Type: text/markdown' in text
        assert '# repo readme' in text
        assert 'Disk' not in text

    def test_data_metadata_cached(self, metadata):
        """data_metadata is a cached property."""
        assert metadata.data_metadata is metadata.data_metadata

    def test_license_files(self, metadata):
        """license-files are RepoPath objects with the commit content."""
        paths = metadata.standard_metadata.license_files
        assert all(isinstance(path, RepoPath) for path in paths)
        assert metadata.license_files == {
            'LICENSE': b'repo license\n',
            'licenses/A.txt': b'repo A\n',
        }

    def test_no_local_file_read(self, make_repo, monkeypatch):
        """StandardMetadata is not allowed to read a local file."""
        metadata = RepoStandardMetadata(make_repo(FILES), 'demo_pkg')

        def fail(*args, **kwargs):
            raise AssertionError('local file read')

        monkeypatch.setattr(Path, 'read_text', fail)
        assert '# repo readme' in metadata.data_metadata.decode('utf-8')
        assert metadata.license_files['LICENSE'] == b'repo license\n'

    def test_dynamic_version(self, make_repo):
        """A dynamic version is read from {folder}/__init__.py."""
        repo = make_repo({
            'pyproject.toml': b'[project]\nname = "demo-pkg"\ndynamic = ["version"]\n',
            'demo_pkg/__init__.py': b'__version__ = "2.3.4"\n',
        })
        metadata = RepoStandardMetadata(repo, 'demo_pkg')
        assert metadata.version == '2.3.4'
        assert 'Version: 2.3.4' in metadata.data_metadata.decode('utf-8')

    def test_classic_license_file(self, make_repo):
        """license = {file = ...} is read from the commit."""
        repo = make_repo({
            'pyproject.toml': (
                b'[project]\n'
                b'name = "demo-pkg"\n'
                b'version = "1.0.0"\n'
                b'license = { file = "LICENSE" }\n'
            ),
            'LICENSE': b'repo license\n',
            'demo_pkg/__init__.py': b'',
        })
        metadata = RepoStandardMetadata(repo, 'demo_pkg')
        assert 'License: repo license' in metadata.data_metadata.decode('utf-8')
        assert metadata.standard_metadata.license.text == 'repo license\n'
        assert isinstance(metadata.standard_metadata.license.file, RepoPath)
        assert metadata.license_files == {}

    def test_missing_pyproject_toml(self, make_repo):
        """A repo without pyproject.toml fails with ValueError."""
        repo = make_repo({'demo_pkg/__init__.py': b''})
        metadata = RepoStandardMetadata(repo, 'demo_pkg')
        with pytest.raises(ValueError, match='Missing pyproject.toml'):
            _ = metadata.project_data

    def test_empty_name(self, make_repo):
        """A missing project.name fails with ValueError."""
        repo = make_repo({'pyproject.toml': b'[project]\nversion = "1.0.0"\n'})
        with pytest.raises(ValueError, match='Empty project.name'):
            _ = RepoStandardMetadata(repo, 'demo_pkg').metaname

    def test_empty_version(self, make_repo):
        """A missing version fails with ValueError."""
        repo = make_repo({'pyproject.toml': b'[project]\nname = "demo-pkg"\n'})
        with pytest.raises(ValueError, match='Empty version'):
            _ = RepoStandardMetadata(repo, 'demo_pkg').version


PYPROJECT_ENTRY_POINTS = b'''\
[project]
name = "demo-pkg"
version = "1.0.0"

[project.scripts]
demo = "demo_pkg.cli:main"

[project.gui-scripts]
demo-gui = "demo_pkg.gui:main"

[project.entry-points."demo.plugins"]
plugin = "demo_pkg.plugin:Plugin"

[project.entry-points."pytest11"]
demo = "demo_pkg.pytest_plugin"
'''

ENTRY_POINT_FILES = {
    'pyproject.toml': PYPROJECT_ENTRY_POINTS,
    'demo_pkg/__init__.py': b'',
}


class TestEntryPoints:
    """Tests for the entry point groups of a project."""

    @pytest.fixture
    def metadata(self, make_repo):
        return RepoStandardMetadata(make_repo(ENTRY_POINT_FILES), 'demo_pkg')

    def test_no_entry_points(self, make_repo):
        """A project without an entry point declares no group."""
        metadata = RepoStandardMetadata(make_repo(FILES), 'demo_pkg')
        assert metadata.entry_points == {}

    def test_groups(self, metadata):
        """The shortcut tables and the entry point groups are merged."""
        assert metadata.entry_points == {
            'console_scripts': {'demo': 'demo_pkg.cli:main'},
            'demo.plugins': {'plugin': 'demo_pkg.plugin:Plugin'},
            'gui_scripts': {'demo-gui': 'demo_pkg.gui:main'},
            'pytest11': {'demo': 'demo_pkg.pytest_plugin'},
        }

    def test_empty_group_dropped(self, make_repo):
        """An empty group would be an empty section, it is dropped."""
        repo = make_repo({
            'pyproject.toml': (
                b'[project]\n'
                b'name = "demo-pkg"\n'
                b'version = "1.0.0"\n'
                b'\n[project.entry-points."demo.empty"]\n'
            ),
        })
        assert RepoStandardMetadata(repo, 'demo_pkg').entry_points == {}

    def test_shortcut_wins(self, make_repo):
        """A name declared twice is taken from the shortcut table."""
        repo = make_repo({
            'pyproject.toml': (
                b'[project]\n'
                b'name = "demo-pkg"\n'
                b'version = "1.0.0"\n'
                b'\n[project.scripts]\n'
                b'demo = "demo_pkg.cli:main"\n'
                b'\n[project.entry-points.console_scripts]\n'
                b'demo = "demo_pkg.other:main"\n'
                b'extra = "demo_pkg.extra:main"\n'
            ),
        })
        assert RepoStandardMetadata(repo, 'demo_pkg').entry_points == {
            'console_scripts': {
                'demo': 'demo_pkg.cli:main',
                'extra': 'demo_pkg.extra:main',
            },
        }
