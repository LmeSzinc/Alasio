import re
from datetime import datetime, timezone
from typing import Dict, Tuple

import msgspec
from msgspec import Struct, UNSET

from alasio.config.entry.utils import validate_task_name
from alasio.config_dev.parse.base import DefinitionError, ParseBase
from alasio.config_dev.parse.parse_args import ArgData
from alasio.ext.cache import cached_property
from alasio.ext.deep import deep_iter_depth1

TYPE_DASHBOARD = [
    # 61549961
    'value',
    # 2437 / 3000
    'total',
    # 68.35%
    'progress',
    # 56.27% <3.6d
    # (0, 0, 637) / (87, 91, 76)
    'planner',
]


class GroupData(Struct):
    name: str
    dashboard: str = ''
    dashboard_color: str = ''
    args: Dict[str, ArgData] = msgspec.field(default_factory=dict)
    # parent group of variant, or empty string if this group is not a variant
    parent: Tuple[str, ...] = msgspec.field(default=tuple)
    mro: Tuple[str, ...] = msgspec.field(default=tuple)
    # override args in variant, will be set in groups_data()
    override_args: Dict[str, ArgData] = msgspec.field(default_factory=dict)

    @classmethod
    def from_group_data(cls, group: str, data: dict):
        if not data:
            data = {}
        if not isinstance(data, dict):
            raise DefinitionError(f'Group data must be a dict', keys=[group], value=data)
        args: dict = data.get('args', {})
        if not isinstance(args, dict):
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
        data['name'] = group
        # convert parent to tuple
        parent = data.get('parent', None)
        if parent:
            if isinstance(parent, (list, tuple)):
                parent = tuple(parent)
            elif isinstance(parent, str):
                parent = (parent,)
            else:
                raise DefinitionError(f'Invalid group parent, expected str or list[str], got {parent}',
                                      keys=[group, 'parent'], value=parent)
        else:
            parent = ()
        data['parent'] = parent
        # internal property, ignore input
        data['mro'] = ()
        # build object
        try:
            obj = msgspec.convert(data, cls)
        except msgspec.ValidationError as e:
            e = DefinitionError(e, keys=[group], value=data)
            raise e
        # validate
        if obj.name in obj.parent:
            raise DefinitionError(f'Group parent cannot include group itself',
                                  keys=[group, 'parent'], value=obj.parent)
        # validate dashboard color
        if obj.dashboard_color:
            if not re.match(r'^#([A-Fa-f0-9]{3}|[A-Fa-f0-9]{4}|[A-Fa-f0-9]{6}|[A-Fa-f0-9]{8})$', obj.dashboard_color):
                raise DefinitionError(f'Invalid dashboard_color, expects #RGB #RGBA #RRGGBB #RRGGBBAA',
                                      keys=[group, 'dashboard_color'], value=obj.dashboard_color)
        return obj

    def __post_init__(self):
        if not self.dashboard:
            return
        if self.dashboard not in TYPE_DASHBOARD:
            raise DefinitionError(
                f'Invalid dashboard datatype: "{self.dashboard}"',
                keys=[self.name, 'dashboard'], value=self.dashboard
            )
        # dashboard group must have "Time"
        if 'Time' in self.args:
            raise DefinitionError(
                f'Dashboard group cannot have custom arg "Time"',
                keys=[self.name, 'args', 'Time'], value=self.args['Time']
            )
        self.args['Time'] = ArgData.from_arg_data(
            datetime(2020, 1, 1, 0, 0, tzinfo=timezone.utc))
        # validate dashboard="total"
        if self.dashboard == 'total':
            have = {'Value', 'Total'}
            missing = have - set(self.args)
            if missing:
                raise DefinitionError(
                    f'Dashboard datatype "{self.dashboard}" must have args {have}, missing {missing}',
                    keys=[self.name, 'args'], value=self.args
                )
        # validate dashboard="value"
        if self.dashboard in ['value', 'progress']:
            if 'Value' not in self.args:
                raise DefinitionError(
                    f'Dashboard datatype "{self.dashboard}" must have arg "Value"',
                    keys=[self.name, 'args'], value=self.args
                )
        # validate dashboard="progress"
        if self.dashboard == 'progress':
            arg = self.args['Value']
            if not isinstance(arg.value, (int, float)):
                raise DefinitionError(
                    f'Dashboard datatype "{self.dashboard}" must have default value in int or float, '
                    f'got "{arg.value}"',
                    keys=[self.name, 'args', 'Value'], value=arg.value
                )
            arg.dt = 'input-float'
        # validate dashboard="planner"
        if self.dashboard == 'planner':
            for gen in ['Progress', 'Eta']:
                if gen in self.args:
                    raise DefinitionError(
                        f'Dashboard datatype "{self.dashboard}" cannot have custom arg "Eta"',
                        keys=[self.name, 'args', 'Eta'], value=self.args['Eta']
                    )
            # add default Progress and Eta at top
            args = dict(
                Progress=ArgData.from_arg_data(0),
                Eta=ArgData.from_arg_data(''),
            )
            args.update(self.args)
            self.args = args
            # check if "xxxValue" is paired with "xxxTotal"
            for arg_name in self.args:
                if arg_name.endswith('Value'):
                    pair_name = f'{arg_name[:-5]}Total'
                    if pair_name not in self.args:
                        raise DefinitionError(
                            f'Dashboard planner arg "{arg_name}" is not paired with "{pair_name}"',
                            keys=[self.name, 'args', arg_name], value=pair_name
                        )
                if arg_name.endswith('Total'):
                    pair_name = f'{arg_name[:-5]}Value'
                    if pair_name not in self.args:
                        raise DefinitionError(
                            f'Dashboard planner arg "{arg_name}" is not paired with "{pair_name}"',
                            keys=[self.name, 'args', arg_name], value=pair_name
                        )
        self._dashboard_populate_ge0()

    def _dashboard_populate_ge0(self):
        """
        Add constraint >=0 to all args
        """
        populate_dt = {'input-int', 'input-float'}
        for arg in self.args.values():
            if arg is None:
                continue
            if arg.dt not in populate_dt:
                continue
            # already having minimum value
            if arg.ge is not UNSET or arg.gt is not UNSET:
                continue
            # 0 <= value <= arg.le
            if arg.le is not UNSET:
                if arg.le >= 0:
                    arg.ge = 0
                else:
                    continue
            # 0 <= value < arg.le
            if arg.lt is not UNSET:
                if arg.lt > 0:
                    arg.ge = 0
                else:
                    continue
            # no other constraint
            arg.ge = 0


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
        for group_name, group in output.items():
            if not group.parent:
                continue

            group.override_args = group.args.copy()
            # note that args here are just override args
            # arg override and variant base will be validated in MRO build

        return output
