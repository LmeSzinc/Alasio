"""
Bytecode cache layout of python, PEP 3147.

The bytecode of a source file is cached in the __pycache__ folder of the
folder holding the source, named after the source, the cache tag of the
interpreter and the manual optimization:

    demo/core.py -> demo/__pycache__/core.cpython-38.pyc
    demo/core.py -> demo/__pycache__/core.cpython-38.opt-1.pyc

The name is resolved here instead of with importlib.util.cache_from_source(),
which answers the cache of the running interpreter rather than the cache of
the installation: see pyc_path().
"""
import sys

from alasio.ext.path.calc import to_posix

# Folder holding the bytecode compiled from the sources of the folder above it
PYCACHE = '__pycache__'
# Suffix of the bytecode files
PYC_SUFFIX = '.pyc'


def pyc_path(py_file, optimization=None):
    """
    Path of the bytecode file python caches for a source file, PEP 3147.

    The name is built from the source instead of with
    importlib.util.cache_from_source(): that function follows
    sys.pycache_prefix, so an interpreter with a bytecode cache prefix,
    e.g. Apple's python which points it to ~/Library/Caches/com.apple.python,
    answers a path outside the source tree. The callers need the in-tree
    layout of an installation: a RECORD must not hold paths outside of
    site-packages, and the cleanup only manages the pyc files of the
    __pycache__ folders of the installation.

    Args:
        py_file (str): Path of the .py source file, e.g. "demo/core.py"
        optimization (int | str): Optimization level of the manual
            optimization names, e.g. 1 gives "core.cpython-38.opt-1.pyc".
            Defaults to None, use the level of the running interpreter
            (sys.flags.optimize), like cache_from_source()

    Returns:
        str: Posix path of the .pyc file, e.g. "demo/__pycache__/core.cpython-38.pyc"

    Raises:
        ValueError: If the path is not a python source file, e.g. a
            "demo/core.txt", or holds no module name, e.g. a "demo/.py"
    """
    path = to_posix(str(py_file))
    folder, sep, name = path.rpartition('/')
    stem, _, suffix = name.rpartition('.')
    if not stem or suffix.lower() != 'py':
        raise ValueError(f'Not a python source file: "{py_file}"')

    if optimization is None:
        optimization = sys.flags.optimize
    # 0 and "" are the level without any optimization
    optimize = f'.opt-{optimization}' if optimization not in ('', 0) else ''
    cache = f'{stem}.{sys.implementation.cache_tag}{optimize}{PYC_SUFFIX}'
    if sep:
        return f'{folder}/{PYCACHE}/{cache}'
    return f'{PYCACHE}/{cache}'
