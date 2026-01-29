from typing import Any, Optional

import msgspec

from alasio.ext.msgspec_error.parse_anno import get_annotations, get_msgspec_annotations


class TestGetAnnotations:
    """Test suite for annotation_get function"""

    def test_simple_class_with_annotations(self):
        """Test a simple class with annotated fields"""

        class SimpleClass:
            name: str = "default_name"
            age: int = 0
            active: bool = True

        annotations = get_annotations(SimpleClass)

        assert len(annotations) == 3
        assert annotations["name"] == str
        assert annotations["age"] == int
        assert annotations["active"] == bool

    def test_class_without_defaults(self):
        """Test fields without default values are still included"""

        class NoDefaultClass:
            name: str
            age: int

        annotations = get_annotations(NoDefaultClass)

        assert len(annotations) == 2
        assert annotations["name"] == str
        assert annotations["age"] == int

    def test_inheritance_simple(self):
        """Test annotation collection with simple inheritance"""

        class Parent:
            parent_field: str = "parent"
            shared: int = 1

        class Child(Parent):
            child_field: str = "child"

        annotations = get_annotations(Child)

        assert len(annotations) == 3
        assert annotations["parent_field"] == str
        assert annotations["shared"] == int
        assert annotations["child_field"] == str

    def test_inheritance_override(self):
        """Test child class overriding parent annotation"""

        class Parent:
            value: int = 10

        class Child(Parent):
            value: str = "20"  # Different type
            name: str = "child"

        annotations = get_annotations(Child)

        # Child's annotation should override parent's
        assert annotations["value"] == str
        assert annotations["name"] == str

    def test_multilevel_inheritance(self):
        """Test annotation collection in multilevel inheritance"""

        class GrandParent:
            gp_field1: str = "gp1"
            gp_field2: int = 100

        class Parent(GrandParent):
            p_field: str = "parent"

        class Child(Parent):
            c_field: str = "child"

        annotations = get_annotations(Child)

        assert len(annotations) == 4
        assert annotations["gp_field1"] == str
        assert annotations["gp_field2"] == int
        assert annotations["p_field"] == str
        assert annotations["c_field"] == str

    def test_empty_class(self):
        """Test empty class returns no annotations"""

        class EmptyClass:
            pass

        annotations = get_annotations(EmptyClass)

        assert len(annotations) == 0

    def test_class_without_annotations(self):
        """Test class with attributes but no annotations"""

        class NoAnnotations:
            # These won't be picked up because they lack type annotations
            value = 10
            name = "test"

        annotations = get_annotations(NoAnnotations)

        assert len(annotations) == 0

    def test_complex_types(self):
        """Test with complex type annotations"""
        from typing import Dict, List

        class ComplexTypes:
            items: List[str] = []
            optional_value: Optional[int] = None
            mapping: Dict[str, Any] = {}

        annotations = get_annotations(ComplexTypes)

        assert len(annotations) == 3
        assert annotations["items"] == List[str]
        assert annotations["optional_value"] == Optional[int]
        assert annotations["mapping"] == Dict[str, Any]

    def test_multiple_inheritance(self):
        """Test annotation collection with multiple inheritance"""

        class Mixin1:
            mixin1_field: str = "mixin1"

        class Mixin2:
            mixin2_field: int = 42

        class Child(Mixin1, Mixin2):
            child_field: bool = True

        annotations = get_annotations(Child)

        assert len(annotations) == 3
        assert annotations["mixin1_field"] == str
        assert annotations["mixin2_field"] == int
        assert annotations["child_field"] == bool

    def test_annotation_order_with_inheritance(self):
        """Test that later classes can override earlier annotations"""

        class GrandParent:
            field: int = 1

        class Parent(GrandParent):
            field: str = "parent"  # Override type

        class Child(Parent):
            pass

        annotations = get_annotations(Child)

        # Parent's annotation should override GrandParent's
        assert annotations["field"] == str

    def test_unset_fields_included(self):
        """Test that fields with msgspec.UNSET are still included in annotations"""

        class UnsetClass:
            included: str = "value"
            excluded: int = msgspec.UNSET
            also_included: bool = False

        annotations = get_annotations(UnsetClass)

        # annotation_get should include all annotated fields
        assert len(annotations) == 3
        assert annotations["included"] == str
        assert annotations["excluded"] == int
        assert annotations["also_included"] == bool

    def test_non_class_input_raises_error(self):
        """Test that non-class input raises TypeError"""
        import pytest

        with pytest.raises(TypeError):
            get_annotations("not a class")

        with pytest.raises(TypeError):
            get_annotations(42)


class TestGetMsgspecAnnotation:
    """Test suite for msgspec_annotation_get function"""

    def test_simple_class_with_defaults(self):
        """Test a simple class with annotated fields and default values"""

        class SimpleClass:
            name: str = "default_name"
            age: int = 0
            active: bool = True

        annotations = get_msgspec_annotations(SimpleClass)

        assert len(annotations) == 3
        assert annotations["name"] == str
        assert annotations["age"] == int
        assert annotations["active"] == bool

    def test_class_without_defaults(self):
        """Test fields without default values are included"""

        class NoDefaultClass:
            name: str
            age: int

        annotations = get_msgspec_annotations(NoDefaultClass)

        # Fields without defaults should still be included
        assert len(annotations) == 2
        assert annotations["name"] == str
        assert annotations["age"] == int

    def test_mixed_defaults(self):
        """Test class with both default and no-default fields"""

        class MixedClass:
            name: str
            age: int = 25
            email: str

        annotations = get_msgspec_annotations(MixedClass)

        assert len(annotations) == 3
        assert annotations["name"] == str
        assert annotations["age"] == int
        assert annotations["email"] == str

    def test_inheritance_simple(self):
        """Test field iteration with simple inheritance"""

        class Parent:
            parent_field: str = "parent"
            shared: int = 1

        class Child(Parent):
            child_field: str = "child"

        annotations = get_msgspec_annotations(Child)

        assert len(annotations) == 3
        assert annotations["parent_field"] == str
        assert annotations["shared"] == int
        assert annotations["child_field"] == str

    def test_inheritance_override(self):
        """Test child class overriding parent field"""

        class Parent:
            value: int = 10

        class Child(Parent):
            value: int = 20
            name: str = "child"

        annotations = get_msgspec_annotations(Child)

        # Child's value should override parent's
        assert annotations["value"] == int
        assert annotations["name"] == str

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

        annotations = get_msgspec_annotations(Child)

        # age should not be in the results
        assert "age" not in annotations
        # Other parent fields should still be present
        assert "name" in annotations
        assert "email" in annotations
        # Child's new field should be present
        assert "city" in annotations

        assert len(annotations) == 3
        assert annotations["name"] == str
        assert annotations["email"] == str
        assert annotations["city"] == str

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

        annotations = get_msgspec_annotations(Child)

        # name should not be in the results
        assert "name" not in annotations
        # Other fields should be present
        assert "age" in annotations
        assert "email" in annotations
        assert "phone" in annotations

        assert len(annotations) == 3

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

        annotations = get_msgspec_annotations(Child)

        # Removed fields should not be present
        assert "field1" not in annotations
        assert "field3" not in annotations
        # Kept fields should be present
        assert "field2" in annotations
        assert "field4" in annotations
        # New field should be present
        assert "new_field" in annotations

        assert len(annotations) == 3

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

        annotations = get_msgspec_annotations(Child)

        # gp_field1 was removed in Parent, should not appear in Child
        assert "gp_field1" not in annotations
        # Other fields should be present
        assert "gp_field2" in annotations
        assert "p_field" in annotations
        assert "c_field" in annotations

        assert len(annotations) == 3

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

        annotations = get_msgspec_annotations(Child)

        # Field should be present with the restored value
        assert "field" in annotations
        assert annotations["field"] == str

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

        annotations = get_msgspec_annotations(Child)

        assert len(annotations) == 1
        assert annotations["child_field"] == str

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

        annotations = get_msgspec_annotations(Child)

        # mixin1_field should be removed
        assert "mixin1_field" not in annotations
        # mixin2_field should remain
        assert "mixin2_field" in annotations
        assert "child_field" in annotations

        assert len(annotations) == 2

    def test_unset_fields_ignored(self):
        """Test that fields with msgspec.UNSET are ignored"""

        class UnsetClass:
            included: str = "value"
            excluded: int = msgspec.UNSET
            also_included: bool = False

        annotations = get_msgspec_annotations(UnsetClass)

        assert len(annotations) == 2
        assert annotations["included"] == str
        assert annotations["also_included"] == bool
        # excluded should not be in annotations
        assert "excluded" not in annotations

    def test_empty_class(self):
        """Test empty class returns no fields"""

        class EmptyClass:
            pass

        annotations = get_msgspec_annotations(EmptyClass)

        assert len(annotations) == 0

    def test_class_without_annotations(self):
        """Test class with attributes but no annotations"""

        class NoAnnotations:
            # These won't be picked up because they lack type annotations
            value = 10
            name = "test"

        annotations = get_msgspec_annotations(NoAnnotations)

        assert len(annotations) == 0

    def test_complex_types(self):
        """Test with complex type annotations"""
        from typing import Dict, List

        class ComplexTypes:
            items: List[str] = []
            optional_value: Optional[int] = None
            mapping: Dict[str, Any] = {}

        annotations = get_msgspec_annotations(ComplexTypes)

        assert len(annotations) == 3
        assert annotations["items"] == List[str]
        assert annotations["optional_value"] == Optional[int]
        assert annotations["mapping"] == Dict[str, Any]

    def test_none_as_default(self):
        """Test that None can be used as a default value"""

        class NoneDefault:
            value: Optional[str] = None
            number: Optional[int] = None

        annotations = get_msgspec_annotations(NoneDefault)

        assert len(annotations) == 2
        assert annotations["value"] == Optional[str]
        assert annotations["number"] == Optional[int]

    def test_class_variables_vs_instance_variables(self):
        """Test that only class variables with annotations are included"""

        class TestClass:
            class_var: int = 10
            # This won't be included (no annotation)
            another_var = 20

        annotations = get_msgspec_annotations(TestClass)

        assert len(annotations) == 1
        assert annotations["class_var"] == int

    def test_non_class_input_raises_error(self):
        """Test that non-class input raises TypeError"""
        import pytest

        with pytest.raises(TypeError):
            get_msgspec_annotations("not a class")

        with pytest.raises(TypeError):
            get_msgspec_annotations(42)
