import datetime as d

import msgspec as m
import pytest
import typing_extensions as e

from alasio.config.alasio.store_model import (
    DashboardAmount,
    DashboardDynamicTotal,
    DashboardRemain,
    DashboardTotal,
    cap_value,
)
from alasio.config.const import DataInconsistent
from alasio.logger import logger


# ---- Tests: cap_value ----

class TestCapValue:
    """Test suite for cap_value function"""

    def test_cap_below_ge(self):
        """Test capping value below ge limit"""
        meta = m.Meta(ge=0)
        result = cap_value(-5, meta)
        assert result == 0

    def test_cap_above_le(self):
        """Test capping value above le limit"""
        meta = m.Meta(le=100)
        result = cap_value(200, meta)
        assert result == 100

    def test_cap_within_range(self):
        """Test value within range is unchanged"""
        meta = m.Meta(ge=0, le=100)
        result = cap_value(50, meta)
        assert result == 50

    def test_cap_at_ge_boundary(self):
        """Test value at ge boundary is unchanged"""
        meta = m.Meta(ge=0, le=100)
        result = cap_value(0, meta)
        assert result == 0

    def test_cap_at_le_boundary(self):
        """Test value at le boundary is unchanged"""
        meta = m.Meta(ge=0, le=100)
        result = cap_value(100, meta)
        assert result == 100

    def test_cap_no_ge_only_le(self):
        """Test cap with only le limit, value below le unchanged"""
        meta = m.Meta(le=50)
        result = cap_value(49, meta)
        assert result == 49

    def test_cap_no_le_only_ge(self):
        """Test cap with only ge limit, value above ge unchanged"""
        meta = m.Meta(ge=10)
        result = cap_value(15, meta)
        assert result == 15

    def test_cap_no_limits(self):
        """Test cap with no ge or le limits"""
        meta = m.Meta()
        result = cap_value(100, meta)
        assert result == 100

    def test_cap_negative_range_below(self):
        """Test cap with negative range, value below ge"""
        meta = m.Meta(ge=-10, le=-1)
        result = cap_value(-20, meta)
        assert result == -10

    def test_cap_negative_range_above(self):
        """Test cap with negative range, value above le"""
        meta = m.Meta(ge=-10, le=-1)
        result = cap_value(0, meta)
        assert result == -1


# ---- Test helper structs ----

class _DashboardAmountLimited(DashboardAmount):
    """
    Test helper: DashboardAmount with ge=0, le=100
    """
    Value: e.Annotated[int, m.Meta(ge=0, le=100)] = 0


class _DashboardAmountOnlyGe(DashboardAmount):
    """
    Test helper: DashboardAmount with ge=0 only (no le — same as T_INT_GE0)
    """
    Value: e.Annotated[int, m.Meta(ge=0)] = 0


class _DashboardRemainLimited(DashboardRemain):
    """
    Test helper: DashboardRemain with ge=0, le=3
    """
    Value: e.Annotated[int, m.Meta(ge=0, le=3)] = 0


class _DashboardDynamicTotalLimited(DashboardDynamicTotal):
    """
    Test helper: DashboardDynamicTotal with ge=0, le=200 for Value, ge=0 for Total
    """
    Value: e.Annotated[int, m.Meta(ge=0, le=200)] = 0
    Total: e.Annotated[int, m.Meta(ge=0)] = 0


class _DashboardDynamicTotalBothCapped(DashboardDynamicTotal):
    """
    Test helper: DashboardDynamicTotal with ge=0, le=200 for both Value and Total
    """
    Value: e.Annotated[int, m.Meta(ge=0, le=200)] = 0
    Total: e.Annotated[int, m.Meta(ge=0, le=200)] = 0


class _DashboardDynamicTotalNoGe(DashboardDynamicTotal):
    """
    Test helper: DashboardDynamicTotal with no ge on Value (le=200 only)
    """
    Value: e.Annotated[int, m.Meta(le=200)] = 0
    Total: e.Annotated[int, m.Meta(ge=0)] = 0


# ---- Tests: DashboardBase.is_expired ----

class TestDashboardBaseIsExpired:
    """Test suite for DashboardBase.is_expired"""

    def test_default_server_update_never_expired(self):
        """is_expired returns False when ServerUpdate is ''"""
        obj = _DashboardAmountLimited(Value=50)
        assert obj.is_expired() is False

    def test_old_time_still_not_expired(self):
        """Even with old Time, is_expired is False because ServerUpdate is ''"""
        default_time = d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)
        obj = _DashboardAmountLimited(Value=50, Time=default_time)
        assert obj.is_expired() is False


# ---- Tests: DashboardAmount.meta ----

class TestDashboardAmountMeta:
    """Test suite for DashboardAmount.meta"""

    def test_meta_returns_meta_for_value(self):
        """meta returns the msgspec.Meta annotation for Value field"""
        obj = _DashboardAmountLimited(Value=50)
        assert isinstance(obj.meta, m.Meta)
        assert obj.meta.ge == 0
        assert obj.meta.le == 100

    def test_meta_only_ge(self):
        """meta works when only ge is defined"""
        obj = _DashboardAmountOnlyGe(Value=5)
        assert obj.meta.ge == 0
        assert obj.meta.le is None

    def test_meta_static(self):
        """
        meta is a cached_property — verify it returns the same object
        on repeated access
        """
        obj = _DashboardAmountLimited(Value=50)
        meta_1 = obj.meta
        meta_2 = obj.meta
        assert meta_1 is meta_2


# ---- Tests: DashboardAmount.is_empty ----

class TestDashboardAmountIsEmpty:
    """Test suite for DashboardAmount.is_empty"""

    def test_value_at_ge_is_empty(self):
        """Value equal to meta.ge is considered empty"""
        obj = _DashboardAmountLimited(Value=0)
        assert obj.is_empty() is True

    def test_value_below_ge_is_empty(self):
        """Value below meta.ge is considered empty"""
        obj = _DashboardAmountLimited(Value=0)
        meta = obj.meta
        # with ge=0, Value=0 is already at boundary; capped set won't go below
        assert obj.Value <= meta.ge
        assert obj.is_empty() is True

    def test_value_above_ge_is_not_empty(self):
        """Value above meta.ge is not empty"""
        obj = _DashboardAmountLimited(Value=1)
        assert obj.is_empty() is False

    def test_value_mid_range_is_not_empty(self):
        """Value in the middle of range is not empty"""
        obj = _DashboardAmountLimited(Value=50)
        assert obj.is_empty() is False

    def test_value_at_le_is_not_empty(self):
        """Value at meta.le is not empty"""
        obj = _DashboardAmountLimited(Value=100)
        assert obj.is_empty() is False

    def test_is_empty_raises_when_ge_is_none(self):
        """is_empty raises DataInconsistent if meta.ge is None"""

        # A subclass without ge on Value
        class _NoGeAmount(DashboardAmount):
            Value: e.Annotated[int, m.Meta(le=100)] = 0

        obj = _NoGeAmount(Value=50)
        with pytest.raises(DataInconsistent, match='does not have ge defined'):
            obj.is_empty()


# ---- Tests: DashboardAmount.is_full ----

class TestDashboardAmountIsFull:
    """Test suite for DashboardAmount.is_full"""

    def test_value_at_le_is_full(self):
        """Value equal to meta.le is considered full"""
        obj = _DashboardAmountLimited(Value=100)
        assert obj.is_full() is True

    def test_value_above_le_is_full(self):
        """Value above meta.le is considered full (shouldn't happen normally)"""
        obj = _DashboardAmountLimited(Value=100)
        meta = obj.meta
        assert obj.Value >= meta.le
        assert obj.is_full() is True

    def test_value_below_le_is_not_full(self):
        """Value below meta.le is not full"""
        obj = _DashboardAmountLimited(Value=99)
        assert obj.is_full() is False

    def test_value_mid_range_is_not_full(self):
        """Value in the middle of range is not full"""
        obj = _DashboardAmountLimited(Value=50)
        assert obj.is_full() is False

    def test_value_at_ge_is_not_full(self):
        """Value at meta.ge (minimum) is not full"""
        obj = _DashboardAmountLimited(Value=0)
        assert obj.is_full() is False

    def test_is_full_raises_when_le_is_none(self):
        """is_full raises DataInconsistent if meta.le is None"""
        obj = _DashboardAmountOnlyGe(Value=5)
        with pytest.raises(DataInconsistent, match='does not have le defined'):
            obj.is_full()


# ---- Tests: DashboardAmount.set ----

class TestDashboardAmountSet:
    """Test suite for DashboardAmount.set"""

    def test_set_within_range(self):
        """set with a value within range succeeds and returns True"""
        obj = _DashboardAmountLimited(Value=0)
        result = obj.set(50)
        assert result is True
        assert obj.Value == 50
        # Time should be updated
        assert obj.Time > d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)

    def test_set_at_ge(self):
        """set with value at ge boundary works"""
        obj = _DashboardAmountLimited(Value=50)
        result = obj.set(0)
        assert result is True
        assert obj.Value == 0

    def test_set_at_le(self):
        """set with value at le boundary works"""
        obj = _DashboardAmountLimited(Value=0)
        result = obj.set(100)
        assert result is True
        assert obj.Value == 100

    def test_set_above_le_cap(self):
        """set with value above le caps to le and returns False"""
        obj = _DashboardAmountLimited(Value=0)
        result = obj.set(200, error='cap')
        assert result is False
        assert obj.Value == 100

    def test_set_below_ge_cap(self):
        """set with value below ge caps to ge and returns False"""
        obj = _DashboardAmountLimited(Value=50)
        result = obj.set(-10, error='cap')
        assert result is False
        assert obj.Value == 0

    def test_set_above_le_drop(self):
        """set with value above le and error='drop' does nothing and logs warning"""
        obj = _DashboardAmountLimited(Value=0)
        with logger.mock_capture_writer() as capture:
            result = obj.set(200, error='drop')
        assert result is False
        assert obj.Value == 0
        assert capture.backend.any_contains('out of range')

    def test_set_below_ge_drop(self):
        """set with value below ge and error='drop' does nothing and logs warning"""
        obj = _DashboardAmountLimited(Value=50)
        with logger.mock_capture_writer() as capture:
            result = obj.set(-10, error='drop')
        assert result is False
        assert obj.Value == 50
        assert capture.backend.any_contains('out of range')

    def test_set_within_range_drop(self):
        """set with value within range and error='drop' succeeds"""
        obj = _DashboardAmountLimited(Value=0)
        with logger.mock_capture_writer() as capture:
            result = obj.set(50, error='drop')
        assert result is True
        assert obj.Value == 50
        assert len(capture.backend.logs) == 0

    def test_set_above_le_with_raise(self):
        """set with value above le and error='raise' raises ValueError"""
        obj = _DashboardAmountLimited(Value=0)
        with pytest.raises(ValueError, match='out of range'):
            obj.set(200, error='raise')

    def test_set_below_ge_with_raise(self):
        """set with value below ge and error='raise' raises ValueError"""
        obj = _DashboardAmountLimited(Value=50)
        with pytest.raises(ValueError, match='out of range'):
            obj.set(-10, error='raise')

    def test_set_within_range_with_raise(self):
        """set with value within range and error='raise' returns True"""
        obj = _DashboardAmountLimited(Value=0)
        result = obj.set(50, error='raise')
        assert result is True
        assert obj.Value == 50

    def test_set_only_ge_no_le_within(self):
        """set works when only ge is defined (no le cap)"""
        obj = _DashboardAmountOnlyGe(Value=0)
        result = obj.set(9999)
        assert result is True
        assert obj.Value == 9999

    def test_set_only_ge_below(self):
        """set below ge on only-ge struct caps to ge"""
        obj = _DashboardAmountOnlyGe(Value=50)
        result = obj.set(-5, error='cap')
        assert result is False
        assert obj.Value == 0


# ---- Tests: DashboardAmount.add ----

class TestDashboardAmountAdd:
    """Test suite for DashboardAmount.add"""

    def test_add_default_increment(self):
        """add with default value=1 increments by 1"""
        obj = _DashboardAmountLimited(Value=0)
        result = obj.add()
        assert result is True
        assert obj.Value == 1

    def test_add_custom_increment(self):
        """add with custom value adds correctly"""
        obj = _DashboardAmountLimited(Value=10)
        result = obj.add(5)
        assert result is True
        assert obj.Value == 15

    def test_add_to_le(self):
        """add to the le boundary works"""
        obj = _DashboardAmountLimited(Value=99)
        result = obj.add()
        assert result is True
        assert obj.Value == 100

    def test_add_above_le_caps(self):
        """add above le caps to le and returns False"""
        obj = _DashboardAmountLimited(Value=99)
        result = obj.add(5, error='cap')
        assert result is False
        assert obj.Value == 100

    def test_add_above_le_drop(self):
        """add above le with error='drop' does nothing and logs warning"""
        obj = _DashboardAmountLimited(Value=99)
        with logger.mock_capture_writer() as capture:
            result = obj.add(5, error='drop')
        assert result is False
        assert obj.Value == 99
        assert capture.backend.any_contains('out of range')

    def test_add_above_le_raises(self):
        """add above le with error='raise' raises ValueError"""
        obj = _DashboardAmountLimited(Value=99)
        with pytest.raises(ValueError, match='out of range'):
            obj.add(5, error='raise')


# ---- Tests: DashboardAmount.sub ----

class TestDashboardAmountSub:
    """Test suite for DashboardAmount.sub"""

    def test_sub_default_decrement(self):
        """sub with default value=1 decrements by 1"""
        obj = _DashboardAmountLimited(Value=50)
        result = obj.sub()
        assert result is True
        assert obj.Value == 49

    def test_sub_custom_decrement(self):
        """sub with custom value subtracts correctly"""
        obj = _DashboardAmountLimited(Value=50)
        result = obj.sub(10)
        assert result is True
        assert obj.Value == 40

    def test_sub_to_ge(self):
        """sub to the ge boundary works"""
        obj = _DashboardAmountLimited(Value=1)
        result = obj.sub()
        assert result is True
        assert obj.Value == 0

    def test_sub_below_ge_caps(self):
        """sub below ge caps to ge and returns False"""
        obj = _DashboardAmountLimited(Value=1)
        result = obj.sub(5, error='cap')
        assert result is False
        assert obj.Value == 0

    def test_sub_below_ge_drop(self):
        """sub below ge with error='drop' does nothing and logs warning"""
        obj = _DashboardAmountLimited(Value=1)
        with logger.mock_capture_writer() as capture:
            result = obj.sub(5, error='drop')
        assert result is False
        assert obj.Value == 1
        assert capture.backend.any_contains('out of range')

    def test_sub_below_ge_raises(self):
        """sub below ge with error='raise' raises ValueError"""
        obj = _DashboardAmountLimited(Value=1)
        with pytest.raises(ValueError, match='out of range'):
            obj.sub(5, error='raise')


# ---- Tests: DashboardAmount.reset ----

class TestDashboardAmountReset:
    """Test suite for DashboardAmount.reset"""

    def test_reset_sets_to_ge(self):
        """reset sets Value to meta.ge"""
        obj = _DashboardAmountLimited(Value=75)
        obj.reset()
        assert obj.Value == 0

    def test_reset_updates_time(self):
        """reset updates Time"""
        obj = _DashboardAmountLimited(Value=75)
        old_time = obj.Time
        obj.reset()
        assert obj.Time > old_time

    def test_reset_when_already_at_ge(self):
        """reset when already at ge is idempotent"""
        obj = _DashboardAmountLimited(Value=0)
        obj.reset()
        assert obj.Value == 0

    def test_reset_raises_when_ge_is_none(self):
        """reset raises DataInconsistent if meta.ge is None"""

        class _NoGeAmount(DashboardAmount):
            Value: e.Annotated[int, m.Meta(le=100)] = 0

        obj = _NoGeAmount(Value=50)
        with pytest.raises(DataInconsistent, match='does not have ge defined'):
            obj.reset()


# ---- Tests: DashboardAmount.update ----

class TestDashboardAmountUpdate:
    """Test suite for DashboardAmount.update"""

    def test_update_does_not_reset_when_not_expired(self):
        """
        With ServerUpdate='' (default), is_expired() returns False without
        needing GroupProxy. update() should NOT call reset().
        """
        obj = _DashboardAmountLimited(Value=75)
        obj.update()
        # ServerUpdate is '' so is_expired() returns False, no reset
        assert obj.Value == 75

    def test_update_does_not_change_time(self):
        """update does not change Time when not expired"""
        obj = _DashboardAmountLimited(Value=75)
        old_time = obj.Time
        obj.update()
        assert obj.Time == old_time

    def test_update_at_ge_is_idempotent(self):
        """update when already at ge does nothing (not expired)"""
        obj = _DashboardAmountLimited(Value=0)
        obj.update()
        assert obj.Value == 0


# ---- Tests: DashboardTotal ----

class TestDashboardTotal:
    """Test suite for DashboardTotal (inherits DashboardAmount, no overrides)"""

    def test_dashboard_total_is_dashboard_amount(self):
        """DashboardTotal is a subclass of DashboardAmount"""
        assert issubclass(DashboardTotal, DashboardAmount)

    def test_set_works(self):
        """DashboardTotal.set behaves identically to DashboardAmount.set"""

        class _TestTotal(DashboardTotal):
            Value: e.Annotated[int, m.Meta(ge=0, le=100)] = 0

        obj = _TestTotal(Value=0)
        obj.set(75)
        assert obj.Value == 75

    def test_set_caps(self):
        """DashboardTotal caps the same way as DashboardAmount"""

        class _TestTotal(DashboardTotal):
            Value: e.Annotated[int, m.Meta(ge=0, le=100)] = 0

        obj = _TestTotal(Value=0)
        result = obj.set(200, error='cap')
        assert result is False
        assert obj.Value == 100

    def test_set_drop(self):
        """DashboardTotal with error='drop' does nothing and logs warning"""

        class _TestTotal(DashboardTotal):
            Value: e.Annotated[int, m.Meta(ge=0, le=100)] = 0

        obj = _TestTotal(Value=0)
        with logger.mock_capture_writer() as capture:
            result = obj.set(200, error='drop')
        assert result is False
        assert obj.Value == 0
        assert capture.backend.any_contains('out of range')

    def test_reset_resets_to_ge(self):
        """DashboardTotal.reset sets Value to meta.ge (inherited behavior)"""

        class _TestTotal(DashboardTotal):
            Value: e.Annotated[int, m.Meta(ge=0, le=100)] = 0

        obj = _TestTotal(Value=75)
        obj.reset()
        assert obj.Value == 0

    def test_is_empty_works(self):
        """DashboardTotal.is_empty behaves identically to DashboardAmount"""

        class _TestTotal(DashboardTotal):
            Value: e.Annotated[int, m.Meta(ge=0, le=100)] = 0

        obj = _TestTotal(Value=0)
        assert obj.is_empty() is True
        obj = _TestTotal(Value=1)
        assert obj.is_empty() is False

    def test_is_full_works(self):
        """DashboardTotal.is_full behaves identically to DashboardAmount"""

        class _TestTotal(DashboardTotal):
            Value: e.Annotated[int, m.Meta(ge=0, le=100)] = 0

        obj = _TestTotal(Value=100)
        assert obj.is_full() is True
        obj = _TestTotal(Value=99)
        assert obj.is_full() is False

    def test_add_and_sub_work(self):
        """DashboardTotal.add and sub work like DashboardAmount"""

        class _TestTotal(DashboardTotal):
            Value: e.Annotated[int, m.Meta(ge=0, le=100)] = 0

        obj = _TestTotal(Value=50)
        obj.add(10)
        assert obj.Value == 60
        obj.sub(20)
        assert obj.Value == 40


# ---- Tests: DashboardRemain ----

class TestDashboardRemainReset:
    """Test suite for DashboardRemain.reset (overrides parent to reset to le)"""

    def test_reset_sets_to_le(self):
        """
        DashboardRemain.reset sets Value to meta.le,
        unlike DashboardAmount.reset which sets to meta.ge
        """
        obj = _DashboardRemainLimited(Value=1)
        obj.reset()
        assert obj.Value == 3

    def test_reset_updates_time(self):
        """reset updates Time"""
        obj = _DashboardRemainLimited(Value=1)
        old_time = obj.Time
        obj.reset()
        assert obj.Time > old_time

    def test_reset_when_already_at_le(self):
        """reset when already at le is idempotent"""
        obj = _DashboardRemainLimited(Value=3)
        obj.reset()
        assert obj.Value == 3

    def test_reset_raises_when_le_is_none(self):
        """DashboardRemain.reset raises DataInconsistent if meta.le is None"""

        class _NoLeRemain(DashboardRemain):
            Value: e.Annotated[int, m.Meta(ge=0)] = 0

        obj = _NoLeRemain(Value=5)
        with pytest.raises(DataInconsistent, match='does not have le defined'):
            obj.reset()

    def test_is_full_works(self):
        """DashboardRemain inherits is_full from DashboardAmount"""
        obj = _DashboardRemainLimited(Value=3)
        assert obj.is_full() is True
        obj = _DashboardRemainLimited(Value=2)
        assert obj.is_full() is False

    def test_is_empty_works(self):
        """DashboardRemain inherits is_empty from DashboardAmount"""
        obj = _DashboardRemainLimited(Value=0)
        assert obj.is_empty() is True
        obj = _DashboardRemainLimited(Value=1)
        assert obj.is_empty() is False


# ---- Tests: DashboardDynamicTotal ----

class TestDashboardDynamicTotalSet:
    """Test suite for DashboardDynamicTotal.set"""

    def test_set_both_within_range(self):
        """set with value and total both within range succeeds"""
        obj = _DashboardDynamicTotalLimited(Value=0, Total=0)
        result = obj.set(30, 100)
        assert result is True
        assert obj.Value == 30
        assert obj.Total == 100

    def test_set_value_at_total(self):
        """set with value == total works"""
        obj = _DashboardDynamicTotalLimited(Value=0, Total=0)
        result = obj.set(100, 100)
        assert result is True
        assert obj.Value == 100
        assert obj.Total == 100

    def test_set_value_zero(self):
        """set with value=0 works"""
        obj = _DashboardDynamicTotalLimited(Value=50, Total=100)
        result = obj.set(0, 100)
        assert result is True
        assert obj.Value == 0

    def test_set_value_exceeds_total_caps(self):
        """set caps value to total when value > total"""
        obj = _DashboardDynamicTotalLimited(Value=0, Total=0)
        result = obj.set(150, 100, error='cap')
        assert result is False
        assert obj.Value == 100
        assert obj.Total == 100

    def test_set_value_exceeds_total_raises(self):
        """set raises ValueError when value > total and error='raise'"""
        obj = _DashboardDynamicTotalLimited(Value=0, Total=0)
        with pytest.raises(ValueError, match='greater than total'):
            obj.set(150, 100, error='raise')

    def test_set_value_above_le_caps(self):
        """set caps value to le when value > le"""
        obj = _DashboardDynamicTotalLimited(Value=0, Total=50)
        result = obj.set(250, 50, error='cap')
        assert result is False
        assert obj.Value == 50
        assert obj.Total == 50

    def test_set_total_above_le_caps_total_only(self):
        """set caps total to le when total > le, value within range stays"""
        obj = _DashboardDynamicTotalBothCapped(Value=0, Total=0)
        result = obj.set(150, 300, error='cap')
        assert result is False
        assert obj.Value == 150
        assert obj.Total == 200

    def test_set_both_above_le_caps(self):
        """set caps both value and total when both exceed le"""
        obj = _DashboardDynamicTotalBothCapped(Value=0, Total=0)
        result = obj.set(300, 300, error='cap')
        assert result is False
        assert obj.Value == 200
        assert obj.Total == 200

    def test_set_value_below_ge_caps(self):
        """set caps value to ge when value < ge"""
        obj = _DashboardDynamicTotalLimited(Value=50, Total=100)
        result = obj.set(-5, 100, error='cap')
        assert result is False
        assert obj.Value == 0

    def test_set_value_exceeds_total_drop(self):
        """set with value > total and error='drop' does nothing and logs warning"""
        obj = _DashboardDynamicTotalLimited(Value=10, Total=100)
        with logger.mock_capture_writer() as capture:
            result = obj.set(150, 50, error='drop')
        assert result is False
        assert obj.Value == 10
        assert obj.Total == 100
        assert capture.backend.any_contains('greater than total')

    def test_set_value_above_le_drop(self):
        """set with value > le and error='drop' does nothing and logs warning"""
        obj = _DashboardDynamicTotalLimited(Value=0, Total=50)
        with logger.mock_capture_writer() as capture:
            result = obj.set(250, 50, error='drop')
        assert result is False
        assert obj.Value == 0
        assert obj.Total == 50
        assert capture.backend.any_contains('out of range')

    def test_set_total_above_le_drop(self):
        """set with total > le and error='drop' does nothing and logs warning"""
        obj = _DashboardDynamicTotalBothCapped(Value=0, Total=0)
        with logger.mock_capture_writer() as capture:
            result = obj.set(150, 300, error='drop')
        assert result is False
        assert obj.Value == 0
        assert obj.Total == 0
        assert capture.backend.any_contains('out of range')

    def test_set_value_below_ge_drop(self):
        """set with value < ge and error='drop' does nothing and logs warning"""
        obj = _DashboardDynamicTotalLimited(Value=50, Total=100)
        with logger.mock_capture_writer() as capture:
            result = obj.set(-5, 100, error='drop')
        assert result is False
        assert obj.Value == 50
        assert obj.Total == 100
        assert capture.backend.any_contains('out of range')

    def test_set_within_range_drop(self):
        """set with value and total within range and error='drop' succeeds"""
        obj = _DashboardDynamicTotalLimited(Value=0, Total=0)
        with logger.mock_capture_writer() as capture:
            result = obj.set(30, 100, error='drop')
        assert result is True
        assert obj.Value == 30
        assert obj.Total == 100
        assert len(capture.backend.logs) == 0

    def test_set_value_exceeds_total_with_raise(self):
        """set with value > total and error='raise' raises"""
        obj = _DashboardDynamicTotalLimited(Value=0, Total=0)
        with pytest.raises(ValueError, match='greater than total'):
            obj.set(50, 30, error='raise')

    def test_set_value_above_le_with_raise(self):
        """set with value > le and error='raise' raises"""
        obj = _DashboardDynamicTotalBothCapped(Value=0, Total=0)
        with pytest.raises(ValueError, match='out of range'):
            obj.set(999, 100, error='raise')

    def test_set_total_above_le_with_raise(self):
        """set with total > le and error='raise' raises"""
        obj = _DashboardDynamicTotalBothCapped(Value=0, Total=0)
        with pytest.raises(ValueError, match='out of range'):
            obj.set(50, 999, error='raise')

    def test_set_updates_time(self):
        """set updates Time"""
        obj = _DashboardDynamicTotalLimited(Value=0, Total=0)
        result = obj.set(30, 100)
        assert result is True
        assert obj.Time > d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)


class TestDashboardDynamicTotalIsEmpty:
    """Test suite for DashboardDynamicTotal.is_empty"""

    def test_value_at_ge_is_empty(self):
        """Value at or below meta.ge is empty"""
        obj = _DashboardDynamicTotalLimited(Value=0, Total=100)
        assert obj.is_empty() is True

    def test_value_above_ge_is_not_empty(self):
        """Value above meta.ge is not empty"""
        obj = _DashboardDynamicTotalLimited(Value=1, Total=100)
        assert obj.is_empty() is False

    def test_value_mid_range_is_not_empty(self):
        """Value in the middle is not empty"""
        obj = _DashboardDynamicTotalLimited(Value=50, Total=100)
        assert obj.is_empty() is False

    def test_value_at_total_is_not_empty(self):
        """Value at Total is not empty"""
        obj = _DashboardDynamicTotalLimited(Value=100, Total=100)
        assert obj.is_empty() is False

    def test_is_empty_raises_when_ge_is_none(self):
        """is_empty raises DataInconsistent if meta.ge is None"""
        obj = _DashboardDynamicTotalNoGe(Value=0, Total=0)
        with pytest.raises(DataInconsistent, match='does not have ge defined'):
            obj.is_empty()


class TestDashboardDynamicTotalIsFull:
    """Test suite for DashboardDynamicTotal.is_full"""

    def test_value_at_total_is_full(self):
        """Value equal to Total is full"""
        obj = _DashboardDynamicTotalLimited(Value=100, Total=100)
        assert obj.is_full() is True

    def test_value_below_total_is_not_full(self):
        """Value below Total is not full"""
        obj = _DashboardDynamicTotalLimited(Value=50, Total=100)
        assert obj.is_full() is False

    def test_value_at_ge_not_full(self):
        """Value at minimum is not full"""
        obj = _DashboardDynamicTotalLimited(Value=0, Total=100)
        assert obj.is_full() is False

    def test_value_above_total_is_full(self):
        """Value above Total is full (capped by set)"""
        obj = _DashboardDynamicTotalLimited(Value=100, Total=100)
        assert obj.is_full() is True


class TestDashboardDynamicTotalMeta:
    """Test suite for DashboardDynamicTotal.meta and meta_total"""

    def test_meta_returns_meta_for_value(self):
        """meta returns the Meta annotation for Value field"""
        obj = _DashboardDynamicTotalLimited(Value=0, Total=0)
        assert obj.meta.ge == 0
        assert obj.meta.le == 200

    def test_meta_total_returns_meta_for_total(self):
        """meta_total returns the Meta annotation for Total field"""
        obj = _DashboardDynamicTotalLimited(Value=0, Total=0)
        assert obj.meta_total.ge == 0
        assert obj.meta_total.le is None

    def test_meta_total_cached(self):
        """meta_total is a cached_property"""
        obj = _DashboardDynamicTotalLimited(Value=0, Total=0)
        assert obj.meta_total is obj.meta_total


class TestDashboardDynamicTotalInherited:
    """Test suite for DashboardDynamicTotal inherited methods"""

    def test_add_works_correctly_now(self):
        """
        DashboardDynamicTotal now has its own add() override that passes
        self.Total to set(). add increments Value while preserving Total.
        """
        obj = _DashboardDynamicTotalLimited(Value=50, Total=100)
        result = obj.add()
        assert result is True
        assert obj.Value == 51
        assert obj.Total == 100

    def test_add_caps_at_limit(self):
        """add caps Value when exceeding le, and Total is preserved"""
        obj = _DashboardDynamicTotalLimited(Value=199, Total=200)
        result = obj.add(5, error='cap')
        assert result is False
        assert obj.Value == 200
        assert obj.Total == 200

    def test_add_drop_at_limit(self):
        """add with error='drop' beyond limit does nothing and logs warning"""
        obj = _DashboardDynamicTotalLimited(Value=199, Total=200)
        with logger.mock_capture_writer() as capture:
            result = obj.add(5, error='drop')
        assert result is False
        assert obj.Value == 199
        assert obj.Total == 200
        assert capture.backend.any_contains('out of range')

    def test_reset_works(self):
        """DashboardDynamicTotal inherits DashboardAmount.reset"""
        obj = _DashboardDynamicTotalLimited(Value=50, Total=100)
        obj.reset()
        assert obj.Value == 0
        # Total is not affected by reset
        assert obj.Total == 100

    def test_sub_works_correctly_now(self):
        """
        DashboardDynamicTotal now has its own sub() override that passes
        self.Total to set(). sub decrements Value while preserving Total.
        """
        obj = _DashboardDynamicTotalLimited(Value=50, Total=100)
        result = obj.sub()
        assert result is True
        assert obj.Value == 49
        assert obj.Total == 100

    def test_sub_caps_at_limit(self):
        """sub caps Value when going below ge, and Total is preserved"""
        obj = _DashboardDynamicTotalLimited(Value=1, Total=100)
        result = obj.sub(5, error='cap')
        assert result is False
        assert obj.Value == 0
        assert obj.Total == 100

    def test_sub_drop_at_limit(self):
        """sub with error='drop' beyond limit does nothing and logs warning"""
        obj = _DashboardDynamicTotalLimited(Value=1, Total=100)
        with logger.mock_capture_writer() as capture:
            result = obj.sub(5, error='drop')
        assert result is False
        assert obj.Value == 1
        assert obj.Total == 100
        assert capture.backend.any_contains('out of range')

    def test_update_does_nothing_when_not_expired(self):
        """
        DashboardDynamicTotal.update inherits from DashboardAmount.
        With ServerUpdate='' (default), is_expired() returns False,
        so reset() is NOT called. Value and Total remain unchanged.
        """
        obj = _DashboardDynamicTotalLimited(Value=50, Total=100)
        obj.update()
        assert obj.Value == 50
        assert obj.Total == 100
