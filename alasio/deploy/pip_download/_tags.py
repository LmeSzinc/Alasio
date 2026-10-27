"""
The tags a wheel declares and the tags the running interpreter supports.

Ported from `packaging/tags.py` (packaging, Apache-2.0 OR BSD-2-Clause): the
`Tag` class, `parse_tag`, `cpython_tags`, `generic_tags`, `compatible_tags`,
the macOS and Linux platform tags and `sys_tags` are the source
implementation. The parts the fetcher does not need are not copied: the
generic `android_platforms` / `ios_platforms` / `emscripten` families, the
stable pickle of `Tag` and the tag selector helper.

The manylinux and musllinux families live in the sibling modules, like the
source keeps them in `_manylinux.py` and `_musllinux.py`.

Usage:
    from alasio.deploy.pip_download._tags import sys_tags

    tags = list(sys_tags())          # most preferred tag first
    Tag('py3', 'none', 'any') in tags
"""

import logging
import re
import struct
import sys
import sysconfig
from importlib.machinery import EXTENSION_SUFFIXES

from alasio.deploy.pip_download import _manylinux, _musllinux

__all__ = [
    'INTERPRETER_SHORT_NAMES',
    'InvalidTag',
    'Tag',
    'compatible_tags',
    'cpython_tags',
    'generic_tags',
    'interpreter_name',
    'interpreter_version',
    'mac_platforms',
    'parse_tag',
    'platform_tags',
    'pure_python_tags',
    'sys_tags',
]

logger = logging.getLogger(__name__)

# The short name of the interpreter implementations the tags use
INTERPRETER_SHORT_NAMES = {
    'python': 'py',  # generic
    'cpython': 'cp',
    'pypy': 'pp',
    'ironpython': 'ip',
    'jython': 'jy',
}

# Whether the interpreter is a 32 bit build, computed once
_32_BIT_INTERPRETER = struct.calcsize('P') == 4


class InvalidTag(ValueError):
    """
    Raised when a tag is empty, or does not have exactly three components.

    Ported from packaging/tags.py.
    """


class Tag:
    """
    One wheel tag, the triple (interpreter, abi, platform).

    Ported from packaging/tags.py: the components are lowercased and the
    hash is precomputed, because a set of tags is compared against the tags
    of every link of an index page.

    Attributes:
        interpreter (str): Interpreter name, e.g. 'cp38' or 'py3'
        abi (str): ABI name, e.g. 'cp38', 'abi3' or 'none'
        platform (str): Platform name, e.g. 'win_amd64' or 'any'
    """

    __slots__ = ['_abi', '_hash', '_interpreter', '_platform']

    def __init__(self, interpreter, abi, platform):
        """
        Args:
            interpreter (str): Interpreter name, e.g. 'py3'
            abi (str): ABI name, e.g. 'none'
            platform (str): Platform name, e.g. 'any'
        """
        self._interpreter = interpreter.lower()
        self._abi = abi.lower()
        self._platform = platform.lower()
        # the hash of the components, computed here: the tags of a page are
        # compared against the supported set many times, and the hash of a
        # tuple is computed once per instance this way
        self._hash = hash((self._interpreter, self._abi, self._platform))

    @property
    def interpreter(self):
        """
        Returns:
            str: The interpreter name, e.g. 'py3'
        """
        return self._interpreter

    @property
    def abi(self):
        """
        Returns:
            str: The ABI name, e.g. 'none'
        """
        return self._abi

    @property
    def platform(self):
        """
        Returns:
            str: The platform name, e.g. 'any'
        """
        return self._platform

    def __eq__(self, other):
        if not isinstance(other, Tag):
            return NotImplemented
        return (
            (self._hash == other._hash)
            and (self._platform == other._platform)
            and (self._abi == other._abi)
            and (self._interpreter == other._interpreter)
        )

    def __hash__(self):
        return self._hash

    def __str__(self):
        return f'{self._interpreter}-{self._abi}-{self._platform}'

    def __repr__(self):
        return f'<{self} @ {id(self)}>'


def parse_tag(tag, validate_order=False):
    """
    Parse a tag string into the set of tags it names.

    Ported from packaging/tags.py: the set is required because a compressed
    tag set names several tags, e.g. 'py2.py3-none-any' supports both python
    2 and python 3.

    Args:
        tag (str): The tag string, e.g. 'py3-none-any'
        validate_order (bool): Check that the components of a compressed tag
            set are sorted, per PEP 425. Defaults to False

    Returns:
        frozenset[Tag]: The tags of the string

    Raises:
        InvalidTag: If the string does not have exactly three non-empty
            components
    """
    components = tag.split('-')
    for component in components:
        parts = component.split('.')
        if '' in parts:
            raise InvalidTag(f'Tag {tag!r} has an empty component: {component!r}')
        if validate_order and parts != sorted(parts):
            raise InvalidTag(
                f'Tag component {component!r} is not in sorted order per PEP 425')
    try:
        interpreters, abis, platforms = components
    except ValueError:
        raise InvalidTag(f'Tag {tag!r} must have exactly three components') from None

    return frozenset(
        Tag(interpreter, abi, platform_)
        for interpreter in interpreters.split('.')
        for abi in abis.split('.')
        for platform_ in platforms.split('.')
    )


def _get_config_var(name, warn=False):
    """
    Read a sysconfig variable, optionally warning when it is unset.

    Ported from packaging/tags.py.

    Args:
        name (str): Name of the variable, e.g. 'Py_DEBUG'
        warn (bool): Log a debug line when the variable is unset. Defaults
            to False

    Returns:
        int | str | None: Value of the variable
    """
    value = sysconfig.get_config_var(name)
    if value is None and warn:
        logger.debug(
            f"Config variable '{name}' is unset, the Python ABI tag may be incorrect")
    return value


def _normalize_string(string):
    """
    Normalize a platform string into its tag form.

    Ported from packaging/tags.py.

    Args:
        string (str): Platform string, e.g. 'linux-x86_64'

    Returns:
        str: The normalized string, e.g. 'linux_x86_64'
    """
    return string.replace('.', '_').replace('-', '_').replace(' ', '_')


def _is_threaded_cpython(abis):
    """
    Whether the ABI list is a free-threaded (`--disable-gil`) build.

    Ported from packaging/tags.py: the threaded builds carry a 't' in the
    abi flags, e.g. 'cp313t'.

    Args:
        abis (list[str]): ABIs of the interpreter

    Returns:
        bool: True for a free-threaded build
    """
    if len(abis) == 0:
        return False
    # expect e.g. 'cp313'
    m = re.match(r'cp\d+(.*)', abis[0])
    if not m:
        return False
    abiflags = m.group(1)
    return 't' in abiflags


def _abi3_applies(python_version, threading):
    """
    Whether the interpreter supports the abi3 (stable ABI) tags.

    Ported from packaging/tags.py: PEP 384 was first implemented in python
    3.2, and the free-threaded builds do not support abi3.

    Args:
        python_version (Sequence[int]): Python version, e.g. (3, 8)
        threading (bool): Whether the build is free-threaded

    Returns:
        bool: True when abi3 wheels are usable
    """
    return len(python_version) > 1 and tuple(python_version) >= (3, 2) and not threading


def _abi3t_applies(python_version, threading):
    """
    Whether the interpreter supports the abi3t tags.

    Ported from packaging/tags.py: PEP 803 was first implemented in python
    3.15, but the tags go back to 3.2 like abi3.

    Args:
        python_version (Sequence[int]): Python version, e.g. (3, 14)
        threading (bool): Whether the build is free-threaded

    Returns:
        bool: True when abi3t wheels are usable
    """
    return len(python_version) > 1 and tuple(python_version) >= (3, 2) and threading


def _cpython_abis(py_version, warn=False):
    """
    The ABI tags of a CPython version.

    Ported from packaging/tags.py, the debug and pymalloc detection included:
    the tags are built from the sysconfig of the interpreter.

    Args:
        py_version (Sequence[int]): Python version, e.g. (3, 8)
        warn (bool): Log the unset sysconfig variables. Defaults to False

    Returns:
        list[str]: The ABIs, the most specific one first
    """
    py_version = tuple(py_version)
    abis = []
    version = _version_nodot(py_version[:2])
    threading = debug = pymalloc = ucs4 = ''
    with_debug = _get_config_var('Py_DEBUG', warn)
    has_refcount = hasattr(sys, 'gettotalrefcount')
    # windows does not set Py_DEBUG, so the support of debug-compiled
    # extension modules is the best option there
    has_ext = '_d.pyd' in EXTENSION_SUFFIXES
    if with_debug or (with_debug is None and (has_refcount or has_ext)):
        debug = 'd'
    if py_version >= (3, 13) and _get_config_var('Py_GIL_DISABLED', warn):
        threading = 't'
    if py_version < (3, 8):
        with_pymalloc = _get_config_var('WITH_PYMALLOC', warn)
        if with_pymalloc or with_pymalloc is None:
            pymalloc = 'm'
        if py_version < (3, 3):
            unicode_size = _get_config_var('Py_UNICODE_SIZE', warn)
            if unicode_size == 4 or (
                    unicode_size is None and sys.maxunicode == 0x10FFFF):
                ucs4 = 'u'
    elif debug:
        # the debug builds can also load the normal extension modules, and
        # neither ucs4 nor pymalloc is required
        abis.append(f'cp{version}{threading}')
    abis.insert(0, f'cp{version}{threading}{debug}{pymalloc}{ucs4}')
    return abis


def cpython_tags(python_version=None, abis=None, platforms=None, warn=False):
    """
    Yield the tags of a CPython interpreter.

    Ported from packaging/tags.py. The tags, in the order of preference:

    - cp<version>-<abi>-<platform>
    - cp<version>-abi3-<platform>
    - cp<version>-none-<platform>
    - cp<older version>-abi3-<platform> for every older minor down to 3.2

    Args:
        python_version (Sequence[int] | None): Python version to generate
            for. Defaults to None, sys.version_info[:2]
        abis (Iterable[str] | None): Compatible ABIs. Defaults to None, the
            ABIs of the running interpreter
        platforms (Iterable[str] | None): Compatible platforms. Defaults to
            None, the platforms of the running system
        warn (bool): Log the unset sysconfig variables. Defaults to False

    Yields:
        Tag: The tags, most preferred first
    """
    if not python_version:
        python_version = sys.version_info[:2]

    interpreter = f'cp{_version_nodot(python_version[:2])}'

    if abis is None:
        abis = _cpython_abis(python_version, warn) if len(python_version) > 1 else []
    abis = list(abis)
    threading = _is_threaded_cpython(abis)
    # the stable ABIs and 'none' are handled below
    explicit_abis = ('abi3', 'abi3t', 'none') if threading else ('abi3', 'none')
    for explicit_abi in explicit_abis:
        try:
            abis.remove(explicit_abi)
        except ValueError:
            pass

    platforms = list(platforms or platform_tags())
    for abi in abis:
        for platform_ in platforms:
            yield Tag(interpreter, abi, platform_)

    use_abi3 = _abi3_applies(python_version, threading)
    use_abi3t = _abi3t_applies(python_version, threading)
    if use_abi3:
        for platform_ in platforms:
            yield Tag(interpreter, 'abi3', platform_)
    if use_abi3t:
        for platform_ in platforms:
            yield Tag(interpreter, 'abi3t', platform_)

    for platform_ in platforms:
        yield Tag(interpreter, 'none', platform_)

    if use_abi3 or use_abi3t:
        for minor_version in range(python_version[1] - 1, 1, -1):
            for platform_ in platforms:
                version = _version_nodot((python_version[0], minor_version))
                interpreter = f'cp{version}'
                if use_abi3:
                    yield Tag(interpreter, 'abi3', platform_)
                if use_abi3t:
                    # abi3t was introduced in python 3.15, the tag of an older
                    # version is allowed like abi3
                    yield Tag(interpreter, 'abi3t', platform_)


def _generic_abi():
    """
    The ABI tag of a non-CPython interpreter, read from EXT_SUFFIX.

    Ported from packaging/tags.py.

    Returns:
        list[str]: The ABIs

    Raises:
        SystemError: If the EXT_SUFFIX of the interpreter is invalid
    """
    # the parts of EXT_SUFFIX that relate to the ABI, e.g.
    #   linux: '.cpython-310-x86_64-linux-gnu.so' -> cp310
    #   win:   '.cp310-win_amd64.pyd'             -> cp310
    ext_suffix = _get_config_var('EXT_SUFFIX', warn=True)
    if not isinstance(ext_suffix, str) or not ext_suffix.startswith('.'):
        raise SystemError("invalid sysconfig.get_config_var('EXT_SUFFIX')")
    parts = ext_suffix.split('.')
    if len(parts) < 3:
        # CPython 3.7 and earlier use '.pyd' on windows
        return _cpython_abis(sys.version_info[:2])
    soabi = parts[1]
    if soabi.startswith('cpython'):
        # not windows
        cpython_parts = soabi.split('-')
        if len(cpython_parts) < 2 or not cpython_parts[1]:
            raise SystemError("invalid sysconfig.get_config_var('EXT_SUFFIX')")
        abi = 'cp' + cpython_parts[1]
    elif soabi.startswith('cp'):
        # windows
        abi = soabi.split('-')[0]
    elif soabi.startswith('pypy'):
        abi = '-'.join(soabi.split('-')[:2])
    elif soabi.startswith('graalpy'):
        abi = '-'.join(soabi.split('-')[:3])
    elif soabi:
        # pyston, ironpython, others
        abi = soabi
    else:
        return []
    return [_normalize_string(abi)]


def generic_tags(interpreter=None, abis=None, platforms=None, warn=False):
    """
    Yield the tags of an interpreter that needs no specialization.

    Ported from packaging/tags.py: the tags are
    `<interpreter>-<abi>-<platform>`, with 'none' added to the ABIs when the
    caller did not name it.

    Args:
        interpreter (str | None): Interpreter name. Defaults to None, the
            name and version of the running interpreter
        abis (Iterable[str] | None): Compatible ABIs. Defaults to None, the
            ABIs of the running interpreter
        platforms (Iterable[str] | None): Compatible platforms. Defaults to
            None, the platforms of the running system
        warn (bool): Log the unset sysconfig variables. Defaults to False

    Yields:
        Tag: The tags
    """
    if not interpreter:
        interp_name = interpreter_name()
        interp_version = interpreter_version(warn=warn)
        interpreter = f'{interp_name}{interp_version}'
    abis = _generic_abi() if abis is None else list(abis)
    platforms = list(platforms or platform_tags())
    if 'none' not in abis:
        abis.append('none')
    for abi in abis:
        for platform_ in platforms:
            yield Tag(interpreter, abi, platform_)


def _py_interpreter_range(py_version):
    """
    Yield the python version names of a version, descending.

    Ported from packaging/tags.py: the exact version, then the major only,
    then every older minor of the same major.

    Args:
        py_version (Sequence[int]): Python version, e.g. (3, 8)

    Yields:
        str: A version name, e.g. 'py38', 'py3', 'py37'
    """
    if len(py_version) > 1:
        yield f'py{_version_nodot(py_version[:2])}'
    yield f'py{py_version[0]}'
    if len(py_version) > 1:
        for minor in range(py_version[1] - 1, -1, -1):
            yield f'py{_version_nodot((py_version[0], minor))}'


def pure_python_tags(python_version=None):
    """
    Yield the pure python tags compatible with a version.

    Ported from packaging/tags.py: the tags use the 'none' ABI and the 'any'
    platform, so they do not depend on the running system.

    Args:
        python_version (Sequence[int] | None): Python version. Defaults to
            None, sys.version_info[:2]

    Yields:
        Tag: The tags, e.g. Tag('py3', 'none', 'any')

    Raises:
        ValueError: If python_version is an empty sequence
    """
    if python_version is None:
        python_version = sys.version_info[:2]
    elif not python_version:
        raise ValueError('python_version must contain at least one item')
    for version in _py_interpreter_range(python_version):
        yield Tag(version, 'none', 'any')


def compatible_tags(python_version=None, interpreter=None, platforms=None):
    """
    Yield the tags compatible with an interpreter of a python version.

    Ported from packaging/tags.py. The tags, in the order of preference:

    - py*-none-<platform>
    - <interpreter>-none-any, when an interpreter is given
    - py*-none-any

    Args:
        python_version (Sequence[int] | None): Python version. Defaults to
            None, sys.version_info[:2]
        interpreter (str | None): Interpreter name, e.g. 'cp38'. Defaults to
            None, no interpreter tag
        platforms (Iterable[str] | None): Compatible platforms. Defaults to
            None, the platforms of the running system

    Yields:
        Tag: The tags
    """
    if not python_version:
        python_version = sys.version_info[:2]
    platforms = list(platforms or platform_tags())
    for version in _py_interpreter_range(python_version):
        for platform_ in platforms:
            yield Tag(version, 'none', platform_)
    if interpreter:
        yield Tag(interpreter, 'none', 'any')
    for tag in pure_python_tags(python_version):
        yield tag


def _mac_arch(arch, is_32bit=_32_BIT_INTERPRETER):
    """
    The architecture a macOS wheel of a 32 bit interpreter names.

    Ported from packaging/tags.py.

    Args:
        arch (str): Architecture of the machine, e.g. 'x86_64'
        is_32bit (bool): Whether the interpreter is a 32 bit build

    Returns:
        str: The architecture of the tag
    """
    if not is_32bit:
        return arch

    if arch.startswith('ppc'):
        return 'ppc'

    return 'i386'


def _mac_binary_formats(version, cpu_arch):
    """
    The binary formats a macOS version supports for an architecture.

    Ported from packaging/tags.py.

    Args:
        version (tuple[int, int]): macOS version, e.g. (10, 9)
        cpu_arch (str): Architecture, e.g. 'x86_64'

    Returns:
        list[str]: The binary formats, most specific first
    """
    formats = [cpu_arch]
    if cpu_arch == 'x86_64':
        if version < (10, 4):
            return []
        formats.extend(['intel', 'fat64', 'fat32'])

    elif cpu_arch == 'i386':
        if version < (10, 4):
            return []
        formats.extend(['intel', 'fat32', 'fat'])

    elif cpu_arch == 'ppc64':
        # the 32 bit PPC of 10.2 and older is not considered
        if version > (10, 5) or version < (10, 4):
            return []
        formats.append('fat64')

    elif cpu_arch == 'ppc':
        if version > (10, 6):
            return []
        formats.extend(['fat32', 'fat'])

    if cpu_arch in {'arm64', 'x86_64'}:
        formats.append('universal2')

    if cpu_arch in {'x86_64', 'i386', 'ppc64', 'ppc', 'intel'}:
        formats.append('universal')

    return formats


def mac_platforms(version=None, arch=None):
    """
    Yield the macOS platform tags of a version and an architecture.

    Ported from packaging/tags.py: before macOS 11 the major version was
    always 10 and every release bumped the minor version; from 11 on every
    release bumps the major version. The universal2 tag of an x86_64 machine
    reaches back to 10.4.

    Args:
        version (tuple[int, int] | None): macOS version. Defaults to None,
            the version of the running system
        arch (str | None): Architecture. Defaults to None, the architecture
            of the running system

    Yields:
        str: A platform tag, e.g. 'macosx_10_9_x86_64'
    """
    import platform

    if version is None or arch is None:
        version_str, _, cpu_arch = platform.mac_ver()
        if version is None:
            version = tuple(map(int, version_str.split('.')[:2]))
            if version == (10, 16):
                # when built against an older macOS SDK, python reports 10.16
                # instead of the real version
                version = _macos_version_without_compatibility()
        if arch is None:
            arch = _mac_arch(cpu_arch)

    if (10, 0) <= version < (11, 0):
        # before macOS 11, each yearly release bumped the minor version and
        # the major version was always 10
        major_version = 10
        for minor_version in range(version[1], -1, -1):
            compat_version = major_version, minor_version
            binary_formats = _mac_binary_formats(compat_version, arch)
            for binary_format in binary_formats:
                yield f'macosx_{major_version}_{minor_version}_{binary_format}'

    if version >= (11, 0):
        # from macOS 11 on, each yearly release bumps the major version and
        # the minor versions are the midyear updates
        minor_version = 0
        for major_version in range(version[0], 10, -1):
            compat_version = major_version, minor_version
            binary_formats = _mac_binary_formats(compat_version, arch)
            for binary_format in binary_formats:
                yield f'macosx_{major_version}_{minor_version}_{binary_format}'

        # macOS 11 on x86_64 runs the binaries of the previous releases, and
        # the x86_64 half of a universal2 binary may support 10.x
        major_version = 10
        if arch == 'x86_64':
            for minor_version in range(16, 3, -1):
                compat_version = major_version, minor_version
                binary_formats = _mac_binary_formats(compat_version, arch)
                for binary_format in binary_formats:
                    yield f'macosx_{major_version}_{minor_version}_{binary_format}'
        else:
            for minor_version in range(16, 3, -1):
                yield f'macosx_{major_version}_{minor_version}_universal2'


def _macos_version_without_compatibility():
    """
    The real macOS version of a python built against an older SDK.

    Ported from packaging/tags.py: the interpreter is asked with
    SYSTEM_VERSION_COMPAT=0, which makes the system report the real version
    instead of 10.16.

    Returns:
        tuple[int, int]: The version
    """
    import subprocess

    version_str = subprocess.run(
        [sys.executable, '-sS', '-c', 'import platform; print(platform.mac_ver()[0])'],
        check=True,
        env={'SYSTEM_VERSION_COMPAT': '0'},
        stdout=subprocess.PIPE,
        text=True,
    ).stdout
    return tuple(map(int, version_str.split('.')[:2]))


def _linux_platforms(is_32bit=_32_BIT_INTERPRETER):
    """
    Yield the linux platform tags of the running system.

    Ported from packaging/tags.py (the pip vendored copy): the manylinux
    families, the musllinux families and the plain `linux_<arch>`.

    Args:
        is_32bit (bool): Whether the interpreter is a 32 bit build

    Yields:
        str: A platform tag, e.g. 'manylinux_2_17_x86_64'
    """
    linux = _normalize_string(sysconfig.get_platform())
    if not linux.startswith('linux_'):
        # this should never happen, the sysconfig platform is used as it is
        yield linux
        return
    if is_32bit:
        if linux == 'linux_x86_64':
            linux = 'linux_i686'
        elif linux == 'linux_aarch64':
            linux = 'linux_armv8l'
    _, arch = linux.split('_', 1)
    archs = {'armv8l': ['armv8l', 'armv7l']}.get(arch, [arch])
    for tag in _manylinux.platform_tags(archs):
        yield tag
    for tag in _musllinux.platform_tags(archs):
        yield tag
    for arch in archs:
        yield f'linux_{arch}'


def _generic_platforms():
    """
    Yield the platform tag of the running system, as sysconfig names it.

    Ported from packaging/tags.py.

    Yields:
        str: The platform tag
    """
    yield _normalize_string(sysconfig.get_platform())


def platform_tags():
    """
    Yield the platform tags of the running interpreter.

    Ported from packaging/tags.py: macOS, Linux and the generic sysconfig
    platform are supported; the iOS and Android families of the source are
    not ported, this project runs on the desktop.

    Returns:
        Iterator[str]: The platform tags, most preferred first
    """
    import platform

    if platform.system() == 'Darwin':
        return mac_platforms()
    elif platform.system() == 'Linux':
        return _linux_platforms()
    else:
        return _generic_platforms()


def interpreter_name():
    """
    The short name of the running interpreter.

    Ported from packaging/tags.py: the well known implementations have a
    reserved two letter abbreviation.

    Returns:
        str: The name, e.g. 'cp'
    """
    name = sys.implementation.name
    return INTERPRETER_SHORT_NAMES.get(name) or name


def interpreter_version(warn=False):
    """
    The version of the running interpreter, as the tags spell it.

    Ported from packaging/tags.py.

    Args:
        warn (bool): Log the unset sysconfig variables. Defaults to False

    Returns:
        str: The version, e.g. '38'
    """
    version = _get_config_var('py_version_nodot', warn=warn)
    return str(version) if version else _version_nodot(sys.version_info[:2])


def _version_nodot(version):
    """
    Join a python version without the dots, e.g. (3, 8) -> '38'.

    Ported from packaging/tags.py.

    Args:
        version (Sequence[int]): Python version

    Returns:
        str: The version without the dots
    """
    return ''.join(map(str, version))


def sys_tags(warn=False):
    """
    Yield the tags the running interpreter supports, most preferred first.

    Ported from packaging/tags.py: the tags of the interpreter itself, then
    the compatible tags of the interpreter and of the pure python wheels.

    Args:
        warn (bool): Log the unset sysconfig variables. Defaults to False

    Yields:
        Tag: The supported tags, most preferred first
    """
    interp_name = interpreter_name()
    if interp_name == 'cp':
        for tag in cpython_tags(warn=warn):
            yield tag
    else:
        for tag in generic_tags(warn=warn):
            yield tag

    if interp_name == 'pp':
        interp = 'pp3'
    elif interp_name == 'cp':
        interp = 'cp' + interpreter_version(warn=warn)
    else:
        interp = None
    for tag in compatible_tags(interpreter=interp):
        yield tag
