import copy
import io
import tarfile
import time
import zipfile

from alasio.backport import removesuffix
from alasio.deploy.simple_pip.whl_record import RecordManager
from alasio.deploy_dev.get_version import get_version_from_source
from alasio.deploy_dev.wheel.alasio_wheel import RepoWithStatics
from alasio.ext.cache import cached_property
from alasio.ext.deep import deep_get
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import file_remove
from alasio.git.stage.gitref import GitRef
from alasio.logger import logger


def _remove_tmp_file(file):
    """
    Remove the tmp file of a failed build, best effort.

    Args:
        file (str): Temporary file path
    """
    try:
        file_remove(file)
    except OSError:
        pass


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
    def repo(self):
        repo = RepoWithStatics(self.root, folder=self.folder)
        repo.read_lazy()
        return repo

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
        return self.repo.list_repo_files(self.head)

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

    def _read_repo_file(self, path):
        """
        Read a file of the commit being built.

        Args:
            path (str): Repository relative path, POSIX style

        Returns:
            bytes | None: File content, None when the commit has no such file
        """
        entry = self.repo.get_file(self.head, path)
        if entry is None:
            return None
        return self.repo.cat(entry.sha1).decoded

    """
    Wheel files
    """

    @cached_property
    def project_data(self):
        """
        Project table parsed from pyproject.toml of the commit being built.

        The working tree file is never read: the content comes from
        repo.get_file(self.head, 'pyproject.toml').

        Returns:
            dict: Parsed pyproject.toml
        """
        content = self._read_repo_file('pyproject.toml')
        if content is None:
            raise ValueError(f'Missing pyproject.toml at commit {self.head}')

        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib
            except ImportError:
                raise ImportError('Dependency `tomli` is required on Python < 3.11') from None

        return tomllib.loads(content.decode('utf-8'))

    @cached_property
    def metaname(self):
        """
        Distribution Name
        根据 PEP 427 规范，在 Wheel 的文件名和目录名中，分发名称中的 连字符 -、下划线 _ 和点 . 都应该被统一替换为 下划线 _。
        """
        name = str(deep_get(self.project_data, keys='project.name', default=''))
        name = name.replace('-', '_').replace('.', '_')
        if not name:
            raise ValueError(f'Empty project.name in pyproject.toml at commit {self.head}')
        return name

    @cached_property
    def version(self):
        version = deep_get(self.project_data, keys='project.version', default='')
        if version:
            return version

        # try to get from __init__.py of the commit
        content = self._read_repo_file(f'{self.folder}/__init__.py')
        if content is not None:
            version = get_version_from_source(content.decode('utf-8'))
            if version:
                return version

        raise ValueError(
            f'Empty version in pyproject.toml and {self.folder}/__init__.py at commit {self.head}')

    @cached_property
    def data_metadata(self):
        """
        /{metaname}-{version}.dist-info/METADATA

        Returns:
            bytes:
        """
        from pyproject_metadata import StandardMetadata

        # 解析元数据
        # 如果你的版本号是动态生成的，可以在这里手动传入 version
        # StandardMetadata needs project_dir only for file-backed fields
        # (readme, license). The readme is inlined from the commit, so
        # pyproject.toml is never read from the working tree.
        metadata = StandardMetadata.from_pyproject(
            self._metadata_project_data, project_dir=self.root)
        metadata.version = self.version

        # 转换为标准的 RFC822 消息格式（即 Wheel 里的 METADATA 内容）
        return metadata.as_rfc822().as_bytes()

    @cached_property
    def _metadata_project_data(self):
        """
        project_data with the readme file of the commit inlined.

        StandardMetadata reads a readme file from project_dir, which is the
        working tree, while the wheel is built from the commit. Inlining the
        content of the commit keeps the metadata on the revision being built.

        Returns:
            dict: Project data for StandardMetadata
        """
        data = copy.deepcopy(self.project_data)
        project = data.get('project')
        if not isinstance(project, dict):
            return data

        readme = project.get('readme')
        if isinstance(readme, str):
            filename = readme
            content_type = None
        elif isinstance(readme, dict) and readme.get('file'):
            filename = readme['file']
            content_type = readme.get('content-type')
            if not content_type:
                raise ValueError(f'Missing content-type of readme file: "{filename}"')
        else:
            # no readme, or the text is already inline
            return data

        content = self._read_repo_file(filename)
        if content is None:
            raise ValueError(f'Missing readme file at commit {self.head}: "{filename}"')

        if not content_type:
            if filename.endswith('.md'):
                content_type = 'text/markdown'
            elif filename.endswith('.rst'):
                content_type = 'text/x-rst'
            else:
                raise ValueError(f'Cannot infer the content type of readme file: "{filename}"')

        project['readme'] = {
            'text': content.decode('utf-8'),
            'content-type': content_type,
        }
        return data

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
        tmp = file.to_tmp_file()
        file.uppath().makedirs(exist_ok=True)

        def write_all(filename):
            with zipfile.ZipFile(filename, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
                # Package files, including the generated pack files
                for path, content in self.all_files.items():
                    zf.writestr(path, content)
                # Metadata files
                zf.writestr(f'{dist_info}/METADATA', self.data_metadata)
                zf.writestr(f'{dist_info}/WHEEL', self.data_wheel)
                zf.writestr(f'{dist_info}/top_level.txt', self.data_top_level_txt)
                zf.writestr(f'{dist_info}/RECORD', self.data_record)

        try:
            write_all(tmp)
        except BaseException:
            _remove_tmp_file(tmp)
            raise

        tmp.atomic_replace(file)
        return file

    @cached_property
    def file_targz(self):
        # Distribution name for sdist usually uses original name (with hyphens)
        name = str(deep_get(self.project_data, keys='project.name', default=''))
        return self.root / 'dist' / f'{name}-{self.version}.tar.gz'

    def _build_targz(self):
        """
        Build the source distribution (.tar.gz)

        Returns:
            PathStr: Path to the built targz file
        """
        file = self.file_targz
        base_dir = removesuffix(file.name, '.tar.gz')
        tmp = file.to_tmp_file()
        file.uppath().makedirs(exist_ok=True)

        def write_all(filename):
            with tarfile.open(filename, 'w:gz') as tar:
                # Set mtime for reproducibility if possible
                # although it might be better with commit time
                mtime = int(time.time())
                # Source files
                for path, content in self.sdist_files.items():
                    info = tarfile.TarInfo(name=f'{base_dir}/{path}')
                    info.size = len(content)
                    info.mtime = mtime
                    tar.addfile(info, fileobj=io.BytesIO(content))
                # Egg-info files
                for path, content in self.egg_info_files.items():
                    info = tarfile.TarInfo(name=f'{base_dir}/{path}')
                    info.size = len(content)
                    info.mtime = mtime
                    tar.addfile(info, fileobj=io.BytesIO(content))

        try:
            write_all(tmp)
        except BaseException:
            _remove_tmp_file(tmp)
            raise

        tmp.atomic_replace(file)
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
        except ValueError as e:
            logger.error(str(e))
            return False

        logger.info(f'Building {metaname}=={version} commit={self.head}')
        file_whl = self._build_wheel()
        logger.info(f'Wheel built at: {file_whl}')
        file_sdist = self._build_targz()
        logger.info(f'Source distribution built at: {file_sdist}')
        return True
