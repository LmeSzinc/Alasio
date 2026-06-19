import threading
import typing as t

import msgspec as m
import pytest

from alasio.base.servertime import ServerTime
from alasio.config.alasio.group_base import DEFAULT_TIME, GroupBase, T_DATETIME, T_INT_GE0
from alasio.config.alasio.group_proxy import GroupProxy
from alasio.config.base import BatchSetContext
from alasio.config.const import DataInconsistent


# ---- Test models ----
# Must inherit from BaseModel so that BaseModel.classmethod(cls, attr)
# can be called as TestModel.classmethod(attr) naturally.

class MetaTestModel(GroupBase):
    """Model with various msgspec meta annotations for testing BaseModel.get_meta"""
    count: T_INT_GE0 = 0
    name: str = 'default'
    scheduled: T_DATETIME = DEFAULT_TIME


class NoMetaModel(GroupBase):
    """Model with no Meta annotation on any field"""
    value: int = 0


class LiteralTestModel(GroupBase):
    """Model with literal annotation for testing BaseModel.get_option"""
    difficulty: "t.Literal['normal', 'hard']" = 'normal'


class NoLiteralTestModel(GroupBase):
    """Model with no literal annotation"""
    count: int = 0


# ---- Tests: BaseModel.get_meta ----

class TestBaseModelGetMeta:
    """Test suite for BaseModel.get_meta"""

    def test_get_meta_int_ge0(self):
        """Test get_meta returns correct Meta for T_INT_GE0 field"""
        meta = MetaTestModel.get_meta('count')
        assert isinstance(meta, m.Meta)
        assert meta.ge == 0
        assert meta.le is None

    def test_get_meta_time_tz(self):
        """Test get_meta returns correct Meta for T_TIME field"""
        meta = MetaTestModel.get_meta('scheduled')
        assert isinstance(meta, m.Meta)
        assert meta.tz is True

    def test_get_meta_str_no_meta(self):
        """Test get_meta on field with no Meta annotation raises DataInconsistent"""
        with pytest.raises(DataInconsistent, match='Missing anno.__metadata__'):
            MetaTestModel.get_meta('name')

    def test_get_meta_missing_attribute_raises(self):
        """Test get_meta with nonexistent attribute raises DataInconsistent"""
        with pytest.raises(DataInconsistent, match='Missing annotation'):
            MetaTestModel.get_meta('nonexistent')

    def test_get_meta_field_without_meta_annotation_raises(self):
        """Test get_meta on field annotated with plain int and no Meta raises DataInconsistent"""
        with pytest.raises(DataInconsistent, match='Missing anno.__metadata__'):
            NoMetaModel.get_meta('value')


# ---- Tests: BaseModel.get_default ----

class TestBaseModelGetDefault:
    """Test suite for BaseModel.get_default"""

    def test_get_default_int(self):
        """Test get_default returns member descriptor for msgspec struct field"""
        default = MetaTestModel.get_default('count')
        # Note: getattr on msgspec Struct class returns a member_descriptor,
        # not the actual default value. This is current behavior of group_base.py.
        assert 'member' in str(type(default))

    def test_get_default_str(self):
        """Test get_default returns member descriptor for str field"""
        default = MetaTestModel.get_default('name')
        assert 'member' in str(type(default))

    def test_get_default_datetime(self):
        """Test get_default returns member descriptor for datetime field"""
        default = MetaTestModel.get_default('scheduled')
        assert 'member' in str(type(default))

    def test_get_default_missing_raises(self):
        """Test get_default with nonexistent attribute raises DataInconsistent"""
        with pytest.raises(DataInconsistent, match='Missing default'):
            MetaTestModel.get_default('nonexistent')


# ---- Tests: BaseModel.get_option ----

class TestBaseModelGetOption:
    """Test suite for BaseModel.get_option"""

    def test_get_option_literal_string_annotation(self):
        """Test get_option on field with string literal annotation"""
        options = LiteralTestModel.get_option('difficulty')
        assert options == ('normal', 'hard')

    def test_get_option_missing_attribute_raises(self):
        """Test get_option with nonexistent attribute raises DataInconsistent"""
        with pytest.raises(DataInconsistent, match='Missing annotation'):
            MetaTestModel.get_option('nonexistent')

    def test_get_option_non_literal_raises(self):
        """Test get_option on non-literal field raises DataInconsistent"""
        with pytest.raises(DataInconsistent, match='Arg has no literal|Arg is not a literal'):
            NoLiteralTestModel.get_option('count')


# ---- Test models for get_servertime ----

class ServertimeTestModel(GroupBase):
    """Model for testing get_servertime injection magic"""
    value: int = 0


# ---- Tests: GroupBase.get_servertime ----

class _MockConfig:
    """
    Minimal config-like object that provides the required interface
    for GroupProxy.__getattr__ / __setattr__ and the batch_set context.
    """
    def __init__(self):
        self.servertime = ServerTime(8)
        self.auto_save = False
        self._local = threading.local()
        self._local.batch_depth = 0
        self._modified = {}
        self.save_called = False

    def batch_set(self):
        return BatchSetContext(self)

    def register_modify(self, task, group, arg, value):
        key = (task, group, arg)
        self._modified[key] = value


class TestGetServertime:
    """Test suite for GroupBase.get_servertime"""

    def test_raw_struct_raises(self):
        """
        Calling get_servertime on a raw GroupBase struct (not wrapped in GroupProxy)
        raises DataInconsistent because there is no servertime property on the struct.
        """
        model = ServertimeTestModel()
        with pytest.raises(DataInconsistent, match='Missing servertime injection'):
            model.get_servertime()

    def test_via_proxy_returns_config_servertime(self):
        """
        When wrapped in GroupProxy with a proper config, get_servertime() accesses
        the proxy's .servertime property which delegates to config.servertime.
        This validates the "injection magic" described in group_base.py.
        """
        config = _MockConfig()
        obj = ServertimeTestModel()
        proxy = GroupProxy(_obj=obj, _config=config, _task='Main', _group='TestGroup')

        result = proxy.get_servertime()

        assert result is config.servertime
        assert isinstance(result, ServerTime)

    def test_via_proxy_equals_direct_servertime(self):
        """
        The servertime obtained via get_servertime() on the proxy should
        be identical to accessing config.servertime directly.
        """
        config = _MockConfig()
        obj = ServertimeTestModel()
        proxy = GroupProxy(_obj=obj, _config=config, _task='Main', _group='TestGroup')

        proxy_st = proxy.get_servertime()
        direct_st = config.servertime

        assert proxy_st is direct_st  # same object

    def test_proxy_servertime_property(self):
        """
        Accessing proxy.servertime directly (not through get_servertime)
        returns the config's servertime via GroupProxy's servertime property.
        """
        config = _MockConfig()
        obj = ServertimeTestModel()
        proxy = GroupProxy(_obj=obj, _config=config, _task='Main', _group='TestGroup')

        assert proxy.servertime is config.servertime
