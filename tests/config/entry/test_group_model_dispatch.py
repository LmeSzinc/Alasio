"""
Tests of the model loader dispatch in ModBase.get_group_model().

A worker runs the game script, which imports the mod as a package
(``from module.config.main.main_model import Campaign``). The model of a config
group must be the class the script checks against, otherwise
``isinstance(self.config.Campaign, Campaign)`` fails on two different classes
built from the same file.

The backend serves every mod and only validates values, so it loads the model
file on its own and never writes it to sys.modules, which lets a whole
generation of models be dropped on a mod update.

The tests put the mod root on sys.path like the worker does, so the mod is
imported under the same name a deployed mod uses.
"""
import os
import sys
from types import SimpleNamespace

import pytest

from alasio.config.alasio import device_model
from alasio.config.alasio.group_proxy import GroupProxy
from alasio.config.base import AlasioConfigBase
from alasio.config.entry import mod_base
from alasio.config.entry.mod_base import ModBase
from alasio.config.entry.mod_entry import ModEntry, ModEntryModel
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.logger import logger

MODEL_FILE = 'main/main_model.py'
MODULE_NAME = 'module.config.main.main_model'
MOD_ROOT = os.path.abspath('ExampleMod')


def make_mod():
    """
    Build a ModBase of ExampleMod.

    Returns:
        ModBase:
    """
    return ModBase(ModEntry(root=PathStr.new('ExampleMod'), data=ModEntryModel.build(name='example_mod')))


@pytest.fixture
def worker_package(monkeypatch):
    """
    Import ExampleMod the way a worker does: mod root first on sys.path.

    Returns:
        module: The main model of the mod, imported as a package
    """
    import importlib

    monkeypatch.syspath_prepend(MOD_ROOT)
    importlib.invalidate_caches()
    module = importlib.import_module(MODULE_NAME)
    yield module
    # leave sys.modules as found, the name is only valid while the test runs
    for name in [n for n in sys.modules if n == 'module' or n.startswith('module.')]:
        del sys.modules[name]


@pytest.fixture(autouse=True)
def restore_role():
    """Leave the role as it was found."""
    role = env.PROCESS_ROLE
    yield
    env.PROCESS_ROLE = role


class TestPackageName:
    def test_comes_from_the_entry(self):
        """The package name is the first part of path_main, not a guess."""
        assert make_mod().package_name == 'module'

    def test_entry_outside_a_package(self):
        """An entry file at the mod root has no package to import from."""
        mod = ModBase(ModEntry(root=PathStr.new('ExampleMod'), data=ModEntryModel.build(name='example_mod', path_main='main.py')))
        assert mod.package_name == ''


class TestWorkerRole:
    def test_returns_the_class_of_the_script(self, monkeypatch, worker_package):
        """The model is the class the game script imported."""
        monkeypatch.setattr(env, 'PROCESS_ROLE', 'worker')
        assert make_mod().get_group_model(MODEL_FILE, 'Campaign') is worker_package.Campaign

    def test_cross_nav_base_is_the_class_of_the_script(self, monkeypatch, worker_package):
        """A variant inherits the base class of the other nav of the script."""
        monkeypatch.setattr(env, 'PROCESS_ROLE', 'worker')
        model = make_mod().get_group_model('gems/gems_model.py', 'GemsCampaign')
        assert model.__bases__[0] is worker_package.Campaign

    def test_config_group_passes_isinstance(self, monkeypatch, worker_package):
        """The whole point: the config group is an instance of the script class."""
        monkeypatch.setattr(env, 'PROCESS_ROLE', 'worker')

        class Config(AlasioConfigBase):
            entry = ModEntry(env.ALASIO_ROOT.joinpath('ExampleMod'))
            Campaign: "main.Campaign"

        cfg = Config(':memory:', task='Main')
        group = cfg.Campaign
        # a bound group is a proxy, so the set still goes through it
        assert type(group) is GroupProxy
        assert isinstance(group, worker_package.Campaign)

    def test_falls_back_to_loadpy_without_a_package(self, monkeypatch, worker_package):
        """A mod not imported as a package is still loadable."""
        monkeypatch.setattr(env, 'PROCESS_ROLE', 'worker')
        mod = make_mod()
        monkeypatch.setattr(
            type(mod), 'package_name', property(lambda self: ''), raising=False)
        model = mod.get_group_model(MODEL_FILE, 'Campaign')
        # a different class object, but a working model
        assert model is not worker_package.Campaign
        assert model.__name__ == 'Campaign'


class TestAlasioModel:
    """A group of alasio is the class of the library, not a copy of the file."""

    def test_is_the_class_of_the_library(self, monkeypatch):
        """A group of alasio is imported through the package of the library."""
        monkeypatch.setattr(env, 'PROCESS_ROLE', 'worker')
        model = make_mod().get_group_model('alasio/device_model.py', 'Emulator')
        assert model is device_model.Emulator

    def test_inside_the_mod_root_is_the_class_of_the_library(self, monkeypatch):
        """A release ships the library inside the root of the mod.

        The file of an alasio group is then inside the root of the mod but not
        in the package of the mod: the root of the mod alone reports it as
        foreign and falls back to a second copy of the classes.
        """
        monkeypatch.setattr(env, 'PROCESS_ROLE', 'worker')
        # the mod root holds the library, the way a release lays it out under
        # toolkit/lib/site-packages
        mod = ModBase(ModEntry(
            root=env.ALASIO_ROOT.uppath(1),
            data=ModEntryModel.build(name='alasio', path_main='module/main.py')))
        with logger.mock_capture_writer() as capture:
            model = mod.get_group_model('alasio/device_model.py', 'Emulator')
        assert capture.concat() == ''
        assert model is device_model.Emulator

    def test_library_nested_in_the_release_root(self, monkeypatch):
        """A release keeps the library below toolkit/lib/site-packages of its root.

        The name of the module comes from the index, never from where the
        library sits relative to the root of the mod: the nesting of a release
        must not turn the file of the library into a file of the mod, which
        would load a second set of classes.
        """
        site_packages = PathStr.new(os.path.abspath('/release')).joinpath('toolkit/lib/site-packages')
        monkeypatch.setattr(env, 'ALASIO_ROOT', site_packages)
        monkeypatch.setattr(env, 'PROCESS_ROLE', 'worker')
        mod = ModBase(ModEntry(
            root=site_packages.uppath(3),
            data=ModEntryModel.build(name='release_mod', path_main='module/main.py')))
        path = mod.resolve_config_path('alasio/device_model.py')
        assert path == site_packages.joinpath('alasio/config/alasio/device_model.py')
        # the stub answers the import of that file, the test process keeps its
        # own sys.modules
        imported = []
        module = SimpleNamespace(__file__=str(path))

        def import_module(name):
            imported.append(name)
            return module

        monkeypatch.setattr(mod_base, 'importlib', SimpleNamespace(import_module=import_module))
        with logger.mock_capture_writer() as capture:
            assert mod.import_model_module('alasio/device_model.py') is module
        assert capture.concat() == ''
        assert imported == ['alasio.config.alasio.device_model']

    def test_file_in_no_package_is_reported(self, monkeypatch):
        """A file outside the package of path_main is reported, not imported.

        path_main names the package of the mod, the config folder sits in
        another folder: an import would execute the file a second time, under
        a name the game script never uses.
        """
        monkeypatch.setattr(env, 'PROCESS_ROLE', 'worker')
        mod = ModBase(ModEntry(
            root=PathStr.new('ExampleMod'), data=ModEntryModel.build(name='example_mod', path_main='app/main.py')))
        path = mod.resolve_config_path('main/main_model.py')
        with logger.mock_capture_writer() as capture:
            assert mod.import_model_module('main/main_model.py') is None
        assert capture.concat() == (
            f'WARNING | DataInconsistent: model file "{path}" is not in the package "app" of the mod\n'
        )

    def test_non_python_file_is_reported(self):
        """An index that names another file is reported, never imported."""
        mod = make_mod()
        path = mod.resolve_config_path('main/main_model.json')
        with logger.mock_capture_writer() as capture:
            assert mod.import_model_module('main/main_model.json') is None
        assert capture.concat() == (
            f'WARNING | DataInconsistent: model file "{path}" is not a python file\n'
        )


class TestBackendRole:
    def test_does_not_touch_sys_modules(self, monkeypatch):
        """The backend loads the file on its own, the script module is untouched."""
        monkeypatch.setattr(env, 'PROCESS_ROLE', 'backend')
        monkeypatch.delitem(sys.modules, MODULE_NAME, raising=False)
        model = make_mod().get_group_model(MODEL_FILE, 'Campaign')
        assert model.__name__ == 'Campaign'
        assert MODULE_NAME not in sys.modules
