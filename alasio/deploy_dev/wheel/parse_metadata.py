from pathlib import Path
from threading import Lock

from packaging.utils import canonicalize_name
from packaging.version import Version

from alasio.deploy_dev.get_version import get_attr_from_source, get_version_from_source
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
    def dynamic_version(self):
        """
        The rule that reads the version, from the [tool.setuptools.dynamic]
        table of the commit.

        This is the table setuptools reads when a project declares its version
        dynamic, see https://setuptools.pypa.io/en/latest/userguide/pyproject_config.html

        Returns:
            dict | None: The rule, e.g. {"attr": "demo_pkg.__version__"} or
                {"file": ["VERSION"]}, None if the project declares no rule
        """
        return deep_get(self.project_data, keys='tool.setuptools.dynamic.version', default=None)

    @cached_property
    def version_files(self):
        """
        Files the dynamic version is read from, the build inputs an sdist has
        to carry so the version can be rebuilt from it.

        Returns:
            list[str]: Paths relative to the repo root, empty when the version
                is read from an attribute or from a module
        """
        return self._dynamic_version_files() or []

    @cached_property
    def version(self):
        """
        PEP 440 normalized project version.

        The version is project.version, or, when the project declares the
        version dynamic, the rule of [tool.setuptools.dynamic].version:

        - ``version = {attr = "module.attribute"}`` reads a plain string
          assignment of the attribute, the module is never imported;
        - ``version = {file = ["VERSION"]}`` reads the files, joined with a
          newline the way setuptools joins them.

        A project that declares the version dynamic without a rule in
        [tool.setuptools.dynamic] is read from {folder}/__init__.py.

        Returns:
            str: Normalized version string

        Raises:
            ValueError: If project.version is missing and not declared
                dynamic, if the declared rule is unknown, or if the rule does
                not yield a version from the commit
        """
        version = deep_get(self.project_data, keys='project.version', default='')
        if version:
            return str(Version(str(version)))

        self._require_dynamic_version()

        rule = self.dynamic_version
        if rule:
            return self._version_from_dynamic(rule)

        # try to get from __init__.py of the commit
        if self.folder:
            source = self._read_commit_text(f'{self.folder}/__init__.py')
            if source is not None:
                version = self._read_version(path=f'{self.folder}/__init__.py', source=source)
                if version:
                    return str(Version(str(version)))

        detail = f' and {self.folder}/__init__.py' if self.folder else ''
        raise ValueError(f'Empty version in pyproject.toml{detail} at commit {self.commit}')

    def _require_dynamic_version(self):
        """
        Check that the project declares the version dynamic.

        A version that is neither declared nor in project.version can not be
        read by any build backend: setuptools rejects the pyproject.toml with
        "project must contain ['version'] properties".

        Raises:
            ValueError: If project.dynamic does not declare version
        """
        dynamic = deep_get(self.project_data, keys='project.dynamic', default=[])
        if isinstance(dynamic, list) and 'version' in dynamic:
            return
        raise ValueError(
            f'Field project.version is missing at commit {self.commit}: '
            f'declare it in [project], or list "version" in project.dynamic'
        )

    def _version_from_dynamic(self, rule):
        """
        Read the version by the rule of [tool.setuptools.dynamic].version.

        Args:
            rule: The rule, {"attr": str} or {"file": str | list[str]}

        Returns:
            str: Normalized version string

        Raises:
            ValueError: If the rule is not one this builder reads, or its
                target does not yield a version from the commit
        """
        if isinstance(rule, dict) and isinstance(rule.get('attr'), str):
            return self._version_from_attr(rule['attr'])
        files = self._dynamic_version_files()
        if files is not None:
            return self._version_from_file(files)
        raise self._invalid_dynamic_version(rule)

    def _dynamic_version_files(self):
        """
        The files of the [tool.setuptools.dynamic].version rule, if it reads
        files.

        Returns:
            list[str] | None: Paths of the rule, None if the rule reads an
                attribute or is not shaped like the setuptools table
        """
        rule = self.dynamic_version
        if not isinstance(rule, dict):
            return None
        files = rule.get('file')
        if isinstance(files, str):
            return [files]
        if isinstance(files, list) and all(isinstance(file, str) for file in files):
            return files
        return None

    def _invalid_dynamic_version(self, rule):
        """
        Build the error of a rule this builder does not read.

        Args:
            rule: The rule read from the commit

        Returns:
            ValueError: The error to raise
        """
        return ValueError(
            f'Invalid [tool.setuptools.dynamic].version at commit {self.commit}: {rule!r}, '
            f'expected {{attr = "module.attribute"}} or {{file = ["path"]}}'
        )

    def _version_from_attr(self, attr):
        """
        Read the version from a module attribute of the commit, the reader of
        setuptools ``version = {attr = "module.attribute"}``.

        The module is parsed, never imported: the build reads the commit, and
        an import would run code the build environment does not have.

        Args:
            attr (str): Dotted path, e.g. "demo_pkg.__version__"

        Returns:
            str: Normalized version string

        Raises:
            ValueError: If the path is malformed, the module is not in the
                commit, or the attribute is not a plain string assignment
        """
        module, _, name = attr.rpartition('.')
        if not module or not name:
            raise ValueError(
                f'Invalid [tool.setuptools.dynamic].version attr "{attr}" at commit {self.commit}: '
                f'expected a dotted path like "demo_pkg.__version__"'
            )

        base = module.replace('.', '/')
        in_commit = False
        for path in (f'{base}.py', f'{base}/__init__.py'):
            source = self._read_commit_text(path)
            if source is None:
                continue
            in_commit = True
            version = self._read_version(path=path, source=source, name=name)
            if version is not None:
                return str(Version(str(version)))

        if in_commit:
            raise ValueError(
                f'Attribute "{name}" of the module "{module}" is not a plain string assignment '
                f'at commit {self.commit}, so the version can not be read statically'
            )
        raise ValueError(
            f'Module "{module}" of [tool.setuptools.dynamic].version attr "{attr}" is not in the '
            f'commit {self.commit}'
        )

    def _version_from_file(self, files):
        """
        Read the version from files of the commit, joined with a newline the
        way setuptools reads ``version = {file = ["VERSION"]}``.

        Args:
            files (list[str]): Paths relative to the repo root

        Returns:
            str: Normalized version string

        Raises:
            ValueError: If a file is not in the commit, or the content is empty
        """
        contents = []
        for path in files:
            source = self._read_commit_text(path)
            if source is None:
                raise ValueError(
                    f'File "{path}" of [tool.setuptools.dynamic].version is not in the commit '
                    f'{self.commit}'
                )
            contents.append(source)

        version = '\n'.join(contents)
        if not version.strip():
            raise ValueError(
                f'Empty version in the files of [tool.setuptools.dynamic].version at commit '
                f'{self.commit}: {files!r}'
            )
        return str(Version(version))

    def _read_commit_text(self, path):
        """
        Read a file of the commit as text.

        Args:
            path (str): Path relative to the repo root

        Returns:
            str | None: Content of the file, None if the commit has no such
                file

        Raises:
            ValueError: If the file is not UTF-8 text
        """
        try:
            content = self.project_dir.joinpath(path).read_bytes()
        except FileNotFoundError:
            return None
        try:
            return content.decode('utf-8')
        except UnicodeDecodeError as e:
            raise ValueError(f'"{path}" of the commit {self.commit} is not UTF-8: {e}') from e

    def _read_version(self, path, source, name=None):
        """
        Read the version of a module of the commit, without importing it.

        Args:
            path (str): Path of the module, for the error message
            source (str): Source code of the module
            name (str): Attribute to read. Defaults to None, the
                ``__version__`` / ``version`` assignment

        Returns:
            str | None: The value, None if the module assigns no plain string

        Raises:
            ValueError: If the source is not valid Python
        """
        try:
            if name is None:
                return get_version_from_source(source)
            return get_attr_from_source(source, name)
        except SyntaxError as e:
            raise ValueError(f'Can not parse "{path}" at commit {self.commit}: {e}') from e

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
