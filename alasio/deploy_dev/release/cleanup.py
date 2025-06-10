import os

from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_bytes, file_remove, folder_rmtree
from alasio.ext.path.iter import iter_folders


def rmtree(path):
    """
    Remove a directory
    """
    if folder_rmtree(path):
        print(f'rmtree {path}')


def remove(path):
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
    remove(root / 'idlelib/ChangeLog')
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
    rmtree(root / 'async_generator/_tests')
    rmtree(root / 'colorama/tests')
    rmtree(root / 'commonmark/tests')
    rmtree(root / 'Crypto/SelfTest')
    rmtree(root / 'future/tests')
    rmtree(root / 'gevent/tests')
    rmtree(root / 'greenlet/tests')
    rmtree(root / 'h11/tests')
    remove(root / 'humanfriendly/tests.py')
    rmtree(root / 'isapi/doc')
    rmtree(root / 'isapi/samples')
    rmtree(root / 'isapi/test')
    rmtree(root / 'matplotlib/tests')
    rmtree(root / 'mpl_toolkits/tests')
    rmtree(root / 'mpmath/tests')
    rmtree(root / 'numpy/tests')
    rmtree(root / 'numpy/testing/tests')
    rmtree(root / 'psutil/tests')
    rmtree(root / 'pyreadline3/test')
    rmtree(root / 'retry/tests')
    rmtree(root / 'shapely/tests')
    rmtree(root / 'setuptools/tests')
    rmtree(root / 'setuptools/_distutils/tests')
    rmtree(root / 'sniffio/_tests')
    rmtree(root / 'sqlite_bro/tests')
    rmtree(root / 'tornado/test')
    remove(root / 'ua_parser/user_agent_parser_test.py')
    remove(root / 'user_agents/tests.py')
    rmtree(root / 'wcwidth/tests')
    rmtree(root / 'winpython/_vendor/qtpy/tests')
    rmtree(root / 'zmq/tests')
    remove(root / 'zope/event/tests.py')
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
        rmtree(path / 'tests')
    remove(root / 'numpy/_pyinstaller/test_pyinstaller.py')
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
    remove(root / 'mxnet/tools/bandwidth/.gitignore')
    remove(root / 'mxnet/tools/bandwidth/test_measure.py')
    remove(root / 'mxnet/tools/caffe_converter/.gitignore')
    remove(root / 'mxnet/tools/caffe_converter/test_converter.py')


KEEP_EXT = {'py', 'pyi', 'pyd', 'dll', 'so'}


def cleanup_license(root: PathStr):
    print(f'Cleanup license: {root}')
    for file in root.iter_files(recursive=True):
        _, _, name = file.rpartition(os.sep)
        _, _, ext = file.rpartition('.')
        if ext in KEEP_EXT:
            continue

        name = name.lower()
        if name.startswith('license') or name.startswith('licence'):
            remove(file)
        if name.startswith('authors'):
            remove(file)
        if name.startswith('copying'):
            remove(file)
        if name.startswith('notice'):
            remove(file)
        if name.startswith('readme'):
            remove(file)
        if name.startswith('description'):
            remove(file)

    rmtree(root / 'scipy/linalg/src/id_dist/doc')
    remove(root / 'scipy/HACKING.rst.txt')
    remove(root / 'scipy/INSTALL.rst.txt')
    remove(root / 'scipy/THANKS.txt')
    rmtree(root / 'scipy/linalg/src/id_dist/doc')


def find_test_file(root: PathStr):
    for file in root.iter_files(ext='.py', recursive=True):
        path = file.subpath_to(root).replace('\\', '/')
        # testing folders are for dep users to write tests
        if 'testing' in path:
            continue
        content = atomic_read_bytes(file)
        # must startswith \n to ignore `try: import`
        if b'\nimport unittest' in content:
            print(f'import unittest: {path}')
            continue
        if b'\nfrom unittest' in content:
            print(f'from unittest: {path}')
            continue
        if b'\nimport pytest' in content:
            print(f'import pytest: {path}')
            continue
        if b'\nfrom pytest' in content:
            print(f'from pytest: {path}')
            continue


def cleanup_electron(root: PathStr):
    """
    Cleanup electron build
    """
    remove(root / 'LICENSE.electron.txt')
    remove(root / 'LICENSES.chromium.html')
    # keep en-US only
    for file in root.joinpath('locales').iter_files(ext='.pak'):
        if file.endswith('en-US.pak'):
            continue
        else:
            remove(file)


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


if __name__ == '__main__':
    cleanup(r'D:\AlasRelease\AzurLaneAutoScript')
    # cleanup(r'D:\AlasRelease\StarRailCopilot')
