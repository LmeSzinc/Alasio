"""
Probe a python interpreter for the paths of its environment.

An installer that places the files of a wheel needs three paths of the
target environment: the directory its packages are installed to, the root
of the environment (the bound of the directories an uninstallation may
remove) and its install scheme (where the categories of the .data
directory of a wheel go, PEP 427). The paths are not derived from the
layout of a site-packages directory: the interpreter itself is asked in
one call, sysconfig is the only one knowing the real locations of an
environment, a virtual environment keeps its python executable in
Scripts/ on Windows and in bin/ on Linux.

Usage:
    probe = PythonProbe.from_python(sys.executable)
    print(probe.site_packages)   # xxx/envs/alasio/Lib/site-packages
    print(probe.prefix)          # xxx/envs/alasio
    print(probe.scheme.scripts)  # xxx/envs/alasio/Scripts
"""
import json
import os

from alasio.backport import removeprefix
from alasio.ext.concurrent.cmd import CmdlineError, run_cmd
from alasio.ext.path import PathStr

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


def probe_python(python_executable):
    """
    Ask a python interpreter for the paths of its environment, in one call.

    Args:
        python_executable (str): Path of the python executable to probe

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


def prefix_of(site_packages):
    """
    Root of the python environment, derived from the site-packages layout.

    Windows:     <prefix>/Lib/site-packages
    Linux/macOS: <prefix>/lib/python3.X/site-packages

    Only the default install scheme is supported, the derivation is the
    fallback when the prefix of the interpreter is not known, PythonProbe
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


class PythonProbe:
    """
    The environment of a python interpreter, probed in one call.

    The three paths an installer of the environment needs, read from the
    output of the probe of the interpreter itself:

        probe = PythonProbe.from_python(python_executable)
        probe.site_packages   # where the packages of the environment live
        probe.prefix          # the root the environment is bound by
        probe.scheme          # where the categories of .data go

    Attributes:
        site_packages (PathStr): Directory the packages of the environment
            are installed to, posix style
        prefix (PathStr | None): Root of the environment, posix style, None
            when the interpreter did not report one and the layout of
            site-packages has to be derived from (see prefix_of)
        scheme (Scheme | None): Install scheme of the environment, None when
            the interpreter did not report a usable one and the default
            scheme of the layout has to be derived (see Scheme.from_layout)
    """

    def __init__(self, site_packages, prefix=None, scheme=None):
        """
        Args:
            site_packages (str | PathStr): Path of the site-packages directory,
                a native path is accepted, it is normalized to the posix style
            prefix (str | PathStr): Root of the python environment. Defaults to
                None, the layout of site-packages is derived from
            scheme (Scheme): Install scheme of the environment. Defaults to
                None, the default scheme of the layout is derived
        """
        self.site_packages = PathStr.new(site_packages)
        self.prefix = None if prefix is None else PathStr.new(prefix)
        self.scheme = scheme

    @classmethod
    def from_python(cls, python_executable):
        """
        Probe a python executable for the paths of its environment.

        The interpreter is run once (probe_python), the answer names the
        site-packages directories it imports from, the prefix of its
        environment and its install scheme. The site-packages directory
        installed to is the first directory of the answer named
        "site-packages" or "dist-packages" (Debian based distributions
        name it dist-packages); an answer that holds no such directory
        falls back to the purelib directory of the probed scheme, an
        interpreter that reports neither is refused.

        Args:
            python_executable (str): Path of the python executable of the
                environment to probe

        Returns:
            PythonProbe:

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
