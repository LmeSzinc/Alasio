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
"""
import sys

import pytest

# the mod under test, imported as a package the way the scheduler does
import ExampleMod.module.main  # noqa: F401
from alasio.config.alasio.group_proxy import GroupProxy
from alasio.config.base import AlasioConfigBase
from alasio.config.entry.const import ModEntryInfo
from alasio.config.entry.mod_base import ModBase
from alasio.ext import env
from alasio.ext.path import PathStr
from ExampleMod.module.config.const import entry as EXAMPLE_ENTRY
from ExampleMod.module.config.main.main_model import Campaign

MODEL_FILE = 'main/main_model.py'
MODULE_NAME = 'ExampleMod.module.config.main.main_model'


def make_mod():
    """
    Build a ModBase of ExampleMod.

    Returns:
        ModBase:
    """
    return ModBase(ModEntryInfo(name='example_mod', root=PathStr.new('ExampleMod')))


def without_package(monkeypatch, mod):
    """
    Make the mod look like it was never imported as a package.

    Args:
        monkeypatch (MonkeyPatch): pytest fixture
        mod (ModBase): Mod under test
    """
    monkeypatch.setattr(type(mod), 'package_dir', property(lambda self: None), raising=False)


@pytest.fixture(autouse=True)
def restore_role():
    """Leave the role as it was found."""
    role = env.PROCESS_ROLE
    yield
    env.PROCESS_ROLE = role


class TestPackageDir:
    def test_finds_the_package_of_the_mod(self):
        """The package name and folder come from sys.modules, not a guess."""
        name, folder = make_mod().package_dir
        assert name == 'ExampleMod'
        assert folder.replace('\\', '/').lower().endswith('examplemod')

    def test_no_package_means_no_import(self, monkeypatch):
        """A process that did not import the mod has nothing to import from."""
        mod = make_mod()
        without_package(monkeypatch, mod)
        assert mod.import_model_module(PathStr.new('module/config/main/main_model.py')) is None


class TestWorkerRole:
    def test_returns_the_class_of_the_script(self, monkeypatch):
        """The model is the class the game script imported."""
        monkeypatch.setattr(env, 'PROCESS_ROLE', 'worker')
        assert make_mod().get_group_model(MODEL_FILE, 'Campaign') is Campaign

    def test_cross_nav_base_is_the_class_of_the_script(self, monkeypatch):
        """A variant inherits the base class of the other nav of the script."""
        monkeypatch.setattr(env, 'PROCESS_ROLE', 'worker')
        model = make_mod().get_group_model('gems/gems_model.py', 'GemsCampaign')
        assert model.__bases__[0] is Campaign

    def test_config_group_passes_isinstance(self, monkeypatch):
        """The whole point: the config group is an instance of the script class."""
        monkeypatch.setattr(env, 'PROCESS_ROLE', 'worker')

        class Config(AlasioConfigBase):
            entry = EXAMPLE_ENTRY
            Campaign: "main.Campaign"

        cfg = Config(':memory:', task='Main')
        group = cfg.Campaign
        # a bound group is a proxy, so the set still goes through it
        assert type(group) is GroupProxy
        assert isinstance(group, Campaign)

    def test_falls_back_to_loadpy_without_a_package(self, monkeypatch):
        """A mod not imported as a package is still loadable."""
        monkeypatch.setattr(env, 'PROCESS_ROLE', 'worker')
        mod = make_mod()
        without_package(monkeypatch, mod)
        model = mod.get_group_model(MODEL_FILE, 'Campaign')
        # a different class object, but a working model
        assert model is not Campaign
        assert model.__name__ == 'Campaign'


class TestBackendRole:
    def test_does_not_touch_sys_modules(self, monkeypatch):
        """The backend loads the file on its own, the script module is untouched."""
        monkeypatch.setattr(env, 'PROCESS_ROLE', 'backend')
        monkeypatch.delitem(sys.modules, MODULE_NAME, raising=False)
        model = make_mod().get_group_model(MODEL_FILE, 'Campaign')
        assert model.__name__ == 'Campaign'
        assert MODULE_NAME not in sys.modules

    def test_builds_a_separate_class(self, monkeypatch):
        """Two loads give two classes, which is why a worker may not use it."""
        monkeypatch.setattr(env, 'PROCESS_ROLE', 'backend')
        assert make_mod().get_group_model(MODEL_FILE, 'Campaign') is not Campaign
