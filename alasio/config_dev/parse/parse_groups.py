from typing import Dict, Set

import msgspec
from msgspec import Struct

from alasio.config.entry.utils import validate_task_name
from alasio.config_dev.parse.base import DefinitionError, ParseBase
from alasio.config_dev.parse.parse_args import ArgData
from alasio.ext.cache import cached_property
from alasio.ext.deep import deep_iter_depth1


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
                arg = ArgData.from_arg_data(value)
            except DefinitionError as e:
                e.keys = [group, arg_name]
                raise
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


class ParseGroups(ParseBase):
    # Convert variant name to base name
    # dict_variant2base: "dict[str, str]"

    @cached_property
    def groups_data(self) -> "dict[str, GroupData]":
        """
        Structured data of {nav}.args.yaml

        Returns:
            key: {GroupName}
            value: GroupData
        """
        output = {}
        data = self.read_args_yaml()
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
