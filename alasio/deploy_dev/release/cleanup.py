from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_bytes, file_remove, folder_rmtree
from alasio.ext.path.iter import iter_folders


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


def cleanup_python_lib(root: PathStr):
    """
    Cleanup builtin lib of python

    Args:
        root: path to python/Lib
    """
    print(f'Cleanup python/Lib: {root}')
    rmtree(root / 'ctypes/test')
    rmtree(root / 'distutils/test')
    rmtree(root / 'distutils/tests')
    rmtree(root / 'idlelib/idle_test')
    rm(root / 'idlelib/ChangeLog')
    rmtree(root / 'lib2to3/tests')
    rmtree(root / 'sqlite3/test')
    rmtree(root / 'tkinter/test')
    rmtree(root / 'unittest/test')


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
    rmtree(root / 'colorama/tests')
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
    rmtree(root / 'pyreadline3/test')
    rmtree(root / 'retry/tests')
    rmtree(root / 'shapely/tests')
    rmtree(root / 'setuptools/tests')
    rmtree(root / 'setuptools/_distutils/tests')
    rmtree(root / 'sniffio/_tests')
    rmtree(root / 'sqlite_bro/tests')
    rmtree(root / 'tornado/test')
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
        # `numpy/testing` is kept as a whole, including its own tests
        if path.endswith('testing'):
            continue
        rmtree(path / 'tests')
    # Do not remove numpy/testing, `numpy/__init__.py` does `from .testing import Tester`,
    # removing it breaks `import numpy` and everything importing numpy (e.g. cv2).
    rm(root / 'numpy/_pyinstaller/test_pyinstaller.py')
    for path in root.joinpath('scipy').iter_folders(recursive=True):
        rmtree(path / 'tests')
    for path in root.joinpath('sympy').iter_folders(recursive=True):
        rmtree(path / 'tests')
    rmtree(root / 'sympy/parsing/autolev/test-examples')

    # demo images in imageio, so you can access with
    # import imageio.v3 as iio
    # im = iio.imread('imageio:chelsea.png')
    # print(im.shape)  # (300, 451, 3)
    # I don't think you need these in production
    rmtree(root / 'imageio/resources')

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


def cleanup_license(root: PathStr):
    print(f'Cleanup license: {root}')
    license_file = (
        'license',
        'licence',
        'license-',
        'license_',
        'authors',
        'copying',
        'notice',
        'readme',
        'description',
    )
    DOC_SUFFIX = {
        '.md', '.rst', '.txt', '.psf', '.html', '.htm',
        '.mit', '.bsd', '.apache', '.apache2', '.lesser'
    }
    for file in root.iter_files(recursive=True):
        name = file.name.lower()
        ext = file.suffix.lower()
        if ext in KEEP_EXT:
            continue

        if name.startswith(license_file):
            if not ext or ext in DOC_SUFFIX:
                rm(file)

    rmtree(root / 'scipy/linalg/src/id_dist/doc')
    rm(root / 'scipy/HACKING.rst.txt')
    rm(root / 'scipy/INSTALL.rst.txt')
    rm(root / 'scipy/THANKS.txt')
    rmtree(root / 'scipy/linalg/src/id_dist/doc')


def find_test_file(root: PathStr):
    for file in root.iter_files(ext='.py', recursive=True):
        path = file.subpath_to(root).replace('\\', '/')
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
    cleanup_python_lib(root / 'toolkit/Lib')
    cleanup_python_packages(root / 'toolkit/Lib/site-packages')
    cleanup_license(root / 'toolkit/Lib/site-packages')
    # find_test_file(root / 'toolkit/Lib/site-packages')

    # electron
    cleanup_electron(root)
    cleanup_electron(root / 'toolkit/WebApp')

    # verify
    check_runtime_required(root)


if __name__ == '__main__':
    cleanup(r'D:\AlasRelease\AzurLaneAutoScript')
    # cleanup(r'D:\AlasRelease\StarRailCopilot')
