import json
import os
import py_compile
import stat
import zipfile
from importlib.util import cache_from_source

from alasio.backport import removeprefix, removesuffix
from alasio.deploy_dev.simple_pip.whl_record import RecordEntry, RecordManager
from alasio.ext.cache import cached_property
from alasio.ext.concurrent.cmd import CmdlineError, run_cmd
from alasio.ext.concurrent.threadpool import THREAD_POOL
from alasio.ext.path import PathStr
from alasio.ext.path.calc import to_posix

# Categories of the .data directory of a wheel, PEP 427 maps the files of a
# wheel to the keys of the install scheme of the environment. Same keys as pip.
DATA_CATEGORIES = ('purelib', 'platlib', 'scripts', 'data', 'headers')

# Conventions of the install scheme of Windows (Lib/site-packages, Scripts,
# Include). A module constant on purpose: os.name drives the flavour pathlib
# instantiates on every Path() call, patching it breaks pathlib while the
# patch is active, tests patch this constant to cover both layouts
WINDOWS = os.name == 'nt'

# Debian based distributions name the site-packages directory dist-packages
SITE_PACKAGES_NAMES = ('site-packages', 'dist-packages')

# Tag of the probe output, the command line environment of the user prints
# random output as well, only the lines with the tag are read
PROBE_TAG = 'xxxsitepackage'

# The probe asks the interpreter for the paths of its environment in one call:
#   - the site-packages directories (site.getsitepackages)
#   - the prefix of the environment (sys.prefix), the bound of the directories
#     an uninstallation may remove. Not the directory of the executable: a
#     virtual environment keeps its python executable in Scripts/ on Windows,
#     in bin/ on Linux
#   - the install scheme (sysconfig.get_paths), the truth for the locations of
#     the files of a wheel, the layout of the environment is not re-derived
#     here. In a virtual environment pip overrides the include directory, the
#     include directory of the interpreter belongs to the base environment,
#     the override is applied the same way (pip's locations/_sysconfig.py)
PROBE_CODE = """\
import json, os, site, sys, sysconfig
scheme = sysconfig.get_paths()
if sys.prefix != getattr(sys, 'base_prefix', sys.prefix) or hasattr(sys, 'real_prefix'):
    scheme['include'] = os.path.join(sys.prefix, 'include', 'site', 'python%d.%d' % sys.version_info[:2])
print('{tag}', json.dumps(dict(site_packages=site.getsitepackages(), prefix=sys.prefix, scheme=scheme)))
""".format(tag=PROBE_TAG)


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


def prefix_of(site_packages):
    """
    Root of the python environment, derived from the site-packages layout.

    Windows:     <prefix>/Lib/site-packages
    Linux/macOS: <prefix>/lib/python3.X/site-packages

    Only the default install scheme is supported, the derivation is the
    fallback when the prefix of the interpreter is not known, from_python()
    probes the sys.prefix of the interpreter instead.

    Args:
        site_packages (str): Path of the site-packages directory, posix style

    Returns:
        PathStr: Path of the environment root
    """
    site_packages = PathStr.new(site_packages)
    if WINDOWS:
        return site_packages.uppath(2)
    else:
        return site_packages.uppath(3)


class Scheme:
    """
    Install scheme of a python environment, the target of the categories of
    the .data directory of a wheel (PEP 427).

    The paths are the paths of the environment itself, sysconfig.get_paths()
    of the interpreter (see probe_python), not paths re-derived from the
    layout of site-packages: a virtual environment keeps its python
    executable in Scripts/ on Windows and in bin/ on Linux, the include
    directory of the interpreter belongs to the base environment, and
    sysconfig is the only one knowing the real locations.

    Attributes:
        purelib (PathStr): Directory of the pure python packages
        platlib (PathStr): Directory of the platform specific packages
        scripts (PathStr): Directory of the scripts
        data (PathStr): Root of the environment
        include (PathStr): Directory of the headers, the headers of a
            distribution go to a directory named after it below it, pip does
            the same
    """

    def __init__(self, purelib, platlib, scripts, data, include):
        """
        Args:
            purelib (str | PathStr): Directory of the pure python packages
            platlib (str | PathStr): Directory of the platform specific packages
            scripts (str | PathStr): Directory of the scripts
            data (str | PathStr): Root of the environment
            include (str | PathStr): Directory of the headers
        """
        self.purelib = PathStr.new(purelib)
        self.platlib = PathStr.new(platlib)
        self.scripts = PathStr.new(scripts)
        self.data = PathStr.new(data)
        self.include = PathStr.new(include)

    def __repr__(self):
        return (
            f'<Scheme purelib={self.purelib} platlib={self.platlib} scripts={self.scripts} '
            f'data={self.data} include={self.include}>'
        )

    def dir_of(self, category, package_name):
        """
        Directory of a category of the .data directory of a wheel.

        Args:
            category (str): Category of the .data directory, PEP 427
            package_name (str): Name of the distribution, the headers of a
                distribution go to their own directory

        Returns:
            PathStr: Directory to install the files of the category to
        """
        if category == 'purelib':
            return self.purelib
        elif category == 'platlib':
            return self.platlib
        elif category == 'scripts':
            return self.scripts
        elif category == 'headers':
            return self.include / package_name
        else:
            # data
            return self.data

    @classmethod
    def from_dict(cls, payload):
        """
        Build the scheme probed from an interpreter.

        Args:
            payload (dict | None): The "scheme" of the probe output

        Returns:
            Scheme | None: None if the payload does not hold every path of
                the scheme, the caller falls back to from_layout()
        """
        if not isinstance(payload, dict):
            return None
        keys = ('purelib', 'platlib', 'scripts', 'data', 'include')
        if not all(isinstance(payload.get(key), str) and payload[key] for key in keys):
            return None
        return cls(**{key: payload[key] for key in keys})

    @classmethod
    def from_layout(cls, site_packages, prefix=None):
        """
        Build the scheme of the default install scheme from the layout of
        site-packages, the fallback when no interpreter was probed.

        Windows:     <prefix>/Lib/site-packages, <prefix>/Scripts,
                     <prefix>/Include
        Linux/macOS: <prefix>/lib/python3.X/site-packages, <prefix>/bin,
                     <prefix>/include/python3.X

        The include directory of a virtual environment is not reproduced,
        pip uses <prefix>/include/site/python3.X there, the interpreter has
        to be probed to know it.

        Args:
            site_packages (str | PathStr): Path of the site-packages directory
            prefix (str | PathStr): Root of the environment. Defaults to
                None, derived from the layout of site-packages (see prefix_of)

        Returns:
            Scheme:
        """
        site_packages = PathStr.new(site_packages)
        prefix = prefix_of(site_packages) if prefix is None else PathStr.new(prefix)
        if WINDOWS:
            scripts = prefix / 'Scripts'
            include = prefix / 'Include'
        else:
            scripts = prefix / 'bin'
            # <prefix>/lib/python3.X/site-packages -> python3.X
            version = site_packages.uppath(1).name
            include = prefix / 'include' / version if version.startswith('python') else prefix / 'include'
        return cls(
            purelib=site_packages,
            platlib=site_packages,
            scripts=scripts,
            data=prefix,
            include=include,
        )


def probe_python(python_executable):
    """
    Ask a python interpreter for the paths of its environment, in one call.

    Returns:
        dict: "site_packages" (list[str]), "prefix" (str) and "scheme"
            (dict[str, str]), the paths of the interpreter itself

    Raises:
        ValueError: If the interpreter can not be run, or its output can not
            be read
    """
    # The output of the probe is filtered by the tag, the command line
    # environment of the user may print random output as well
    try:
        result = run_cmd([python_executable, '-c', PROBE_CODE]).stdout
    except CmdlineError as e:
        raise ValueError(f'Failed to probe {python_executable}: {e}')

    # xxxsitepackage {"site_packages": ["xxx\\envs\\alas2026\\Lib\\site-packages"], "prefix": "xxx\\envs\\alas2026", "scheme": {...}}
    for row in result.splitlines():
        if not row.startswith(PROBE_TAG):
            continue
        row = removeprefix(row, PROBE_TAG).strip()
        try:
            info = json.loads(row)
        except json.JSONDecodeError:
            raise ValueError(f'Invalid probe return: "{row}"')
        if not isinstance(info, dict):
            raise ValueError(f'Invalid probe return: "{row}"')
        return info

    raise ValueError(f'Failed to probe {python_executable}')


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
        inside_prefix = f'{self.prefix}/'
        out = set()
        for path in self.record_paths.values():
            folder = path.uppath()
            while folder != site_packages and folder.startswith(inside_prefix):
                out.add(folder)
                folder = folder.uppath()
        # The longest path is the deepest directory, it has to be removed first
        return sorted(out, key=len, reverse=True)

    def uninstall(self):
        """
        Remove the files of the distribution recorded in the RECORD.

        The removal is best effort: a file that can not be removed, e.g. a
        file held by a running process, is left, and a directory is only
        removed when it is empty, so the files of an other distribution
        sharing a directory are never removed.

        Returns:
            bool: True if the distribution was removed, False if the
                distribution has no RECORD listing its files, pip refuses
                to uninstall such a distribution as well ("no RECORD file
                was found")
        """
        if not self.record_list:
            if (self.dist_info / 'RECORD').exists():
                print(f'Cannot uninstall {self.dist_info.name}: the RECORD lists no file')
            else:
                print(f'Cannot uninstall {self.dist_info.name}: no RECORD file, unknown files of the package')
            return False

        for path in self.record_paths.values():
            print(f'Delete file: {path}')
            try:
                path.file_remove()
            except OSError:
                # A file that can not be removed is left, the removal is best effort
                continue
        for folder in self.folder_to_delete:
            print(f'Delete folder: {folder}')
            folder.folder_rmtree_empty()
        return True


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
                a native path is accepted, it is normalized to the posix style
            prefix (str | PathStr): Root of the python environment. Defaults to
                None, derived from the layout of site-packages (see prefix_of),
                from_python() passes the sys.prefix of the interpreter it probed
            scheme (Scheme): Install scheme of the environment. Defaults to
                None, derived from the layout of site-packages (see
                Scheme.from_layout), from_python() passes the scheme of the
                interpreter it probed

        Raises:
            ValueError: If site_packages or prefix is not an absolute path
        """
        site_packages = PathStr.new(site_packages)
        if not os.path.isabs(site_packages):
            raise ValueError(f'site-packages is not an absolute path: "{site_packages}"')
        self.site_packages = site_packages

        if prefix is None:
            self.prefix = prefix_of(site_packages)
        else:
            prefix = PathStr.new(prefix)
            if not os.path.isabs(prefix):
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

        The interpreter is probed once for the paths of its own environment:
        the site-packages directory to install to, the prefix of the
        environment and its install scheme.

        Args:
            python_executable (str): Path of the python executable of the
                environment to install to

        Returns:
            SimplePip:

        Raises:
            ValueError: If the interpreter can not be probed, or its output
                holds no site-packages directory
        """
        info = probe_python(python_executable)
        prefix = info.get('prefix')
        if not isinstance(prefix, str) or not prefix:
            prefix = None
        scheme = Scheme.from_dict(info.get('scheme'))

        for path in info.get('site_packages') or []:
            if not isinstance(path, str):
                continue
            if not path.endswith(SITE_PACKAGES_NAMES):
                continue
            return cls(path, prefix=prefix, scheme=scheme)

        # The listing of the interpreter named no site-packages directory,
        # the purelib directory of its scheme is the one
        if scheme is not None:
            return cls(scheme.purelib, prefix=prefix, scheme=scheme)

        raise ValueError(f'Failed to get sitepackage from {python_executable}')

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
            print(f'Package not exist: {name}')
            return False

        print(f'Uninstalling {name}')
        return DistInfo(folder, prefix=self.prefix).uninstall()

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
                print(f'Installing {package_name} to {self.site_packages}')
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
        print(f'Compiling {len(files)} py files')
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
        print(f'Successfully installed {package_name}')

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
            print(f'Warning: {"/".join(groups)} of the wheel are not generated by SimplePip')

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
            # Compile to .pyc
            pyc_file = cache_from_source(py_file)
            py_compile.compile(py_file, cfile=pyc_file, dfile=path)
        except Exception as e:
            # Some .py files might not be compilable (e.g. templates, incomplete scripts)
            print(f'Failed to compile {path}: {e}')
            return

        # Add .pyc to record
        rel_pyc = to_posix(os.path.relpath(pyc_file, self.site_packages))
        record.add_content(rel_pyc, None)


if __name__ == '__main__':
    self = SimplePip.from_python(r'E:\ProgramFiles\Anaconda3\envs\alas2026\python.exe')
    self.install(r'E:\ProgramData\Pycharm\Alasio\dist\alasio-0.1.0-py3-none-any.whl')
