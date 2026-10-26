from typing import Dict, Optional, Tuple

import msgspec
from msgspec import Struct

from alasio.config.entry.utils import validate_task_name
from alasio.config_dev.format.validate_color import validate_dashboard_color
from alasio.config_dev.parse.base import DefinitionError, ParseBase
from alasio.config_dev.parse.parse_arg_utils import has_range_constraint, validate_range_default
from alasio.config_dev.parse.parse_args import TYPE_ARG_NUMERIC, ArgData
from alasio.ext.cache import cached_property
from alasio.ext.deep import deep_iter_depth1


class GroupData(Struct, forbid_unknown_fields=True, dict=True):
    """
    One group of {nav}.args.yaml

    `dict=True` is for the parser back reference only: the parser holds the file
    paths of the nav and forms a reference cycle with its groups, so it is kept
    out of the msgspec fields (see the `parser` property). A GroupData therefore
    encodes to json / msgpack without the parser, which is the only member that
    msgspec can not handle.
    """

    name: str
    args: Dict[str, ArgData] = msgspec.field(default_factory=dict)
    # parent group of variant, or empty tuple if this group is not a variant
    parent: Tuple[str, ...] = msgspec.field(default_factory=tuple)
    # color of the dot on frontend dashboard component, #RGB #RGBA #RRGGBB #RRGGBBAA
    dashboard_color: str = ''

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

    @property
    def parser(self) -> "Optional[ParseGroups]":
        """
        Parser that owns this group, None when the group is built by hand.

        Runtime state only, not a msgspec field: it is kept in `__dict__` so a
        GroupData stays serializable. Use it for the file paths and the per nav
        state, never as part of the group definition.

        Returns:
            ParseGroups | None: Owning parser
        """
        return self.__dict__.get('parser', None)

    @parser.setter
    def parser(self, value):
        self.__dict__['parser'] = value

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
        if parent is None:
            parent = ()
        elif isinstance(parent, (list, tuple)):
            parent = tuple(parent)
        elif isinstance(parent, str):
            parent = (parent,) if parent else ()
        else:
            raise DefinitionError(f'Invalid group parent, expected str or list[str], got {parent}',
                                  keys=[group, 'parent'], value=parent)
        # validate parent names
        seen = set()
        for parent_name in parent:
            if not validate_task_name(parent_name):
                raise DefinitionError(
                    f'Group parent format invalid: "{parent_name}"',
                    keys=[group, 'parent'], value=parent_name)
            if parent_name == group:
                raise DefinitionError(f'Group parent cannot include group itself',
                                      keys=[group, 'parent'], value=parent)
            if parent_name in seen:
                raise DefinitionError(
                    f'Duplicate group parent: "{parent_name}"',
                    keys=[group, 'parent'], value=parent)
            seen.add(parent_name)
        data['parent'] = parent
        # internal property, ignore input
        data.pop('mro', None)
        data.pop('override_args', None)
        data.pop('dashboard', None)
        data.pop('parser', None)
        # build object
        try:
            obj = msgspec.convert(data, cls)
        except msgspec.ValidationError as e:
            e = DefinitionError(e, keys=[group], value=data)
            raise e
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
        automatic population. A numeric arg without any range constraint gets
        ge=0: a dashboard value is a count or a percentage, a negative value is
        meaningless. An arg with an explicit constraint keeps exactly what the
        author wrote.

        The default value is validated against the final range afterwards, see
        validate_range_default(): msgspec does not re-validate a default value
        against the constraint of its own field, so an out of range default
        would be generated into the model and only fail when a config is loaded.

        Raises:
            DefinitionError: The args of a Planner group are not paired, or the
                range of an arg excludes its default value
        """
        if not self.dashboard:
            return
        if self.dashboard == 'Planner':
            self._validate_planner_pairs()
        for arg_name, arg in self.args.items():
            if arg is None:
                continue
            if arg.dt not in TYPE_ARG_NUMERIC:
                continue
            if not has_range_constraint(arg):
                arg.ge = 0
            try:
                validate_range_default(arg)
            except DefinitionError as e:
                # same keys as the arg errors of from_group_data()
                e.file = self.parser.file if self.parser else ''
                e.keys = [self.name, arg_name]
                raise

    def _validate_planner_pairs(self):
        """
        Validate that Planner Value/Total pairs are complete and adjacent.

        A Value opens a pair and only its matching Total may follow, so the
        pair order is the definition order of the Value args. Other args are
        allowed between pairs, but not between a Value and its Total.
        """
        file = self.parser.file if self.parser else ''
        # Value arg waiting for its Total, empty when the next arg may be a Value
        current_value = ''
        for arg_name in self.args:
            if arg_name.endswith('Value'):
                if current_value:
                    expected = f'{current_value[:-5]}Total'
                    raise DefinitionError(
                        f'Dashboard planner arg "{current_value}" must be followed by "{expected}", '
                        f'got "{arg_name}"',
                        file=file, keys=[self.name, 'args', current_value], value=arg_name
                    )
                current_value = arg_name
            elif arg_name.endswith('Total'):
                if not current_value:
                    value_name = f'{arg_name[:-5]}Value'
                    raise DefinitionError(
                        f'Dashboard planner arg "{arg_name}" is not paired with "{value_name}"',
                        file=file, keys=[self.name, 'args', arg_name], value=value_name
                    )
                expected = f'{current_value[:-5]}Total'
                if arg_name != expected:
                    raise DefinitionError(
                        f'Dashboard planner arg "{current_value}" must be followed by "{expected}", '
                        f'got "{arg_name}"',
                        file=file, keys=[self.name, 'args', current_value], value=arg_name
                    )
                current_value = ''
            elif current_value:
                expected = f'{current_value[:-5]}Total'
                raise DefinitionError(
                    f'Dashboard planner arg "{current_value}" must be followed by "{expected}", '
                    f'got "{arg_name}"',
                    file=file, keys=[self.name, 'args', current_value], value=arg_name
                )
        if current_value:
            expected = f'{current_value[:-5]}Total'
            raise DefinitionError(
                f'Dashboard planner arg "{current_value}" is not paired with "{expected}"',
                file=file, keys=[self.name, 'args', current_value], value=expected
            )


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
