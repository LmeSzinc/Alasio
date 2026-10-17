"""
Screenshot golden tests of game script modules.

A game script module reads the game from `device.image`, so the unit test of a
module starts by binding a screenshot to the device the module runs on.
Screenshots are collected by hand under `<project root>/collection`, a test binds
one with the `image` decorator and asks for the modules it needs by annotating
its parameters, the parameter names are free:

    from alasio.testing.screenshot import image

    class TestFleetBarNotExpanded:
        @image("map/fleet_bar/not_expanded.webp")
        def test_not_expanded(self, main: FleetPreparation, combat: CombatModule):
            print(main.detect_fleet_bar())

The flow of a golden test:

1. The draft test prints what the modules return, without a single assert.
2. Run it, the printed output is the golden result.
3. Copy the printed output into the test as asserts, on the Python side, so the
   test may also assert attributes, warnings and logs.
4. A human reads the screenshot and the asserts together and corrects them, and
   the asserts stay in the test file as the readable record of that review.

A module parameter is built the way the scheduler builds it - the module class
declares its own config class, and it is constructed with the config of the mod
and the device of the scheduler - except that the device is the offline device of
the test, which holds exactly the screenshot of the test, see
screenshot_device.py:

- `device.image` is the screenshot, and the values cached on a screenshot
  (@cached_on_screenshot) are computed on it, like on a real screenshot
- `device.screenshot()` raises instead of capturing: a module that needs a
  second screenshot is a test missing a screenshot, not a module that re-reads a
  stale image
- `device.click()` / `device.drag()` are recorded and do nothing, so a test may
  follow a module into an action and assert on it afterwards

All the modules of a test run on the one device of the test, so the test reads it
from any of them (`main.device`) and builds more modules with
`build_main(cls, device)`.

A test module only imports the `image` decorator; the modules a test asks for are
looked up by the annotations of its parameters, nothing is registered as a
fixture. The plugin is enabled in the pytest configuration of the project:

    [tool.pytest.ini_options]
    addopts = ["-p", "alasio.testing.screenshot"]

It is loaded by module path instead of being an installed entry point, so a
plain checkout runs the tests without installing the package first.

The project root of a test is the nearest ancestor of the test file holding
`module/config/const.py`, which covers both layouts of a mod: a mod inside the
Alasio repository (`<repo>/ExampleMod`) and a mod that is the repository itself
(`<repo>` as a whole).
"""
import inspect
from typing import get_type_hints

import pytest
from msgspecerror import get_class_annotation

from alasio.base.image.imfile import image_load
from alasio.config.base import AlasioConfigBase
from alasio.ext.path import PathStr
from alasio.testing.screenshot_device import ScreenshotDevice, ScreenshotTestError

# Attribute of the test function holding the screenshot of the `image` decorator
ATTR_SCREENSHOT = '__screenshot__'
# Folder of the collected screenshots, relative to the project root
COLLECTION = 'collection'
# Entry file of a mod, the folder holding it is the project root of the mod
MOD_CONST = 'module/config/const.py'
# Config name of the tests: a template config, which is read only and never
# touches the database, every group falls back to its default values
CONFIG_NAME = 'template'


class ScreenshotNotFound(ScreenshotTestError):
    """
    Raised if the screenshot bound by `image` does not exist
    """
    pass


class ModuleRequest:
    """
    The placeholder of an annotated module parameter, see module_parameters().

    pytest parametrizes the parameter with a request instead of a fixture, the
    module is built when the test runs, see pytest_pyfunc_call().
    """

    def __init__(self, cls):
        """
        Args:
            cls (type): The module class of the annotation
        """
        self.cls = cls

    def __repr__(self):
        return f'{self.__class__.__name__}({self.cls.__name__})'


def image(path):
    """
    Bind a screenshot of the collection folder to a test

    Args:
        path (str): Path of the screenshot relative to `<project root>/collection`,
            e.g. "map/fleet_bar/not_expanded.png"

    Returns:
        callable: The decorator
    """
    def decorator(func):
        setattr(func, ATTR_SCREENSHOT, path)
        return func

    return decorator


class ProjectRootManager:
    """
    The project roots of the mods of the running session.

    A project root is a folder holding `module/config/const.py`, the entry file of
    a mod. A real mod has many test files and they all live under the one root of
    the mod, so a root is discovered once and reused: resolving a test file under
    a known root walks no folder, it is a sub-path check.

    Attributes:
        roots (list[PathStr]): The project roots discovered so far
    """

    def __init__(self):
        self.roots = []

    def get_project_root(self, test_file):
        """
        Get the project root of a test file, which is the nearest ancestor of the
        test file holding `module/config/const.py`

        Args:
            test_file (str): Path of the test file, absolute like the path pytest
                reports, the result of a relative path depends on the working folder

        Returns:
            PathStr:

        Raises:
            ScreenshotTestError: If no project root is found
        """
        path = PathStr.new(test_file).abspath()

        # The test file belongs to a mod discovered by an earlier test file
        root = self.get_known_root(path)
        if root is not None:
            return root

        # First test file of this mod: walk up once and remember the root
        root = self.find_root(path, test_file)
        self.roots.append(root)
        return root

    def get_known_root(self, path):
        """
        Get the known project root holding a path, the deepest one if several do

        Args:
            path (str): Normalized absolute path

        Returns:
            PathStr | None: The root, None if no known root holds the path
        """
        path = PathStr.new(path)
        matched = None
        for root in self.roots:
            if path.subpath_to(root) is None:
                continue
            if matched is None or len(root) > len(matched):
                matched = root
        return matched

    @staticmethod
    def find_root(path, test_file=None):
        """
        Walk up from a path to the nearest folder holding `module/config/const.py`

        Args:
            path (str): Normalized absolute path
            test_file (str): Path to report in the error, `path` if not given

        Returns:
            PathStr:

        Raises:
            ScreenshotTestError: If no project root is found
        """
        folder = PathStr.new(path)
        while folder:
            if folder.joinpath(MOD_CONST).isfile():
                return folder
            parent = folder.uppath(1)
            if parent == folder:
                break
            folder = parent

        raise ScreenshotTestError(
            f'No project root above the test file "{test_file or path}", a project root is '
            f'a folder holding "{MOD_CONST}"'
        )


# The project roots of the mods of the running session
PROJECT_ROOT_MANAGER = ProjectRootManager()


def test_name(item):
    """
    The name of a test, from a pytest item or a fixture request

    Args:
        item (Function | FixtureRequest):

    Returns:
        str:
    """
    node = getattr(item, 'node', item)
    return node.name


def screenshot_of(item):
    """
    Resolve the screenshot bound by the `image` decorator

    The file is not checked here: a check is one IO call that the read
    duplicates, so the missing file is reported by load_screenshot() from the
    read itself instead.

    Args:
        item (Function | FixtureRequest): The test item or request, both carry
            the test function and the path of the test file

    Returns:
        PathStr: Path of the screenshot

    Raises:
        ScreenshotNotFound: If the test has no `image` decorator
    """
    path = getattr(item.function, ATTR_SCREENSHOT, None)
    if path is None:
        raise ScreenshotNotFound(
            f'"{test_name(item)}" has no screenshot, decorate the test with '
            f'@image("<screenshot>") to bind one of the collection folder'
        )

    root = PROJECT_ROOT_MANAGER.get_project_root(str(item.fspath))
    return root.joinpath(COLLECTION).joinpath(path)


def load_screenshot(item):
    """
    The screenshot bound by the `image` decorator, loaded from the collection
    folder

    Args:
        item (Function | FixtureRequest): The test item or request

    Returns:
        tuple[PathStr, np.ndarray]: Path of the screenshot, and the image

    Raises:
        ScreenshotNotFound: If the test has no `image` decorator, or if the
            screenshot does not exist
    """
    file = screenshot_of(item)
    try:
        return file, image_load(file)
    except FileNotFoundError:
        raise ScreenshotNotFound(
            f'Screenshot of "{test_name(item)}" does not exist: {file}'
        ) from None


def config_of(cls):
    """
    Build the config of a module class, the way the scheduler does: the module
    class declares its own config class, and the config is loaded with the mod
    entry of that class

    The config of a test is a template config, which is read only: no database
    and no user config file is involved, every group of the config falls back to
    its default values.

    Args:
        cls (type): The module class under test

    Returns:
        AlasioConfigBase:

    Raises:
        ScreenshotTestError: If the class does not declare a config class of its own
    """
    try:
        config_cls = get_class_annotation(cls, 'config')
    except AttributeError:
        raise ScreenshotTestError(
            f'"{cls.__name__}" has no "config" annotation, annotate it like the '
            f'mod does: "config: Config"'
        ) from None

    if config_cls is AlasioConfigBase:
        raise ScreenshotTestError(
            f'"{cls.__name__}" declares no config class of its own, annotate it '
            f'like the mod does: "config: Config"'
        )

    return config_cls(CONFIG_NAME)


def build_main(cls, device, **kwargs):
    """
    Build a module class the way the scheduler builds it, except that its device
    is the offline device of a test

    Args:
        cls (type): The module class under test
        device (DeviceBase): The device of the test, holding the screenshot
        **kwargs: Extra arguments of the constructor of the class

    Returns:
        object: The module under test
    """
    config = config_of(cls)
    return cls(config, device=device, **kwargs)


def module_parameters(function):
    """
    The parameters of a test that ask for a module, by the annotation of the
    parameter: `def test(self, main: FleetPreparation)`, the name of the
    parameter is free.

    Args:
        function (callable): The test function

    Returns:
        dict[str, type]: {parameter name: module class}
    """
    try:
        # Resolve the annotations written as a string, e.g. "FleetPreparation"
        annotations = get_type_hints(function)
    except NameError:
        # An annotation does not resolve, report it as it is written
        annotations = getattr(function, '__annotations__', {})

    out = {}
    for name, annotation in annotations.items():
        if name == 'self':
            continue
        if not isinstance(annotation, type):
            continue
        # A module class declares the config class of its mod, see build_main()
        try:
            get_class_annotation(annotation, 'config')
        except (AttributeError, TypeError):
            continue
        out[name] = annotation
    return out


def pytest_generate_tests(metafunc):
    """
    Parametrize the module parameters of a screenshot test with a placeholder, so
    pytest does not look for a fixture named after the parameter: the module is
    built from its annotation when the test runs, see pytest_pyfunc_call()
    """
    if getattr(metafunc.function, ATTR_SCREENSHOT, None) is None:
        return

    for name, cls in module_parameters(metafunc.function).items():
        metafunc.parametrize(name, [ModuleRequest(cls)], ids=[cls.__name__])


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    """
    Build the modules of a screenshot test on the screenshot of the test, and
    call the test with them
    """
    requests = {
        name: pyfuncitem.funcargs[name]
        for name in module_parameters(pyfuncitem.function)
        if isinstance(pyfuncitem.funcargs.get(name), ModuleRequest)
    }
    if not requests:
        return None
    if inspect.iscoroutinefunction(pyfuncitem.function):
        raise ScreenshotTestError(f'"{pyfuncitem.name}" is async, a screenshot test is sync')

    # All the modules of the test run on the one screenshot of the test
    device = None
    modules = {}
    for name, request in requests.items():
        if device is None:
            file, image = load_screenshot(pyfuncitem)
            device = ScreenshotDevice(image=image, file=file)
        modules[name] = build_main(request.cls, device)

    # Call the test with its fixtures and its modules
    names = set(inspect.signature(pyfuncitem.function).parameters) - {'self'}
    kwargs = {name: value for name, value in pyfuncitem.funcargs.items() if name in names}
    kwargs.update(modules)
    pyfuncitem.obj(**kwargs)
    return True
