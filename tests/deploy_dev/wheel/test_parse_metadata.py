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
        """A version that is absent and not declared dynamic fails."""
        repo = make_repo({'pyproject.toml': b'[project]\nname = "demo-pkg"\n'})
        with pytest.raises(ValueError, match='project.version is missing'):
            _ = RepoStandardMetadata(repo, 'demo_pkg').version


PYPROJECT_DYNAMIC = b'''\
[project]
name = "demo-pkg"
dynamic = ["version"]
'''

PYPROJECT_DYNAMIC_ATTR = PYPROJECT_DYNAMIC + b'''\
[tool.setuptools.dynamic]
version = {attr = "demo_pkg.__version__"}
'''


class TestDynamicVersion:
    """Tests for the version of a project that declares it dynamic."""

    def test_static_version_wins(self, make_repo):
        """A static project.version is used, the rule is not read."""
        repo = make_repo({
            'pyproject.toml': (
                b'[project]\nname = "demo-pkg"\nversion = "1.0.0"\n'
                b'\n[tool.setuptools.dynamic]\nversion = {file = ["VERSION"]}\n'
            ),
            'VERSION': b'9.9.9\n',
        })
        assert RepoStandardMetadata(repo, 'demo_pkg').version == '1.0.0'

    def test_attr(self, make_repo):
        """version = {attr} reads a plain string of the module, never imports it."""
        repo = make_repo({
            'pyproject.toml': PYPROJECT_DYNAMIC_ATTR,
            # The import would fail if the module was executed
            'demo_pkg/__init__.py': b'"""demo."""\nfrom .missing import x\n\n__version__ = "2.3.4"\n',
        })
        metadata = RepoStandardMetadata(repo, 'demo_pkg')
        assert metadata.version == '2.3.4'
        assert 'Version: 2.3.4' in metadata.data_metadata.decode('utf-8')
        assert metadata.version_files == []

    def test_attr_submodule(self, make_repo):
        """An attr path may name a submodule of the package."""
        repo = make_repo({
            'pyproject.toml': (
                PYPROJECT_DYNAMIC
                + b'[tool.setuptools.dynamic]\nversion = {attr = "demo_pkg.__about__.version"}\n'
            ),
            'demo_pkg/__init__.py': b'',
            'demo_pkg/__about__.py': b'version = "2.3.4"\n',
        })
        assert RepoStandardMetadata(repo, 'demo_pkg').version == '2.3.4'

    def test_attr_package_init(self, make_repo):
        """An attr path may name the __init__.py of a subpackage."""
        repo = make_repo({
            'pyproject.toml': (
                PYPROJECT_DYNAMIC
                + b'[tool.setuptools.dynamic]\nversion = {attr = "demo_pkg.version.VERSION"}\n'
            ),
            'demo_pkg/version/__init__.py': b'VERSION = "2.3.4"\n',
        })
        assert RepoStandardMetadata(repo, 'demo_pkg').version == '2.3.4'

    def test_attr_annotated_assignment(self, make_repo):
        """__version__: str = "..." is a plain string assignment too."""
        repo = make_repo({
            'pyproject.toml': PYPROJECT_DYNAMIC_ATTR,
            'demo_pkg/__init__.py': b'__version__: str = "2.3.4"\n',
        })
        assert RepoStandardMetadata(repo, 'demo_pkg').version == '2.3.4'

    def test_attr_normalized(self, make_repo):
        """The attr value is normalized by PEP 440."""
        repo = make_repo({
            'pyproject.toml': PYPROJECT_DYNAMIC_ATTR,
            'demo_pkg/__init__.py': b'__version__ = "2.3.4-alpha"\n',
        })
        assert RepoStandardMetadata(repo, 'demo_pkg').version == '2.3.4a0'

    def test_attr_computed(self, make_repo):
        """A computed attribute is not read, the module is not imported."""
        repo = make_repo({
            'pyproject.toml': PYPROJECT_DYNAMIC_ATTR,
            'demo_pkg/__init__.py': b'from setuptools_scm import get_version\n__version__ = get_version()\n',
        })
        with pytest.raises(ValueError, match='not a plain string assignment'):
            _ = RepoStandardMetadata(repo, 'demo_pkg').version

    def test_attr_missing_attribute(self, make_repo):
        """A module that does not assign the attribute fails."""
        repo = make_repo({
            'pyproject.toml': PYPROJECT_DYNAMIC_ATTR,
            'demo_pkg/__init__.py': b'x = 1\n',
        })
        with pytest.raises(ValueError, match='not a plain string assignment'):
            _ = RepoStandardMetadata(repo, 'demo_pkg').version

    def test_attr_missing_module(self, make_repo):
        """An attr of a module the commit does not have fails."""
        repo = make_repo({
            'pyproject.toml': PYPROJECT_DYNAMIC_ATTR,
            'demo_pkg/core.py': b'a = 1\n',
        })
        with pytest.raises(ValueError, match='is not in the commit'):
            _ = RepoStandardMetadata(repo, 'demo_pkg').version

    def test_attr_malformed(self, make_repo):
        """An attr without a module path fails."""
        repo = make_repo({
            'pyproject.toml': (
                PYPROJECT_DYNAMIC
                + b'[tool.setuptools.dynamic]\nversion = {attr = "__version__"}\n'
            ),
        })
        with pytest.raises(ValueError, match='expected a dotted path'):
            _ = RepoStandardMetadata(repo, 'demo_pkg').version

    def test_file(self, make_repo):
        """version = {file} reads the file of the commit."""
        repo = make_repo({
            'pyproject.toml': (
                PYPROJECT_DYNAMIC
                + b'[tool.setuptools.dynamic]\nversion = {file = ["VERSION"]}\n'
            ),
            'VERSION': b'3.1.4\n',
        })
        metadata = RepoStandardMetadata(repo, 'demo_pkg')
        assert metadata.version == '3.1.4'
        assert metadata.version_files == ['VERSION']

    def test_file_string(self, make_repo):
        """version = {file = "VERSION"} is the single file form."""
        repo = make_repo({
            'pyproject.toml': (
                PYPROJECT_DYNAMIC
                + b'[tool.setuptools.dynamic]\nversion = {file = "VERSION"}\n'
            ),
            'VERSION': b'3.1.4\n',
        })
        metadata = RepoStandardMetadata(repo, 'demo_pkg')
        assert metadata.version == '3.1.4'
        assert metadata.version_files == ['VERSION']

    def test_file_list(self, make_repo):
        """The files are joined with a newline, PEP 440 drops the whitespace."""
        repo = make_repo({
            'pyproject.toml': (
                PYPROJECT_DYNAMIC
                + b'[tool.setuptools.dynamic]\nversion = {file = ["VERSION", "EXTRA"]}\n'
            ),
            'VERSION': b'2.3.4\n',
            'EXTRA': b'',
        })
        metadata = RepoStandardMetadata(repo, 'demo_pkg')
        assert metadata.version == '2.3.4'
        assert metadata.version_files == ['VERSION', 'EXTRA']

    def test_file_missing(self, make_repo):
        """A version file the commit does not have fails."""
        repo = make_repo({
            'pyproject.toml': (
                PYPROJECT_DYNAMIC
                + b'[tool.setuptools.dynamic]\nversion = {file = ["VERSION"]}\n'
            ),
        })
        with pytest.raises(ValueError, match='is not in the commit'):
            _ = RepoStandardMetadata(repo, 'demo_pkg').version

    def test_file_empty(self, make_repo):
        """An empty version file fails."""
        repo = make_repo({
            'pyproject.toml': (
                PYPROJECT_DYNAMIC
                + b'[tool.setuptools.dynamic]\nversion = {file = ["VERSION"]}\n'
            ),
            'VERSION': b'\n',
        })
        with pytest.raises(ValueError, match='Empty version'):
            _ = RepoStandardMetadata(repo, 'demo_pkg').version

    def test_unknown_rule(self, make_repo):
        """A rule this builder does not read fails instead of guessing."""
        repo = make_repo({
            'pyproject.toml': (
                PYPROJECT_DYNAMIC
                + b'[tool.setuptools.dynamic]\nversion = {glob = "VERSION"}\n'
            ),
            'VERSION': b'3.1.4\n',
        })
        with pytest.raises(ValueError, match='Invalid'):
            _ = RepoStandardMetadata(repo, 'demo_pkg').version

    def test_undeclared_dynamic(self, make_repo):
        """A version only in __init__.py has to be declared dynamic."""
        repo = make_repo({
            'pyproject.toml': b'[project]\nname = "demo-pkg"\n',
            'demo_pkg/__init__.py': b'__version__ = "5.0.0"\n',
        })
        with pytest.raises(ValueError, match='project.version is missing'):
            _ = RepoStandardMetadata(repo, 'demo_pkg').version

    def test_declared_without_rule(self, make_repo):
        """A declared dynamic version without a rule is read from __init__.py."""
        repo = make_repo({
            'pyproject.toml': PYPROJECT_DYNAMIC,
            'demo_pkg/__init__.py': b'__version__ = "2.3.4"\n',
        })
        metadata = RepoStandardMetadata(repo, 'demo_pkg')
        assert metadata.version == '2.3.4'
        assert metadata.version_files == []


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
