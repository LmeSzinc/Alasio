"""
Tests for alasio.testing.screenshot, the screenshot golden test harness.

The harness resolves the project root of a test from the test file, binds the
screenshot of `collection` to an offline device, and builds the modules a test
asks for by the annotations of its parameters. The end to end usage of the pytest
plugin is covered by ExampleMod/tests, this file tests the pieces.
"""
import os
import subprocess
import sys

import pytest

from alasio.config.base import AlasioConfigBase
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.testing import screenshot as screenshot_module
from alasio.testing.screenshot import (
    ATTR_SCREENSHOT, CONFIG_NAME, PROJECT_ROOT_MANAGER, ProjectRootManager, ScreenshotNotFound, ScreenshotTestError,
    build_main, config_of, image, load_screenshot, module_parameters, screenshot_of
)
from alasio.testing.screenshot_device import ScreenshotDevice, ScreenshotNotProvided
from ExampleMod.module.config.config import Config


class TestImport:
    def test_import_does_not_load_image_stack(self):
        """Loading the pytest plugin must not import numpy/opencv."""
        code = (
            "import sys\n"
            "import alasio.testing.screenshot\n"
            "assert 'numpy' not in sys.modules, 'numpy imported at plugin load'\n"
            "assert 'cv2' not in sys.modules, 'cv2 imported at plugin load'\n"
            "assert 'alasio.base.image.imfile' not in sys.modules, 'imfile imported at plugin load'\n"
        )
        process_env = os.environ.copy()
        process_env['PYTHONPATH'] = str(env.ALASIO_ROOT) + os.pathsep + process_env.get('PYTHONPATH', '')
        result = subprocess.run(
            [sys.executable, '-c', code],
            cwd=str(env.ALASIO_ROOT),
            env=process_env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


class StubModule:
    """
    A module of the tests: the class under test declares the config class of its
    mod, which is what config_of() and build_main() read
    """
    config: Config

    def __init__(self, config, device=None, task=''):
        self.config = config
        self.device = device
        if task:
            self.config.task = task


class StubFunction:
    """
    A stand-in of a test function, for the attributes the harness reads
    """
    pass


class StubNode:
    """
    A stand-in of a pytest node, for the attributes the harness reads
    """
    name = 'test_stub'


class StubRequest:
    """
    A stand-in of a pytest item or fixture request, enough for screenshot_of()
    and load_screenshot()
    """

    def __init__(self, file, screenshot=None, function=None):
        """
        Args:
            file (str): Path of the test file
            screenshot (str): Screenshot bound by the @image decorator, if any
            function (callable): Test function of the request, a plain stub if
                the caller does not pass one
        """
        self.fspath = file
        self.node = StubNode()
        self.function = function if function is not None else StubFunction()
        if screenshot is not None:
            setattr(self.function, ATTR_SCREENSHOT, screenshot)


class FakeImage:
    """
    A stand-in of a screenshot
    """
    pass


# The example mod of the repository, and a screenshot test of it. These are the
# real paths of the repository: the resolution of a test file to the project
# root of its mod is the very behavior under test.
EXAMPLE_MOD = env.ALASIO_ROOT.joinpath('ExampleMod')
TEST_FILE = EXAMPLE_MOD.joinpath('tests/map/test_fleet_bar.py')
SCREENSHOT = 'map/fleet_bar/not_expanded.webp'


@pytest.fixture(autouse=True)
def check_no_database():
    """
    A screenshot test never touches the database: the config of a test is a
    template config, every group falls back to its defaults
    """
    from alasio.db.conn import SQLITE_POOL

    before = set(SQLITE_POOL.all_pool)
    yield
    assert set(SQLITE_POOL.all_pool) == before, \
        f'The test created a database: {set(SQLITE_POOL.all_pool) - before}'


class TestProjectRootManager:
    """
    The project root of a test is the nearest ancestor of the test file holding
    `module/config/const.py`, the entry file of a mod
    """

    def test_project_root_of_a_test_inside_a_mod(self):
        """A test inside ExampleMod resolves to ExampleMod, not to the repository."""
        assert PROJECT_ROOT_MANAGER.get_project_root(TEST_FILE) == EXAMPLE_MOD

    def test_project_root_of_a_mod_that_is_the_project_root(self):
        """A mod that is the repository itself: its root is the repository root."""
        assert PROJECT_ROOT_MANAGER.get_project_root(EXAMPLE_MOD.joinpath('tests/test_x.py')) == EXAMPLE_MOD

    def test_no_project_root(self):
        """A test outside of any mod has no project root, and no screenshot."""
        with pytest.raises(ScreenshotTestError):
            PROJECT_ROOT_MANAGER.get_project_root(env.ALASIO_ROOT.joinpath('alasio/testing/screenshot.py'))

    def test_the_walk_runs_once_per_mod(self, monkeypatch):
        """
        Many test files of one mod share one walk of the folders: the root of a
        mod is discovered once, not once per test file.
        """
        manager = ProjectRootManager()
        monkeypatch.setattr(screenshot_module, 'PROJECT_ROOT_MANAGER', manager)
        # A step of the walk is one existence check of a folder. os.stat is not
        # counted as well: on Windows python 3.12+ os.path.isfile() takes the
        # nt._path_isfile() fast path and never calls the python level os.stat,
        # which made the walk read as a walk of no folder
        calls = []
        original = os.path.isfile

        def counting_isfile(path):
            calls.append(path)
            return original(path)

        monkeypatch.setattr(os.path, 'isfile', counting_isfile)

        manager.get_project_root(TEST_FILE)
        walk = len(calls)
        # Other test files of the same mod, in the same and in other folders
        manager.get_project_root(EXAMPLE_MOD.joinpath('tests/map/test_other.py'))
        manager.get_project_root(EXAMPLE_MOD.joinpath('tests/island/handler/test_order.py'))
        manager.get_project_root(EXAMPLE_MOD.joinpath('tests/test_entry.py'))

        assert walk > 0
        assert len(calls) == walk

    def test_known_root_prefers_the_longest_match(self):
        """A mod nested in another mod resolves to the inner one, without IO."""
        manager = ProjectRootManager()
        manager.roots[:] = [PathStr.new('/a/mod'), PathStr.new('/a/mod/inner')]

        assert manager.get_known_root('/a/mod/inner/tests/test_x.py') == PathStr.new('/a/mod/inner')
        assert manager.get_known_root('/a/mod/tests/test_x.py') == PathStr.new('/a/mod')
        # A prefix of the folder name is not a folder of the mod
        assert manager.get_known_root('/a/modular/tests/test_x.py') is None


class TestImageDecorator:
    def test_binds_the_screenshot(self):
        @image(SCREENSHOT)
        def test_x():
            pass

        assert getattr(test_x, ATTR_SCREENSHOT) == SCREENSHOT

    def test_keeps_the_function(self):
        @image(SCREENSHOT)
        def test_x():
            return 1

        assert test_x() == 1


class TestScreenshotOf:
    def test_resolves_the_collection_path(self):
        request = StubRequest(TEST_FILE, screenshot=SCREENSHOT)

        assert screenshot_of(request) == EXAMPLE_MOD.joinpath('collection/map/fleet_bar/not_expanded.webp')

    def test_test_without_the_decorator(self):
        request = StubRequest(TEST_FILE)

        with pytest.raises(ScreenshotNotFound) as e:
            screenshot_of(request)
        assert 'has no screenshot' in str(e.value)

    def test_resolving_does_not_check_the_file(self):
        """The path is resolved without IO, a missing file is reported on the read."""
        request = StubRequest(TEST_FILE, screenshot='map/fleet_bar/no_such_screenshot.webp')

        file = screenshot_of(request)

        assert file == EXAMPLE_MOD.joinpath('collection/map/fleet_bar/no_such_screenshot.webp')


class TestLoadScreenshot:
    def test_returns_the_path_and_the_image(self):
        request = StubRequest(TEST_FILE, screenshot=SCREENSHOT)

        file, image = load_screenshot(request)

        assert file == EXAMPLE_MOD.joinpath('collection/map/fleet_bar/not_expanded.webp')
        assert image.shape == (720, 1280, 3)

    def test_screenshot_that_does_not_exist(self):
        request = StubRequest(TEST_FILE, screenshot='map/fleet_bar/no_such_screenshot.webp')

        with pytest.raises(ScreenshotNotFound) as e:
            load_screenshot(request)
        assert 'does not exist' in str(e.value)

    def test_test_without_the_decorator(self):
        request = StubRequest(TEST_FILE)

        with pytest.raises(ScreenshotNotFound):
            load_screenshot(request)


class TestScreenshotDevice:
    """
    The device of a screenshot test holds the screenshot of the test and never
    captures or clicks anything
    """

    def test_holds_the_screenshot(self):
        screenshot = FakeImage()
        device = ScreenshotDevice(screenshot, file='x.webp')

        assert device.image is screenshot
        assert device.has_cached_screenshot() is True

    def test_screenshot_raises(self):
        device = ScreenshotDevice(FakeImage(), file='map/fleet_bar/not_expanded.webp')

        with pytest.raises(ScreenshotNotProvided) as e:
            device.screenshot()
        assert 'not_expanded.webp' in str(e.value)

    def test_click_is_recorded(self):
        device = ScreenshotDevice(FakeImage(), file='x.webp')

        device.click('BUTTON', record_check=True)

        assert device.clicks == ['BUTTON']

    def test_drag_is_recorded(self):
        device = ScreenshotDevice(FakeImage(), file='x.webp')

        device.drag((0, 0), (10, 10), name='SCROLL')
        device.swipe((1, 1), (2, 2))

        assert device.drags == [((0, 0), (10, 10)), ((1, 1), (2, 2))]

    def test_sleep_is_recorded(self):
        device = ScreenshotDevice(FakeImage(), file='x.webp')

        device.sleep(0.5)

        assert device.sleeps == [0.5]


class TestModuleParameters:
    """
    The modules of a test are the parameters annotated with a module class, the
    names of the parameters are free
    """

    def test_free_names(self):
        def test_x(self, main: StubModule, combat: StubModule):
            pass

        assert module_parameters(test_x) == {'main': StubModule, 'combat': StubModule}

    def test_quoted_annotation(self):
        """An annotation written as a string resolves against the test module."""
        def test_x(self, main: "StubModule"):
            pass

        assert module_parameters(test_x) == {'main': StubModule}

    def test_parameter_without_an_annotation(self):
        def test_x(self, main):
            pass

        assert module_parameters(test_x) == {}

    def test_annotation_that_is_not_a_class(self):
        def test_x(self, main: SCREENSHOT):
            pass

        assert module_parameters(test_x) == {}

    def test_class_that_is_not_a_module(self):
        """A class without the config annotation of a mod is not a module."""
        def test_x(self, item: object):
            pass

        assert module_parameters(test_x) == {}

    def test_self_is_not_a_module(self):
        def test_x(self):
            pass

        assert module_parameters(test_x) == {}


class TestConfigOf:
    def test_config_of_a_module_class(self):
        """The config class of the module is loaded as a template config."""
        config = config_of(StubModule)

        assert isinstance(config, AlasioConfigBase)
        assert config.config_name == CONFIG_NAME
        assert config.is_template_config is True

    def test_group_falls_back_to_its_default(self):
        """The groups of a template config read their defaults, no database."""
        config = config_of(StubModule)

        # Default of Campaign.Name in the example mod, see main_model.py
        assert config.Campaign.Name == '12-4'

    def test_class_without_a_config_annotation(self):
        class NoConfig:
            pass

        with pytest.raises(ScreenshotTestError) as e:
            config_of(NoConfig)
        assert 'has no "config" annotation' in str(e.value)

    def test_class_that_declares_no_config_class_of_its_own(self):
        """
        ModuleBase declares `config: AlasioConfigBase`, a module under test must
        declare the config class of its mod
        """
        from alasio.base.base import ModuleBase

        class BaseConfig(ModuleBase):
            pass

        with pytest.raises(ScreenshotTestError) as e:
            config_of(BaseConfig)
        assert 'declares no config class of its own' in str(e.value)


class TestBuildMain:
    def test_builds_the_module_on_the_device(self):
        device = ScreenshotDevice(FakeImage(), file='x.webp')

        main = build_main(StubModule, device)

        assert isinstance(main, StubModule)
        assert main.device is device
        assert main.config.config_name == CONFIG_NAME

    def test_passes_the_extra_arguments(self):
        device = ScreenshotDevice(FakeImage(), file='x.webp')

        main = build_main(StubModule, device, task='Main')

        assert main.config.task == 'Main'
