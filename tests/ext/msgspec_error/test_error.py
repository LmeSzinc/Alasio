# test_error_parser.py

import pytest
import msgspec
from datetime import datetime, date, time, timedelta
from decimal import Decimal
from enum import Enum
from typing import Union, Tuple, List, Dict, Any
from typing_extensions import Annotated
from uuid import UUID

# Import the code to be tested from local files
from alasio.ext.msgspec_error.const import ErrorType
from alasio.ext.msgspec_error.error import (
    ErrorCtx,
    MsgspecError,
    get_error_path,
    get_error_type,
    NODEFAULT,
)


def check_error(
        model_type: Any,
        invalid_data: Any,
        expected_type: ErrorType,
        expected_loc: Tuple[Union[str, int], ...],
        expected_ctx: Union[ErrorCtx, object] = NODEFAULT,
        *,
        use_msgpack: bool = False,
):
    """
    Helper to test the full error parsing pipeline.

    It decodes invalid data to trigger a real msgspec.ValidationError,
    then asserts that the parsing functions (`get_error_path`, `get_error_type`)
    correctly interpret the resulting error message.
    """
    if use_msgpack:
        decoder = msgspec.msgpack.Decoder(model_type)
        # If data is already bytes (for custom msgpack extensions), use it directly.
        encoded_data = invalid_data if isinstance(invalid_data, bytes) else msgspec.msgpack.encode(invalid_data)
    else:
        decoder = msgspec.json.Decoder(model_type)
        encoded_data = msgspec.json.encode(invalid_data)

    try:
        decoder.decode(encoded_data)
        pytest.fail(f"ValidationError was not raised for type {model_type} with data {invalid_data}")
    except msgspec.ValidationError as e:
        error_str = str(e)
        error_type, ctx = get_error_type(error_str)
        path = get_error_path(error_str)

        assert path == expected_loc, f"get_error_path failed. Expected {expected_loc}, got {path}"
        assert error_type == expected_type, (f"get_error_type returned wrong type. Expected {expected_type}, "
                                             f"got {error_type}")

        if isinstance(expected_ctx, ErrorCtx):
            assert ctx == expected_ctx, (f"get_error_type returned wrong context. Expected {expected_ctx}, "
                                         f"got {ctx}")
        else:
            assert ctx is expected_ctx, "get_error_type should return NODEFAULT"


# --- Parametrization Scenarios for Nested Paths ---

def struct_wrapper(model, data):
    """Wrapper to nest the target model inside another struct."""

    class Wrapper(msgspec.Struct):
        payload: model

    return Wrapper, {'payload': data}


def list_wrapper(model, data):
    """Wrapper to nest the target model inside a list."""

    class Wrapper(msgspec.Struct):
        items: List[model]

    return Wrapper, {'items': [data]}


def dict_wrapper(model, data):
    """Wrapper to nest the target model inside a dict."""

    class Wrapper(msgspec.Struct):
        items_by_id: Dict[str, model]

    return Wrapper, {'items_by_id': {'item1': data}}


# This fixture provides scenarios for testing how paths are extended when a model is nested.
# The root case is now handled by dedicated tests.
nesting_scenarios = [
    pytest.param(struct_wrapper, ('payload',), id="nested_in_struct"),
    pytest.param(list_wrapper, ('items', 0), id="nested_in_list"),
    # msgspec only shows $.item1[...] we can't get the dict key
    pytest.param(dict_wrapper, ('items_by_id', '...'), id="nested_in_dict"),
]


# --- Test Classes ---

class TestStructuralErrors:
    """Tests for missing fields, unknown fields, and type mismatches."""

    def test_type_mismatch_root(self):
        """A type mismatch on a primitive at the root has an empty path."""
        check_error(int, "a string", ErrorType.TYPE_MISMATCH, ())

    @pytest.mark.parametrize("wrapper, path_prefix", nesting_scenarios)
    def test_type_mismatch_nested(self, wrapper, path_prefix):
        """A type mismatch on a field inside a model."""

        class Model(msgspec.Struct): age: int

        model, data = wrapper(Model, {'age': 'not-an-int'})
        check_error(model, data, ErrorType.TYPE_MISMATCH, path_prefix + ('age',))

    def test_missing_field_base(self):
        """A missing field error path is always relative to the struct."""

        class Model(msgspec.Struct):
            id: int
            name: str

        check_error(Model, {'name': 'test'}, ErrorType.MISSING_FIELD, ('id',))

    @pytest.mark.parametrize("wrapper, path_prefix", nesting_scenarios)
    def test_missing_field_nested(self, wrapper, path_prefix):
        """Tests path extension for a missing field error."""

        class Model(msgspec.Struct):
            id: int
            name: str

        model, data = wrapper(Model, {'name': 'test'})
        check_error(model, data, ErrorType.MISSING_FIELD, path_prefix + ('id',))


class TestConstraintErrors:
    """Tests for constraint violations (length, numeric, pattern, timezone)."""

    def test_length_constraint_root(self):
        """A length constraint on a top-level list has an empty path."""
        T = Annotated[List[int], msgspec.Meta(min_length=3)]
        check_error(T, [1, 2], ErrorType.ARRAY_LENGTH_CONSTRAINT, (), ErrorCtx(min_length=3))

    @pytest.mark.parametrize("wrapper, path_prefix", nesting_scenarios)
    def test_length_constraint_nested(self, wrapper, path_prefix):
        """Tests path extension for a length constraint error."""
        T = Annotated[List[int], msgspec.Meta(min_length=3)]

        class Model(msgspec.Struct): tags: T

        model, data = wrapper(Model, {'tags': [1, 2]})
        check_error(model, data, ErrorType.ARRAY_LENGTH_CONSTRAINT, path_prefix + ('tags',), ErrorCtx(min_length=3))

    def test_numeric_constraint_root(self):
        """A numeric constraint on a top-level number has an empty path."""
        T = Annotated[int, msgspec.Meta(ge=18)]
        check_error(T, 17, ErrorType.NUMERIC_CONSTRAINT, (), ErrorCtx(ge=18))

    @pytest.mark.parametrize("wrapper, path_prefix", nesting_scenarios)
    def test_numeric_constraint_nested(self, wrapper, path_prefix):
        """Tests path extension for a numeric constraint error."""
        T = Annotated[int, msgspec.Meta(ge=18)]

        class Model(msgspec.Struct): age: T

        model, data = wrapper(Model, {'age': 17})
        check_error(model, data, ErrorType.NUMERIC_CONSTRAINT, path_prefix + ('age',), ErrorCtx(ge=18))


class TestInvalidValueErrors:
    """Tests for values of the right type but with invalid format or value."""

    def test_invalid_tag_value_base(self):
        """An invalid tag error path is always relative to the tag field."""

        # This is the modern, correct way to define tagged unions.
        class TaggedBase(msgspec.Struct, tag_field='type'): pass

        class Cat(TaggedBase): name: str

        class Dog(TaggedBase): name: str

        T = Union[Cat, Dog]
        invalid_data = {'type': 'Bird', 'name': 'tweety'}
        check_error(T, invalid_data, ErrorType.INVALID_TAG_VALUE, ('type',))


class TestWrappedAndMsgpackErrors:
    """Tests for user-code-generated errors and msgpack-specific issues."""

    # This byte string is a structurally valid `timestamp64` extension, but its
    # nanosecond component is > 999_999_999, making it semantically invalid.
    # It will correctly raise a ValidationError upon decoding.
    INVALID_MSGPACK_TS_DATA = b'\xd7\xff\xff\xff\xff\xfc\x00\x00\x00\x00'

    def test_wrapped_error_post_init_root(self):
        """A post-init error on a root object has an empty path."""

        class Model(msgspec.Struct):
            a: int

            def __post_init__(self):
                raise ValueError("Custom validation failed")

        check_error(Model, {'a': 1}, ErrorType.WRAPPED_ERROR, ())

    @pytest.mark.parametrize("wrapper, path_prefix", nesting_scenarios)
    def test_wrapped_error_post_init_nested(self, wrapper, path_prefix):
        """Tests path extension for a post-init error."""

        class Model(msgspec.Struct):
            a: int

            def __post_init__(self):
                raise ValueError("Custom validation failed")

        model, data = wrapper(Model, {'a': 1})
        check_error(model, data, ErrorType.WRAPPED_ERROR, path_prefix)

    def test_invalid_msgpack_timestamp_root(self):
        """An invalid msgspec timestamp at the root has an empty path."""
        check_error(datetime, self.INVALID_MSGPACK_TS_DATA, ErrorType.INVALID_MSGPACK_TIMESTAMP, (), use_msgpack=True)

    def test_invalid_msgpack_timestamp_nested(self):
        """Tests path extension for an invalid msgspec timestamp."""

        class Model(msgspec.Struct): ts: datetime

        encoded_obj = msgspec.msgpack.encode({'ts': msgspec.Raw(self.INVALID_MSGPACK_TS_DATA)})
        check_error(Model, encoded_obj, ErrorType.INVALID_MSGPACK_TIMESTAMP, ('ts',), use_msgpack=True)


class TestAdvancedAdversarial:
    """Adversarial tests to check robustness against tricky inputs."""

    tricky_field_names = [
        pytest.param('key.with.dots', id='field_with_dots'),
        pytest.param('key with - at - keyword', id='field_with_at_keyword'),
        pytest.param('`key`with`backticks`', id='field_with_internal_backticks'),
        pytest.param('RepairThresho`ld1', id='field_with_user_specific_backtick'),
        pytest.param('', id='field_is_empty_string'),
        pytest.param('🚀', id='field_with_emoji'),
    ]

    @pytest.mark.skip
    @pytest.mark.parametrize("tricky_field_name", tricky_field_names)
    def test_path_parser_with_tricky_field_names(self, tricky_field_name):
        """
        Tests that the path parser correctly extracts field names even when they
        contain characters that the parser itself uses as delimiters.
        """

        class Model(msgspec.Struct): data: Dict[str, int]

        check_error(
            Model,
            {'data': {tricky_field_name: 'not-an-int'}},
            ErrorType.TYPE_MISMATCH,
            ('data', tricky_field_name)
        )

    confusing_error_messages = [
        pytest.param("Object missing required field `fake_field`", id="mimic_missing_field"),
        pytest.param("Expected `int` >= 10", id="mimic_numeric_constraint"),
    ]

    @pytest.mark.skip
    @pytest.mark.parametrize("error_message", confusing_error_messages)
    def test_parser_with_confusing_wrapped_error(self, error_message):
        """
        Tests that user-generated errors are always treated as WRAPPED_ERROR,
        even if their message content mimics a structured msgspec error.
        """

        class Model(msgspec.Struct):
            a: int

            def __post_init__(self):
                raise ValueError(error_message)

        # This is a root-level check
        check_error(Model, {'a': 1}, ErrorType.WRAPPED_ERROR, ())
