import json
import os
import zipfile

from alasio.deploy_dev.whl_record import RecordEntry, RecordManager
from alasio.ext.backport import removeprefix, removesuffix
from alasio.ext.cache import cached_property
from alasio.ext.concurrent.cmd import run_cmd
from alasio.ext.concurrent.threadpool import THREAD_POOL
from alasio.ext.path import PathStr
from alasio.ext.path.calc import to_posix


class DistInfo:
    def __init__(self, dist_info: PathStr):
        self.dist_info = dist_info
        self.site_packages = dist_info.uppath()

    @cached_property
    def top_level_list(self):
        file = self.dist_info / 'top_level.txt'
        try:
            content = file.atomic_read_text()
        except FileNotFoundError:
            return []

        out = []
        for row in content.splitlines():
            # adodbapi
            # win32\lib\afxres
            if not row:
                continue
            row = to_posix(row)
            out.append(row)
        return out

    @cached_property
    def record_list(self) -> "dict[str, RecordEntry]":
        file = self.dist_info / 'RECORD'
        try:
            content = file.atomic_read_bytes()
        except FileNotFoundError:
            return {}

        record = RecordManager()
        record.load_bytes(content)
        return record.entries

    @cached_property
    def folder_to_delete(self) -> "dict[str, PathStr]":
        out = {}
        # top_level.txt
        for top_level in self.top_level_list:
            if '/' in top_level:
                top_level, _, _ = top_level.partition('/')
            out[top_level] = self.site_packages / top_level

        # dist-info
        out[self.dist_info.name] = self.dist_info

        # folders of RECORD
        for record in self.record_list:
            # ../../Scripts/flask.exe
            if record.startswith('.'):
                continue
            if record.startswith('__pycache__'):
                continue
            folder, sep, _ = record.partition('/')
            if not sep:
                continue
            out[folder] = self.site_packages / folder
        return out

    @cached_property
    def file_to_delete(self) -> "dict[str, PathStr]":
        out = {}
        for record in self.record_list:
            # ../../Scripts/flask.exe
            if record.startswith('.'):
                file = os.path.abspath(os.path.join(self.site_packages, record))
                file = PathStr.new(file)
                out[record] = file
                continue

            folder, sep, _ = record.partition('/')
            if sep and folder in self.folder_to_delete:
                # will be delete in folder_to_delete, no need to add
                # flask/__init__.py
                continue
            out[record] = self.site_packages / record
        return out

    def uninstall(self):
        for folder in self.folder_to_delete.values():
            print(f'Delete folder: {folder}')
            folder.atomic_rmtree()
        for file in self.file_to_delete.values():
            print(f'Delete file: {file}')
            file.file_remove()


class SimplePip:
    def __init__(self, site_packages):
        self.site_packages = PathStr(site_packages)

    @classmethod
    def from_python(cls, python_executable):
        if not os.path.exists(python_executable):
            raise ValueError(f'Python executable not exists')

        # add xxxsitepackage tag to avoid random output in user cmd env
        code = 'import site, json; print("xxxsitepackage", json.dumps(site.getsitepackages()))'
        result = run_cmd([python_executable, '-c', code])
        # xxxsitepackage ['xxx\alas2026', 'xxx\alas2026\lib\site-packages']
        for row in result.splitlines():
            if not row.startswith('xxxsitepackage'):
                continue
            row = removeprefix(row, 'xxxsitepackage').strip()
            try:
                paths = json.loads(row)
            except json.JSONDecodeError:
                raise ValueError(f'Invalid getsitepackages return: "{row}"')

            for path in paths:
                if isinstance(path, str) and path.endswith('site-packages'):
                    return SimplePip(path)

        raise ValueError(f'Failed to get sitepackage from {python_executable}')

    @cached_property
    def dist_info(self):
        """
        Returns:
            dict[str, PathStr]:
                key: package name, value: path
        """
        out = {}
        for folder in self.site_packages.iter_folders():
            name = folder.name
            if not name.endswith('.dist-info'):
                continue
            name = removesuffix(name, '.dist-info')
            if not '-' in name:
                continue
            package, _, _ = name.partition('-')
            out[package] = folder
        return out

    def get_dist_info(self, name: str):
        # Distribution Name
        # 根据 PEP 427 规范，在 Wheel 的文件名和目录名中，分发名称中的 连字符 -、下划线 _ 和点 . 都应该被统一替换为 下划线 _。
        name = name.replace('-', '_').replace('.', '_')
        # find under lowercase
        name = name.lower()

        for package, folder in self.dist_info.items():
            if name == package.lower():
                return folder
        return None

    def uninstall(self, name: str):
        folder = self.get_dist_info(name)
        if folder is None:
            print(f'Package not exist: {name}')
            return False

        dist = DistInfo(folder)
        if dist.folder_to_delete or dist.file_to_delete:
            print(f'Uninstalling {name}')
        dist.uninstall()

    def install(self, wheel):
        """
        Install a wheel file

        Args:
            wheel (str | PathStr): Path to the .whl file
        """
        wheel = PathStr.new(wheel)
        with THREAD_POOL.wait_jobs() as pool:
            with zipfile.ZipFile(wheel, 'r') as zf:
                # 1. Find .dist-info folder
                dist_info_folder = None
                for name in zf.namelist():
                    if name.endswith('.dist-info/METADATA'):
                        dist_info_folder = name.rpartition('/')[0]
                        break

                if dist_info_folder is None:
                    raise ValueError(f'Invalid wheel: missing .dist-info/METADATA in {wheel}')

                # 2. Get package name and version
                # dist_info_folder is like "alasio-0.1.0.dist-info"
                package_version = removesuffix(dist_info_folder, '.dist-info')
                package_name = package_version.partition('-')[0]
                data_folder = package_version + '.data'

                # 3. Uninstall if exists
                self.uninstall(package_name)

                # 4. Extract files
                print(f'Installing {package_name} to {self.site_packages}')
                record = RecordManager()
                for member in zf.infolist():
                    if member.is_dir():
                        continue

                    rel_path = member.filename
                    # Skip RECORD, will be updated and written at the end
                    if rel_path == f'{dist_info_folder}/RECORD':
                        continue

                    if rel_path.startswith(data_folder + '/'):
                        # Handle .data directory (PEP 427)
                        content_path = removeprefix(rel_path, data_folder + '/')
                        category, sep, subpath = content_path.partition('/')
                        if not sep:
                            continue

                        if category in ['purelib', 'platlib']:
                            target = self.site_packages / subpath
                        elif category == 'scripts':
                            # On Windows: python_root/Scripts
                            # On Linux: python_root/bin
                            if os.name == 'nt':
                                target = self.site_packages.uppath(2) / 'Scripts' / subpath
                            else:
                                target = self.site_packages.uppath(3) / 'bin' / subpath
                        elif category == 'headers':
                            target = self.site_packages.uppath(2) / 'include' / subpath
                        elif category == 'data':
                            target = self.site_packages.uppath(2) / subpath
                        else:
                            continue
                    else:
                        # Normal files and .dist-info files
                        target = self.site_packages / rel_path

                    data = zf.read(member)
                    pool.start_thread_soon(target.file_write, data)

                    # Add to record
                    # Path in RECORD should be relative to site-packages
                    record_rel_path = to_posix(os.path.relpath(target, self.site_packages))
                    record.add_content(record_rel_path, data)

        # 5. Compile .py files
        import py_compile
        from importlib.util import cache_from_source
        files = list(record.iter_py_files())
        print(f'Compiling {len(files)} py files')

        def create_pyc(path: str):
            py_file = str(self.site_packages / path)
            try:
                # Compile to .pyc
                pyc_file = cache_from_source(py_file)
                py_compile.compile(py_file, cfile=pyc_file, dfile=path)
                # Add .pyc to record
                rel_pyc = to_posix(os.path.relpath(pyc_file, self.site_packages))
                record.add_content(rel_pyc, None)
            except Exception as e:
                # Some .py files might not be compilable (e.g. templates, incomplete scripts)
                print(f'Failed to compile {path}: {e}')

        with THREAD_POOL.wait_jobs() as pool:
            for entry in files:
                pool.start_thread_soon(create_pyc, entry.path)

        # 6. Write INSTALLER
        installer_rel_path = f'{dist_info_folder}/INSTALLER'
        record.add_content(installer_rel_path, b'pip\n')
        (self.site_packages / installer_rel_path).file_write(b'pip\n')

        # 7. Write updated RECORD
        record.add_content(f'{dist_info_folder}/RECORD', None)
        record_file = self.site_packages / dist_info_folder / 'RECORD'
        record_file.file_write(record.dump_bytes())
        print(f'Successfully installed {package_name}')


if __name__ == '__main__':
    self = SimplePip.from_python(r'E:\ProgramFiles\Anaconda3\envs\alas2026\python.exe')
    self.install(r'E:\ProgramData\Pycharm\Alasio\dist\alasio-0.1.0-py3-none-any.whl')
