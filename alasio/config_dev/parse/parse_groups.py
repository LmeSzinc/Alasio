from typing import Dict, Optional, Tuple

import msgspec
from msgspec import UNSET, Struct

from alasio.config.entry.utils import validate_task_name
from alasio.config_dev.format.validate_color import validate_dashboard_color
from alasio.config_dev.parse.base import DefinitionError, ParseBase
from alasio.config_dev.parse.parse_args import ArgData
from alasio.ext.cache import cached_property
from alasio.ext.deep import deep_iter_depth1


class GroupData(Struct):
    name: str
    args: Dict[str, ArgData] = msgspec.field(default_factory=dict)
    # parent group of variant, or empty tuple if this group is not a variant
    parent: Tuple[str, ...] = msgspec.field(default_factory=tuple)
    # color of the dot on frontend dashboard component, #RGB #RGBA #RRGGBB #RRGGBBAA
    dashboard_color: str = ''

    parser: "Optional[ParseGroups]" = None
    # MRO inherit chain like python class
    mro: Tuple[str, ...] = msgspec.field(default_factory=tuple)
    # Args defined in the yaml of this group, copied before MRO merge
    # - for a variant group: the override args
    # - for a base group: all args, kept so variants can resolve their i18ngroup
    override_args: Dict[str, ArgData] = msgspec.field(default_factory=dict)
    # override i18n of this group
    # - if True and this group has a parent, generate its own _info in i18n
    # - if False and this group has a parent, reuse parent's _info from MRO chain
    # - dashboard groups always have its _info
    override_i18n: bool = False
    # dashboard group name without "Dashboard" prefix, e.g. "Amount", "Count"
    # real value will be set in MRO build
    # if parent or any ancestor is dashboard group, `dashboard` will be set
    dashboard: str = ''

    @classmethod
    def from_group_data(cls, group: str, data: dict):
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise DefinitionError(f'Group data must be a dict', keys=[group], value=data)
        # Do not mutate the input yaml data, it may be reused by the caller
        data = dict(data)
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
        data.pop('mro', None)
        data.pop('override_args', None)
        data.pop('dashboard', None)
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
        try:
            validate_dashboard_color(obj.dashboard_color)
        except DefinitionError as e:
            e.keys = [group, 'dashboard_color']
            raise
        return obj

    def populate_dashboard(self):
        """
        Populate the args of a dashboard group.

        Called by CrossNavGenerator.build_group_mro() after the dashboard type
        is resolved, because assigning a msgspec field does not trigger any
        automatic population. Numeric args of a dashboard group default to >= 0.
        """
        if not self.dashboard:
            return
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
            # 0 <= value < arg.lt
            if arg.lt is not UNSET:
                if arg.lt > 0:
                    arg.ge = 0
                else:
                    continue
            # no other constraint
            arg.ge = 0


class ParseGroups(ParseBase):

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
                group.parser = self
            except DefinitionError as e:
                e.file = self.file
                raise
            # Set
            output[group_name] = group

        # validate group variants
        for group_name, group in output.items():
            # note that args here are just override args
            # arg override and variant base will be validated in MRO build
            group.override_args = group.args.copy()

        return output
