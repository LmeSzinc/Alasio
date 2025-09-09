import datetime
import decimal
import sys
import typing
import uuid
from collections import namedtuple
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Final, List, Literal, NewType, TypedDict

import attrs
import msgspec
import pytest
from msgspec import NODEFAULT
from typing_extensions import Annotated

from alasio.ext.msgspec_error.parse_type import get_default


class MyEnum(Enum):
    A = 1


class MyStruct(msgspec.Struct):
    # This struct cannot be default-constructed
    x: int


class MyDefaultConstructibleStruct(msgspec.Struct):
    # This struct CAN be default-constructed
    x: int = 1


@dataclass
class MyDataClass:
    x: int


class MyTypedDict(TypedDict):
    x: int


MyNamedTuple = namedtuple("MyNamedTuple", ["x"])


@attrs.define
class MyAttrsClass:
    x: int


# -- Pytest Test Functions --

@pytest.mark.parametrize(
    "type_hint, expected_default",
    [
        (int, 0),
        (str, ""),
        (bool, False),
        (float, 0.0),
        (bytes, b""),
        (type(None), None),
        (None, None),
    ],
)
def test_primitive_types_with_guessing(type_hint, expected_default):
    """Tests that primitive types get a zero/empty value when guessing is enabled."""
    assert get_default(type_hint, guess_default=True) == expected_default


@pytest.mark.parametrize(
    "type_hint", [int, str, bool, float, bytes]
)
def test_primitive_types_without_guessing(type_hint):
    """Attack Test: Checks that primitives return NODEFAULT when guessing is disabled."""
    assert get_default(type_hint, guess_default=False) is NODEFAULT


@pytest.mark.parametrize(
    "type_hint, expected_default",
    [
        (list, []),
        (dict, {}),
        (set, set()),
        (frozenset, frozenset()),
        (tuple, ()),
        (bytearray, bytearray()),
        (memoryview, memoryview(b"")),
    ],
)
def test_container_types_always_default_to_empty(type_hint, expected_default):
    """Tests that container types always return an empty container, regardless of the guess_default flag."""
    assert get_default(type_hint, guess_default=True) == expected_default
    assert get_default(type_hint, guess_default=False) == expected_default


@pytest.mark.parametrize(
    "type_hint",
    [
        MyEnum,
        Literal["a", "b"],
        datetime.datetime,
        datetime.date,
        uuid.UUID,
        decimal.Decimal,
        msgspec.Raw,
        typing.Any,
    ],
)
def test_unguessable_types_always_return_nodefault(type_hint):
    """Tests that types without a safe default always return NODEFAULT."""
    assert get_default(type_hint, guess_default=True) is NODEFAULT
    assert get_default(type_hint, guess_default=False) is NODEFAULT


@pytest.mark.parametrize(
    "struct_type, struct_flag, expected",
    [
        # --- Default behavior (struct=False) ---
        (MyStruct, False, {}),
        (MyDataClass, False, {}),
        (MyTypedDict, False, {}),
        (MyNamedTuple, False, {}),
        (MyAttrsClass, False, {}),
        (MyDefaultConstructibleStruct, False, {}),

        # --- New behavior (struct=True) ---
        # msgspec.Struct that is not default-constructible -> NODEFAULT
        (MyStruct, True, NODEFAULT),
        # msgspec.Struct that IS default-constructible -> instance
        (MyDefaultConstructibleStruct, True, MyDefaultConstructibleStruct()),
        # Other struct-like types are unaffected -> {}
        (MyDataClass, True, {}),
        (MyTypedDict, True, {}),
        (MyNamedTuple, True, {}),
        (MyAttrsClass, True, {}),
    ]
)
def test_structured_types(struct_type, struct_flag, expected):
    """Tests how structured types default, including the behavior of the `struct` parameter."""
    # The result here should be independent of guess_default
    res = get_default(struct_type, return_struct=struct_flag)
    if expected is NODEFAULT:
        assert res is NODEFAULT
    else:
        assert res == expected


@pytest.mark.parametrize(
    "type_hint, expected_guess, expected_no_guess",
    [
        # Optional[T] should always prefer None
        (typing.Optional[int], None, None),
        (typing.Union[int, None], None, None),
        # Union picking the first available default
        (typing.Union[int, str], 0, NODEFAULT),
        (typing.Union[str, int], "", NODEFAULT),
        # Attack Test: Union where the first type is unguessable
        (typing.Union[MyEnum, str], "", NODEFAULT),
        # Attack Test: Union where one has a non-guessed default (list)
        (typing.Union[MyEnum, list], [], []),
        (typing.Union[int, list], 0, []),  # With guessing, `int` comes first
        # Union with a structured type
        (typing.Union[MyEnum, MyDataClass], {}, {}),
        # Union with only unguessable types
        (typing.Union[MyEnum, uuid.UUID], NODEFAULT, NODEFAULT),
        # Union including UnsetType
        (typing.Union[msgspec.UnsetType, int], 0, NODEFAULT),
    ],
)
def test_union_and_optional_logic(type_hint, expected_guess, expected_no_guess):
    """Tests the complex logic for Union and Optional types."""
    # Test with guess_default=True
    res_guess = get_default(type_hint, guess_default=True)
    if expected_guess is NODEFAULT:
        assert res_guess is NODEFAULT
    else:
        assert res_guess == expected_guess

    # Test with guess_default=False
    res_no_guess = get_default(type_hint, guess_default=False)
    if expected_no_guess is NODEFAULT:
        assert res_no_guess is NODEFAULT
    else:
        assert res_no_guess == expected_no_guess


@pytest.mark.skipif(sys.version_info < (3, 10), reason="Union `|` syntax requires Python 3.10+")
def test_union_pipe_syntax():
    """Tests the `|` union syntax on Python 3.10+."""
    # With guessing, int is picked
    assert get_default(int | list, guess_default=True) == 0
    # Without guessing, int has no default, but list does
    assert get_default(int | list, guess_default=False) == []
    # Optional always results in None
    assert get_default(int | None, guess_default=True) is None
    assert get_default(int | None, guess_default=False) is None


@pytest.mark.parametrize(
    "type_hint, expected_guess, expected_no_guess",
    [
        # Wrappers around primitives
        (Annotated[int, "meta"], 0, NODEFAULT),
        (Final[int], 0, NODEFAULT),
        (NewType("MyInt", int), 0, NODEFAULT),
        # Wrappers around containers
        (Annotated[list, "meta"], [], []),
        (Final[dict], {}, {}),
        (NewType("MyStrList", list), [], []),
        # Wrappers around unguessable types
        (Annotated[MyEnum, "meta"], NODEFAULT, NODEFAULT),
        # TypeVars
        (typing.TypeVar("T"), NODEFAULT, NODEFAULT),
        (typing.TypeVar("T_bound_int", bound=int), 0, NODEFAULT),
        (typing.TypeVar("T_bound_list", bound=list), [], []),
    ],
)
def test_typing_wrappers(type_hint, expected_guess, expected_no_guess):
    """
    Attack Test: Checks that `get_default` correctly unwraps special typing
    constructs like Annotated, Final, NewType, and TypeVar.
    """
    assert get_default(type_hint, guess_default=True) == expected_guess
    assert get_default(type_hint, guess_default=False) == expected_no_guess


def test_deeply_nested_types():
    """Attack Test: Probes the function's ability to handle deeply nested types."""
    # Case 1: Optional is the outermost type, so the default must be None.
    nested_optional = typing.Optional[List[Dict[str, typing.Union[int, MyEnum]]]]
    assert get_default(nested_optional, guess_default=True) is None
    assert get_default(nested_optional, guess_default=False) is None

    # Case 2: Union is outermost. The first defaultable type is list.
    nested_union = typing.Union[MyEnum, List[Dict[str, typing.Optional[int]]]]
    assert get_default(nested_union, guess_default=True) == []
    assert get_default(nested_union, guess_default=False) == []  # list always defaults to []


# --- Forward Reference Test Setup ---
# These classes must be defined at the module level for `get_type_hints` to find them.

class ForwardRefTargetStruct(msgspec.Struct):
    """A target for forward-ref testing that should default to {}."""
    pass


class ForwardRefTargetEnum(Enum):
    """A target for forward-ref testing that should have no default."""
    ITEM = "item"


class TestForwardRefContainer:
    """A container class to hold annotations with forward references as strings."""
    struct_ref: "ForwardRefTargetStruct"
    enum_ref: "ForwardRefTargetEnum"
    optional_ref: typing.Optional["ForwardRefTargetStruct"]
    union_ref: typing.Union["ForwardRefTargetEnum", list]
    # An attack case: a forward reference inside another type hint
    nested_ref: List["ForwardRefTargetStruct"]


@pytest.mark.parametrize(
    "attr_name, expected_default",
    [
        # A simple forward reference to a struct should resolve to {}
        ("struct_ref", {}),
        # A forward reference to an Enum should resolve to NODEFAULT
        ("enum_ref", NODEFAULT),
        # An Optional forward reference should resolve to None
        ("optional_ref", None),
        # A Union containing a forward ref should pick the defaultable type (list)
        ("union_ref", []),
        # A list of a forward-referenced type should default to an empty list
        ("nested_ref", []),
    ]
)
def test_forward_references(attr_name, expected_default):
    """
    Attack Test: Validates that forward references are correctly handled.
    This test uses `typing.get_type_hints()` to resolve the string annotations
    at runtime, which is the standard library's method for this task.
    """
    # Resolve all forward references in the container class.
    # We pass `globals()` so it can find `ForwardRefTargetStruct` etc.
    resolved_hints = typing.get_type_hints(TestForwardRefContainer, globalns=globals())

    # Get the specific, now-resolved type hint for the current test case.
    type_hint = resolved_hints[attr_name]

    # The behavior should be the same regardless of the `guess_default` flag
    # because none of the results depend on guessing a primitive.
    assert get_default(type_hint, guess_default=True) == expected_default
    assert get_default(type_hint, guess_default=False) == expected_default


def test_union_with_struct_parameter():
    """Tests the interaction between Union types and the `struct=True` parameter."""
    # Case 1: Union with a non-default-constructible Struct.
    # `get_default` should skip it and find the next defaultable type.
    type_hint1 = typing.Union[MyStruct, list]
    assert get_default(type_hint1, return_struct=True) == []

    # Case 2: Union with a default-constructible Struct.
    # `get_default` should return an instance of that struct.
    type_hint2 = typing.Union[MyDefaultConstructibleStruct, list]
    assert get_default(type_hint2, return_struct=True) == MyDefaultConstructibleStruct()

    # Case 3: Order matters. `int` with guessing enabled is chosen over the struct.
    type_hint3 = typing.Union[int, MyDefaultConstructibleStruct]
    assert get_default(type_hint3, return_struct=True, guess_default=True) == 0
    # Without guessing, `int` is skipped, and the struct is chosen.
    assert get_default(type_hint3, return_struct=True, guess_default=False) == MyDefaultConstructibleStruct()

    # Case 4: Another order check. Struct is first, so it's always chosen.
    type_hint4 = typing.Union[MyDefaultConstructibleStruct, int]
    assert get_default(type_hint4, return_struct=True, guess_default=True) == MyDefaultConstructibleStruct()
    assert get_default(type_hint4, return_struct=True, guess_default=False) == MyDefaultConstructibleStruct()
