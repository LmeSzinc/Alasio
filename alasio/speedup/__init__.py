"""
Loader of the optional alasio_speedup accelerators.

The accelerators are a distribution of their own and are loaded from the
site-packages of the interpreter, never from the checkout: running from a
repository puts the repository root on sys.path, where its own
alasio_speedup directory would shadow the installed one and silently run a
different encoder than the deployed server does.

Every accelerator is loaded and checked on its own, see
alasio_speedup.ACCELERATORS: one that is not installed, cannot build or
cannot load its library stays None, the caller falls back to its pure
Python implementation, and the other accelerators keep working.

Usage:
    from alasio.speedup import accelerator

    bit2 = accelerator('bit2')
    if bit2 is None:
        # not available, the Python implementation is in use
"""
import importlib
import importlib.util
import os
import sys

# name of the accelerator package
SPEEDUP = 'alasio_speedup'

# why the package could not be loaded, None while it was not even tried
PACKAGE_ERROR = None

# why an accelerator is unavailable, name -> message
ERRORS = {}


def site_packages_dirs():
    """
    Returns:
        list[str]: Site-packages directories of the interpreter, most
            specific first, duplicates removed
    """
    dirs = []
    try:
        import site
    except ImportError:  # pragma: no cover - site is always importable
        pass
    else:
        try:
            dirs.extend(site.getsitepackages())
        except (AttributeError, OSError):
            pass
        try:
            dirs.append(site.getusersitepackages())
        except (AttributeError, OSError):
            pass
    # site-packages entries of sys.path cover the layouts that
    # site.getsitepackages() does not know about
    dirs.extend(
        entry for entry in sys.path
        if os.path.basename(entry) in ('site-packages', 'dist-packages')
    )

    seen = set()
    result = []
    for path in dirs:
        if not path:
            continue
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        # site.getusersitepackages() reports a directory that may not exist
        if os.path.isdir(path):
            result.append(path)
    return result


def find_installed():
    """
    Returns:
        str: Path of the installed alasio_speedup package, None when it is
            not installed
    """
    for base in site_packages_dirs():
        path = os.path.join(base, SPEEDUP)
        if os.path.isfile(os.path.join(path, '__init__.py')):
            return path
    return None


def is_installed(module):
    """
    Args:
        module: Module to check

    Returns:
        bool: True when the module was loaded from a site-packages directory
    """
    file = getattr(module, '__file__', None)
    if not file:
        return False
    file = os.path.normcase(os.path.abspath(file))
    for base in site_packages_dirs():
        if file.startswith(os.path.normcase(os.path.abspath(base)) + os.sep):
            return True
    return False


def import_from(path):
    """
    Import the package at path as alasio_speedup. A module of that name
    loaded from somewhere else (the checkout) is dropped first, its
    submodules would otherwise be mixed into the loaded package.

    Args:
        path (str): Path of the package directory

    Returns:
        module: The loaded package
    """
    for name in [name for name in sys.modules if name == SPEEDUP or name.startswith(SPEEDUP + '.')]:
        del sys.modules[name]

    spec = importlib.util.spec_from_file_location(
        SPEEDUP, os.path.join(path, '__init__.py'), submodule_search_locations=[path],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[SPEEDUP] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(SPEEDUP, None)
        raise
    return module


def package():
    """
    The installed alasio_speedup package, imported on first use.

    Returns:
        module | None: The package, None when it is not installed or cannot
            be imported, the reason is in PACKAGE_ERROR
    """
    global PACKAGE_ERROR
    module = sys.modules.get(SPEEDUP)
    if module is not None and is_installed(module):
        return module

    path = find_installed()
    if path is None:
        return None
    try:
        return import_from(path)
    except Exception as e:
        PACKAGE_ERROR = f'{type(e).__name__}: {e}'
        return None


def load(name):
    """
    Load the installed accelerator of that name and check that its library
    works.

    Args:
        name (str): Name of the accelerator, one of alasio_speedup.ACCELERATORS

    Returns:
        module | None: The loaded accelerator module, None when it is not
            installed or not usable, the reason is in ERRORS[name]
    """
    if package() is None:
        ERRORS[name] = PACKAGE_ERROR or f'{SPEEDUP} is not installed'
        return None
    try:
        module = importlib.import_module(f'{SPEEDUP}.{name}')
        module.check()
    except Exception as e:
        ERRORS[name] = f'{type(e).__name__}: {e}'
        return None
    return module


_loaded = {}


def accelerator(name):
    """
    Load and check one accelerator of the installed alasio_speedup, once.

    Args:
        name (str): Name of the accelerator, one of alasio_speedup.ACCELERATORS

    Returns:
        module | None: The accelerator module, None when it is not
            installed or not usable, the reason is in ERRORS[name]
    """
    if name not in _loaded:
        _loaded[name] = load(name)
    return _loaded[name]


def accelerator_names():
    """
    Returns:
        tuple[str, ...]: Names of the accelerators of the installed package,
            empty when the package is not installed
    """
    module = package()
    if module is None:
        return ()
    return tuple(getattr(module, 'ACCELERATORS', ()))
