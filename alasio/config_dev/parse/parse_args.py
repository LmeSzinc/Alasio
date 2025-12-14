from datetime import datetime, timezone
from typing import Any, Dict, Literal, Set, Union

import msgspec
from msgspec import Struct, UNSET, UnsetType

from alasio.config.entry.utils import validate_task_name
from alasio.config_dev.parse.base import DefinitionError, ParseBase
from alasio.config_dev.parse.parse_range import parse_range
from alasio.ext.backport import to_literal
from alasio.ext.cache import cached_property
from alasio.ext.codegen import ReprWrapper
from alasio.ext.deep import deep_iter_depth1, deep_set
from alasio.ext.file.yamlfile import read_yaml

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
    'multi-select': 't.Tuple[str, ...]',
    'multi-radio': 't.Tuple[str, ...]',
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
        return {'dt': dt, 'value': value}

    # dict
    if vtype is dict:
        try:
            default = value['value']
        except KeyError:
            raise DefinitionError(f'Missing "value" attribute')
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
    # data type
    dt: to_literal(TYPE_DT_TO_PYTHON.keys())
    # default value
    value: Any
    option: Union[list, UnsetType] = UNSET

    # Data topics events will have `option_i18n` for option translations
    # key is option, value is translation
    # option_i18n: Union[Dict[any, str], UnsetType] = UNSET

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
    layout: Literal['hori', 'vert', 'vert-rev'] = UNSET

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
        value = self.value
        vtype = type(value)
        # Timezone default to UTC
        if vtype is datetime and not value.tzinfo:
            self.value = value.replace(tzinfo=timezone.utc)
        # Split filters
        if self.dt in TYPE_ARG_TUPLE and vtype is str:
            self.value = tuple(s.strip() for s in value.split('>'))
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

    def get_value(self):
        """
        Generate default value in python code from ArgData

        Examples:
            d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)
        """
        if type(self.value) is datetime:
            value = repr(self.value)
            value = value.replace('datetime.datetime', 'd.datetime')
            value = value.replace('datetime.timezone', 'd.timezone')
            return ReprWrapper(value)
        # others
        return self.value


class GroupData(Struct):
    name: str
    args: Dict[str, ArgData] = msgspec.field(default_factory=dict)
    # base group of variant, or empty string if this group is not a variant
    base: str = ''
    # override args in variant, will be set in groups_data()
    override_args: Set[str] = msgspec.field(default_factory=set)

    @classmethod
    def from_group_data(cls, group: str, data: dict):
        if not data:
            data = {}
        if type(data) != dict:
            raise DefinitionError(f'Group data must be a dict', keys=[group], value=data)
        args = data.get('args', {})
        if type(args) != dict:
            raise DefinitionError(f'Group args must be a dict', keys=[group, 'args'], value=data)
        new = {}
        for arg_name, value in args.items():
            # check arg_name
            if not validate_task_name(arg_name):
                raise DefinitionError(
                    f'Arg name format invalid: "{arg_name}"',
                    keys=[group], value=arg_name
                )
            try:
                value = populate_arg(value)
            except DefinitionError as e:
                e.keys = [group, arg_name]
                e.value = value
                raise
            try:
                arg = msgspec.convert(value, ArgData)
            except msgspec.ValidationError as e:
                ne = DefinitionError(e, keys=[group, arg_name], value=value)
                raise ne
            new[arg_name] = arg
        data['args'] = new
        # build object
        data['name'] = group
        try:
            obj = msgspec.convert(data, cls)
        except msgspec.ValidationError as e:
            e = DefinitionError(e, keys=[group], value=data)
            raise e
        # validate
        if obj.base == obj.name:
            raise DefinitionError(f'Group variant base cannot be self', keys=[group, 'base'], value=obj.base)
        return obj


class ParseArgs(ParseBase):
    # Convert variant name to base name
    # dict_variant2base: "dict[str, str]"

    @cached_property
    def groups_data(self) -> "dict[str, GroupData]":
        """
        Structured data of {nav}.tasks.yaml

        Returns:
            key: {GroupName}
            value: GroupData
        """
        output = {}
        data = read_yaml(self.file)
        for group_name, group_value in deep_iter_depth1(data):
            # check group_name
            if not validate_task_name(group_name):
                raise DefinitionError(
                    f'Group name format invalid: "{group_name}"',
                    file=self.file, keys=[], value=group_name)
            # allow empty group to be an inforef group
            # if not group_value:
            #     pass
            # Keep empty group in args, so they can be empty group to display on GUI
            try:
                group = GroupData.from_group_data(group_name, group_value)
            except DefinitionError as e:
                e.file = self.file
                raise
            # Set
            output[group_name] = group

        # validate group variants
        dict_variant2base = {}
        for group_name, group in output.items():
            if not group.base:
                continue
            try:
                base = output[group.base]
            except KeyError:
                raise DefinitionError(
                    f'No such base group: "{group.base}"',
                    file=self.file, keys=[group_name, 'base'], value=group.base)
            dict_variant2base[group_name] = group.base
            if group.base in dict_variant2base:
                raise DefinitionError(
                    f'Group variant cannot be nested',
                    file=self.file, keys=[group.name, 'base'], value=group.base)
            # validate arg override
            for arg_name in group.args:
                if arg_name not in base.args:
                    raise DefinitionError(
                        f'Cannot add new arg in group variant, maybe add it to base group and static in variant?',
                        file=self.file, keys=[group_name, 'args'], value=arg_name)

            group.override_args = set(group.args)
            args = base.args.copy()
            args.update(group.args)
            group.args = args

        return output
