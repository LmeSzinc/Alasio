from datetime import datetime, timezone
from typing import Any, Union

import msgspec
from msgspec import Struct, UNSET, UnsetType

from alasio.ext.backport import to_literal
from alasio.ext.cache import cached_property
from alasio.ext.codegen import ReprWrapper
from alasio.ext.deep import deep_iter_depth1, deep_set
from alasio.ext.file.yamlfile import read_yaml
from .base import DefinitionError, ParseBase
from .parse_range import parse_range

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
    # text input
    'input': 'str',
    'input-int': 'int',
    'input-float': 'float',
    'textarea': 'str',
    # checkbox
    'checkbox': 'bool',
    # select
    'select': 'str',
    'radio': 'str',
    'multi-select': 't.Tuple[str]',
    'multi-radio': 't.Tuple[str]',
    # datatime
    'datetime': 'e.Annotated[d.datetime, m.Meta(tz=True)]',
    # filter
    'filter': 't.Tuple[str]',
    'filter-order': 't.Tuple[str]',
}
# Define which "dt" is literal type
# Example:
#   {'dt': 'select'} is a literal in python: Literal["option-A", "option-B"]
TYPE_ARG_LITERAL = {
    'select', 'radio', 'multi-select', 'multi-radio',
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
        return {'dt': dt, 'default': value}

    # dict
    if vtype is dict:
        try:
            default = value['default']
        except KeyError:
            raise DefinitionError(f'Missing "default" attribute')
        # Parse range
        if 'range' in value:
            range_str = value['range']
            try:
                dict_range = parse_range(range_str)
            except ValueError as e:
                raise DefinitionError(f'Cannot parse range, {e}')
            value.update(dict_range)
        if 'dt' in value:
            # Having pre-defined type, check if valid
            dt = value['dt']
            if dt not in TYPE_DT_TO_PYTHON:
                raise DefinitionError(f'Invalid datatype {dt}')
            # Check if literal args have option
            if dt in TYPE_ARG_LITERAL and 'option' not in value:
                raise DefinitionError(f'datatype {dt} must have "option" defined')
            return value
        # No pre-defined type, if it has option, predict as select
        if 'option' in value:
            option = value['option']
            if not option:
                raise DefinitionError('"option" is an empty list')
            if default not in option:
                raise DefinitionError('Default value is not in "option"')
            value['dt'] = 'select'
            return value
        # No pre-defined type, predict by default value
        vtype = type(default)
        dt = TYPE_YAML_TO_DT.get(vtype, None)
        if dt:
            value['dt'] = dt
            return value

    raise DefinitionError(f'Cannot predict "dt"')


# No need to modify
MSGSPEC_CONSTRAINT = [
    'gt', 'ge', 'lt', 'le', 'multiple_of', 'pattern', 'min_length', 'max_length', 'tz',
    # 'title', 'description', 'examples', 'extra_json_schema', 'extra'
]


class ArgData(Struct, omit_defaults=True):
    dt: to_literal(TYPE_DT_TO_PYTHON.keys())
    default: Any
    option: Union[list, UnsetType] = UNSET
    advanced: bool = False

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

    def __post_init__(self):
        default = self.default
        vtype = type(default)
        # Timezone default to UTC
        if vtype is datetime and not default.tzinfo:
            self.default = default.replace(tzinfo=timezone.utc)
        # Split filters
        if self.dt in TYPE_ARG_TUPLE and vtype is str:
            self.default = tuple(s.strip() for s in default.split('>'))
        return self

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
            return f"e.Annotated[{python_type}, {meta}]"
        else:
            return python_type

    def get_default(self):
        """
        Generate default value in python code from ArgData

        Examples:
            d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)
        """
        if type(self.default) is datetime:
            default = repr(self.default)
            default = default.replace('datetime.datetime', 'd.datetime')
            default = default.replace('datetime.timezone', 'd.timezone')
            return ReprWrapper(default)
        # others
        return self.default


class ParseArgs(ParseBase):
    @cached_property
    def args_data(self):
        """
        Structured data of {nav}.tasks.yaml

        Returns:
            dict[str, dict[str, ArgData]]:
                key: {group_name}.{arg_name}
                value: ArgsData
        """
        output = {}
        data = read_yaml(self.file)
        for group_name, group_value in deep_iter_depth1(data):
            # Keep empty group in args, so they can be empty group to display on GUI
            output[group_name] = {}
            for arg_name, value in deep_iter_depth1(group_value):
                # Create ArgData object from manual arg definition
                try:
                    value = populate_arg(value)
                except DefinitionError as e:
                    e.file = self.file
                    e.keys = [group_name, arg_name]
                    e.value = value
                    raise
                try:
                    arg = msgspec.convert(value, ArgData)
                except msgspec.ValidationError as e:
                    ne = DefinitionError(e, file=self.file, keys=[group_name, arg_name], value=value)
                    raise ne
                # Set
                deep_set(output, keys=[group_name, arg_name], value=arg)
                # print(msgspec.json.encode(arg))

        return output
