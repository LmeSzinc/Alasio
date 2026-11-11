from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_bytes, file_remove, folder_rmtree, folder_rmtree_empty
from alasio.ext.path.iter import iter_files, iter_folders


def rmtree(path):
    """
    Remove a directory
    """
    if folder_rmtree(path):
        print(f'rmtree {path}')


def rm(path):
    """
    Remove a file
    """
    if file_remove(path):
        print(f'remove {path}')


def cleanup_pycache(root):
    """
    Remove all __pycache__ directory
    """
    print(f'Cleanup pycache: {root}')
    for path in iter_folders(root, recursive=True):
        if path.endswith('__pycache__'):
            rmtree(path)


def cleanup_pdb(root: PathStr):
    """
    Remove program database files of the bundled python and its DLLs

    Debug symbols are only read by a debugger, the interpreter never loads them.

    Args:
        root: path to the release folder
    """
    print(f'Cleanup pdb: {root}')
    for file in iter_files(root, ext='.pdb', recursive=True):
        rm(file)


def cleanup_python_lib(root: PathStr):
    """
    Cleanup builtin lib of python

    Args:
        root: path to python/Lib
    """
    print(f'Cleanup python/Lib: {root}')
    # Not imported at runtime: the bundled pip wheels of ensurepip, the venv
    # bootstrap that is built on ensurepip, and the turtle demo application
    rmtree(root / 'ensurepip')
    rmtree(root / 'turtledemo')
    rmtree(root / 'venv')
    rmtree(root / 'ctypes/test')
    rmtree(root / 'distutils/test')
    rmtree(root / 'distutils/tests')
    rmtree(root / 'idlelib/idle_test')
    # Documentation of the IDLE editor, it is only shown by the About dialog
    # and the help window, which are never opened outside of IDLE
    for name in (
        'ChangeLog',
        'CREDITS.txt',
        'HISTORY.txt',
        'NEWS.txt',
        'NEWS2x.txt',
        'README.txt',
        'TODO.txt',
        'extend.txt',
        'help.html',
        'Icons/README.txt',
    ):
        rm(root / f'idlelib/{name}')
    rmtree(root / 'lib2to3/tests')
    rmtree(root / 'sqlite3/test')
    rmtree(root / 'tkinter/test')
    rmtree(root / 'unittest/test')


def cleanup_tcl(root: PathStr):
    """
    Cleanup the tcl runtime folder of the bundled python

    Args:
        root: path to python/tcl
    """
    print(f'Cleanup python/tcl: {root}')
    # Demo applications of tk and tix, they are standalone scripts that nothing
    # in the runtime sources or loads
    rmtree(root / 'tk8.6/demos')
    rmtree(root / 'tix8.4.3/demos')
    # License terms and the readme of the demo images, they are only referenced
    # by the "See the file license.terms" comments of the tcl scripts
    rm(root / 'tk8.6/license.terms')
    # The tk logo images are only used by the demos, every image of the runtime
    # widgets is created from inline -data of icons.tcl / msgbox.tcl / tkfbox.tcl,
    # the bitmaps of tix are in tix8.4.3/bitmaps instead of here
    rmtree(root / 'tk8.6/images')


def cleanup_python_packages(root: PathStr):
    """
    Cleanup python site-packages

    Args:
        root: Path to python/Lib/site-packages
    """
    print(f'Cleanup python/Lib/site-packages: {root}')
    # Keep folders and modules named `testing`, many libraries are non-standard:
    # `numpy/testing` is imported by `numpy/__init__.py`, `anyio/_core/_testing.py` is
    # imported by anyio 3.x, `pygments/lexers/testing.py` is a gherkin/TAP lexer, etc.
    # Only folders and modules named `test` / `tests` are removed, no one imports them.
    rmtree(root / 'async_generator/_tests')
    rmtree(root / 'certifi/tests')
    rmtree(root / 'colorama/tests')
    rm(root / 'coloredlogs/tests.py')
    rmtree(root / 'commonmark/tests')
    rmtree(root / 'Crypto/SelfTest')
    rmtree(root / 'future/tests')
    rmtree(root / 'gevent/tests')
    rm(root / 'google/protobuf/internal/_parameterized.py')
    rmtree(root / 'greenlet/tests')
    rmtree(root / 'h11/tests')
    rm(root / 'humanfriendly/tests.py')
    rmtree(root / 'isapi/doc')
    rmtree(root / 'isapi/samples')
    rmtree(root / 'isapi/test')
    rmtree(root / 'matplotlib/tests')
    rmtree(root / 'mpl_toolkits/tests')
    rmtree(root / 'mpmath/tests')
    rmtree(root / 'numpy/tests')
    rmtree(root / 'psutil/tests')
    rm(root / 'py/test.py')
    rmtree(root / 'pyreadline3/test')
    rmtree(root / 'retry/tests')
    rmtree(root / 'shapely/tests')
    rmtree(root / 'setuptools/tests')
    rmtree(root / 'setuptools/_distutils/tests')
    rmtree(root / 'sniffio/_tests')
    rmtree(root / 'sqlite_bro/tests')
    rmtree(root / 'tornado/test')
    rmtree(root / 'trio/_core/_tests')
    rmtree(root / 'trio/_tests')
    rm(root / 'ua_parser/user_agent_parser_test.py')
    rm(root / 'user_agents/tests.py')
    rmtree(root / 'wcwidth/tests')
    rmtree(root / 'winpython/_vendor/qtpy/tests')
    rmtree(root / 'zmq/tests')
    rm(root / 'zope/event/tests.py')
    rmtree(root / 'zope/interface/tests')
    rmtree(root / 'zope/interface/common/tests')

    # pip/_vender
    rmtree(root / 'pip/_vendor/colorama/tests')

    # pywin32 tests, demos, docs
    rmtree(root / 'adodbapi/examples')
    rmtree(root / 'adodbapi/test')
    rmtree(root / 'pythonwin/pywin/Demos')
    rmtree(root / 'win32/test')
    rmtree(root / 'win32/Demos')
    rmtree(root / 'win32com/demos')
    rmtree(root / 'win32com/HTML')
    rmtree(root / 'win32com/test')
    # Too many arbitrary folder names, check
    # https://github.com/mhammond/pywin32/tree/main/com/win32comext
    rmtree(root / 'win32comext/adsi/demos')
    rmtree(root / 'win32comext/authorization/demos')
    rmtree(root / 'win32comext/axcontrol/demos')
    rmtree(root / 'win32comext/axdebug/Test')
    rmtree(root / 'win32comext/axscript/Demos')
    rmtree(root / 'win32comext/axscript/demos')
    rmtree(root / 'win32comext/axscript/test')
    rmtree(root / 'win32comext/bits/test')
    rmtree(root / 'win32comext/directsound/test')
    rmtree(root / 'win32comext/ifilter/demo')
    rmtree(root / 'win32comext/ifilter/test')
    rmtree(root / 'win32comext/mapi/demos')
    rmtree(root / 'win32comext/propsys/test')
    rmtree(root / 'win32comext/shell/demos')
    rmtree(root / 'win32comext/shell/test')
    rmtree(root / 'win32comext/taskscheduler/test')

    # scipy tests in submodules
    for path in root.joinpath('numpy').iter_folders(recursive=False):
        # `numpy/testing` itself is kept, `numpy/__init__.py` imports it,
        # but its own tests are never imported
        rmtree(path / 'tests')
    # Do not remove numpy/testing, `numpy/__init__.py` does `from .testing import Tester`,
    # removing it breaks `import numpy` and everything importing numpy (e.g. cv2).
    rm(root / 'numpy/_pyinstaller/test_pyinstaller.py')
    rm(root / 'numpy/conftest.py')
    # numpy ships its documentation as python modules
    rmtree(root / 'numpy/doc')
    for path in root.joinpath('scipy').iter_folders(recursive=True):
        rmtree(path / 'tests')
    rm(root / 'scipy/conftest.py')
    for path in root.joinpath('sympy').iter_folders(recursive=True):
        rmtree(path / 'tests')
        rmtree(path / 'benchmarks')
    rmtree(root / 'sympy/benchmarks')
    rmtree(root / 'sympy/parsing/autolev/test-examples')
    rm(root / 'sympy/conftest.py')

    # demo images in imageio, so you can access with
    # import imageio.v3 as iio
    # im = iio.imread('imageio:chelsea.png')
    # print(im.shape)  # (300, 451, 3)
    # I don't think you need these in production
    rmtree(root / 'imageio/resources')
    # The demo image of `scipy.misc.face()`, which is deprecated by scipy itself
    rm(root / 'scipy/misc/face.dat')

    # mxnet tools
    rm(root / 'mxnet/tools/bandwidth/.gitignore')
    rm(root / 'mxnet/tools/bandwidth/test_measure.py')
    rm(root / 'mxnet/tools/caffe_converter/.gitignore')
    rm(root / 'mxnet/tools/caffe_converter/test_converter.py')

    # opencv face-detection features
    # which means you can't use `cv2.CascadeClassifier`, `detectMultiScale()`
    # Keep `cv2/data/__init__.py`, `cv2/__init__.py` does `from .data import *`,
    # remove the haarcascade xml files (9.3 MB) only
    for file in (root / 'cv2/data').iter_files(recursive=True):
        if file.name == '__init__.py':
            continue
        rm(file)

    # Cython sources are used to build the packages, never imported at runtime
    for file in root.iter_files(recursive=True):
        if file.endswith(('.pyx', '.pxd', '.pxi')):
            rm(file)


# Files that are imported at runtime, do not remove them in the cleanup above.
# (installed package, files that the package requires when it is installed)
RUNTIME_REQUIRED = (
    # `numpy/__init__.py` does `from .testing import Tester`
    ('numpy', (
        'numpy/testing/__init__.py',
        'numpy/testing/_private/utils.py',
    )),
    # `cv2/__init__.py` does `from .data import *`
    ('cv2', (
        'cv2/data/__init__.py',
    )),
    # `pygments/lexers/_mapping.py` registers the gherkin/TAP lexer
    ('pygments', (
        'pygments/lexers/testing.py',
    )),
    # Testing helpers are kept, some libraries import them in runtime code
    ('matplotlib', ('matplotlib/testing/__init__.py',)),
    ('gevent', ('gevent/testing/__init__.py',)),
    ('click', ('click/testing.py',)),
    ('tornado', ('tornado/testing.py',)),
    ('imageio', ('imageio/testing.py',)),
    ('asgiref', ('asgiref/testing.py',)),
    ('pyparsing', ('pyparsing/testing.py',)),
    ('humanfriendly', ('humanfriendly/testing.py',)),
    ('shapely', ('shapely/testing.py',)),
)


def check_runtime_required(root: PathStr):
    """
    Check files that are imported at runtime, but are easy to remove by mistake

    Args:
        root: path to the release folder
    """
    print(f'Check runtime required files: {root}')
    site_packages = root / 'toolkit/Lib/site-packages'
    missing = []
    for package, files in RUNTIME_REQUIRED:
        if not (site_packages / package).exists():
            # Package is not installed in this release
            continue
        for file in files:
            if not (site_packages / file).exists():
                missing.append(file)
    if missing:
        print('=' * 72)
        print('MISSING files that are imported at runtime, the release may fail:')
        for file in missing:
            print(f'MISSING {site_packages / file}')
        print('=' * 72)


KEEP_EXT = {'.py', '.pyi', '.pyd', '.dll', '.so'}

# Files with these name prefixes are licenses, readmes or changelogs,
# none of them is imported at runtime
DOC_NAME_PREFIX = (
    'license',
    'licence',
    'license-',
    'license_',
    'authors',
    'changelog',
    'copying',
    'credits',
    'description',
    'notice',
    'readme',
    'thirdparty',
)


def cleanup_doc_files(root: PathStr):
    """
    Remove the documentation files of a folder tree

    Args:
        root: path to the folder tree to clean
    """
    for file in root.iter_files(recursive=True):
        # Keep the files that may be imported
        if file.suffix.lower() in KEEP_EXT:
            continue
        if file.name.lower().startswith(DOC_NAME_PREFIX):
            rm(file)


def cleanup_license(root: PathStr):
    print(f'Cleanup license: {root}')
    cleanup_doc_files(root)

    rmtree(root / 'scipy/linalg/src/id_dist/doc')
    rm(root / 'scipy/HACKING.rst.txt')
    rm(root / 'scipy/INSTALL.rst.txt')
    rm(root / 'scipy/THANKS.txt')
    rmtree(root / 'scipy/linalg/src/id_dist/doc')


def cleanup_empty_folders(root: PathStr):
    """
    Remove folders that were emptied after their files were removed

    Args:
        root: path to the folder tree to clean
    """
    print(f'Cleanup empty folders: {root}')
    # iter_folders() yields a folder before its subfolders, iterate in reverse
    # so a subfolder is removed before its parent is checked
    for path in reversed(list(iter_folders(root, recursive=True))):
        if folder_rmtree_empty(path):
            print(f'rmdir {path}')


def find_test_file(root: PathStr):
    for file in root.iter_files(ext='.py', recursive=True):
        path = file.subpath(root).replace('\\', '/')
        content = atomic_read_bytes(file)
        # must startswith \n to ignore `try: import`
        if b'\nimport unittest' in content:
            print(f'import unittest: {path}')
            print(f"rm(root / '{path}')")
            continue
        if b'\nfrom unittest' in content:
            print(f'from unittest: {path}')
            print(f"rm(root / '{path}')")
            continue
        if b'\nimport pytest' in content:
            print(f'import pytest: {path}')
            print(f"rm(root / '{path}')")
            continue
        if b'\nfrom pytest' in content:
            print(f'from pytest: {path}')
            print(f"rm(root / '{path}')")
            continue


def cleanup_electron(root: PathStr):
    """
    Cleanup electron build
    """
    rm(root / 'LICENSE.electron.txt')
    rm(root / 'LICENSES.chromium.html')
    # keep en-US only
    for file in root.joinpath('locales').iter_files(ext='.pak'):
        if file.endswith('en-US.pak'):
            continue
        else:
            rm(file)


def cleanup(root: str):
    root = PathStr.new(root)

    # python
    cleanup_pycache(root)
    cleanup_pdb(root)
    cleanup_python_lib(root / 'toolkit/Lib')
    cleanup_tcl(root / 'toolkit/tcl')
    site_packages = root / 'toolkit/Lib/site-packages'
    cleanup_python_packages(site_packages)
    cleanup_license(site_packages)
    # The standard library ships documentation too, e.g. the CREDITS.txt and
    # the NEWS.txt of idlelib, the README of distutils
    cleanup_doc_files(root / 'toolkit/Lib')
    cleanup_empty_folders(site_packages)
    # find_test_file(site_packages)

    # electron
    cleanup_electron(root)
    cleanup_electron(root / 'toolkit/WebApp')

    # verify
    check_runtime_required(root)


if __name__ == '__main__':
    cleanup(r'D:\AlasRelease\AzurLaneAutoScript')
    # cleanup(r'D:\AlasRelease\StarRailCopilot')
