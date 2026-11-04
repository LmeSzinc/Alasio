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

import pytest

from alasio.config.alasio.group_proxy import GroupProxy
from alasio.config.base import AlasioConfigBase
from alasio.config.entry.mod_base import ModBase
from alasio.config.entry.mod_entry import ModEntry, ModEntryModel
from alasio.ext import env
from alasio.ext.path import PathStr

MODEL_FILE = 'main/main_model.py'
MODULE_NAME = 'module.config.main.main_model'
MOD_ROOT = os.path.abspath('ExampleMod')


def make_mod():
    """
    Build a ModBase of ExampleMod.

    Returns:
        ModBase:
    """
    return ModBase(ModEntry(root=PathStr.new('ExampleMod'), data=ModEntryModel(name='example_mod')))


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
        mod = ModBase(ModEntry(root=PathStr.new('ExampleMod'), data=ModEntryModel(name='example_mod', path_main='main.py')))
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

        from ExampleMod.module.config.const import entry as EXAMPLE_ENTRY

        class Config(AlasioConfigBase):
            entry = EXAMPLE_ENTRY
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


class TestBackendRole:
    def test_does_not_touch_sys_modules(self, monkeypatch):
        """The backend loads the file on its own, the script module is untouched."""
        monkeypatch.setattr(env, 'PROCESS_ROLE', 'backend')
        monkeypatch.delitem(sys.modules, MODULE_NAME, raising=False)
        model = make_mod().get_group_model(MODEL_FILE, 'Campaign')
        assert model.__name__ == 'Campaign'
        assert MODULE_NAME not in sys.modules
