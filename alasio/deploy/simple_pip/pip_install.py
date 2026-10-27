"""
Install a wheel file into a python environment, the way pip installs one.

The files of a wheel are placed by PEP 427: the members below the root of
the wheel (the modules and the .dist-info directory) go to the site-packages
directory of the environment, the members of the .data directory go to the
locations of its install scheme (Scripts, Include, the root of the
environment). The installation is then written the way pip writes it: the
.py files are compiled to .pyc, an INSTALLER file and a RECORD listing every
installed file are written. The distribution is a PEP 376 installation, pip
can uninstall it ("pip uninstall", "pip show -f"), importlib.metadata and
"pip list" read it, and an uninstallation removes exactly the files of the
RECORD.

The scope is one local wheel file at a time, on purpose: no requirement is
resolved, no index is queried, no dependency of the distribution is checked,
and the wheel is not checked against the environment it is installed to (the
platform tags of its filename, its WHEEL metadata). The caller decides what
is installed where, the installer reproduces the result of the installation
only, see the known limitations of SimplePip.

Usage:
    python -m alasio.deploy.simple_pip.pip_install demo-1.0-py3-none-any.whl
    python -m alasio.deploy.simple_pip.pip_install --python D:/toolkit/python.exe demo-1.0-py3-none-any.whl

    from alasio.deploy.simple_pip.pip_install import SimplePip
    pip = SimplePip.from_python(python_executable)
    pip.install(wheel)
"""
import argparse
import os
import py_compile
import stat
import sys
import zipfile

from alasio.backport import removeprefix, removesuffix
from alasio.deploy.simple_pip.pyc import pyc_path
from alasio.deploy.simple_pip.python_probe import PythonProbe, Scheme, prefix_of
from alasio.deploy.simple_pip.whl_record import RecordEntry, RecordManager
from alasio.ext.cache import cached_property
from alasio.ext.concurrent.threadpool import THREAD_POOL
from alasio.ext.path import PathStr
from alasio.ext.path.calc import is_abspath, subpath_to, to_posix
from alasio.logger import logger

# Categories of the .data directory of a wheel, PEP 427 maps the files of a
# wheel to the keys of the install scheme of the environment. Same keys as pip.
DATA_CATEGORIES = ('purelib', 'platlib', 'scripts', 'data', 'headers')


def assert_inside(root, path):
    """
    Check that a path is inside a directory.

    The members of a wheel are not trusted, a member named "../../evil.py"
    would be installed outside of the install scheme. pip rejects such wheels
    the same way (assert_no_path_traversal in pip's wheel.py).

    Args:
        root (str): Directory the files of the wheel may be installed to
        path (str): Target path of a wheel member

    Raises:
        ValueError: If path is not inside root
    """
    root = to_posix(os.path.normpath(root)).rstrip('/')
    path = to_posix(os.path.normpath(path))
    if path != root and not path.startswith(f'{root}/'):
        raise ValueError(f'File of the wheel is outside of the install directory: {path}')


def join_target(root, subpath):
    """
    Build the target path of a wheel member.

    Args:
        root (str): Directory to install the member to
        subpath (str): Path of the member, relative to the directory

    Returns:
        PathStr: Absolute posix path of the target

    Raises:
        ValueError: If the member is absolute, or escapes the directory
    """
    # os.path.join() replaces the root on an absolute member, the check
    # below rejects it, os.path.normpath() collapses ".." of the member
    target = PathStr.new(os.path.normpath(os.path.join(root, subpath)))
    assert_inside(root, target)
    return target


def zip_item_is_executable(member):
    """
    Check if a member of a wheel is an executable file.

    Args:
        member (zipfile.ZipInfo): Member of the wheel

    Returns:
        bool: True if the file has to keep the executable permission,
            pip does the same (zip_item_is_executable in pip's wheel.py)
    """
    mode = member.external_attr >> 16
    return bool(mode and stat.S_ISREG(mode) and mode & 0o111)


class DistInfo:
    """
    The .dist-info directory of an installed distribution, PEP 376.

    Files are deleted the way pip deletes them: only the files recorded in
    the RECORD are removed, a directory is removed only when it becomes
    empty, so the files of an other distribution sharing a directory are
    never removed. The removal is best effort, a file that can not be
    removed is left.

    Attributes:
        dist_info (PathStr): Path of the .dist-info directory
        site_packages (PathStr): Path of the site-packages directory
        prefix (PathStr): Path of the environment root
    """

    def __init__(self, dist_info, prefix=None):
        """
        Args:
            dist_info (str | PathStr): Path of the .dist-info directory
            prefix (str | PathStr): Root of the python environment, the base
                of the install scheme. Defaults to None, derived from the
                layout of site-packages (see prefix_of)
        """
        self.dist_info = PathStr.new(dist_info)
        self.site_packages = self.dist_info.uppath()
        if prefix is None:
            self.prefix = prefix_of(self.site_packages)
        else:
            self.prefix = PathStr.new(prefix)

    @cached_property
    def top_level_list(self):
        """
        Top level names of the distribution, read from top_level.txt.

        Informational, the file is not written by all build backends, use
        the RECORD to know the files of the distribution.

        Returns:
            list[str]: Import names, top level only, posix style
        """
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
        """
        Returns:
            dict[str, RecordEntry]: key is the path of the RECORD entry,
                relative to site-packages
        """
        file = self.dist_info / 'RECORD'
        try:
            content = file.atomic_read_bytes()
        except FileNotFoundError:
            return {}

        record = RecordManager()
        record.load_bytes(content)
        return record.entries

    def resolve_path(self, path):
        """
        Resolve a path of a RECORD entry against site-packages.

        The paths of a RECORD are relative to site-packages, PEP 376, files
        installed outside of it, e.g. scripts, are recorded with "../..".

        Args:
            path (str): Path of a RECORD entry

        Returns:
            PathStr: Absolute posix path
        """
        return PathStr.new(os.path.abspath(os.path.join(self.site_packages, to_posix(path))))

    @cached_property
    def record_paths(self) -> "dict[str, PathStr]":
        """
        The recorded paths resolved against site-packages.

        The paths are not checked against the disk: a RECORD lists files,
        PEP 376, and removing a file that is already gone is cheaper than
        the stat() of every entry.

        Returns:
            dict[str, PathStr]: key is the path of the RECORD entry,
                value is the absolute path of the file
        """
        return {path: self.resolve_path(path) for path in self.record_list}

    @cached_property
    def folder_to_delete(self) -> "list[PathStr]":
        """
        The directories of the distribution, the deepest first.

        Every directory holding a recorded file is listed, the removal of
        the files empties the deepest directories first, os.rmdir() then
        removes them from the deepest to the shallowest. A directory that
        is not empty, e.g. one holding a file of an other distribution or
        a file created after the installation, is left as is, the removal
        is best effort.

        The directories outside of site-packages holding the files
        installed outside of it, e.g. Scripts, are listed too. site-packages
        itself and the root of the environment are never removed.

        Returns:
            list[PathStr]: Paths of the directories
        """
        site_packages = self.site_packages
        prefix = self.prefix
        out = set()
        for path in self.record_paths.values():
            folder = path.uppath()
            # The prefix is a directory of the environment, the root "/" of a
            # POSIX environment included: subpath_to() checks the boundary at
            # a separator, a plain f'{prefix}/' would build "//" for the root
            # and then no directory would be inside of it
            while (
                folder != site_packages
                and folder != prefix
                and subpath_to(folder, prefix) is not None
            ):
                out.add(folder)
                folder = folder.uppath()
        # The longest path is the deepest directory, it has to be removed first
        return sorted(out, key=len, reverse=True)

    @property
    def name(self):
        """
        Name of the distribution of the .dist-info directory.

        "demo-1.0.dist-info" gives "demo", PEP 427 replaces the hyphens of
        a name with underscores in the directory name.

        Returns:
            str: Name of the distribution
        """
        return removesuffix(self.dist_info.name, '.dist-info').rpartition('-')[0]

    def uninstall(self):
        """
        Remove the files of the distribution recorded in the RECORD.

        The removal is best effort: a file that can not be removed, e.g. a
        file held by a running process, is left, and a directory is only
        removed when it is empty, so the files of an other distribution
        sharing a directory are never removed.

        Returns:
            tuple[int, int] | None: Number of files and directories removed,
                None if the distribution has no RECORD of its files, pip
                refuses to uninstall such a distribution as well ("no RECORD
                file was found")
        """
        if not self.record_list:
            if (self.dist_info / 'RECORD').exists():
                logger.warning(f'Cannot uninstall {self.name}: the RECORD lists no file')
            else:
                logger.warning(f'Cannot uninstall {self.name}: no RECORD file, unknown files of the package')
            return None

        files = 0
        for path in self.record_paths.values():
            try:
                if path.file_remove():
                    files += 1
            except OSError:
                # A file that can not be removed is left, the removal is best effort
                continue
        folders = 0
        for folder in self.folder_to_delete:
            if folder.folder_rmtree_empty():
                folders += 1
        return files, folders


def is_absolute_env_path(path):
    """
    Check that a path of a python environment is absolute.

    The paths of this module are posix style: an environment of Windows keeps
    its drive (``E:/envs/alasio/Lib/site-packages``), an environment of another
    platform starts at the posix root (``/envs/alasio/lib/python3.8``). Both
    forms are absolute for is_abspath(), which reads a rooted path as the root
    of the current drive instead of asking os.path.isabs(): the answer of
    os.path.isabs() for a posix path on Windows changed in python 3.13.

    Args:
        path (str): Path to check

    Returns:
        bool: True if the path is absolute
    """
    return is_abspath(path)


class SimplePip:
    """
    A minimal pip, install and uninstall wheels in a python environment.

    The installation follows PEP 427 and PEP 376, and behaves like pip:
    the files are installed to the locations of the install scheme of pip
    (site-packages, Scripts, include...) and the RECORD written lists every
    installed file with the checksum of its content, so pip can uninstall
    the installation ("pip uninstall", "pip show -f").

    The environment is a directory of a python environment, the site-packages
    of an environment running this process or of another one, see from_python().

    Known limitations:
        - the console scripts declared in entry_points.txt are not generated,
          a warning is printed, only the .data/scripts files are installed
        - only the .dist-info installations are supported, PEP 376, the legacy
          .egg-info installations of setuptools are not

    Attributes:
        site_packages (PathStr): Path of the site-packages directory, posix style
        prefix (PathStr): Path of the environment root, the bound of the
            directories an uninstallation may remove, posix style
        scheme (Scheme): Install scheme of the environment, where the
            categories of the .data directory of a wheel are installed to
    """

    def __init__(self, site_packages, prefix=None, scheme=None):
        """
        Args:
            site_packages (str | PathStr): Path of the site-packages directory,
                a native path is accepted, it is normalized to the posix style,
                a posix path without a drive is a path of the current drive on
                Windows
            prefix (str | PathStr): Root of the python environment. Defaults to
                None, derived from the layout of site-packages (see prefix_of),
                from_python() passes the sys.prefix of the interpreter it probed,
                the posix style of site_packages applies to it as well
            scheme (Scheme): Install scheme of the environment. Defaults to
                None, derived from the layout of site-packages (see
                Scheme.from_layout), from_python() passes the scheme of the
                interpreter it probed

        Raises:
            ValueError: If site_packages or prefix is not an absolute path
        """
        site_packages = PathStr.new(site_packages)
        if not is_absolute_env_path(site_packages):
            raise ValueError(f'site-packages is not an absolute path: "{site_packages}"')
        self.site_packages = site_packages

        if prefix is None:
            self.prefix = prefix_of(site_packages)
        else:
            prefix = PathStr.new(prefix)
            if not is_absolute_env_path(prefix):
                raise ValueError(f'prefix is not an absolute path: "{prefix}"')
            self.prefix = prefix

        if scheme is None:
            self.scheme = Scheme.from_layout(site_packages, prefix=self.prefix)
        else:
            self.scheme = scheme

    @classmethod
    def from_python(cls, python_executable):
        """
        Create a SimplePip of the environment of a python executable.

        The interpreter is probed once for the paths of its own environment
        (PythonProbe): the site-packages directory to install to, the prefix
        of the environment and its install scheme.

        Args:
            python_executable (str): Path of the python executable of the
                environment to install to

        Returns:
            SimplePip:

        Raises:
            ValueError: If the interpreter can not be probed, or its output
                holds no site-packages directory
        """
        probe = PythonProbe.from_python(python_executable)
        return cls(probe.site_packages, prefix=probe.prefix, scheme=probe.scheme)

    @property
    def dist_info(self) -> "dict[str, PathStr]":
        """
        The distributions installed in the environment.

        Not cached on purpose: the listing changes when a package is
        installed or uninstalled by this instance.

        Returns:
            dict[str, PathStr]: key is the package name of the .dist-info
                directory, value is the path of the .dist-info directory
        """
        out = {}
        for folder in self.site_packages.iter_folders():
            name = folder.name
            if not name.endswith('.dist-info'):
                continue
            name = removesuffix(name, '.dist-info')
            if '-' not in name:
                continue
            package, _, _ = name.partition('-')
            out[package] = folder
        return out

    def get_dist_info(self, name):
        """
        Find the .dist-info directory of a package.

        Args:
            name (str): Name of the package, the normalization of PEP 427
                is applied ("-" and "." are "_") and the match is case
                insensitive

        Returns:
            PathStr | None: Path of the .dist-info directory, None if the
                package is not installed
        """
        # Distribution Name
        # PEP 427 replaces the hyphen "-", the underscore "_" and the dot "."
        # of a distribution name with an underscore in the filename of a
        # wheel and in the directory name of its .dist-info
        name = name.replace('-', '_').replace('.', '_')
        # The names of the environment are not case sensitive
        name = name.lower()

        for package, folder in self.dist_info.items():
            if name == package.lower():
                return folder
        return None

    def uninstall(self, name):
        """
        Uninstall a package.

        Args:
            name (str): Name of the package

        Returns:
            bool: True if the package was uninstalled
        """
        folder = self.get_dist_info(name)
        if folder is None:
            logger.debug(f'Package not exist: {name}')
            return False

        result = DistInfo(folder, prefix=self.prefix).uninstall()
        if result is None:
            return False
        files, folders = result
        logger.info(f'Uninstalled {name}: {files} files, {folders} folders removed')
        return True

    def install(self, wheel):
        """
        Install a wheel file.

        The RECORD of the wheel is not used as is: like pip, the installation
        writes its own RECORD, listing the .pyc files compiled at install time,
        the INSTALLER file, and the files installed outside of site-packages
        under their real location ("../../Scripts/xxx"), so pip can uninstall
        the installation.

        Args:
            wheel (str | PathStr): Path of the .whl file

        Raises:
            ValueError: If the wheel is invalid, e.g. it holds no or several
                .dist-info directories, an unknown .data category, or a file
                outside of the install directory
            FileNotFoundError: If the wheel does not exist
            zipfile.BadZipFile: If the wheel is not a zip file
        """
        wheel = PathStr.new(wheel)
        with THREAD_POOL.wait_jobs() as pool:
            with zipfile.ZipFile(wheel, 'r') as zf:
                # 1. Find the .dist-info folder, PEP 427 requires exactly one
                dist_info_folder = self._find_dist_info(wheel, zf)

                # 2. Get package name and version
                # dist_info_folder is like "alasio-0.1.0.dist-info"
                package_version = removesuffix(dist_info_folder, '.dist-info')
                package_name, _, _ = package_version.partition('-')
                data_folder = f'{package_version}.data'

                # 3. Uninstall the previous installation of the package
                self.uninstall(package_name)

                # 4. Warn about the files that are not installed
                self._warn_entry_points(zf, dist_info_folder)

                # 5. Extract files
                logger.info(f'Installing {package_name} to {self.site_packages}')
                record = RecordManager()
                for member in zf.infolist():
                    if member.is_dir():
                        continue

                    rel_path = member.filename
                    # Skip RECORD, the RECORD of the installation is written
                    # at the end, it lists the files created at install time
                    if rel_path == f'{dist_info_folder}/RECORD':
                        continue

                    target = self._member_target(rel_path, data_folder, package_name, wheel)
                    if target is None:
                        continue

                    data = zf.read(member)
                    pool.start_thread_soon(self._write_file, target, data, zip_item_is_executable(member))

                    # Add to record
                    # Path in RECORD should be relative to site-packages
                    record_rel_path = to_posix(os.path.relpath(target, self.site_packages))
                    record.add_content(record_rel_path, data)

        # 6. Compile .py files
        files = list(record.iter_py_files())
        logger.info(f'Compiling {len(files)} py files')
        with THREAD_POOL.wait_jobs() as pool:
            for entry in files:
                pool.start_thread_soon(self._create_pyc, record, entry.path)

        # 7. Write INSTALLER
        installer_rel_path = f'{dist_info_folder}/INSTALLER'
        record.add_content(installer_rel_path, b'pip\n')
        (self.site_packages / installer_rel_path).file_write(b'pip\n')

        # 8. Write updated RECORD
        record.add_content(f'{dist_info_folder}/RECORD', None)
        record_file = self.site_packages / dist_info_folder / 'RECORD'
        record_file.file_write(record.dump_bytes())
        logger.info(f'Successfully installed {package_name}')

    @staticmethod
    def _find_dist_info(wheel, zf):
        """
        Find the .dist-info directory of a wheel, PEP 427 requires exactly one.

        Args:
            wheel (str): Path of the wheel, for error messages
            zf (zipfile.ZipFile): Opened wheel

        Returns:
            str: Name of the .dist-info directory

        Raises:
            ValueError: If the wheel holds no or several .dist-info
                directories, or no METADATA
        """
        names = zf.namelist()
        folders = {name.partition('/')[0] for name in names}
        dist_info_list = [folder for folder in folders if folder.endswith('.dist-info')]
        if len(dist_info_list) != 1:
            raise ValueError(
                f'Invalid wheel, expected 1 .dist-info directory, got {len(dist_info_list)}: {wheel}')
        dist_info_folder = dist_info_list[0]
        if '-' not in removesuffix(dist_info_folder, '.dist-info'):
            raise ValueError(f'Invalid wheel, invalid .dist-info directory "{dist_info_folder}": {wheel}')
        if f'{dist_info_folder}/METADATA' not in names:
            raise ValueError(f'Invalid wheel, missing .dist-info/METADATA: {wheel}')
        return dist_info_folder

    @staticmethod
    def _warn_entry_points(zf, dist_info_folder):
        """
        Warn about the console scripts declared by a wheel.

        The entry_points.txt of a wheel declares the scripts to generate,
        pip generates them with the launcher of the platform. SimplePip
        installs the files of the wheel only, the declared but not generated
        scripts are reported, so a missing script is not a surprise.

        Args:
            zf (zipfile.ZipFile): Opened wheel
            dist_info_folder (str): Name of the .dist-info directory
        """
        try:
            content = zf.read(f'{dist_info_folder}/entry_points.txt')
        except KeyError:
            return
        text = content.decode('utf-8')
        groups = [group for group in ('console_scripts', 'gui_scripts') if f'[{group}]' in text]
        if groups:
            logger.warning(f'{"/".join(groups)} of the wheel are not generated by SimplePip')

    def _member_target(self, rel_path, data_folder, package_name, wheel):
        """
        Resolve the install path of a member of a wheel, PEP 427.

        Args:
            rel_path (str): Name of the member in the wheel
            data_folder (str): Name of the .data directory, "<name>-<version>.data"
            package_name (str): Name of the distribution
            wheel (str): Path of the wheel, for error messages

        Returns:
            PathStr | None: Path of the target, None if the member is a
                directory entry of .data, its files are handled one by one

        Raises:
            ValueError: If the member holds an unknown .data category, or
                would be installed outside of the install directory
        """
        if not rel_path.startswith(f'{data_folder}/'):
            # Normal files and .dist-info files, installed to site-packages
            return join_target(self.site_packages, rel_path)

        content_path = removeprefix(rel_path, f'{data_folder}/')
        category, sep, subpath = content_path.partition('/')
        if not sep:
            # A directory entry of .data, the files of it are handled one by one
            return None
        if category not in DATA_CATEGORIES:
            raise ValueError(
                f'Invalid wheel, unsupported .data directory "{category}", '
                f'expected one of {", ".join(DATA_CATEGORIES)}: {wheel}')
        return join_target(self.scheme.dir_of(category, package_name), subpath)

    @staticmethod
    def _write_file(target, data, executable=False):
        """
        Write a file of a wheel, keep the executable permission of the member.

        Args:
            target (PathStr): Path of the file to write
            data (bytes): Content of the file
            executable (bool): True to set the executable permission,
                os.chmod() is a no op for it on Windows
        """
        target.file_write(data)
        if executable:
            os.chmod(target, os.stat(target).st_mode | 0o111)

    def _create_pyc(self, record, path):
        """
        Compile a .py file of the installation and record the .pyc file.

        The .pyc file is recorded without checksum and size, pip does the
        same, the file is regenerated by python when the source changes.
        Like pip, the .py files are compiled at install time, so that the
        first import does not need to write to site-packages.

        Args:
            record (RecordManager): RECORD of the installation
            path (str): Path of the .py file, relative to site-packages
        """
        py_file = str(self.site_packages / path)
        try:
            # The in-tree name of the installation, see pyc_path() for why it is
            # not importlib.util.cache_from_source()
            pyc_file = pyc_path(py_file)
            py_compile.compile(py_file, cfile=pyc_file, dfile=path)
        except Exception as e:
            # Some .py files might not be compilable (e.g. templates, incomplete scripts)
            logger.warning(f'Failed to compile {path}: {e}')
            return

        # Add .pyc to record
        rel_pyc = to_posix(os.path.relpath(pyc_file, self.site_packages))
        record.add_content(rel_pyc, None)


def main():
    """
    Command line entry, install the wheels into the environment of a python
    interpreter.

    The environment is the one of the running interpreter unless another one
    is named: --python probes the environment of that interpreter (see
    PythonProbe.from_python), --site-packages installs into the given
    site-packages directory without probing an interpreter, e.g. for a tree
    of an other device whose python can not be run.

    Raises:
        SystemExit: If the arguments are invalid, the target environment can
            not be read, or a wheel can not be installed
    """
    parser = argparse.ArgumentParser(
        description='Install a local wheel into the environment of a python interpreter',
    )
    parser.add_argument(
        'wheel', nargs='+',
        help='path of the .whl file to install, one or more',
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        '--python',
        help='python executable of the environment to install to, default to the running interpreter',
    )
    target.add_argument(
        '--site-packages',
        help='site-packages directory of the environment to install to, no interpreter is probed',
    )
    parser.add_argument(
        '--prefix',
        help='root of the environment to install to, only with --site-packages, '
             'derived from the layout of site-packages by default',
    )
    args = parser.parse_args()
    if args.prefix and not args.site_packages:
        parser.error('--prefix requires --site-packages')

    try:
        if args.site_packages:
            pip = SimplePip(args.site_packages, prefix=args.prefix)
        else:
            pip = SimplePip.from_python(args.python or sys.executable)
    except ValueError as e:
        parser.error(str(e))

    for wheel in args.wheel:
        try:
            pip.install(wheel)
        except (ValueError, FileNotFoundError, zipfile.BadZipFile) as e:
            parser.error(f'{wheel}: {e}')


if __name__ == '__main__':
    main()
