from alasio.config.entry.utils import validate_nav_name
from alasio.ext.backport import removesuffix
from alasio.ext.file.msgspecfile import read_msgspec
from alasio.ext.file.yamlfile import read_yaml
from alasio.ext.path import PathStr

_EMPTY = object()


class DefinitionError(Exception):
    def __init__(self, msg, file='', keys='', value=_EMPTY):
        """
        Args:
            msg (str | Exception): Error message
            file (str):
            keys (list[str] | str):
            value (Any):
        """
        if isinstance(msg, Exception):
            msg = f'{msg.__class__.__name__}({msg})'
        self.msg = msg
        self.file = file
        self.keys = keys
        self.value = value

    def __str__(self):
        """
        Show error as:

        {self.__class__.__name__},
            error={self.msg}
            file={self.file}
            keys={self.keys}
            value={self.value}
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


class ParseBase:
    # a set of protected alasio nav name
    alasio_nav = {'alasio', 'device'}
    # key: nav_name, value: list of postprocess functions that receives {nav}.args.yaml data and returns it
    dict_args_yaml_postprocess: "dict[str, list[callable]]" = {}
    # key: nav_name, value: list of postprocess functions that receives {nav}.i18n.json data and returns it
    dict_i18n_json_postprocess: "dict[str, list[callable]]" = {}

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
        data = read_yaml(self.file)
        if self.nav_name in ParseBase.alasio_nav:
            return data
        list_postprocess = ParseBase.dict_args_yaml_postprocess.get(self.nav_name, None)
        if not list_postprocess:
            return data
        for postprocess in list_postprocess:
            data = self._run_postprocess(postprocess, data)
        return data

    @staticmethod
    def _run_postprocess(postprocess, data):
        """
        Run a postprocess function, automatically adapt to (data) or (cls, data)
        """
        # 1. Handle bound methods (classmethod or bound instance method)
        if hasattr(postprocess, '__self__'):
            return postprocess(data)

        # 2. Determine argument count and resolve class if needed
        co_argcount = 1
        cls = None
        try:
            # Check positional argument count
            co_argcount = postprocess.__code__.co_argcount
        except AttributeError:
            # Not a function/method or doesn't have __code__
            pass
        if co_argcount == 2:
            # Try to resolve class from qualname and globals for dynamic loading support
            qualname = getattr(postprocess, '__qualname__', '')
            parts = qualname.split('.')
            if len(parts) >= 2:
                # Resolve class by traversing globals and attributes
                container = postprocess.__globals__
                for part in parts[:-1]:
                    if isinstance(container, dict):
                        container = container.get(part)
                    else:
                        container = getattr(container, part, None)
                    if container is None:
                        break
                if isinstance(container, type):
                    cls = container

        # 3. Call postprocess outside of try-except to avoid catching its errors
        if co_argcount == 2:
            return postprocess(cls, data)
        else:
            return postprocess(data)

    def read_tasks_yaml(self):
        """
        Returns:
            dict:
        """
        return read_yaml(self.tasks_file)

    def read_i18n_json(self):
        """
        Returns:
            dict:
        """
        data = read_msgspec(self.i18n_file)
        if self.nav_name in ParseBase.alasio_nav:
            return data
        list_postprocess = ParseBase.dict_i18n_json_postprocess.get(self.nav_name, None)
        if not list_postprocess:
            return data
        for postprocess in list_postprocess:
            data = self._run_postprocess(postprocess, data)
        return data


def args_yaml_postprocess(nav_name):
    """
    Register a postprocess function for {nav}.args.yaml

    Args:
        nav_name (str): Nav name to register

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
        if nav_name in ParseBase.alasio_nav:
            print(f'Warning: {nav_name} is a protected nav name, postprocess ignored')
            return func

        try:
            co_argcount = func.__code__.co_argcount
        except AttributeError:
            co_argcount = 1

        if co_argcount not in (1, 2):
            print(f'Warning: Postprocess function "{func.__name__}" must have 1 or 2 arguments, '
                  f'got {co_argcount}. Ignoring.')
            return func

        if nav_name in ParseBase.dict_args_yaml_postprocess:
            ParseBase.dict_args_yaml_postprocess[nav_name].append(func)
        else:
            ParseBase.dict_args_yaml_postprocess[nav_name] = [func]
        return func

    return decorator


def i18n_json_postprocess(nav_name):
    """
    Register a postprocess function for {nav}_i18n.json

    Args:
        nav_name (str): Nav name to register

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
        if nav_name in ParseBase.alasio_nav:
            print(f'Warning: {nav_name} is a protected nav name, postprocess ignored')
            return func

        try:
            co_argcount = func.__code__.co_argcount
        except AttributeError:
            co_argcount = 1

        if co_argcount not in (1, 2):
            print(f'Warning: Postprocess function "{func.__name__}" must have 1 or 2 arguments, '
                  f'got {co_argcount}. Ignoring.')
            return func

        if nav_name in ParseBase.dict_i18n_json_postprocess:
            ParseBase.dict_i18n_json_postprocess[nav_name].append(func)
        else:
            ParseBase.dict_i18n_json_postprocess[nav_name] = [func]
        return func

    return decorator
