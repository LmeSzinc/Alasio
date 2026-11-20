import copy
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

# Args a dashboard item renders, by dashboard type.
# The item is exactly these args: a hidden one is not written to {nav}_config.json,
# so the view carries no such row and the item would have nothing to show.
# Keep in sync with the branches of frontend PrettyValue.svelte.
DASHBOARD_DISPLAY_ARGS = {
    'Amount': ('Value',),
    'Total': ('Value',),
    'Remain': ('Value',),
    'DynamicTotal': ('Value', 'Total'),
    'SoftTotal': ('Value', 'Total'),
    'Progress': ('Value',),
    'Planner': ('Progress', 'Eta'),
}


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
    # Filled by from_group_data(), a hand built group carries it too
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
        # Validate the raw color before convert: msgspec would report a wrong
        # type as a group level error, which points one level too high
        validate_dashboard_color(data.get('dashboard_color', ''), group=group)
        # build object
        try:
            obj = msgspec.convert(data, cls)
        except msgspec.ValidationError as e:
            e = DefinitionError(e, keys=[group], value=data)
            raise e
        # Keep the args exactly as written in this yaml: the MRO merge in
        # CrossNavGenerator.build_group_mro() overwrites `args` with the whole
        # inheritance chain, while `override_args` is the set of the args a
        # variant overrides, and the model and i18n generation read it directly.
        # Filled here, so a group built by hand has the same data as a parsed
        # one, without the caller having to populate it.
        obj.override_args = obj.args.copy()
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
            DefinitionError: The args of a Planner group are not paired, the
                hard total of a SoftTotal group is missing, a rendered arg is
                hidden, or the range of an arg excludes its default value
        """
        if not self.dashboard:
            return
        self._validate_display_args()
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
        if self.dashboard == 'SoftTotal':
            # after the automatic ge=0, so Total keeps it when the hard total
            # is the only bound the type fills in
            self._populate_soft_total()

    def _validate_display_args(self):
        """
        Validate that the args a dashboard item renders are not hidden

        A hidden arg is left out of {nav}_config.json, and the view the GUI reads
        is built from that file, so the item would receive a group without the
        value it is made of. Hiding Time / ServerUpdate stays allowed: the item
        shows the record time on its own, it does not read those args.

        Raises:
            DefinitionError: A rendered arg of this dashboard type is hidden
        """
        for arg_name in DASHBOARD_DISPLAY_ARGS.get(self.dashboard, ()):
            arg = self.args.get(arg_name, None)
            if arg is None or not arg.hide:
                continue
            raise DefinitionError(
                f'Dashboard arg "{arg_name}" must not be hidden, '
                f'the {self.dashboard} item renders it',
                file=self.parser.file if self.parser else '',
                keys=[self.name, arg_name])

    def _populate_soft_total(self):
        """
        Bound the soft total of a SoftTotal group by the hard total of Value

        The author declares the hard total as the upper bound of Value, e.g.
        `range: "0~25000"`. Total is the soft total the game reports at
        runtime: Value may exceed it, but neither of them may exceed the hard
        total, so Total gets the same upper bound here.

        A Total inherited from the base group is copied before it is bounded:
        the merged `args` of the inheritance chain share the ArgData objects,
        and the base group is shared by every SoftTotal group, each with a hard
        total of its own.

        Raises:
            DefinitionError: Value has no upper bound, Total is missing, or the
                bound the author wrote on Total exceeds the hard total
        """
        if self.name.startswith('Dashboard'):
            # A base group declares the fields only, its hard total is written
            # by every concrete group as the range of Value
            return
        file = self.parser.file if self.parser else ''
        value = self.args.get('Value', None)
        if value is None or value.le is msgspec.UNSET:
            raise DefinitionError(
                'Dashboard SoftTotal group requires an upper bound (le) on "Value", '
                'e.g. range: "0~25000"',
                file=file, keys=[self.name, 'args', 'Value'])
        hard = value.le
        total = self.args.get('Total', None)
        if total is None:
            raise DefinitionError(
                'Dashboard SoftTotal group requires arg "Total"',
                file=file, keys=[self.name, 'args'])
        if 'Total' not in self.override_args:
            # Inherited from the base group: this group's hard total decides
            total = copy.copy(total)
            total.le = hard
            self.args['Total'] = total
        elif total.le is msgspec.UNSET:
            total.le = hard
        elif total.le > hard:
            raise DefinitionError(
                f'Dashboard SoftTotal arg "Total" must not exceed the hard total of "Value" (le={hard})',
                file=file, keys=[self.name, 'args', 'Total'], value=total.le)
        try:
            # the bound of Total is final now, its default was validated against
            # the range it had before
            validate_range_default(total)
        except DefinitionError as e:
            e.file = file
            e.keys = [self.name, 'args', 'Total']
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

        # A parent declared in this file must be defined before the group
        # inheriting it: the model classes are generated in this order, so a
        # later class would reference a name that is not defined yet. A parent
        # of another nav or a dashboard group is not in this file, it is
        # imported or exported instead.
        order = {name: index for index, name in enumerate(output)}
        for index, (group_name, group) in enumerate(output.items()):
            for parent_name in group.parent:
                try:
                    parent_index = order[parent_name]
                except KeyError:
                    continue
                if parent_index > index:
                    raise DefinitionError(
                        f'Group parent must be defined before the group: "{parent_name}"',
                        file=self.file, keys=[group_name, 'parent'], value=parent_name)

        return output
