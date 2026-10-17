"""
The environment of a screenshot test, see alasio.testing.screenshot.

A test changes no process global state: the working folder stays the one of the
test session, and the assets of the game script resolve their image files against
the project root of the mod instead of the working folder. That is what these
tests hold down: the harness must not resort to os.chdir(), and the assets must
keep resolving wherever the test session runs from.

The plugin builds the modules a test asks for from the annotations of its
parameters, so the parameter names are free and all the modules of one test run
on the one screenshot of the test.
"""
import os

import pytest
from module.base.base import ModuleBase
from module.config.const import entry
from module.map.assets import FLEET_1_CLEAR

from alasio.ext.path import PathStr
from alasio.testing.screenshot import image

# The working folder at collection time, before any fixture of the tests ran
CWD = os.getcwd()


@pytest.fixture(autouse=True)
def forbid_chdir(monkeypatch):
    """
    The working folder of the process is global state shared by everything in the
    process, a test must not change it, not even for the duration of the test
    """

    def forbidden(path):
        raise AssertionError(f'a screenshot test changed the working folder: {path}')

    monkeypatch.setattr(os, 'chdir', forbidden)


class TestScreenshotEnvironment:
    @image("map/fleet_bar/not_expanded.webp")
    def test_does_not_change_the_working_folder(self, main: ModuleBase):
        """The module runs on the screenshot without touching the working folder."""
        assert main.device.has_cached_screenshot()
        assert os.getcwd() == CWD

    def test_assets_resolve_from_the_project_root(self):
        """An asset file is an absolute path under the project root of the mod."""
        file = PathStr.new(FLEET_1_CLEAR.file)

        assert file.is_abspath()
        assert file.subpath_to(entry.root) is not None
        assert file.isfile()


class TestScreenshotPlugin:
    @image("map/fleet_bar/not_expanded.webp")
    def test_parameters_are_named_freely(self, first: ModuleBase, second: ModuleBase):
        """Every annotated parameter is a module, the names are free, and the
        modules of one test share the one screenshot of the test."""
        assert isinstance(first, ModuleBase)
        assert isinstance(second, ModuleBase)
        assert first is not second
        assert first.device is second.device
