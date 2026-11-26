import typing as t

import msgspec as m
import typing_extensions as e

import alasio.config.alasio.group_base as a
from alasio.base.timer import getnow
from alasio.config.alasio.group_proxy import batch_set
from alasio.config.const import DataInconsistent
from alasio.ext.cache import cached_property
from alasio.logger import logger

# if error == 'raise', raise ValueError if value not match
# if error == 'drop', drop current set operation, do nothing
# if error == 'cap', value will be capped to satisfy constraints
DASHBOARD_VALUE_ERROR_MODE = t.Literal['raise', 'drop', 'cap']


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


class DashboardBase(a.GroupBase):
    # Record time
    Time: a.T_DATETIME = a.DEFAULT_TIME
    # ServerUpdate is empty by default, meaning no auto update and is_expired() is always False
    ServerUpdate: t.Literal[''] = ''

    @batch_set
    def is_expired(self):
        """
        Check if record time is expired

        The record is expired if it has not been updated since the last
        server update, e.g. a daily record is expired when the 04:00
        reset has passed.

        Returns:
            bool: True if the record time is before the last server update
        """
        if not self.ServerUpdate:
            return False
        servertime = self.get_servertime()
        update = servertime.get_last_update(self.ServerUpdate)
        return self.Time < update


class DashboardAmount(DashboardBase, dict=True):
    """
    Dashboard an integer value, integer is >= 0
    Typical usage: to Dashboard item amount

    Write this in {nav}.args.yaml
        ItemBlade:
            parent: DashboardAmount
    """
    Value: a.T_INT_GE0 = 0

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

    def pretty(self):
        """
        Return human-readable representation

        Returns:
            str:
        """
        return f'{self.Value}'

    @batch_set
    def set(self, value, error: DASHBOARD_VALUE_ERROR_MODE = 'drop'):
        """
        Args:
            value (int): value to set
            error: 'raise' means raise ValueError if value is out of range
                'drop' means drop current set operation, do nothing
                'cap' means cap value to range

        Returns:
            bool: True if value is set
                False if value is capped or value is dropped
        """
        if error == 'raise':
            cap = cap_value(value, self.meta)
            if cap != value:
                raise ValueError(
                    f'{self.__class__.__name__}.set({value}) is out of range {self.meta.ge}~{self.meta.le}')
            self.Value = value
            self.Time = getnow()
            return True
        elif error == 'drop':
            cap = cap_value(value, self.meta)
            if cap != value:
                logger.warning(
                    f'{self.__class__.__name__}.set({value}) is out of range {self.meta.ge}~{self.meta.le}, '
                    f'drop set operation')
                return False
            self.Value = value
            self.Time = getnow()
            return True
        elif error == 'cap':
            cap = cap_value(value, self.meta)
            if cap != value:
                logger.warning(
                    f'{self.__class__.__name__}.set({value}) is out of range {self.meta.ge}~{self.meta.le}, '
                    f'cap value to {cap}')
            self.Value = cap
            self.Time = getnow()
            return cap == value
        else:
            raise ValueError(f'Invalid error mode: {error}')

    @batch_set
    def add(self, value=1, error: DASHBOARD_VALUE_ERROR_MODE = 'drop'):
        """
        Add value to current value
        """
        return self.set(self.Value + value, error)

    @batch_set
    def sub(self, value=1, error: DASHBOARD_VALUE_ERROR_MODE = 'drop'):
        """
        Subtract value from current value
        """
        return self.set(self.Value - value, error)

    @batch_set
    def reset(self):
        """
        Force reset DashboardAmount to the minimum of range
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
        if self.is_expired():
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

    def pretty(self):
        """
        Return human-readable representation

        Returns:
            str:
        """
        if self.meta.le is not None:
            return f'{self.Value}/{self.meta.le}'
        else:
            return f'{self.Value}/<Unknown_meta_le>'


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

    def pretty(self):
        """
        Return human-readable representation

        Returns:
            str:
        """
        if self.meta.le is not None:
            return f'{self.Value}/{self.meta.le}'
        else:
            return f'{self.Value}/<Unknown_meta_le>'

    @batch_set
    def reset(self):
        """
        Force reset value
        Reset DashboardRemain to the maximum of range
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
    Total: a.T_INT_GE0 = 0

    @cached_property
    def meta_total(self):
        return self.get_meta('Total')

    @batch_set
    def add(self, value=1, error: DASHBOARD_VALUE_ERROR_MODE = 'drop'):
        """
        Add value to current value
        Total remains unchanged
        """
        return self.set(self.Value + value, self.Total, error)

    @batch_set
    def sub(self, value=1, error: DASHBOARD_VALUE_ERROR_MODE = 'drop'):
        """
        Subtract value from current value
        Total remains unchanged
        """
        return self.set(self.Value - value, self.Total, error)

    @batch_set
    def set(self, value, total, error: DASHBOARD_VALUE_ERROR_MODE = 'drop'):
        """
        Args:
            value (int): value to set
            total (int): total value
            error (DASHBOARD_VALUE_ERROR_MODE):
                'raise': raise error if value is out of range
                'drop': drop current set operation, do nothing
                'cap': cap value to range

        Returns:
            bool: True if value is set
                False if value is capped or value is dropped
        """
        if error == 'raise':
            cap = cap_value(value, self.meta)
            if cap != value:
                raise ValueError(
                    f'{self.__class__.__name__}.set({value}, {total}) is out of range {self.meta.ge}~{self.meta.le}')
            cap = cap_value(total, self.meta_total)
            if cap != total:
                raise ValueError(
                    f'{self.__class__.__name__}.set({value}, {total}) is out of range '
                    f'{self.meta_total.ge}~{self.meta_total.le}')
            if value > total:
                raise ValueError(f'{self.__class__.__name__}.set({value}, {total}) is greater than total {total}')
            self.Value = value
            self.Total = total
            self.Time = getnow()
            return True
        elif error == 'drop':
            cap = cap_value(value, self.meta)
            if cap != value:
                logger.warning(
                    f'{self.__class__.__name__}.set({value}, {total}) is out of range '
                    f'{self.meta.ge}~{self.meta.le}, drop set operation')
                return False
            cap = cap_value(total, self.meta_total)
            if cap != total:
                logger.warning(
                    f'{self.__class__.__name__}.set({value}, {total}) is out of range '
                    f'{self.meta_total.ge}~{self.meta_total.le}, drop set operation')
                return False
            if value > total:
                logger.warning(
                    f'{self.__class__.__name__}.set({value}, {total}) is greater than total {total}, '
                    f'drop set operation')
                return False
            self.Value = value
            self.Total = total
            self.Time = getnow()
            return True
        elif error == 'cap':
            cap = cap_value(value, self.meta)
            if cap != value:
                logger.warning(
                    f'{self.__class__.__name__}.set({value}, {total}) is out of range '
                    f'{self.meta.ge}~{self.meta.le}, cap value to {cap}')
            cap_total = cap_value(total, self.meta_total)
            if cap_total != total:
                logger.warning(
                    f'{self.__class__.__name__}.set({value}, {total}) is out of range '
                    f'{self.meta_total.ge}~{self.meta_total.le}, cap total to {cap_total}')
            if cap > cap_total:
                logger.warning(
                    f'{self.__class__.__name__}.set({value}, {total}) is greater than total {total}, '
                    f'cap value to {cap_total}')
                cap = cap_total
            self.Value = cap
            self.Total = cap_total
            self.Time = getnow()
            return cap == value and cap_total == total
        else:
            raise ValueError(f'Invalid error mode: {error}')

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

    def pretty(self):
        """
        Return human-readable representation

        Returns:
            str:
        """
        return f'{self.Value}/{self.Total}'


class DashboardSoftTotal(DashboardAmount):
    """
    Dashboard amount/total, where the total is dynamic and may be exceeded

    Typical usage:
        A weekly reward track holds at most 25000 points, the hard total.
        How many points the account can reach this week is dynamic, e.g. 23145,
        the soft total. Value may pass the soft total, but neither Value nor
        Total may pass the hard total.

    Write this in {nav}.args.yaml
        SimulatedUniverse:
            parent: DashboardSoftTotal
            args:
                # the range of `Value` is the hard total
                Value:
                    value: 0
                    range: "0~25000"
                ServerUpdate: "weekday1-04:00"

    `Total` is the soft total of the runtime record, it gets the same upper
    bound as `Value` when the config is generated, so the hard total is written
    once, as the range of `Value`.
    """
    Total: a.T_INT_GE0 = 0

    @cached_property
    def meta_total(self):
        return self.get_meta('Total')

    @property
    def hard_total(self):
        """
        Hard upper bound of both Value and Total

        Returns:
            int: Upper bound of Value, e.g. 25000

        Raises:
            DataInconsistent: Value has no upper bound
        """
        if self.meta.le is None:
            raise DataInconsistent(f'Class {self.__class__.__name__} does not have le defined')
        return self.meta.le

    @batch_set
    def add(self, value=1, error: DASHBOARD_VALUE_ERROR_MODE = 'drop'):
        """
        Add value to current value
        Soft total remains unchanged
        """
        return self.set(self.Value + value, self.Total, error)

    @batch_set
    def sub(self, value=1, error: DASHBOARD_VALUE_ERROR_MODE = 'drop'):
        """
        Subtract value from current value
        Soft total remains unchanged
        """
        return self.set(self.Value - value, self.Total, error)

    @batch_set
    def set(self, value, total, error: DASHBOARD_VALUE_ERROR_MODE = 'drop'):
        """
        Args:
            value (int): value to set, may exceed the soft total
            total (int): soft total to set
            error (DASHBOARD_VALUE_ERROR_MODE):
                'raise': raise error if value or total is out of range
                'drop': drop current set operation, do nothing
                'cap': cap value and total to range

        Returns:
            bool: True if value is set
                False if value is capped or value is dropped
        """
        meta = self.meta
        meta_total = self.meta_total
        # The hard total bounds both numbers, the range of Total only bounds
        # itself (a type built by hand may not have one)
        hard = self.hard_total
        cap = cap_value(value, meta)
        cap_total = cap_value(total, meta_total)
        if cap_total > hard:
            cap_total = hard

        if error == 'raise':
            if cap != value:
                raise ValueError(
                    f'{self.__class__.__name__}.set({value}, {total}) is out of range {meta.ge}~{hard}')
            if cap_total != total:
                raise ValueError(
                    f'{self.__class__.__name__}.set({value}, {total}) is out of range {meta_total.ge}~{hard}')
            self.Value = value
            self.Total = total
            self.Time = getnow()
            return True
        elif error == 'drop':
            if cap != value:
                logger.warning(
                    f'{self.__class__.__name__}.set({value}, {total}) is out of range '
                    f'{meta.ge}~{hard}, drop set operation')
                return False
            if cap_total != total:
                logger.warning(
                    f'{self.__class__.__name__}.set({value}, {total}) is out of range '
                    f'{meta_total.ge}~{hard}, drop set operation')
                return False
            self.Value = value
            self.Total = total
            self.Time = getnow()
            return True
        elif error == 'cap':
            if cap != value:
                logger.warning(
                    f'{self.__class__.__name__}.set({value}, {total}) is out of range '
                    f'{meta.ge}~{hard}, cap value to {cap}')
            if cap_total != total:
                logger.warning(
                    f'{self.__class__.__name__}.set({value}, {total}) is out of range '
                    f'{meta_total.ge}~{hard}, cap total to {cap_total}')
            self.Value = cap
            self.Total = cap_total
            self.Time = getnow()
            return cap == value and cap_total == total
        else:
            raise ValueError(f'Invalid error mode: {error}')

    def is_full(self):
        """
        Check if value has reached the soft total
        """
        return self.Value >= self.Total

    def pretty(self):
        """
        Return human-readable representation

        Returns:
            str:
        """
        return f'{self.Value}/{self.Total}'


class DashboardProgress(DashboardBase):
    """
    Dashboard a progress in percent
    Typical usage: to Dashboard item progress

    Write this in {nav}.args.yaml
        Progress:
            parent: DashboardProgress
    """
    # A progress is a percentage, so it is bounded on both sides
    Value: e.Annotated[float, m.Meta(ge=0, le=100)] = 0


class DashboardPlanner(DashboardBase):
    """
    Dashboard a progress with an ETA
    Typical usage: to Dashboard item planner

    Write this in {nav}.args.yaml
        Planner:
            parent: DashboardPlanner
            args:
                # Value/Total pairs are defined by the concrete group
                GreenValue:
                    value: 0
                    range: x>=0
                GreenTotal:
                    value: 0
                    range: x>=0
    """
    # A progress is a percentage, so it is bounded on both sides
    Progress: e.Annotated[int, m.Meta(ge=0, le=100)] = 0
    Eta: str = ''
