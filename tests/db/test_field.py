from typing import Any, Optional

import msgspec

from alasio.db.field import iter_class_field


class TestIterClassField:
    """Test suite for iter_class_field function"""

    def test_simple_class_with_defaults(self):
        """Test a simple class with annotated fields and default values"""

        class SimpleClass:
            name: str = "default_name"
            age: int = 0
            active: bool = True

        fields = list(iter_class_field(SimpleClass))

        assert len(fields) == 3
        assert ("name", str, "default_name") in fields
        assert ("age", int, 0) in fields
        assert ("active", bool, True) in fields

    def test_class_without_defaults(self):
        """Test fields without default values return msgspec.NODEFAULT"""

        class NoDefaultClass:
            name: str
            age: int

        fields = list(iter_class_field(NoDefaultClass))

        assert len(fields) == 2
        assert ("name", str, msgspec.NODEFAULT) in fields
        assert ("age", int, msgspec.NODEFAULT) in fields

    def test_mixed_defaults(self):
        """Test class with both default and no-default fields"""

        class MixedClass:
            name: str
            age: int = 25
            email: str

        fields = list(iter_class_field(MixedClass))

        assert len(fields) == 3
        assert ("name", str, msgspec.NODEFAULT) in fields
        assert ("age", int, 25) in fields
        assert ("email", str, msgspec.NODEFAULT) in fields

    def test_inheritance_simple(self):
        """Test field iteration with simple inheritance"""

        class Parent:
            parent_field: str = "parent"
            shared: int = 1

        class Child(Parent):
            child_field: str = "child"

        fields = list(iter_class_field(Child))

        assert len(fields) == 3
        assert ("parent_field", str, "parent") in fields
        assert ("shared", int, 1) in fields
        assert ("child_field", str, "child") in fields

    def test_inheritance_override(self):
        """Test child class overriding parent field"""

        class Parent:
            value: int = 10

        class Child(Parent):
            value: int = 20
            name: str = "child"

        fields = list(iter_class_field(Child))

        # Child's value should override parent's
        field_dict = {name: (anno, val) for name, anno, val in fields}
        assert field_dict["value"] == (int, 20)
        assert field_dict["name"] == (str, "child")

    def test_child_unset_overrides_parent_with_default(self):
        """Test child using UNSET to remove parent field with default value"""

        class Parent:
            name: str = "parent_name"
            age: int = 30
            email: str = "parent@example.com"

        class Child(Parent):
            # Remove 'age' field from parent
            age: int = msgspec.UNSET
            # Keep other fields and add new one
            city: str = "New York"

        fields = list(iter_class_field(Child))
        field_names = [name for name, _, _ in fields]

        # age should not be in the results
        assert "age" not in field_names
        # Other parent fields should still be present
        assert "name" in field_names
        assert "email" in field_names
        # Child's new field should be present
        assert "city" in field_names

        assert len(fields) == 3
        assert ("name", str, "parent_name") in fields
        assert ("email", str, "parent@example.com") in fields
        assert ("city", str, "New York") in fields

    def test_child_unset_overrides_parent_without_default(self):
        """Test child using UNSET to remove parent field without default value"""

        class Parent:
            name: str
            age: int
            email: str = "default@example.com"

        class Child(Parent):
            # Remove 'name' field (which had no default in parent)
            name: str = msgspec.UNSET
            # Add new field
            phone: str = "123-456-7890"

        fields = list(iter_class_field(Child))
        field_names = [name for name, _, _ in fields]

        # name should not be in the results
        assert "name" not in field_names
        # Other fields should be present
        assert "age" in field_names
        assert "email" in field_names
        assert "phone" in field_names

        assert len(fields) == 3

    def test_multiple_unset_in_child(self):
        """Test child using UNSET to remove multiple parent fields"""

        class Parent:
            field1: str = "value1"
            field2: int = 10
            field3: bool = True
            field4: float = 3.14

        class Child(Parent):
            # Remove multiple fields
            field1: str = msgspec.UNSET
            field3: bool = msgspec.UNSET
            # Add new field
            new_field: str = "new"

        fields = list(iter_class_field(Child))
        field_names = [name for name, _, _ in fields]

        # Removed fields should not be present
        assert "field1" not in field_names
        assert "field3" not in field_names
        # Kept fields should be present
        assert "field2" in field_names
        assert "field4" in field_names
        # New field should be present
        assert "new_field" in field_names

        assert len(fields) == 3

    def test_multilevel_inheritance_with_unset(self):
        """Test UNSET behavior in multilevel inheritance"""

        class GrandParent:
            gp_field1: str = "gp1"
            gp_field2: int = 100

        class Parent(GrandParent):
            p_field: str = "parent"
            # Remove gp_field1
            gp_field1: str = msgspec.UNSET

        class Child(Parent):
            c_field: str = "child"

        fields = list(iter_class_field(Child))
        field_names = [name for name, _, _ in fields]

        # gp_field1 was removed in Parent, should not appear in Child
        assert "gp_field1" not in field_names
        # Other fields should be present
        assert "gp_field2" in field_names
        assert "p_field" in field_names
        assert "c_field" in field_names

        assert len(fields) == 3

    def test_grandchild_can_restore_unset_field(self):
        """Test that grandchild can restore a field that parent marked as UNSET"""

        class GrandParent:
            field: str = "original"

        class Parent(GrandParent):
            # Remove field
            field: str = msgspec.UNSET

        class Child(Parent):
            # Restore field with new value
            field: str = "restored"

        fields = list(iter_class_field(Child))
        field_dict = {name: (anno, val) for name, anno, val in fields}

        # Field should be present with the restored value
        assert "field" in field_dict
        assert field_dict["field"] == (str, "restored")

    def test_unset_all_parent_fields(self):
        """Test child that removes all parent fields"""

        class Parent:
            field1: str = "value1"
            field2: int = 10

        class Child(Parent):
            # Remove all parent fields
            field1: str = msgspec.UNSET
            field2: int = msgspec.UNSET
            # Only have child field
            child_field: str = "child"

        fields = list(iter_class_field(Child))

        assert len(fields) == 1
        assert ("child_field", str, "child") in fields

    def test_unset_with_multiple_inheritance(self):
        """Test UNSET behavior with multiple inheritance"""

        class Mixin1:
            mixin1_field: str = "mixin1"

        class Mixin2:
            mixin2_field: int = 42

        class Child(Mixin1, Mixin2):
            # Remove field from Mixin1
            mixin1_field: str = msgspec.UNSET
            child_field: bool = True

        fields = list(iter_class_field(Child))
        field_names = [name for name, _, _ in fields]

        # mixin1_field should be removed
        assert "mixin1_field" not in field_names
        # mixin2_field should remain
        assert "mixin2_field" in field_names
        assert "child_field" in field_names

        assert len(fields) == 2

    def test_unset_fields_ignored(self):
        """Test that fields with msgspec.UNSET are ignored"""

        class UnsetClass:
            included: str = "value"
            excluded: int = msgspec.UNSET
            also_included: bool = False

        fields = list(iter_class_field(UnsetClass))

        assert len(fields) == 2
        assert ("included", str, "value") in fields
        assert ("also_included", bool, False) in fields
        # excluded should not be in fields
        field_names = [name for name, _, _ in fields]
        assert "excluded" not in field_names

    def test_empty_class(self):
        """Test empty class returns no fields"""

        class EmptyClass:
            pass

        fields = list(iter_class_field(EmptyClass))

        assert len(fields) == 0

    def test_class_without_annotations(self):
        """Test class with attributes but no annotations"""

        class NoAnnotations:
            # These won't be picked up because they lack type annotations
            value = 10
            name = "test"

        fields = list(iter_class_field(NoAnnotations))

        assert len(fields) == 0

    def test_complex_types(self):
        """Test with complex type annotations"""
        from typing import List, Dict

        class ComplexTypes:
            items: List[str] = []
            optional_value: Optional[int] = None
            mapping: Dict[str, Any] = {}

        fields = list(iter_class_field(ComplexTypes))

        assert len(fields) == 3
        field_dict = {name: anno for name, anno, _ in fields}
        assert field_dict["items"] == List[str]
        assert field_dict["optional_value"] == Optional[int]
        assert field_dict["mapping"] == Dict[str, Any]

    def test_generator_behavior(self):
        """Test that function returns a generator"""

        class TestClass:
            field: int = 1

        result = iter_class_field(TestClass)

        # Check it's a generator
        assert hasattr(result, '__iter__')
        assert hasattr(result, '__next__')

    def test_field_order_with_inheritance(self):
        """Test that fields are yielded in MRO order"""

        class GrandParent:
            gp_field: str = "gp"

        class Parent(GrandParent):
            p_field: str = "p"

        class Child(Parent):
            c_field: str = "c"

        fields = list(iter_class_field(Child))
        field_names = [name for name, _, _ in fields]

        # GrandParent fields should come before Parent, which come before Child
        assert field_names.index("gp_field") < field_names.index("p_field")
        assert field_names.index("p_field") < field_names.index("c_field")

    def test_none_as_default(self):
        """Test that None can be used as a default value"""

        class NoneDefault:
            value: Optional[str] = None
            number: Optional[int] = None

        fields = list(iter_class_field(NoneDefault))

        assert len(fields) == 2
        assert ("value", Optional[str], None) in fields
        assert ("number", Optional[int], None) in fields

    def test_class_variables_vs_instance_variables(self):
        """Test that only class variables with annotations are included"""

        class TestClass:
            class_var: int = 10
            # This won't be included (no annotation)
            another_var = 20

        fields = list(iter_class_field(TestClass))

        assert len(fields) == 1
        assert ("class_var", int, 10) in fields
