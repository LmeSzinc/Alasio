from typing import ForwardRef, Generic, List, Optional, TypeVar, Union

import pytest
from msgspec import NODEFAULT, Struct, UNSET, UnsetType, field

from alasio.ext.msgspec_error.parse_struct import (
    get_field_default,
    get_field_name,
    get_field_typehint,
)


# --- Test Data Setup ---
# A comprehensive Struct for testing field names and defaults.
class SimpleStruct(Struct):
    a_required: int
    b_aliased: str = field(name="bField")
    c_default_value: float = 3.14
    d_default_factory: list = field(default_factory=list)
    e_unset: Union[str, UnsetType] = UNSET
    f_default_none: Optional[int] = None


# Structs for testing type hint resolution.
class AnotherStruct(Struct):
    id: int


class ForwardRefStruct(Struct):
    # Test for string-based forward reference
    ref_field_str: "AnotherStruct"
    # Test for ForwardRef object reference
    ref_field_obj: ForwardRef("AnotherStruct")


# Generic Structs for testing TypeVar resolution.
T = TypeVar("T")


class GenericBase(Struct, Generic[T]):
    generic_data: T
    constant_field: int  # A non-generic field for control


class ConcreteInt(GenericBase[int]):
    pass


class ConcreteListStr(GenericBase[List[str]]):
    pass


# Structs for testing inheritance (MRO traversal).
class Parent(Struct):
    parent_field: str
    overridden_field: str = "parent_default"


class Child(Parent, kw_only=True):
    child_field: int
    overridden_field: UnsetType = UNSET


# A simple class that is not a msgspec.Struct, for error testing.
class NotAStruct:
    pass


# A struct with a factory that will raise an error, for testing error handling.
def _failing_factory():
    raise ValueError("Factory intentionally fails")


class StructWithFailingFactory(Struct):
    failing_field: list = field(default_factory=_failing_factory)


# =================================================================
# Test Class for `get_field_name`
# =================================================================
class TestGetFieldName:
    """Tests for the `get_field_name` utility function."""

    def test_get_name_with_alias(self):
        """
        Tests if the function correctly converts an aliased `encode_name`
        (defined with `field(name=...)`) back to its Python attribute name.
        """
        assert get_field_name(SimpleStruct, "bField") == "b_aliased"

    def test_get_name_without_alias(self):
        """
        Tests if the function correctly handles fields without an alias,
        where the `encode_name` is the same as the attribute name.
        """
        assert get_field_name(SimpleStruct, "a_required") == "a_required"

    def test_non_existent_encode_name_raises_attribute_error(self):
        """
        Tests that an AttributeError is raised when the provided `encode_name`
        does not exist on the model.
        """
        with pytest.raises(
                AttributeError,
                match=f'Type {SimpleStruct} has no field with encode_name="non_existent"',
        ):
            get_field_name(SimpleStruct, "non_existent")

    def test_invalid_model_type_raises_attribute_error(self):
        """
        Tests that an AttributeError is raised if the provided `model` is not
        a valid `msgspec.Struct` subclass.
        """
        with pytest.raises(
                AttributeError, match=f"Type {NotAStruct} is not a valid msgspec.Struct"
        ):
            get_field_name(NotAStruct, "a")


# =================================================================
# Test Class for `get_field_default`
# =================================================================
class TestGetFieldDefault:
    """Tests for the `get_field_default` utility function."""

    @pytest.mark.parametrize(
        "field_name, expected_value",
        [
            # A required field has no default.
            ("a_required", NODEFAULT),
            # A field with a simple default value.
            ("c_default_value", 3.14),
            # A field with a default_factory; the factory is called to get the value.
            ("d_default_factory", []),
            # A field marked as UNSET has no effective default.
            ("e_unset", NODEFAULT),
            # A field with `None` as its default value.
            ("f_default_none", None),
        ],
    )
    def test_get_defaults(self, field_name, expected_value):
        """
        Tests various default scenarios: required, simple value,
        default_factory, UNSET, and None.
        """
        default = get_field_default(SimpleStruct, field_name)
        assert default == expected_value

    def test_default_unset_in_child_class(self):
        """
        Tests that a field with a default in the parent, when set to UNSET
        in the child, is correctly identified as having no default.
        """
        # The child makes `overridden_field` required, so it has no default.
        assert get_field_default(Child, "overridden_field") is NODEFAULT

    def test_failing_factory_returns_nodefault(self):
        """
        Tests that if a default_factory raises an exception during invocation,
        the function returns NODEFAULT.
        """
        assert get_field_default(StructWithFailingFactory, "failing_field") is NODEFAULT

    def test_non_existent_field_name_raises_attribute_error(self):
        """
        Tests that an AttributeError is raised when the provided `field_name`
        does not exist on the model.
        """
        with pytest.raises(
                AttributeError,
                match=f'Type {SimpleStruct} has no field with field_name="non_existent"',
        ):
            get_field_default(SimpleStruct, "non_existent")

    def test_invalid_model_type_raises_attribute_error(self):
        """
        Tests that an AttributeError is raised if the provided `model` is not
        a valid `msgspec.Struct` subclass.
        """
        with pytest.raises(
                AttributeError, match=f"Type {NotAStruct} is not a valid msgspec.Struct"
        ):
            get_field_default(NotAStruct, "a")


# =================================================================
# Test Class for `get_field_typehint`
# =================================================================
class TestGetFieldTypehint:
    """Tests for the `get_field_typehint` utility function."""

    @pytest.mark.parametrize(
        "model, field_name, expected_type",
        [
            (SimpleStruct, "a_required", int),
            (SimpleStruct, "b_aliased", str),
            (SimpleStruct, "c_default_value", float),
            (SimpleStruct, "d_default_factory", list),
            (SimpleStruct, "e_unset", Union[str, UnsetType]),
            (SimpleStruct, "f_default_none", Optional[int]),
            (Child, "parent_field", str),
            (Child, "child_field", int),
        ],
    )
    def test_basic_and_union_types(self, model, field_name, expected_type):
        """Tests retrieval of basic types (int, str) and composite types (Union, Optional)."""
        assert get_field_typehint(model, field_name) == expected_type

    def test_typehint_of_field_unset_in_child(self):
        """
        Tests that the type hint for a field overridden with UNSET in a child
        class is correctly identified as `UnsetType`.
        """
        # The MRO search should find the annotation in `Child` first.
        assert get_field_typehint(Child, "overridden_field") is UnsetType

    def test_forward_ref_as_string(self):
        """Tests that a string-based forward reference is correctly resolved."""
        assert get_field_typehint(ForwardRefStruct, "ref_field_str") is AnotherStruct

    def test_forward_ref_as_object(self):
        """Tests that a `ForwardRef` object is correctly resolved."""
        assert get_field_typehint(ForwardRefStruct, "ref_field_obj") is AnotherStruct

    def test_typevar_resolution_with_simple_type(self):
        """Tests that a TypeVar is correctly replaced by a concrete simple type (int)."""
        assert get_field_typehint(ConcreteInt, "generic_data") is int

    def test_typevar_resolution_with_complex_type(self):
        """Tests that a TypeVar is correctly replaced by a concrete complex type (List[str])."""
        assert get_field_typehint(ConcreteListStr, "generic_data") == List[str]

    def test_non_generic_field_in_generic_struct(self):
        """
        Tests that a non-generic field in a generic class returns its own
        type without being affected by TypeVar substitution.
        """
        assert get_field_typehint(ConcreteInt, "constant_field") is int

    def test_inheritance_from_parent_class(self):
        """Tests that the function can find a type hint for a field defined in a parent class."""
        assert get_field_typehint(Child, "parent_field") is str

    def test_field_on_child_class(self):
        """Tests that the function finds the type hint on the child class itself."""
        assert get_field_typehint(Child, "child_field") is int

    def test_non_existent_field_raises_attribute_error(self):
        """
        Tests that an AttributeError is raised with the standardized message if a field
        cannot be found in the model's MRO.
        """
        with pytest.raises(
                AttributeError,
                match=f'Type {Child} has no field with field_name="non_existent"',
        ):
            get_field_typehint(Child, "non_existent")

    def test_model_without_annotations_raises_specific_error(self):
        """
        Tests that the function raises a specific AttributeError for types
        lacking the `__annotations__` attribute.
        """
        # UPDATED: Check for the new explicit error from the try/except block.
        with pytest.raises(
                AttributeError,
                match=f"Type {NotAStruct} is not a valid msgspec.Struct"
        ):
            get_field_typehint(NotAStruct, "any_field")
