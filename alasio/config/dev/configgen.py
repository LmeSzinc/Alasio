from datetime import datetime, timezone
from typing import Any, Dict, List, Union

import msgspec
from msgspec import Struct, UNSET, UnsetType, ValidationError

from alasio.base.backport import to_literal
from alasio.base.cache import cached_property
from alasio.base.codegen import CodeGen
from alasio.base.deep import deep_iter_depth1, deep_iter_depth2, deep_set
from alasio.base.path import PathStr

# Requires manual maintain
TYPE_YAML_TO_ARG = {
    bool: 'checkbox',
    int: 'input-int',
    float: 'input-float',
    str: 'input',
    datetime: 'datetime',
}
TYPE_ARG_TO_PYTHON = {
    # text input
    'input': 'str',
    'input-int': 'int',
    'input-float': 'float',
    'textarea': 'str',
    # checkbox
    'checkbox': 'bool',
    # select
    'dropdown': 'str',
    'radio': 'str',
    'multi-dropdown': 't.List[str]',
    'multi-radio': 't.List[str]',
    # datatime
    'datetime': 'datetime',
    # filter
    'filter': 't.List[str]',
    'filter-order': 't.List[str]',
}
TYPE_ARG_LITERAL = {
    'dropdown', 'radio', 'multi-dropdown', 'multi-radio',
}
TYPE_ARG_LIST = {
    'multi-dropdown', 'multi-radio',
    'filter', 'filter-order',
}

_EMPTY = object()


class DefinitionError(Exception):
    def __init__(self, msg, file: str = '', keys: "str | list[str]" = '', value=_EMPTY):
        self.msg = msg
        self.file = file
        self.keys = keys
        self.value = value

    def __str__(self):
        parts = [self.__class__.__name__]

        has_location_info = False

        # Add any location information that's available
        if self.file:
            parts.append(f'file={self.file}')
            has_location_info = True

        if self.keys:
            parts.append(f'keys={self.keys}')
            has_location_info = True

        if self.value is not _EMPTY:
            parts.append(f'value={self.value}')
            has_location_info = True

        # Add a separator only if we have location info
        if has_location_info:
            parts.append(f'error={self.msg}')
            return ',\n    '.join(parts)
        else:
            return f'{self.__class__.__name__},\n    error={self.msg}'


def populate_yaml(value) -> dict:
    """
    Populate shortened struct definition to dict.
    If type is not given, predict type by default value

    Enable: true
    -> Enable: {'type': 'checkbox', 'value': true}
    """
    # Only default value
    vtype = type(value)
    typ = TYPE_YAML_TO_ARG.get(vtype, None)
    if typ:
        return {'type': typ, 'value': value}

    # dict
    if vtype is dict:
        try:
            v = value['value']
        except KeyError:
            raise DefinitionError(f'Missing "value" attribute')
        if 'type' in value:
            # Having pre-defined type, check if valid
            typ = value['type']
            if typ not in TYPE_ARG_TO_PYTHON:
                raise DefinitionError(f'Invalid type {typ}')
            # Check if literal args have option
            if typ in TYPE_ARG_LITERAL and 'option' not in value:
                raise DefinitionError(f'type {typ} must have "option"')
            return value
        # No pre-defined type, if it has option, predict as dropdown
        if 'option' in value:
            option = value['option']
            if not option:
                raise DefinitionError('"option" is an empty list')
            if v not in option:
                raise DefinitionError('Default value is not in "option"')
            value['type'] = 'dropdown'
            return value
        # No pre-defined type, predict by default value
        vtype = type(v)
        typ = TYPE_YAML_TO_ARG.get(vtype, None)
        if typ:
            value['type'] = typ
            return value

    raise DefinitionError(f'Cannot predict "type"')


# No need to modify
MSGSPEC_CONSTRAINT = [
    'gt', 'ge', 'lt', 'le', 'multiple_of', 'pattern',
    'min_length', 'max_length', 'tz', 'title', 'description',
    'examples', 'extra_json_schema', 'extra'
]


class ArgsData(Struct, omit_defaults=True):
    type: to_literal(TYPE_ARG_TO_PYTHON.keys())
    value: Any
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
    title: Union[str, UnsetType] = UNSET
    # The description to use for the annotated value when generating a json-schema.
    description: Union[str, UnsetType] = UNSET
    # A list of examples to use for the annotated value when generating a json-schema.
    examples: Union[List, UnsetType] = UNSET
    # A dict of extra fields to set for the annotated value when generating a json-schema.
    extra_json_schema: Union[Dict, UnsetType] = UNSET
    # Any additional user-defined metadata.
    extra: Union[Dict, UnsetType] = UNSET

    def __post_init__(self):
        v = self.value
        vtype = type(v)
        # Timezone default to UTC
        if vtype is datetime and not v.tzinfo:
            self.value = v.replace(tzinfo=timezone.utc)
        # Split filters
        if self.type in TYPE_ARG_LIST and vtype is str:
            self.value = [s.strip() for s in self.value.split('>')]
        return self

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
        if self.type in TYPE_ARG_LITERAL:
            option = ', '.join([repr(o) for o in self.option])
            return f't.Literal[{option}]'
        # Find in pre-defined dict
        return TYPE_ARG_TO_PYTHON.get(self.type, 'Any')

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
            return f"t.Annotated[{python_type}, {meta}]"
        else:
            return python_type


class GenMsgspec:
    def __init__(self, file):
        """
        Args:
            file (PathStr): Path to *.group.yaml
        """
        self.file = file

    @cached_property
    def args(self):
        """
        Returns:
            dict[str, dict[str, ArgsData]]:
                <group_name>:
                    <arg_name>:
                        ArgsData
        """
        output = {}
        content = self.file.atomic_read_bytes()
        data = msgspec.yaml.decode(content)
        for group_name, arg_name, value in deep_iter_depth2(data):
            # Create ArgsData object from manual arg definition
            try:
                value = populate_yaml(value)
            except DefinitionError as e:
                e.file = self.file
                e.keys = [group_name, arg_name]
                e.value = value
                raise
            try:
                arg = msgspec.convert(value, ArgsData)
            except ValidationError as e:
                ne = DefinitionError(e, file=self.file, keys=[group_name, arg_name], value=value)
                raise ne

            deep_set(output, keys=[group_name, arg_name], value=arg)
            # print(msgspec.json.encode(arg))
            # if isinstance(arg.value, datetime):
            #     print(arg.value.tzinfo)

        return output

    @cached_property
    def codegen(self) -> CodeGen:
        """
        Generate msgspec models
        """
        gen = CodeGen()
        gen.RawImport("""
        import typing as t

        import msgspec as m
        """)
        gen.Empty()
        gen.CommentCodeGen('alasio.config.dev.configgen')
        for group_name, arg_data in deep_iter_depth1(self.args):
            with gen.Class(group_name, inherit='m.Struct, omit_defaults=True'):
                for arg_name, arg in deep_iter_depth1(arg_data):
                    # Expand list
                    if arg.type in TYPE_ARG_LIST:
                        gen.Var(arg_name, anno=arg.get_anno(), value=arg.value, auto_multiline=120)
                        continue
                    # Expand literal
                    if arg.type in TYPE_ARG_LITERAL:
                        anno = arg.get_anno()
                        if len(anno) > 60:
                            # {name}: t.Literal[
                            #     ...
                            # } = ...
                            with gen.tab(prefix=f'{arg_name}: t.Literal[', suffix=f'] = {repr(arg.value)}',
                                         line_ending=',', tab_type='list'):
                                for option in arg.option:
                                    gen.Item(option)
                            continue
                    # inline
                    gen.Anno(arg_name, anno=arg.get_anno(), value=arg.value)
            gen.Empty(2)

        # gen.print()
        return gen

    def write(self):
        """
        Generate and write msgspec models
        """
        gen = self.codegen
        file = self.file.with_multisuffix('model.py')
        gen.write(file)
