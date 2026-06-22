"""
Tests for alasio.base.state

States are class-based (no instantiation). Fields must have type annotations and static default values.
"""
import pytest
import msgspec

from alasio.base.state import GlobalState, TaskState


class TestBasicState:
    """Test basic state class definition and default values"""

    def test_basic_state(self):
        class StateA(GlobalState):
            a: int = 1
            b: str = '2'

        # Class attribute access
        assert StateA.a == 1
        assert StateA.b == '2'
        # dict_defaults contains all annotated fields with defaults
        assert StateA.dict_defaults == {'a': 1, 'b': '2'}

    def test_requires_annotation(self):
        """Fields without type annotations are not included in dict_defaults"""

        class StateA(GlobalState):
            a: int = 1
            b = '2'  # no annotation — not a state field

        assert StateA.a == 1
        assert StateA.b == '2'
        assert StateA.dict_defaults == {'a': 1}

    def test_cannot_instantiate(self):
        class StateA(GlobalState):
            a: int = 1

        with pytest.raises(TypeError, match='cannot be instantiated'):
            StateA()


class TestInheritanceAndOverride:
    """Test inheritance and field override"""

    def test_inheritance_and_override(self):
        class StateA(GlobalState):
            a: int = 1
            b: int = 2

        class StateB(StateA):
            b: int = 3
            c: int = 4

        # Inherited and overridden values
        assert StateB.a == 1
        assert StateB.b == 3
        assert StateB.c == 4
        assert StateB.dict_defaults == {'a': 1, 'b': 3, 'c': 4}

        # Parent state is not affected by children
        assert StateA.a == 1
        assert StateA.b == 2
        assert StateA.dict_defaults == {'a': 1, 'b': 2}

    def test_inherit_task_state(self):
        """TaskState also supports inheritance"""

        class StateA(TaskState):
            a: int = 1
            b: int = 2

        class StateB(StateA):
            b: int = 3

        assert StateB.a == 1
        assert StateB.b == 3
        assert StateA.b == 2


class TestAttrModification:
    """Test modifying, checking, and resetting fields"""

    def test_modify_and_check(self):
        class StateA(GlobalState):
            a: int = 1

        # Not modified initially
        assert not StateA.is_modified('a')

        # Modify
        StateA.a = 10
        assert StateA.a == 10
        assert StateA.is_modified('a')

        # Reset to default
        StateA.reset_field('a')
        assert StateA.a == 1
        assert not StateA.is_modified('a')

    def test_get_default(self):
        class StateA(GlobalState):
            a: int = 1
            b: str = 'hello'

        assert StateA.get_default('a') == 1
        assert StateA.get_default('b') == 'hello'

        with pytest.raises(ValueError, match='does not exist'):
            StateA.get_default('nonexistent')

    def test_is_modified_nonexistent_field(self):
        class StateA(GlobalState):
            a: int = 1

        with pytest.raises(ValueError, match='does not exist'):
            StateA.is_modified('nonexistent')

    def test_is_modified_after_reset(self):
        class StateA(GlobalState):
            a: int = 1

        StateA.a = 100
        assert StateA.is_modified('a')
        StateA.reset_field('a')
        assert not StateA.is_modified('a')

    def test_value_validation(self):
        """Setting wrong type raises ValidationError"""

        class StateA(GlobalState):
            a: int = 1

        with pytest.raises(msgspec.ValidationError):
            StateA.a = 'not_an_int'

    def test_undefined_field_raises_error(self):
        """Setting a field not defined in the class raises ValidationError"""

        class StateA(GlobalState):
            a: int = 1

        with pytest.raises(msgspec.ValidationError):
            StateA.update(b=2)

    def test_reset_field_nonexistent(self):
        class StateA(GlobalState):
            a: int = 1

        with pytest.raises(ValueError, match='does not exist'):
            StateA.reset_field('nonexistent')


class TestResetAndClear:
    """Test reset_field and reset_all_fields (replaces old unset/clear)"""

    def test_reset_field(self):
        class StateA(GlobalState):
            a: int = 1
            b: int = 2

        StateA.a = 10
        assert StateA.a == 10
        assert StateA.is_modified('a')

        StateA.reset_field('a')
        assert StateA.a == 1
        assert not StateA.is_modified('a')

    def test_reset_all_fields(self):
        class StateA(GlobalState):
            a: int = 1
            b: int = 2

        StateA.a = 10
        StateA.b = 20
        assert StateA.is_modified('a')
        assert StateA.is_modified('b')

        StateA.reset_all_fields()
        assert StateA.a == 1
        assert StateA.b == 2
        assert not StateA.is_modified('a')
        assert not StateA.is_modified('b')

    def test_is_modified_with_default_value(self):
        """Setting a field to its default value means it's not modified"""

        class StateA(GlobalState):
            a: int = 1
            b: int = 2

        StateA.a = 1  # set to same as default
        StateA.b = 10  # set to different value
        assert not StateA.is_modified('a')
        assert StateA.is_modified('b')


class TestBatchUpdate:
    """Test update method for batch-setting fields"""

    def test_batch_update(self):
        class StateA(GlobalState):
            a: int = 1
            b: int = 2

        StateA.update(a=10)
        assert StateA.a == 10
        assert StateA.b == 2
        assert StateA.is_modified('a')
        assert not StateA.is_modified('b')

    def test_batch_update_validation(self):
        """update with wrong type raises ValidationError"""

        class StateA(GlobalState):
            a: int = 1

        with pytest.raises(msgspec.ValidationError):
            StateA.update(a='not_an_int')

    def test_update_unknown_field_raises_error(self):
        """update with unknown field raises ValidationError"""

        class StateA(GlobalState):
            a: int = 1

        with pytest.raises(msgspec.ValidationError):
            StateA.update(unknown_field=42)


class TestTaskStateResetAll:
    """Test TaskState-specific reset_all_subclasses functionality"""

    def test_task_state_reset_all_subclasses(self):
        class StateA(TaskState):
            a: int = 1

        class StateB(TaskState):
            b: int = 2

        # Modify values
        StateA.a = 10
        StateB.b = 20
        assert StateA.is_modified('a')
        assert StateB.is_modified('b')

        # Reset all TaskState subclasses
        TaskState.reset_all_subclasses()

        # All should be back to defaults
        assert not StateA.is_modified('a')
        assert not StateB.is_modified('b')
        assert StateA.a == 1
        assert StateB.b == 2

    def test_task_state_reset_does_not_affect_global_state(self):
        """TaskState.reset_all_subclasses should not affect GlobalState subclasses"""

        class GlobalA(GlobalState):
            x: int = 1

        class TaskA(TaskState):
            y: int = 100

        GlobalA.x = 999
        TaskA.y = 888

        TaskState.reset_all_subclasses()

        # Global state should remain modified
        assert GlobalA.is_modified('x')
        assert GlobalA.x == 999

        # Task state should be reset
        assert not TaskA.is_modified('y')
        assert TaskA.y == 100

    def test_deep_inheritance_reset(self):
        """reset_all_subclasses should work on deep inheritance chains"""

        class BaseA(TaskState):
            a: int = 1

        class MiddleB(BaseA):
            b: int = 2

        class LeafC(MiddleB):
            c: int = 3

        MiddleB.b = 20
        LeafC.c = 30

        TaskState.reset_all_subclasses()

        assert MiddleB.b == 2
        assert LeafC.c == 3
        # Parent fields should also be reset
        assert LeafC.a == 1


class TestMatch:
    """Test match and _match methods on state classes"""

    def test_match_single_condition_true(self):
        class StateA(GlobalState):
            a: str = 'x'
            b: str = 'y'

        assert StateA.match(a='x')
        assert StateA.match(b='y')

    def test_match_single_condition_false(self):
        class StateA(GlobalState):
            a: str = 'x'
            b: str = 'y'

        assert not StateA.match(a='z')
        assert not StateA.match(b='w')

    def test_match_multiple_conditions_all_match(self):
        class StateA(GlobalState):
            a: str = 'x'
            b: str = 'y'

        assert StateA.match(a='x', b='y')

    def test_match_multiple_conditions_one_fails(self):
        class StateA(GlobalState):
            a: str = 'x'
            b: str = 'y'

        assert not StateA.match(a='x', b='w')
        assert not StateA.match(a='z', b='y')
        assert not StateA.match(a='z', b='w')

    def test_match_nonexistent_key(self):
        class StateA(GlobalState):
            a: str = 'x'

        assert not StateA.match(nonexistent='value')
        assert not StateA.match(foo='bar')

    def test_match_mixed_existent_and_nonexistent(self):
        class StateA(GlobalState):
            a: str = 'x'
            b: str = 'y'

        assert not StateA.match(a='x', nonexistent='value')
        assert not StateA.match(a='x', b='y', fake='attr')

    def test_match_empty_kwargs(self):
        class StateA(GlobalState):
            a: str = 'x'

        assert StateA.match()

    @pytest.mark.parametrize("a, b, expected", [
        ('x', 'y', True),
        ('x', 'z', False),
        ('z', 'y', False),
        ('z', 'w', False),
    ])
    def test_match_parametrized_combinations(self, a, b, expected):
        class StateA(GlobalState):
            a: str = 'x'
            b: str = 'y'

        assert StateA.match(a=a, b=b) == expected

    def test_match_after_assignment(self):
        class StateA(GlobalState):
            a: str = 'x'

        StateA.a = 'z'
        assert StateA.match(a='z')
        assert not StateA.match(a='x')

    def test_match_with_multiple_conditions_after_assignment(self):
        class StateA(GlobalState):
            a: str = 'x'
            b: str = 'y'

        StateA.a = 'z'
        StateA.b = 'w'
        assert StateA.match(a='z', b='w')
        assert not StateA.match(a='z', b='y')

    def test_match_on_subclass_with_custom_defaults(self):
        class BaseA(GlobalState):
            a: str = 'default_a'
            b: str = 'default_b'

        class SubA(BaseA):
            a: str = 'custom_a'
            b: str = 'custom_b'

        assert SubA.match(a='custom_a')
        assert SubA.match(b='custom_b')
        assert not SubA.match(a='default_a')
        assert not SubA.match(a='default_a', b='custom_b')

    def test_match_on_task_state(self):
        """match should work on TaskState subclasses too"""
        class StateA(TaskState):
            a: int = 1
            b: int = 2

        assert StateA.match(a=1, b=2)
        assert not StateA.match(a=100)


class TestStructModel:
    """Test the internal struct model behavior"""

    def test_omit_defaults(self):
        """struct model uses omit_defaults"""

        class StateA(GlobalState):
            a: int = 1
            b: int = 2

        # msgspec encoder should omit default values
        encoded = msgspec.json.encode(StateA.struct_model(a=1, b=2))
        assert encoded == b'{}'

        encoded = msgspec.json.encode(StateA.struct_model(a=10, b=2))
        assert encoded == b'{"a":10}'

    def test_forbid_unknown(self):
        """struct model forbids unknown fields"""

        class StateA(GlobalState):
            a: int = 1

        with pytest.raises(TypeError):
            StateA.struct_model(a=1, unknown=2)

    def test_dict_defaults_matches_struct_defaults(self):
        """dict_defaults should match struct model defaults"""

        class StateA(GlobalState):
            a: int = 10
            b: str = 'hello'
            c: float = 3.14

        for name, expected in StateA.dict_defaults.items():
            field_idx = StateA.struct_model.__struct_fields__.index(name)
            struct_default = StateA.struct_model.__struct_defaults__[field_idx]
            assert expected == struct_default
