import yaml
from msgspec import DecodeError
from msgspec.json import decode

from alasio.backport import removesuffix
from alasio.config.entry.utils import validate_nav_name
from alasio.config_dev.parse.postprocess import PostprocessInfo, run_postprocess
from alasio.ext import env
from alasio.ext.file.yamlfile import yaml_loads
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_bytes
from alasio.ext.path.calc import subpath_to, to_posix
from alasio.logger import logger

_EMPTY = object()


class DefinitionError(Exception):
    def __init__(self, msg, file='', keys='', value=_EMPTY):
        """
        Args:
            msg (str | Exception): Error message
            file (str): Source file of the definition, empty if unknown
            keys (list[str] | str): Key path to the broken definition
            value (Any): Offending value, omitted from the rendered error if not given
        """
        if isinstance(msg, Exception):
            msg = f'{msg.__class__.__name__}({msg})'
        # Fill Exception.args with the formatted message, not with the original
        # argument: the traceback, the logger and the third party exception
        # handlers read args, they must not see the wrapped exception object
        super().__init__(msg)
        self.msg = msg
        self.file = file
        self.keys = keys
        self.value = value

    def __str__(self):
        """
        Show error as:

        DefinitionError,
            file={self.file!r},
            keys={self.keys!r},
            value={self.value!r},
            error={self.msg!r}

        The location lines are printed only when they are set, value is printed
        as long as it is given, even when it is falsy.
        """
        parts = [self.__class__.__name__]

        has_location_info = False

        # Add any location information that's available
        if self.file:
            parts.append(f'file={repr(self.file)}')
            has_location_info = True

        if self.keys:
            parts.append(f'keys={repr(self.keys)}')
            has_location_info = True

        if self.value is not _EMPTY:
            parts.append(f'value={repr(self.value)}')
            has_location_info = True

        # Add a separator only if we have location info
        if has_location_info:
            parts.append(f'error={repr(self.msg)}')
            return ',\n    '.join(parts)
        else:
            return f'{self.__class__.__name__},\n    error={repr(self.msg)}'


def read_yaml_mapping(file):
    """
    Read a generation input yaml file as a mapping.

    Unlike read_yaml, malformed yaml is an error. A missing or empty document
    is an empty mapping.

    Args:
        file (str): Source file path

    Returns:
        dict: Parsed mapping

    Raises:
        DefinitionError: Malformed yaml or a non-mapping document root
    """
    try:
        data = atomic_read_bytes(file)
    except FileNotFoundError:
        return {}
    try:
        data = yaml_loads(data)
    except yaml.YAMLError as e:
        raise DefinitionError(f'Invalid yaml: {e}', file=file) from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise DefinitionError(
            f'Expected a mapping at yaml root, got {type(data).__name__}',
            file=file, value=data)
    return data


def read_json_mapping(file):
    """
    Read a generation input json file as a mapping.

    Unlike read_msgspec, malformed json is an error. A missing or empty
    document is an empty mapping.

    Args:
        file (str): Source file path

    Returns:
        dict: Parsed mapping

    Raises:
        DefinitionError: Malformed json or a non-mapping document root
    """
    try:
        data = atomic_read_bytes(file)
    except FileNotFoundError:
        return {}
    if not data:
        return {}
    try:
        data = decode(data)
    except DecodeError as e:
        raise DefinitionError(f'Invalid json: {e}', file=file) from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise DefinitionError(
            f'Expected a mapping at json root, got {type(data).__name__}',
            file=file, value=data)
    return data


class ParseBase:
    # Navs reserved for the alasio entry: their generated files are shared by
    # every mod, so only alasio itself may declare a postprocess on them, see
    # defined_by_alasio(). Immutable on purpose: it describes the nav layout,
    # it is not a switch to turn off.
    alasio_nav = frozenset(('alasio', 'device'))
    # key: nav_name, value: list of registered PostprocessInfo
    dict_args_yaml_postprocess: "dict[str, list[PostprocessInfo]]" = {}
    # key: nav_name, value: list of registered PostprocessInfo
    dict_i18n_json_postprocess: "dict[str, list[PostprocessInfo]]" = {}

    def __init__(self, file):
        """
        Args:
            file (PathStr): Absolute filepath to {nav}.args.yaml
        """
        if not file.endswith('.args.yaml'):
            raise DefinitionError('Arg file must endswith ".args.yaml"', file=file)
        # {nav}.args.yaml
        self.file: PathStr = file

        # nav name
        nav = removesuffix(file.name, '.args.yaml')
        if not validate_nav_name(nav):
            raise DefinitionError(f'Invalid nav name: "{nav}"', file=file)
        self.nav_name = nav

        # {nav}.tasks.yaml
        self.tasks_file = file.with_name(f'{nav}.tasks.yaml')
        # {nav}_i18n.json
        self.i18n_file = file.with_name(f'{nav}_i18n.json')
        # {nav}_config.json
        self.config_file = file.with_name(f'{nav}_config.json')
        # {nav}_model.py
        # Use "_" in file name because python can't import filename with "." easily
        self.model_file = file.with_name(f'{nav}_model.py')

    def read_args_yaml(self):
        """
        Returns:
            dict:
        """
        data = read_yaml_mapping(self.file)
        list_postprocess = ParseBase.dict_args_yaml_postprocess.get(self.nav_name, None)
        if not list_postprocess:
            return data
        for info in list_postprocess:
            try:
                data = self._run_postprocess(info, data)
            except DefinitionError as e:
                if not e.file:
                    e.file = self.file
                raise
        return data

    @staticmethod
    def _run_postprocess(info, data):
        """
        Run a registered postprocess with its cached call plan.

        Args:
            info (PostprocessInfo): Registered postprocess info
            data (dict): Data to process

        Returns:
            Any: Processed data

        Raises:
            DefinitionError: The info has no call plan, a (cls, data) function
                can not resolve its class, or the postprocess returned None
        """
        return run_postprocess(info, data)

    def read_tasks_yaml(self):
        """
        Returns:
            dict:
        """
        return read_yaml_mapping(self.tasks_file)

    def read_i18n_json(self):
        """
        Returns:
            dict:
        """
        data = read_json_mapping(self.i18n_file)
        list_postprocess = ParseBase.dict_i18n_json_postprocess.get(self.nav_name, None)
        if not list_postprocess:
            return data
        for info in list_postprocess:
            try:
                data = self._run_postprocess(info, data)
            except DefinitionError as e:
                if not e.file:
                    e.file = self.i18n_file
                raise
        return data


# Directory of the alasio package: the code that owns the navs shared by every
# mod. It is {ALASIO_ROOT}/alasio, the project layout, not the folder of a file
# of the package, so moving a module does not change it.
ALASIO_PACKAGE = to_posix(env.ALASIO_ROOT.joinpath('alasio'))


def defined_by_alasio(func):
    """
    Check whether a postprocess is declared by alasio itself.

    The navs of the alasio entry are generated once and shared by every mod, so
    only the code of alasio may postprocess them. The decorator runs at import
    time, before any generator or project root exists, so the file a postprocess
    is written in is the ownership it can check.

    Args:
        func (callable): Postprocess function or callable object

    Returns:
        bool: True if the postprocess is declared in the alasio package
    """
    func = getattr(func, '__func__', func)
    code = getattr(func, '__code__', None)
    if code is not None:
        file = to_posix(PathStr.new(code.co_filename).abspath())
        if subpath_to(file, ALASIO_PACKAGE) is not None:
            return True
    # A module imported from a zip file or frozen has no usable path, it still
    # declares the package it belongs to
    module = getattr(func, '__module__', None)
    if not isinstance(module, str):
        module = type(func).__module__
    return module.partition('.')[0] == 'alasio'


def args_yaml_postprocess(nav_name):
    """
    Register a postprocess function for {nav}.args.yaml

    Args:
        nav_name (str): Nav name to register

    The function may accept (data) or (cls, data). A (cls, data) function is
    resolved from its __qualname__ when it is called; if the class can not be
    resolved, generation fails with DefinitionError. The data argument may be
    keyword-only, e.g. ``def process(cls, *, data)``. The signature and the
    __qualname__ shape are validated when the decorator runs, so an invalid
    signature or a qualname that can never resolve to a class fails immediately.

    Examples:
        # As a global function
        @args_yaml_postprocess('main')
        def process_main_args(data):
            return data

        # As a class method
        class MyParser:
            @args_yaml_postprocess('main')
            def process_main_args(cls, data):
                return data
    """

    def decorator(func):
        if nav_name in ParseBase.alasio_nav and not defined_by_alasio(func):
            logger.warning(f'{nav_name} is a protected nav name, postprocess ignored')
            return func

        info = PostprocessInfo.from_function(func)
        if nav_name in ParseBase.dict_args_yaml_postprocess:
            ParseBase.dict_args_yaml_postprocess[nav_name].append(info)
        else:
            ParseBase.dict_args_yaml_postprocess[nav_name] = [info]
        return func

    return decorator


def i18n_json_postprocess(nav_name):
    """
    Register a postprocess function for {nav}_i18n.json

    Args:
        nav_name (str): Nav name to register

    The function may accept (data) or (cls, data). A (cls, data) function is
    resolved from its __qualname__ when it is called; if the class can not be
    resolved, generation fails with DefinitionError. The data argument may be
    keyword-only, e.g. ``def process(cls, *, data)``. The signature and the
    __qualname__ shape are validated when the decorator runs, so an invalid
    signature or a qualname that can never resolve to a class fails immediately.

    Examples:
        # As a global function
        @i18n_json_postprocess('main')
        def process_main_i18n(data):
            return data

        # As a class method
        class MyParser:
            @i18n_json_postprocess('main')
            def process_main_i18n(cls, data):
                return data
    """

    def decorator(func):
        if nav_name in ParseBase.alasio_nav and not defined_by_alasio(func):
            logger.warning(f'{nav_name} is a protected nav name, postprocess ignored')
            return func

        info = PostprocessInfo.from_function(func)
        if nav_name in ParseBase.dict_i18n_json_postprocess:
            ParseBase.dict_i18n_json_postprocess[nav_name].append(info)
        else:
            ParseBase.dict_i18n_json_postprocess[nav_name] = [info]
        return func

    return decorator
