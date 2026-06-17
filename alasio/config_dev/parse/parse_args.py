from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Union

import msgspec
from msgspec import Struct, UNSET, UnsetType

from alasio.backport import to_literal
from alasio.codegen.python import ReprWrapper
from alasio.config_dev.parse.base import DefinitionError
from alasio.config_dev.parse.parse_range import parse_range

"""
The following dicts require manual maintain

Concepts explained:
    1. "dt" is the data type of an "arg".
        It tells frontend which component should be used to display the "arg"
"""
# Convert yaml value to "dt"
# Example:
#   `Enable: true` will be populated to {'dt': 'checkbox'}
TYPE_YAML_TO_DT = {
    bool: 'checkbox',
    int: 'input-int',
    float: 'input-float',
    str: 'input',
    datetime: 'datetime',
}
# Convert "dt" to python typehint
# Example:
#   {'dt': 'input-float'} means this field is a float in python
# Notes:
#   Syntax "tuple[str]" is on py3.9, to be compatible with py3.8, you should use "t.Tuple" instead of "tuple[str]"
#   Annotated is on py3.9, to be compatible with py3.8, you should use "e.Annotated", where "e" is typing_extensions
#   Default value must be static, you should use "t.Tuple" instead of "t.List"
#   All datetime should be timezone aware
TYPE_DT_TO_PYTHON = {
    'static': 'str',
    # text input
    'input': 'str',
    'input-int': 'int',
    'input-float': 'float',
    'textarea': 'str',
    # checkbox
    'checkbox': 'bool',
    # enable is like checkbox but with "true" and "false" as options
    # frontend will display it as a button with i18n support
    'enable': 'bool',
    # select
    'select': 'str',
    'radio': 'str',
    'multi-select': 't.Tuple[str, ...]',
    'multi-radio': 't.Tuple[str, ...]',
    # secondary-select is like select in data structure level
    # frontend will display wth secondary select menu, group -> option
    'secondary-select': 'str',
    # datatime
    'datetime': 'e.Annotated[d.datetime, m.Meta(tz=True)]',
    # filter
    'filter': 't.Tuple[str, ...]',
    'filter-order': 't.Tuple[str, ...]',
}
# Define which "dt" is literal type
# Example:
#   {'dt': 'select'} is a literal in python: Literal["option-A", "option-B"]
TYPE_ARG_LITERAL = {
    'static',
    'select', 'radio', 'multi-select', 'multi-radio', 'secondary-select',
}
# Define which "dt" is a tuple of value
# Example:
#   You define `value: option-A > option-B > option-C`
#   It's default value will be: ("option-A", "option-B", "option-C")
TYPE_ARG_TUPLE = set()
for _dt, _anno in TYPE_DT_TO_PYTHON.items():
    if 't.Tuple' in _anno:
        TYPE_ARG_TUPLE.add(_dt)


def populate_arg(value) -> dict:
    """
    Populate shortened struct definition to dict.
    If type is not given, predict type by default value

    Enable: true
    -> Enable: {'dt': 'checkbox', 'default': true}
    """
    # Only default value
    vtype = type(value)
    dt = TYPE_YAML_TO_DT.get(vtype, None)
    if dt:
        return {'dt': dt, 'value': value}

    # dict
    if vtype is dict:
        try:
            default = value['value']
        except KeyError:
            raise DefinitionError(f'Missing "value" attribute')
        if 'dt' in value:
            return value
        # No pre-defined type, if it has option, predict as select
        if 'option' in value:
            value['dt'] = 'select'
            return value
        # No pre-defined type, predict by default value
        vtype = type(default)
        dt = TYPE_YAML_TO_DT.get(vtype, None)
        if dt:
            value['dt'] = dt
            return value

    raise DefinitionError(f'Cannot predict "dt"')


def _validate_option(option: list, value: Any) -> None:
    if not option:
        raise DefinitionError('"option" is empty')
    if value not in option:
        raise DefinitionError(f'Default value "{value}" is not in "option"')
    counts = Counter(option)
    for _option, _count in counts.items():
        if _count > 1:
            raise DefinitionError(f'"option" has duplicate value "{_option}"')


def preprocess_arg(arg: dict) -> dict:
    """
    Additional pre-process before validating as ArgData

    Args:
        arg: {'dt': 'checkbox', 'default': true}
    """
    try:
        value = arg['value']
    except KeyError:
        raise DefinitionError(f'Missing "value" attribute')
    try:
        dt = arg['dt']
    except KeyError:
        raise DefinitionError(f'Missing "dt" attribute')

    # Parse range
    range_str = arg.get('range', '')
    if range_str:
        try:
            dict_range = parse_range(range_str)
        except ValueError as e:
            raise DefinitionError(f'Cannot parse range "{range_str}", {e}')
        arg.update(dict_range)

    # check if dt valid
    if dt not in TYPE_DT_TO_PYTHON:
        raise DefinitionError(f'Invalid datatype "{dt}"')
    # dt="static" must have option
    if dt == 'static':
        arg['option'] = [value]
    if dt == 'enable':
        arg['option'] = ['true', 'false']
    # Check if literal args have option
    if dt in TYPE_ARG_LITERAL and 'option' not in arg:
        raise DefinitionError(f'datatype "{dt}" must have "option" defined')

    # populate secondary-select
    # convert 'option' and 'option_dict'
    if dt == 'secondary-select':
        if 'option' in arg:
            if 'option_dict' in arg:
                raise DefinitionError('"option" and "option_dict" cannot be defined at the same time')
            else:
                # copy option to option_dict
                arg['option_dict'] = arg['option']
                arg.pop('option', None)
        if 'option_dict' in arg:
            option_dict = arg['option_dict']
            if not option_dict:
                raise DefinitionError('"option_dict" is empty')
            # validate all options
            try:
                option = []
                for _group, _options in option_dict.items():
                    option.extend(_options)
                _validate_option(option, value)
                option = set(option)
                for _group in option_dict:
                    if _group in option:
                        raise DefinitionError(f'"option_dict" group name "{_group}" cannot be in "option"')
            except (AttributeError, TypeError) as e:
                raise DefinitionError(f'"option_dict" must be a dict[str, list] if dt="secondary-select", {e}')
        else:
            raise DefinitionError('dt="secondary-select" must have "option" or "option_dict" defined')
    else:
        arg.pop('option_dict', None)
        # check option
        if dt != 'enable':
            if 'option' in arg:
                _validate_option(arg['option'], value)
    
    vtype = type(value)
    # Timezone default to UTC
    if vtype is datetime and not value.tzinfo:
        arg['value'] = value = value.replace(tzinfo=timezone.utc)
    # Split filters
    if dt in TYPE_ARG_TUPLE and vtype is str:
        arg['value'] = value = tuple(s.strip() for s in value.split('>'))
    # textarea is default to vert layout
    if 'layout' not in arg:
        if dt in ['textarea', 'filter', 'filter-order']:
            arg['layout'] = 'vert'

    return arg


# No need to modify
MSGSPEC_CONSTRAINT = [
    'gt', 'ge', 'lt', 'le', 'multiple_of', 'pattern', 'min_length', 'max_length', 'tz',
    # 'title', 'description', 'examples', 'extra_json_schema', 'extra'
]


class ArgData(Struct, omit_defaults=True):
    # data type
    dt: to_literal(TYPE_DT_TO_PYTHON.keys())
    # default value
    value: Any
    option: Union[list, UnsetType] = UNSET
    # {group: [option1, option2, ...]} in secondary-select
    option_dict: Union[Dict[str, list], UnsetType] = UNSET

    # Data topics events will have `option_i18n` for option translations
    # key is option, value is translation
    # option_i18n: Union[Dict[any, str], UnsetType] = UNSET

    # Dot color if arg is dashboard arg (`dt` startswith "dashboard")
    # Might be #RGB, #RGBA, #RRGGBB, #RRGGBBAA
    # dashboard_color is defined at group level and insert to arg level
    # dashboard_color: Union[str, UnsetType] = UNSET

    # True to hide arg on UI, but still accessible in runtime config
    hide: bool = False
    # True to collapse help text by default, user need to click to expand
    fold_help: bool = False
    # Advanced settings are hide by default, user need to toggle to show advanced settings
    advanced: bool = False
    # Layout style, layout is determined according to `dt` by frontend (see $lib/component/arg/Arg.svelte)
    # Most `dt` are show as horizontal, some are special e.g. dt=textarea is vertical
    # If you need custom layout, set this value
    # 1. "hori" for horizontal layout
    # - {name} {input}
    # - {help} (placeholder)
    # placeholder disappear when component in compact mode
    # 2. "vert" for vertical layout with 3 rows:
    # - {name}
    # - {help}
    # - {input}
    # 3. "vert-rev" for reversed vertical layout with 3 rows:
    # - {name}
    # - {input}
    # - {help}
    # 4. "desc" to show arg like plain text
    # - {name} {input}
    # help is ignored, input follows name instead of right aligned
    layout: Union[Literal['hori', 'vert', 'vert-rev', 'desc'], UnsetType] = UNSET

    # Msgspec constraints
    # https://jcristharif.com/msgspec/constraints.html
    # The annotated value must be greater than gt.
    gt: Union[int, float, UnsetType] = UNSET
    # The annotated value must be greater than or equal to ge.
    ge: Union[int, float, UnsetType] = UNSET
    # The annotated value must be less than lt.
    lt: Union[int, float, UnsetType] = UNSET
    # The annotated value must be less than or equal to le.
    le: Union[int, float, UnsetType] = UNSET
    # The annotated value must be a multiple of multiple_of.
    multiple_of: Union[int, float, UnsetType] = UNSET
    # A regex pattern that the annotated value must match against.
    pattern: Union[str, UnsetType] = UNSET
    # The annotated value must have a length greater than or equal to min_length.
    min_length: Union[int, UnsetType] = UNSET
    # The annotated value must have a length less than or equal to max_length.
    max_length: Union[int, UnsetType] = UNSET
    # Configures the timezone-requirements for annotated datetime/time types.
    tz: Union[bool, UnsetType] = UNSET

    # The title to use for the annotated value when generating a json-schema.
    # title: Union[str, UnsetType] = UNSET
    # The description to use for the annotated value when generating a json-schema.
    # description: Union[str, UnsetType] = UNSET
    # A list of examples to use for the annotated value when generating a json-schema.
    # examples: Union[List, UnsetType] = UNSET
    # A dict of extra fields to set for the annotated value when generating a json-schema.
    # extra_json_schema: Union[Dict, UnsetType] = UNSET
    # Any additional user-defined metadata.
    # extra: Union[Dict, UnsetType] = UNSET

    def merge(self, other: "ArgData") -> "ArgData":
        """
        Merge with another ArgData.
        The other one's values override this one's.

        Args:
            other (ArgData): Another ArgData object

        Returns:
            ArgData: A new ArgData object
        """
        kwargs = {}
        for field in self.__struct_fields__:
            val = getattr(other, field)
            if val is UNSET:
                val = getattr(self, field)

            if isinstance(val, list):
                val = val[:]
            elif isinstance(val, dict):
                val = val.copy()

            kwargs[field] = val

        return msgspec.structs.replace(self, **kwargs)

    @classmethod
    def from_arg_data(cls, data) -> "ArgData":
        try:
            data = populate_arg(data)
            data = preprocess_arg(data)
        except DefinitionError as e:
            e.value = data
            raise
        try:
            arg = msgspec.convert(data, ArgData)
        except msgspec.ValidationError as e:
            ne = DefinitionError(e, value=data)
            raise ne
        return arg

    def to_dict(self) -> dict:
        """
        Returns:

        """
        return msgspec.to_builtins(self)

    def get_meta(self) -> str:
        """
        Generate a Meta representation string from the validation constraints.

        Example:
            data = ArgsData(type='int', value=512, option=[])
            data.ge = 256
            data.le = 8192
            data.gen_meta()
            'Meta(ge=256, le=8192)'
        """
        # Filter out unset constraints and format each as key=value
        params = []
        for key in MSGSPEC_CONSTRAINT:
            value = getattr(self, key, UNSET)
            if value is not UNSET:
                # Use repr() for all values to ensure proper string formatting
                params.append(f'{key}={repr(value)}')

        # Combine all parameters into the Meta string
        if params:
            return f'm.Meta({", ".join(params)})'
        else:
            return ''

    def get_python_type(self) -> str:
        """
        Generate python typehint in string from ArgData

        Examples:
            int
            List[str]
            Literal['zh', 'en', 'ja', 'kr']
        """
        # Convert options to literal
        if self.dt in TYPE_ARG_LITERAL:
            option = ', '.join([repr(o) for o in self.option])
            return f't.Literal[{option}]'
        # Find in pre-defined dict
        return TYPE_DT_TO_PYTHON.get(self.dt, 'Any')

    def get_anno(self) -> str:
        """
        Generate annotation string from ArgData

        Examples:
            Annotated[int, Meta(ge=256, le=8192)]
            List[str]
        """
        python_type = self.get_python_type()
        meta = self.get_meta()
        if meta:
            anno = f"e.Annotated[{python_type}, {meta}]"
        else:
            anno = python_type
        # reuse alias
        if anno == 'e.Annotated[d.datetime, m.Meta(tz=True)]':
            anno = 'a.T_DATETIME'
        if anno == 'e.Annotated[int, m.Meta(ge=0)]':
            anno = 'a.T_INT_GE0'
        if anno == 't.Tuple[str, ...]':
            anno = 'a.T_TUPLE_STR'
        return anno

    def get_value(self):
        """
        Generate default value in python code from ArgData

        Examples:
            d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)
        """
        if type(self.value) is datetime:
            # use DEFAULT_TIME
            return ReprWrapper('a.DEFAULT_TIME')
            # value = repr(self.value)
            # value = value.replace('datetime.datetime', 'd.datetime')
            # value = value.replace('datetime.timezone', 'd.timezone')
            # return ReprWrapper(value)
        # others
        return self.value
