import msgspec as m

from alasio.config.alasio.store_model import cap_value


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
