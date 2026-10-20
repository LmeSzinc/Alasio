from alasio.backport import removesuffix
from alasio.deploy.simple_pip.whl_record import RecordManager
from alasio.deploy_dev.wheel.alasio_wheel import RepoWithStatics
from alasio.deploy_dev.wheel.archive import GzipFileExtend, ZipFileExtend
from alasio.deploy_dev.wheel.parse_metadata import RepoStandardMetadata
from alasio.ext.cache import cached_property
from alasio.ext.deep import deep_get
from alasio.ext.path import PathStr
from alasio.git.stage.gitref import GitRef
from alasio.logger import logger


class BuildWheel:
    def __init__(self, root, folder):
        """
        Args:
            root (str): Project root
            folder (str): Import Name
        """
        self.root = PathStr.new(root)
        self.folder = folder

    """
    Git files
    """

    @cached_property
    def head(self):
        return GitRef(self.root).head_get()

    @cached_property
    def commit_time(self):
        """
        Committer time of the commit being built, in UTC+0.

        Every member of the built artifacts is stamped with this time, so
        building the same commit twice produces the same bytes.

        Returns:
            int: Unix timestamp

        Raises:
            ValueError: If the head does not point to a commit
        """
        obj = self.repo.cat(self.head)
        # an annotated tag points to its object, a tag may tag another tag
        while obj.type == 4:
            obj = self.repo.cat(obj.decoded.object)
        if obj.type != 1:
            raise ValueError(f'HEAD is not a commit: {self.head}')
        return int(obj.decoded.committer_time)

    @cached_property
    def repo(self):
        repo = RepoWithStatics(self.root, folder=self.folder)
        repo.read_lazy()
        return repo

    @cached_property
    def repo_metadata(self):
        """
        Standard metadata parsed from the commit.

        Returns:
            RepoStandardMetadata:
        """
        return RepoStandardMetadata(self.repo, self.folder, commit=self.head)

    @cached_property
    def package_files(self):
        """
        Files of the library tree in the commit.

        Returns:
            dict[str, FileEntry]: {filepath: FileEntry}
        """
        return self.repo.list_files(self.head)

    @cached_property
    def all_repo_files(self):
        """
        Every file of the commit, not only the files of the library tree.

        Returns:
            dict[str, FileEntry]: {filepath: FileEntry}
        """
        return self.repo_metadata.repo_files

    @cached_property
    def all_files(self):
        """
        Returns:
            dict[str, bytes]:
                key: filepath, value: file content
        """
        out = {}
        for entry in self.package_files.values():
            obj = self.repo.cat(entry.sha1)
            content = obj.decoded
            out[entry.path] = content

        # sort by filepath
        out = {k: v for k, v in sorted(out.items())}
        return out

    """
    Wheel files
    """

    @cached_property
    def project_data(self):
        """
        Project table parsed from pyproject.toml of the commit being built.

        Returns:
            dict: Parsed pyproject.toml
        """
        return self.repo_metadata.project_data

    @cached_property
    def metaname(self):
        """
        Distribution name used by the wheel file and the dist-info folder.

        Returns:
            str: Normalized distribution name
        """
        return self.repo_metadata.metaname

    @cached_property
    def version(self):
        """
        Project version.

        Returns:
            str: Version string
        """
        return self.repo_metadata.version

    @cached_property
    def data_metadata(self):
        """
        /{metaname}-{version}.dist-info/METADATA

        Returns:
            bytes:
        """
        return self.repo_metadata.data_metadata

    @cached_property
    def data_top_level_txt(self):
        """
        /{metaname}-{version}.dist-info/top_level.txt

        Returns:
            bytes:
        """
        data = f'{self.folder}\n'
        return data.encode(encoding='utf-8')

    @cached_property
    def data_wheel(self):
        """
        /{metaname}-{version}.dist-info/WHEEL

        Returns:
            bytes:
        """
        return '\n'.join([
            'Wheel-Version: 1.0',
            'Generator: setuptools (75.3.4)',
            'Root-Is-Purelib: true',
            'Tag: py3-none-any',
            '',
            ''
        ]).encode(encoding='utf-8')

    @cached_property
    def data_entry_points(self):
        """
        /{metaname}-{version}.dist-info/entry_points.txt

        The file declares the console scripts, the gui scripts and the other
        entry point groups of the project, an installer generates the scripts
        of the first two groups from this file.

        None when the project declares no entry point: a file without a
        section declares nothing and is not written at all.

        Returns:
            bytes | None:
        """
        entry_points = self.repo_metadata.entry_points
        if not entry_points:
            return None

        # The format of setuptools: a section per group, a blank line between
        # the sections. The groups and the names are sorted, so the same commit
        # produces the same file and the diff of a version is readable.
        lines = []
        for group, entries in sorted(entry_points.items()):
            if lines:
                lines.append('')
            lines.append(f'[{group}]')
            for name, value in sorted(entries.items()):
                lines.append(f'{name} = {value}')

        # The file ends with a single newline, not with a blank line
        return ('\n'.join(lines) + '\n').encode(encoding='utf-8')

    @cached_property
    def data_record(self):
        """
        /{metaname}-{version}.dist-info/RECORD

        Returns:
            bytes:
        """
        record = RecordManager()
        for file, content in self.all_files.items():
            record.add_content(file, content)

        folder = f'{self.metaname}-{self.version}.dist-info'
        record.add_content(f'{folder}/METADATA', self.data_metadata)
        record.add_content(f'{folder}/WHEEL', self.data_wheel)
        record.add_content(f'{folder}/top_level.txt', self.data_top_level_txt)
        entry_points = self.data_entry_points
        if entry_points is not None:
            record.add_content(f'{folder}/entry_points.txt', entry_points)
        for path, content in self.repo_metadata.license_files.items():
            record.add_content(f'{folder}/licenses/{path}', content)
        record.add_content(f'{folder}/RECORD', None)

        return record.dump_bytes()

    """
    Source
    """

    @cached_property
    def data_requires_txt(self):
        """
        {metaname}.egg-info/requires.txt

        Returns:
            bytes:
        """
        lines = []
        # Main dependencies
        dependencies = deep_get(self.project_data, keys='project.dependencies', default=[])
        for dep in dependencies:
            lines.append(dep)

        # Optional dependencies (extras)
        optional = deep_get(self.project_data, keys='project.optional-dependencies', default={})
        for extra, deps in optional.items():
            if lines:
                lines.append('')
            lines.append(f'[{extra}]')
            for dep in deps:
                lines.append(dep)

        return '\n'.join(lines).encode('utf-8')

    @cached_property
    def sdist_files(self):
        """
        Files for source distribution (sdist)

        Returns:
            dict[str, bytes]:
        """
        out = self.all_files.copy()

        # Include the files needed to build the project from the sdist
        extra = ['pyproject.toml']
        readme = deep_get(self.project_data, keys='project.readme', default=None)
        if isinstance(readme, str):
            extra.append(readme)
        elif isinstance(readme, dict) and readme.get('file'):
            extra.append(readme['file'])
        if 'README.md' not in extra:
            extra.append('README.md')
        license_file = deep_get(self.project_data, keys='project.license.file', default='')
        if license_file:
            extra.append(license_file)

        for path in extra:
            entry = self.all_repo_files.get(path)
            if entry is None:
                continue
            out[path] = self.repo.cat(entry.sha1).decoded

        # License files are project files and must be in the sdist
        out.update(self.repo_metadata.license_files)

        # Add PKG-INFO to root
        out['PKG-INFO'] = self.data_metadata

        return {k: v for k, v in sorted(out.items())}

    @cached_property
    def egg_info_files(self):
        """
        Files in {metaname}.egg-info/ directory

        Returns:
            dict[str, bytes]:
        """
        folder = f'{self.metaname}.egg-info'
        out = {}

        # PKG-INFO
        out[f'{folder}/PKG-INFO'] = self.data_metadata
        # top_level.txt
        out[f'{folder}/top_level.txt'] = self.data_top_level_txt
        # requires.txt
        out[f'{folder}/requires.txt'] = self.data_requires_txt
        # entry_points.txt
        entry_points = self.data_entry_points
        if entry_points is not None:
            out[f'{folder}/entry_points.txt'] = entry_points

        # SOURCES.txt
        # Need to include all files in sdist + egg-info itself
        sources = list(self.sdist_files.keys())
        for k in out.keys():
            sources.append(k)
        sources.append(f'{folder}/SOURCES.txt')
        out[f'{folder}/SOURCES.txt'] = '\n'.join(sorted(sources)).encode('utf-8')

        return out

    """
    build
    """

    @cached_property
    def file_wheel(self):
        return self.root / 'dist' / f'{self.metaname}-{self.version}-py3-none-any.whl'

    def _build_wheel(self):
        """
        Build the wheel file

        Returns:
            PathStr: Path to the built wheel file
        """
        file = self.file_wheel
        dist_info = f'{self.metaname}-{self.version}.dist-info'

        with ZipFileExtend(file, self.commit_time) as zf:
            # Package files
            for path, content in self.all_files.items():
                zf.write_member(path, content)
            # Metadata files
            zf.write_member(f'{dist_info}/METADATA', self.data_metadata)
            zf.write_member(f'{dist_info}/WHEEL', self.data_wheel)
            zf.write_member(f'{dist_info}/top_level.txt', self.data_top_level_txt)
            entry_points = self.data_entry_points
            if entry_points is not None:
                zf.write_member(f'{dist_info}/entry_points.txt', entry_points)
            # License files, PEP 639
            for path, content in self.repo_metadata.license_files.items():
                zf.write_member(f'{dist_info}/licenses/{path}', content)
            zf.write_member(f'{dist_info}/RECORD', self.data_record)

        return file

    @cached_property
    def file_targz(self):
        # PEP 625: the sdist filename uses the PEP 503 canonical name
        return self.root / 'dist' / f'{self.repo_metadata.canonical_name}-{self.version}.tar.gz'

    def _build_targz(self):
        """
        Build the source distribution (.tar.gz)

        Returns:
            PathStr: Path to the built targz file
        """
        file = self.file_targz
        base_dir = removesuffix(file.name, '.tar.gz')

        with GzipFileExtend(file, self.commit_time) as gz:
            # Source files
            for path, content in self.sdist_files.items():
                gz.write_member(f'{base_dir}/{path}', content)
            # Egg-info files
            for path, content in self.egg_info_files.items():
                gz.write_member(f'{base_dir}/{path}', content)

        return file

    def build(self):
        """
        Execute the build process

        Returns:
            bool: True if success
        """
        if not self.head:
            logger.error(f'Empty repo head: {self.root}')
            return False

        if not self.package_files:
            logger.error(f'No files found for package folder "{self.folder}" at commit {self.head}')
            return False

        try:
            metaname = self.metaname
            version = self.version
            commit_time = self.commit_time
        except ValueError as e:
            logger.error(str(e))
            return False

        logger.info(f'Building {metaname}=={version} commit={self.head} time={commit_time}')
        file_whl = self._build_wheel()
        logger.info(f'Wheel built at: {file_whl}')
        file_sdist = self._build_targz()
        logger.info(f'Source distribution built at: {file_sdist}')
        return True
