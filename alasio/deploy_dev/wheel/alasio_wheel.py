from alasio.codegen.asar import AsarArchive
from alasio.deploy_dev.pack.encode_manifest import PackEncodeManifest
from alasio.ext.cache import cached_property
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_bytes
from alasio.ext.path.calc import subpath, to_posix
from alasio.git.obj.obj import GitLooseObject
from alasio.git.repo import GitRepo
from alasio.git.stage.gitreset import FileEntry
from alasio.git.stage.hashobj import blob_hash
from alasio.logger import logger


class MemoryObject(GitLooseObject):
    path: str
    sha1: str
    mode: bytes


class RepoWithStatics(GitRepo):
    def __init__(self, path, folder='alasio'):
        """
        Args:
            path (str): Absolute path to repo, the repo should contain .git folder
            folder (str): Top level folder of the library in the repo.
                Defaults to 'alasio'.
        """
        super().__init__(path)
        self.folder = folder

    def list_repo_files(self, sha1):
        """
        List every file of the commit, including the files outside of the
        library folder.

        The static files are not injected: they are build artifacts, not
        source files. The sdist needs the repository files (pyproject.toml,
        README.md, ...), while the wheel and the pack need the library tree
        only.

        Args:
            sha1 (str): Commit sha1, tree sha1, or tag sha1

        Returns:
            dict[str, FileEntry]: {filepath: FileEntry}
        """
        return super().list_files(sha1)

    @cached_property
    def static_files(self):
        out = {}
        root = PathStr.new(self.path)

        # frontend
        frontend_root = root / 'frontend/build'
        if frontend_root.isdir():
            count = 0
            frontend_manifest = PackEncodeManifest()
            for file in frontend_root.iter_files(recursive=True):
                content = atomic_read_bytes(file)
                path = f'{self.folder}/deploy_data/frontend/{to_posix(subpath(file, frontend_root))}'
                out[path] = self._build_file_obj(path, content)
                frontend_manifest.add_file(path, content)
                count += 1
            logger.info(f'Loaded {count} frontend files')

            # frontend-manifest.pack
            content = b''.join(frontend_manifest.iter_manifest_data())
            path = f'{self.folder}/deploy_data/frontend-manifest.pack'
            out[path] = self._build_file_obj(path, content)
        elif (root / 'frontend').isdir():
            logger.warning(f'Frontend build not found, the wheel has no frontend: {frontend_root}')

        # webapp
        webapp_root = root / 'webapp'
        if (webapp_root / 'dist').isdir() and (webapp_root / 'package.json').isfile():
            count_webapp = 0
            with AsarArchive() as asar:
                count_webapp += asar.add_folder(webapp_root / 'dist')
                asar.add_file(webapp_root / 'package.json')
                count_webapp += 1
                asar.write(webapp_root / 'release/app.asar')
            content = atomic_read_bytes(webapp_root / 'release/app.asar')
            path = f'{self.folder}/deploy_data/webapp/app.asar'
            out[path] = self._build_file_obj(path, content)
            logger.info(f'Loaded {count_webapp} webapp files -> 1 webapp files')
        elif webapp_root.isdir():
            logger.warning(f'Webapp build not found, the wheel has no webapp: {webapp_root}')

        return out

    @staticmethod
    def _build_file_obj(path, content, mode=b'100644'):
        return MemoryObject(type=3, size=len(content), data=content, sha1=blob_hash(content), mode=mode, path=path)

    def list_files(self, sha1):
        files = super().list_files(sha1)
        for path, obj in self.static_files.items():
            files[path] = FileEntry(sha1=obj.sha1, mode=obj.mode, path=obj.path)

        out = {}
        prefix = f'{self.folder}/'
        for path, obj in files.items():
            if not obj.path.startswith(prefix):
                continue
            out[path] = obj
        return out

    @cached_property
    def _dict_cat_index(self):
        out = {}
        for path, obj in self.static_files.items():
            out[obj.sha1] = obj
        return out

    def cat(self, sha1):
        obj = self._dict_cat_index.get(sha1)
        if obj is not None:
            return obj
        return super().cat(sha1)
