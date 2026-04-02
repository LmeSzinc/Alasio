from alasio.base.state import StateBase, TaskState


def test_basic_state():
    class StateA(StateBase):
        a = 1
        b = '2'

    # Singleton ensures we get the same instance for each class
    s = StateA()
    # Initial values
    assert s.a == 1
    assert s.b == '2'
    # dict_defaults should contain 'a' and 'b' from the class definition
    assert s.dict_defaults == {'a': 1, 'b': '2'}


def test_inheritance_and_override():
    class StateA(StateBase):
        a = 1
        b = 2

    class StateB(StateA):
        b = 3
        c = 4

    sa = StateA()
    sb = StateB()

    # Check inherited and overridden values
    assert sb.a == 1
    assert sb.b == 3
    assert sb.c == 4
    # Multi-level inheritance should be supported by dir()
    assert sb.dict_defaults == {'a': 1, 'b': 3, 'c': 4}

    # Check that parent state is not affected by inheritance or overrides in children
    assert sa.a == 1
    assert sa.b == 2
    assert sa.dict_defaults == {'a': 1, 'b': 2}


def test_runtime_attr_isolation():
    class StateA(StateBase):
        a = 1

    s = StateA()
    # Attr only depends on definition in the class
    # Setting an unrelated attribute at runtime should not interfere with state mechanisms
    s.b = 2

    assert s.b == 2
    assert s.a == 1

    # dict_defaults are calculated based on class definition, runtime attrs are ignored
    assert 'b' not in s.dict_defaults
    assert 'a' in s.dict_defaults

    # is_set only returns True for attributes defined in the class
    assert s.is_set('a') is True
    assert s.is_set('b') is False

    # get_attrs includes defined defaults + current set values, excludes runtime unrelated attrs
    attrs = s.get_attrs()
    assert attrs == {'a': 1}
    assert 'b' not in attrs

    # get_attrs_set only includes defined attributes that have been set to instance
    # s.b was set at runtime but it's not a defined attribute in the class
    attrs_set = s.get_attrs_set()
    assert attrs_set == {}

    # If we set a defined attribute, it should be in get_attrs_set
    s.a = 10
    attrs_set = s.get_attrs_set()
    assert attrs_set == {'a': 10}
    assert 'b' not in attrs_set


def test_unset_and_clear():
    class StateA(StateBase):
        a = 1
        b = 2

    s = StateA()

    # Test unset
    s.a = 10
    assert s.a == 10
    v = s.unset('a')
    assert v == 10
    # Should return to default value after unset
    assert s.a == 1
    assert 'a' not in s.__dict__

    # Test unset_defaults
    s.a = 1  # set to same as default
    s.b = 10  # set to different value
    assert s.get_attrs_set() == {'a': 1, 'b': 10}
    s.unset_defaults()
    # Should remove 'a' as it matches default, keep 'b'
    assert s.get_attrs_set() == {'b': 10}

    # Test clear
    s.a = 10
    s.b = 20
    s.clear()
    # Should clear all defined attributes back to their defaults
    assert s.get_attrs_set() == {}
    assert s.a == 1
    assert s.b == 2


def test_merge():
    class StateA(StateBase):
        a = 1
        b = 2

    class StateB(StateBase):
        a = 0
        c = 10

    s1 = StateA()
    s1.clear()
    s2 = StateB()
    s2.clear()

    s2.a = 10
    s2.c = 20  # Set an unrelated attr on source state

    # If s2.c is NOT defined in StateA, it should NOT merge into s1
    s1.merge(s2)
    assert s1.a == 10
    assert s1.b == 2
    assert not hasattr(s1, 'c')


def test_task_state_collection_and_clear():
    # Define subclasses of TaskState
    class StateA(TaskState):
        a = 1

    class StateB(TaskState):
        b = 2

    # Initial instances
    s1 = StateA()
    s2 = StateB()

    # Modify values
    s1.a = 10
    s2.b = 20

    # Ensure they are registered in TaskState._subclasses
    assert id(s1) in TaskState._subclasses
    assert id(s2) in TaskState._subclasses
    assert TaskState._subclasses[id(s1)] is s1
    assert TaskState._subclasses[id(s2)] is s2

    # Clear all TaskState subclasses
    TaskState.subclasses_clear_all()

    # Registry should be empty
    assert len(TaskState._subclasses) == 0

    # Original instances should have been cleared (reset to defaults)
    assert s1.a == 1
    assert s2.b == 2

    # Singletons should have been cleared
    # Accessing them again should result in new instances
    s1_new = StateA()
    assert s1_new is not s1
    assert s1_new.a == 1

    # The new instances should be in the new registry
    assert id(s1_new) in TaskState._subclasses
    assert len(TaskState._subclasses) == 1
