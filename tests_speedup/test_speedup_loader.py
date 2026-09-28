"""
Tests for ``alasio.speedup``, the loader of the optional accelerator.

The loader must use the installed copy of ``alasio_speedup`` only: the
directory of a checkout is the development source, not the accelerator of
the deployed server, and a machine without the install must keep running
on the pure Python encoder.
"""
import importlib
import os
import sys
import types

import pytest

from alasio import speedup
from alasio.ext.algorithm.bit2coding.bit2coding_decode import decode_bit2
from alasio.ext.algorithm.bit2coding.bit2coding_encode_c import encode_bit2
from alasio.ext.algorithm.bit2coding.bit2coding_encode_python import encode_bit2 as encode_bit2_python

speedup_module = pytest.importorskip('alasio_speedup')


def site_dir(*parts):
    """
    Args:
        *parts (str): Path parts below a fake site-packages

    Returns:
        str: The path
    """
    return os.path.join(os.sep + 'py', 'site-packages', *parts)


def fake_module(file):
    """
    Args:
        file (str): Value of the module __file__

    Returns:
        types.SimpleNamespace: Stand in for an imported module
    """
    return types.SimpleNamespace(__file__=file)


class TestSitePackagesDirs:
    """The directories the accelerator may live in."""

    def test_dirs_exist_and_are_unique(self):
        """Every directory exists and no directory is listed twice."""
        dirs = speedup.site_packages_dirs()
        assert dirs
        for path in dirs:
            assert os.path.isdir(path), f'{path} is not a directory'
        keys = [os.path.normcase(os.path.abspath(path)) for path in dirs]
        assert len(keys) == len(set(keys))

    def test_only_existing_directories(self, monkeypatch):
        """An entry of sys.path that does not exist is dropped."""
        missing = os.path.join(os.sep + 'nowhere', 'site-packages')
        monkeypatch.setattr(sys, 'path', [missing] + sys.path)
        assert missing not in speedup.site_packages_dirs()

    def test_sys_path_entries_are_included(self, monkeypatch):
        """A site-packages directory of sys.path is a candidate, whatever its layout."""
        in_path = [
            path for path in sys.path
            if os.path.basename(path) in ('site-packages', 'dist-packages') and os.path.isdir(path)
        ]
        if not in_path:
            pytest.skip('sys.path holds no site-packages directory')
        dirs = speedup.site_packages_dirs()
        for path in in_path:
            assert path in dirs


class TestFindInstalled:
    """The accelerator is looked up in site-packages, nowhere else."""

    def test_found_path_is_a_package_of_a_site_packages(self):
        """A found path is a real package directory below a site-packages."""
        path = speedup.find_installed()
        if path is None:
            pytest.skip('alasio_speedup is not installed')
        assert os.path.basename(path) == speedup.SPEEDUP
        assert os.path.isfile(os.path.join(path, '__init__.py'))
        dirs = [os.path.normcase(os.path.abspath(d)) for d in speedup.site_packages_dirs()]
        parent = os.path.normcase(os.path.abspath(os.path.dirname(path)))
        assert parent in dirs

    def test_nothing_found_without_site_packages(self, monkeypatch):
        """Without a site-packages directory there is no accelerator."""
        monkeypatch.setattr(speedup, 'site_packages_dirs', lambda: [])
        assert speedup.find_installed() is None

    def test_directory_without_init_is_not_a_package(self, monkeypatch):
        """A directory of that name without __init__.py is not the package."""
        monkeypatch.setattr(speedup, 'site_packages_dirs', lambda: [os.sep + 'nowhere'])
        assert speedup.find_installed() is None


class TestIsInstalled:
    """The loader tells the installed copy from the checkout copy."""

    def test_module_of_a_site_packages(self, monkeypatch):
        """A module loaded from site-packages is the accelerator."""
        monkeypatch.setattr(speedup, 'site_packages_dirs', lambda: [site_dir()])
        module = fake_module(site_dir(speedup.SPEEDUP, '__init__.py'))
        assert speedup.is_installed(module) is True

    def test_module_of_a_checkout(self, monkeypatch):
        """A module loaded from a checkout is not the accelerator."""
        monkeypatch.setattr(speedup, 'site_packages_dirs', lambda: [site_dir()])
        module = fake_module(os.path.join(os.sep + 'repo', 'Alasio', speedup.SPEEDUP, '__init__.py'))
        assert speedup.is_installed(module) is False

    def test_module_without_a_file(self):
        """A module without __file__ is not the accelerator."""
        assert speedup.is_installed(object()) is False


class TestLoad:
    """The load decision of the accelerator."""

    def test_without_install(self, monkeypatch):
        """No install, no accelerator, the caller falls back to Python."""
        monkeypatch.delitem(sys.modules, speedup.SPEEDUP, raising=False)
        monkeypatch.setattr(speedup, 'find_installed', lambda: None)
        assert speedup.load('bit2') is None
        assert speedup.ERRORS['bit2']

    def test_module_of_the_checkout_is_not_enough(self, monkeypatch):
        """An imported checkout module does not satisfy the loader."""
        checkout = os.path.join(os.sep + 'repo', 'Alasio', speedup.SPEEDUP, '__init__.py')
        monkeypatch.setitem(sys.modules, speedup.SPEEDUP, fake_module(checkout))
        monkeypatch.setattr(speedup, 'site_packages_dirs', lambda: [site_dir()])
        monkeypatch.setattr(speedup, 'find_installed', lambda: None)
        assert speedup.load('bit2') is None

    def test_unusable_accelerator_falls_back(self, monkeypatch):
        """An accelerator that cannot build or load its library falls back to Python."""
        monkeypatch.delitem(sys.modules, speedup.SPEEDUP, raising=False)
        monkeypatch.delitem(sys.modules, f'{speedup.SPEEDUP}.bit2', raising=False)

        def check():
            raise RuntimeError('no C compiler')

        package = fake_module(site_dir(speedup.SPEEDUP, '__init__.py'))
        monkeypatch.setitem(sys.modules, f'{speedup.SPEEDUP}.bit2', types.SimpleNamespace(check=check))
        monkeypatch.setattr(speedup, 'find_installed', lambda: site_dir(speedup.SPEEDUP))
        monkeypatch.setattr(speedup, 'import_from', lambda path: package)
        assert speedup.load('bit2') is None
        assert 'no C compiler' in speedup.ERRORS['bit2']


class TestAccelerators:
    """The registry of the installed package."""

    def test_names_come_from_the_package(self):
        """The names are the ACCELERATORS of the installed package."""
        names = speedup.accelerator_names()
        if names:
            assert 'bit2' in names
        else:
            assert speedup.find_installed() is None or speedup.PACKAGE_ERROR

    def test_accelerator_is_loaded_once(self, monkeypatch):
        """Two calls of accelerator() load the module once."""
        calls = []

        def fake_load(name):
            calls.append(name)
            return None

        monkeypatch.setattr(speedup, 'load', fake_load)
        saved = dict(speedup._loaded)
        try:
            speedup._loaded.clear()
            assert speedup.accelerator('bit2') is None
            assert speedup.accelerator('bit2') is None
            assert calls == ['bit2']
        finally:
            speedup._loaded.clear()
            speedup._loaded.update(saved)


class TestImportFrom:
    """Importing the package of a path directly."""

    def test_import_from_path(self):
        """import_from() loads the package of the given path."""
        path = os.path.dirname(os.path.abspath(speedup_module.__file__))
        saved = sys.modules.get(speedup.SPEEDUP)
        saved_submodules = {
            key: value for key, value in sys.modules.items() if key.startswith(speedup.SPEEDUP + '.')
        }
        try:
            loaded = speedup.import_from(path)
            assert sys.modules[speedup.SPEEDUP] is loaded
            assert os.path.normcase(os.path.abspath(loaded.__file__)).startswith(
                os.path.normcase(os.path.abspath(path)))
            # the accelerators of the package are importable from the loaded one
            assert 'bit2' in loaded.ACCELERATORS
            bit2 = importlib.import_module(f'{speedup.SPEEDUP}.bit2')
            assert callable(bit2.encode_bit2_stream)
        finally:
            if saved is None:
                sys.modules.pop(speedup.SPEEDUP, None)
            else:
                sys.modules[speedup.SPEEDUP] = saved
            sys.modules.update(saved_submodules)

    def test_import_from_replaces_a_foreign_module(self):
        """A module of the same name from somewhere else is replaced."""
        path = os.path.dirname(os.path.abspath(speedup_module.__file__))
        saved = sys.modules.get(speedup.SPEEDUP)
        saved_submodules = {
            key: value for key, value in sys.modules.items() if key.startswith(speedup.SPEEDUP + '.')
        }
        try:
            foreign = fake_module(os.path.join(os.sep + 'repo', 'Alasio', speedup.SPEEDUP, '__init__.py'))
            sys.modules[speedup.SPEEDUP] = foreign
            loaded = speedup.import_from(path)
            assert loaded is not foreign
            assert sys.modules[speedup.SPEEDUP] is loaded
        finally:
            if saved is None:
                sys.modules.pop(speedup.SPEEDUP, None)
            else:
                sys.modules[speedup.SPEEDUP] = saved
            sys.modules.update(saved_submodules)


class TestEncoderSwitch:
    """bit2coding picks the encoder of the environment it runs in."""

    def test_encode_bit2_is_the_encoder_of_the_environment(self):
        """The encoder module falls back to Python exactly when the accelerator is missing."""
        from alasio.ext.algorithm.bit2coding import bit2coding_encode_c

        if speedup.accelerator('bit2') is not None:
            assert bit2coding_encode_c.encode_bit2 is not encode_bit2_python
        else:
            assert bit2coding_encode_c.encode_bit2 is encode_bit2_python

    def test_both_encoders_agree_on_the_values(self):
        """The Python encoder is always usable and both decode back."""
        data = [0, 1, 2, 3] * 8 + [2] * 40 + [0, 1, 0, 1]
        for encoder in (encode_bit2_python, encode_bit2):
            decoded, read = decode_bit2(encoder(data))
            assert decoded == data
            assert read == len(encoder(data))

    def test_c_encoder_is_not_larger(self):
        """The Python encoder is the reference, the C one must not be larger."""
        data = [(i * 5 + i // 7) % 4 for i in range(500)]
        if speedup.accelerator('bit2') is None:
            pytest.skip('the bit2 accelerator of alasio_speedup is not installed')
        assert len(encode_bit2(data)) <= len(encode_bit2_python(data))
