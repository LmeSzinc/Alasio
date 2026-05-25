import datetime as d
import typing as t

import msgspec as m
import typing_extensions as e

from alasio.base.timer import getnow
from alasio.config.const import DataInconsistent
from alasio.config.group_proxy import batch_set
from alasio.ext.cache import cached_property
from alasio.ext.msgspec_error.parse_anno import get_msgspec_annotation

if t.TYPE_CHECKING:
    from alasio.base.servertime import ServerTime

DEFAULT_TIME = d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)
T_DATETIME = e.Annotated[d.datetime, m.Meta(tz=True)]
T_INT_GE0 = e.Annotated[int, m.Meta(ge=0)]
T_TUPLE_STR = t.Tuple[str, ...]


def parse_literal_arg(arg):
    """
    Parse literal arg like:
    'normal' -> normal

    Args:
        arg (str):
    
    Returns:
        str:
    """
    if arg.startswith("'") and arg.endswith("'"):
        return arg[1:-1]
    if arg.startswith('"') and arg.endswith('"'):
        return arg[1:-1]
    return arg


def parse_literal_string(anno):
    """
    Parse literal string like:
    t.Literal[('normal', 'hard')] -> ['normal', 'hard']
    t.Literal['normal', 'hard'] -> ['normal', 'hard']
    Literal['normal', 'hard'] -> ['normal', 'hard']
    typing.Literal['normal', 'hard'] -> ['normal', 'hard']

    Args:
        anno (str):

    Returns:
        list[str]:
    
    Raises:
        ValueError: If anno is not a literal
    """
    if not anno.startswith(('Literal', 't.Literal', 'typing.Literal')):
        raise ValueError('Annotation is not a literal')
    args = anno.partition('[')[2].rpartition(']')[0]
    if args.startswith('(') and args.endswith(')'):
        args = args[1:-1]
    return [parse_literal_arg(arg.strip()) for arg in args.split(',')]


class GroupBase(m.Struct, omit_defaults=True, dict=True):
    @classmethod
    def get_meta(cls, attr) -> m.Meta:
        """
        Args:
            attr (str):
        """
        try:
            anno = get_msgspec_annotation(cls, attr)
        except AttributeError:
            raise DataInconsistent(f'Missing annotation for {cls}.{attr}')
        try:
            annotated_args = anno.__metadata__
        except AttributeError:
            raise DataInconsistent(f'Missing anno.__metadata__ for {cls}.{attr}')
        for arg in annotated_args:
            if isinstance(arg, m.Meta):
                return arg
        raise DataInconsistent(f'Missing msgspec.Meta for {cls}.{attr}')

    @classmethod
    def get_default(cls, attr):
        """
        Args:
            attr (str):
        """
        try:
            return getattr(cls, attr)
        except AttributeError:
            raise DataInconsistent(f'Missing default for {cls}.{attr}')

    @classmethod
    def get_option(cls, attr):
        """
        Get options if attr is a literal

        Args:
            attr (str):
        """
        try:
            anno = get_msgspec_annotation(cls, attr)
        except AttributeError:
            raise DataInconsistent(f'Missing annotation for {cls}.{attr}')
        # t.Literal[('normal', 'hard')]
        # usually because of `from __future__ import annotations`
        if isinstance(anno, str):
            try:
                return parse_literal_string(anno)
            except ValueError:
                raise DataInconsistent(f'Arg is not a literal: {cls}.{attr}')
        # typing.Literal object
        literal_args = getattr(anno, '__args__', None)
        if literal_args is not None:
            return list(literal_args)
        raise DataInconsistent(f'Arg is not a literal: {cls}.{attr}')

    @batch_set
    def get_servertime(self) -> "ServerTime":
        """
        To access config.servertime in group model, so we can use group.is_expired()
        This is something magic that:
        GroupProxy.__getattr__ has method redirection: `attr.__func__(self, ...)`
        The `self` of @batch_set decorated method is GroupProxy object, not DashboardBase(msgspec.Struct) object
        So when using `self.servertime`, it looks up GroupProxy.servertime, and returns GroupProxy._config.servertime

        Notes to keep this magic, every method and its parent caller which access to servertime
        - must be decorated by @batch_set
        - must be used with config, must not be used individually
        """
        try:
            return self.servertime
        except AttributeError:
            raise DataInconsistent(f'Missing servertime injection. '
                                   f'To access servertime, group model must be wrapped in GroupProxy')


def cap_value(value, meta):
    """
    Cap value to meta range
    Note that only ge and le are supported

    Args:
        value (int): value to cap
        meta (msgspec.Meta): msgspec.Meta object

    Returns:
        value (int): capped value
    """
    if meta.ge is not None and value < meta.ge:
        value = meta.ge
    if meta.le is not None and value > meta.le:
        value = meta.le
    return value


class DashboardBase(GroupBase):
    # Record time
    Time: T_DATETIME = DEFAULT_TIME
    # ServerUpdate is empty by default, meaning no auto update and is_expired() is always False
    ServerUpdate: t.Literal[''] = ''

    @batch_set
    def is_expired(self):
        """
        Check if record time is expired
        """
        if not self.ServerUpdate:
            return False
        servertime = self.get_servertime()
        update = servertime.get_next_update(self.ServerUpdate)
        return self.Time > update


class DashboardAmount(DashboardBase, dict=True):
    """
    Dashboard an integer value, integer is >= 0
    Typical usage: to Dashboard item amount

    Write this in {nav}.args.yaml
        ItemBlade:
            parent: DashboardAmount
    """
    Value: T_INT_GE0 = 0

    @cached_property
    def meta(self):
        return self.get_meta('Value')

    def is_empty(self):
        """
        Check if value is empty
        """
        if self.meta.ge is None:
            raise DataInconsistent(f'Class {self.__class__.__name__} does not have ge defined')
        return self.Value <= self.meta.ge

    def is_full(self):
        """
        Check if value is full
        """
        if self.meta.le is None:
            raise DataInconsistent(f'Class {self.__class__.__name__} does not have le defined')
        return self.Value >= self.meta.le

    @batch_set
    def set(self, value, raise_error=False):
        """
        Args:
            value (int): value to set
            raise_error (bool):
                True: raise error if value is out of range
                False: cap value to range

        Returns:
            bool: True if value is set
                False if value is capped to range and raise_error is False
        """
        if raise_error:
            cap = cap_value(value, self.meta)
            if cap != value:
                raise ValueError(f'Value {value} is out of range {self.meta.ge}~{self.meta.le}')
            self.Value = value
            self.Time = getnow()
            return True
        else:
            cap = cap_value(value, self.meta)
            self.Value = cap
            self.Time = getnow()
            return cap == value

    @batch_set
    def add(self, value=1, raise_error=False):
        """
        Add value to current value
        """
        return self.set(self.Value + value, raise_error)

    @batch_set
    def sub(self, value=1, raise_error=False):
        """
        Subtract value from current value
        """
        return self.set(self.Value - value, raise_error)

    @batch_set
    def reset(self):
        """
        Reset value
        Count object will reset to the minimum of range
        """
        meta = self.meta
        if meta.ge is None:
            raise DataInconsistent(f'Class {self.__class__.__name__} does not have ge defined')
        self.Value = meta.ge
        self.Time = getnow()

    @batch_set
    def update(self):
        """
        Reset value if record is expired
        """
        if self.is_expired:
            self.reset()


class DashboardTotal(DashboardAmount):
    """
    Typical usage:
        There is 14000 points of reward every week, user already gain 7500 points.
        Count resets at 4:00 AM every Monday.

    Write this in {nav}.args.yaml
        SimulatedUniverse:
            parent: DashboardTotal
            args:
                # must configure the range of `Value`
                Value:
                    value: 0
                    range: "0~14000"
                ServerUpdate: "weekday1-04:00"
    """
    pass


class DashboardRemain(DashboardAmount):
    """
    Dashboard an integer value, integer is >= 0
    Typical usage:
        A campaign can run 3 times a day, user already run it 1 time.
        Remain resets at 00:00 AM every day.

    Write this in {nav}.args.yaml
        HardCampaign:
            parent: DashboardRemain
            args:
                # must configure the range of `Value`
                Value:
                    value: 0
                    range: "0~3"
                ServerUpdate: "00:00"
    """

    @batch_set
    def reset(self):
        """
        Force reset value
        Remain object will reset to the maximum of range
        """
        meta = self.meta
        if meta.le is None:
            raise DataInconsistent(f'Class {self.__class__.__name__} does not have le defined')
        self.Value = meta.le
        self.Time = getnow()


class DashboardDynamicTotal(DashboardAmount):
    """
    Dashboard amount/total
    Typical usage:
        There is 8000 points of reward every week, user already gain 3600 points.
        Count resets at 4:00 AM every Monday.
        Total reward is dynamic, and increases depends on user progress.
        Late game accounts can gain 14000 points every week.

    Write this in {nav}.args.yaml
        SimulatedUniverse:
            parent: DashboardDynamicTotal
            args:
                # no need to configure `Value`
                ServerUpdate: "weekday1-04:00"
    """
    Total: T_INT_GE0 = 0

    @cached_property
    def meta_total(self):
        return self.get_meta('Total')

    @batch_set
    def set(self, value, total, raise_error=False):
        """
        Args:
            value (int): value to set
            total (int): total value
            raise_error (bool):
                True: raise error if value is out of range
                False: cap value to range

        Returns:
            bool: True if value is set
                False if value is capped to range and raise_error is False
        """
        if raise_error:
            cap = cap_value(value, self.meta)
            if cap != value:
                raise ValueError(f'Value {value} is out of range {self.meta.ge}~{self.meta.le}')
            cap = cap_value(total, self.meta_total)
            if cap != total:
                raise ValueError(f'Total {total} is out of range {self.meta_total.ge}~{self.meta_total.le}')
            if value > total:
                raise ValueError(f'Value {value} is greater than total {total}')
            self.Value = value
            self.Total = total
            self.Time = getnow()
            return True
        else:
            cap = cap_value(value, self.meta)
            cap_total = cap_value(total, self.meta_total)
            if cap > cap_total:
                cap = cap_total
            self.Value = cap
            self.Total = cap_total
            self.Time = getnow()
            return cap == value and cap_total == total

    def is_empty(self):
        """
        Check if value is empty
        """
        if self.meta.ge is None:
            raise DataInconsistent(f'Class {self.__class__.__name__} does not have ge defined')
        return self.Value <= self.meta.ge

    def is_full(self):
        """
        Check if value is full
        """
        return self.Value >= self.Total
