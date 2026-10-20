from pathlib import Path
from threading import Lock

from packaging.utils import canonicalize_name
from packaging.version import Version

from alasio.deploy_dev.get_version import get_version_from_source
from alasio.deploy_dev.wheel.repo_path import RepoPath
from alasio.ext.cache import cached_property
from alasio.ext.deep import deep_get
from alasio.ext.file.tomlfile import toml_loads

# pyproject_metadata calls the module level getters while from_pyproject()
# runs. The patch is global to the module, so metadata builds are serialized.
_METADATA_PATCH_LOCK = Lock()


class RepoStandardMetadata:
    """
    Standard metadata parsed from a git repository.

    The pyproject.toml and every file-backed metadata field are read from the
    commit, never from the working tree. The result is exposed by the cached
    data_metadata property.
    """

    def __init__(self, repo, folder='', commit=''):
        """
        Args:
            repo (GitRepo): Repository object, it must provide cat() and
                list_files() (or list_repo_files())
            folder (str): Top level folder of the library in the repo, used
                to read a dynamic version from {folder}/__init__.py.
                Defaults to "", no dynamic version fallback
            commit (str): Commit sha1. Defaults to "", the HEAD of the repo
        """
        self.repo = repo
        self.folder = folder
        self.commit = commit or repo.head_get()

    @cached_property
    def repo_files(self):
        """
        Every file of the commit, not filtered by the library folder.

        Returns:
            dict[str, FileEntry]: {filepath: FileEntry}
        """
        list_repo_files = getattr(self.repo, 'list_repo_files', None)
        if list_repo_files is not None:
            return list_repo_files(self.commit)
        return self.repo.list_files(self.commit)

    @cached_property
    def project_dir(self):
        """
        Cached Path-like root of the project, backed by the commit.

        Returns:
            RepoPath: Root path of the project
        """
        return RepoPath(self.repo, self.commit, files=self.repo_files)

    @cached_property
    def project_data(self):
        """
        Project table parsed from pyproject.toml of the commit.

        Returns:
            dict: Parsed pyproject.toml
        """
        try:
            content = self.project_dir.joinpath('pyproject.toml').read_bytes()
        except FileNotFoundError:
            raise ValueError(f'Missing pyproject.toml at commit {self.commit}') from None

        return toml_loads(content)

    @cached_property
    def canonical_name(self):
        """
        PEP 503 normalized distribution name.

        The name is lowercased and runs of '-', '_' and '.' are replaced by
        a single '-'. This is also the name used by the sdist filename.

        Returns:
            str: Canonical distribution name
        """
        name = canonicalize_name(
            str(deep_get(self.project_data, keys='project.name', default='')))
        if not name:
            raise ValueError(f'Empty project.name in pyproject.toml at commit {self.commit}')
        return name

    @cached_property
    def metaname(self):
        """
        Distribution name used by the wheel file and the dist-info folder.

        PEP 427 uses the PEP 503 name with '-' replaced by '_'.

        Returns:
            str: Normalized distribution name
        """
        return self.canonical_name.replace('-', '_')

    @cached_property
    def version(self):
        """
        PEP 440 normalized project version, from project.version or
        {folder}/__init__.py.

        Returns:
            str: Normalized version string
        """
        version = deep_get(self.project_data, keys='project.version', default='')
        if version:
            return str(Version(str(version)))

        # try to get from __init__.py of the commit
        if self.folder:
            try:
                content = self.project_dir.joinpath(f'{self.folder}/__init__.py').read_bytes()
            except FileNotFoundError:
                pass
            else:
                version = get_version_from_source(content.decode('utf-8'))
                if version:
                    return str(Version(str(version)))

        detail = f' and {self.folder}/__init__.py' if self.folder else ''
        raise ValueError(f'Empty version in pyproject.toml{detail} at commit {self.commit}')

    @cached_property
    def standard_metadata(self):
        """
        StandardMetadata built from the commit.

        pyproject_metadata reads readme / license files from project_dir when
        they are file-backed. The getters are patched to use the cached
        RepoPath of the commit instead of the Path of the working tree.

        Returns:
            StandardMetadata: Parsed metadata object
        """
        from contextlib import ExitStack
        from unittest.mock import patch

        from pyproject_metadata import StandardMetadata, pyproject as pyproject_module

        repo_path = self.project_dir
        original_get_license = pyproject_module.get_license
        original_get_license_files = pyproject_module.get_license_files
        original_get_readme = pyproject_module.get_readme

        def get_license(project, project_dir, error_collector):
            return original_get_license(project, repo_path, error_collector)

        def get_license_files(project, project_dir, error_collector):
            return original_get_license_files(project, repo_path, error_collector)

        def get_readme(project, project_dir, error_collector):
            return original_get_readme(project, repo_path, error_collector)

        with _METADATA_PATCH_LOCK, ExitStack() as stack:
            stack.enter_context(patch.object(pyproject_module, 'get_license', get_license))
            stack.enter_context(patch.object(pyproject_module, 'get_license_files', get_license_files))
            stack.enter_context(patch.object(pyproject_module, 'get_readme', get_readme))
            metadata = StandardMetadata.from_pyproject(
                self.project_data,
                # A path that does not exist: if a getter is left unpatched it
                # fails instead of silently reading the working tree.
                project_dir=Path(self.repo.path).joinpath('.pyproject-metadata-from-repo'),
            )

        metadata.version = self.version
        return metadata

    @cached_property
    def entry_points(self):
        """
        Entry point groups of the project, PEP 621.

        project.scripts and project.gui-scripts are shortcuts of the
        console_scripts and gui_scripts groups. A name declared by both the
        shortcut table and the group table is taken from the shortcut table,
        an empty group is dropped so it does not produce an empty section.

        Returns:
            dict[str, dict[str, str]]: {group: {name: value}}
        """
        metadata = self.standard_metadata
        entry_points = {
            group: dict(entries)
            for group, entries in metadata.entrypoints.items()
            if entries
        }
        for group, entries in (
            ('console_scripts', metadata.scripts),
            ('gui_scripts', metadata.gui_scripts),
        ):
            if entries:
                entry_points.setdefault(group, {}).update(entries)

        return entry_points

    @cached_property
    def license_files(self):
        """
        Contents of project.license-files resolved against the commit.

        Returns:
            dict[str, bytes]: {relative path: content}
        """
        paths = self.standard_metadata.license_files
        if not paths:
            return {}
        for path in paths:
            if not isinstance(path, RepoPath):
                raise ValueError(f'Unexpected license file path type: {type(path)!r}')
        return {path.as_posix(): path.read_bytes() for path in sorted(paths)}

    @cached_property
    def data_metadata(self):
        """
        /{metaname}-{version}.dist-info/METADATA

        Returns:
            bytes: Content of the METADATA file
        """
        return self.standard_metadata.as_rfc822().as_bytes()
