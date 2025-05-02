from datetime import datetime, timezone
from typing import Any, Union

import msgspec
from msgspec import Struct, UNSET, UnsetType, ValidationError

from alasio.config.const import Const
from alasio.ext.backport import to_literal
from alasio.ext.cache import cached_property, del_cached_property
from alasio.ext.codegen import CodeGen
from alasio.ext.deep import deep_get, deep_iter_depth1, deep_iter_depth2, deep_set
from alasio.ext.path import PathStr
from alasio.ext.path.jsonread import read_msgspec, write_json

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
    -> Enable: {'dt': 'checkbox', 'default': true}
    """
    # Only default value
    vtype = type(value)
    dt = TYPE_YAML_TO_ARG.get(vtype, None)
    if dt:
        return {'dt': dt, 'default': value}

    # dict
    if vtype is dict:
        try:
            default = value['default']
        except KeyError:
            raise DefinitionError(f'Missing "default" attribute')
        if 'dt' in value:
            # Having pre-defined type, check if valid
            dt = value['dt']
            if dt not in TYPE_ARG_TO_PYTHON:
                raise DefinitionError(f'Invalid datatype {dt}')
            # Check if literal args have option
            if dt in TYPE_ARG_LITERAL and 'option' not in value:
                raise DefinitionError(f'datatype {dt} must have "option" defined')
            return value
        # No pre-defined type, if it has option, predict as dropdown
        if 'option' in value:
            option = value['option']
            if not option:
                raise DefinitionError('"option" is an empty list')
            if default not in option:
                raise DefinitionError('Default value is not in "option"')
            value['dt'] = 'dropdown'
            return value
        # No pre-defined type, predict by default value
        vtype = type(default)
        dt = TYPE_YAML_TO_ARG.get(vtype, None)
        if dt:
            value['dt'] = dt
            return value

    raise DefinitionError(f'Cannot predict "dt"')


# No need to modify
MSGSPEC_CONSTRAINT = [
    'gt', 'ge', 'lt', 'le', 'multiple_of', 'pattern', 'min_length', 'max_length', 'tz',
    # 'title', 'description', 'examples', 'extra_json_schema', 'extra'
]


class ArgsData(Struct, omit_defaults=True):
    dt: to_literal(TYPE_ARG_TO_PYTHON.keys())
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
        if self.dt in TYPE_ARG_LIST and vtype is str:
            self.default = [s.strip() for s in default.split('>')]
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
        return TYPE_ARG_TO_PYTHON.get(self.dt, 'Any')

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
            file (PathStr): Path to *.args.yaml
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
            # if isinstance(arg.default, datetime):
            #     print(arg.default.tzinfo)

        return output

    @cached_property
    def model_file(self):
        # {aside}.args.yaml -> {aside}_model.py
        # Use "_" in file name because python can't import filename with "." easily
        return self.file.with_name(f'{self.file.rootstem}_model.py')

    @cached_property
    def model(self) -> CodeGen:
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
                    if arg.dt in TYPE_ARG_LIST:
                        gen.Var(arg_name, anno=arg.get_anno(), value=arg.default, auto_multiline=120)
                        continue
                    # Expand literal
                    if arg.dt in TYPE_ARG_LITERAL:
                        anno = arg.get_anno()
                        if len(anno) > 60:
                            # {name}: t.Literal[
                            #     ...
                            # } = ...
                            with gen.tab(prefix=f'{arg_name}: t.Literal[', suffix=f'] = {repr(arg.default)}',
                                         line_ending=',', tab_type='list'):
                                for option in arg.option:
                                    gen.Item(option)
                            continue
                    # inline
                    gen.Anno(arg_name, anno=arg.get_anno(), value=arg.default)
            gen.Empty(2)

        # gen.print()
        return gen

    @cached_property
    def gui_file(self):
        # {aside}.args.yaml -> {aside}_gui.json
        return self.file.with_name(f'{self.file.rootstem}_gui.json')

    @cached_property
    def gui_old(self):
        return read_msgspec(self.gui_file)

    def _update_gui_arg(self, group_name, arg_name, arg: ArgsData):
        old = deep_get(self.gui_old, [group_name, arg_name], default={})
        new = arg.to_dict()
        for lang in Const.GUI_LANGUAGE:
            # name
            key = ['i18n', lang, 'name']
            value = deep_get(old, key, default='')
            if not value:
                value = f'{group_name}.{arg_name}'
            deep_set(new, key, value)
            # help
            key = ['i18n', lang, 'help']
            value = deep_get(old, key, default='')
            deep_set(new, key, value)
        # option
        if arg.option:
            for option in arg.option:
                for lang in Const.GUI_LANGUAGE:
                    key = ['i18n_option', lang, option]
                    value = deep_get(old, key, default=option)
                    deep_set(new, key, value)
        return new

    def _update_gui_info(self, group_name, arg_name):
        old = deep_get(self.gui_old, [group_name, arg_name], default={})
        new = {}
        for lang in Const.GUI_LANGUAGE:
            # name
            key = ['i18n', lang, 'name']
            value = deep_get(old, key, default='')
            if not value:
                value = f'{group_name}.{arg_name}'
            deep_set(new, key, value)
            # help
            key = ['i18n', lang, 'help']
            value = deep_get(old, key, default='')
            deep_set(new, key, value)
        return new

    @cached_property
    def gui(self):
        _ = self.gui_old
        new = {}
        for group_name, arg_data in deep_iter_depth1(self.args):
            # {group}._info
            row = self._update_gui_info(group_name, '_info')
            deep_set(new, [group_name, '_info'], row)
            for arg_name, arg in deep_iter_depth1(arg_data):
                # {group}.{arg}
                row = self._update_gui_arg(group_name, arg_name, arg)
                deep_set(new, [group_name, arg_name], row)
        del_cached_property(self, 'i18n_old')
        return new

    def write(self):
        """
        Generate and write msgspec models
        """
        self.model.write(self.model_file)
        write_json(self.gui_file, self.gui)


if __name__ == '__main__':
    f = PathStr.new(r'E:\ProgramData\Pycharm\Alasio\alasio\config\alasio\opsi.args.yaml')
    self = GenMsgspec(f)
    self.write()
