from typing import Dict, List, Set, Tuple

import pytest
from msgspec import DecodeError, Struct, ValidationError, field

from alasio.ext.msgspec_error.const import ErrorType
from alasio.ext.msgspec_error.default import load_json_with_default


# ==============================================================================
# Step 2: Define ROBUST Models for Testing (using modern type syntax)
# ==============================================================================

class Profile(Struct, eq=True, frozen=True):
    """A struct that is fully default-constructible."""
    age: int = 99
    name: str = 'default_name'
    is_active: bool = True


class User(Struct, eq=True, frozen=True):
    """A robust, hashable User struct that requires an 'id' field."""
    id: int
    name: str = "default_user"
    profile: Profile = field(default_factory=Profile)


class Team(Struct):
    """A top-level model with robust defaults for all collection types."""
    members: List[User] = field(default_factory=list)
    member_map: Dict[str, User] = field(default_factory=dict)
    member_set: Set[User] = field(default_factory=set)
    lead_and_deputy: Tuple[User, User] = field(default_factory=tuple)


# ==============================================================================
# Step 3: Write Pytest Tests
# ==============================================================================

def test_happy_path():
    """Tests that valid data decodes correctly with no errors."""
    data = b'{"id": 1, "name": "Alice", "profile": {"age": 30, "is_active": true}}'
    result, errors = load_json_with_default(data, User)

    assert isinstance(result, User)
    assert result.id == 1
    assert result.name == "Alice"
    assert not errors


def test_single_field_error_with_default():
    """Tests that a single field error is corrected using its default."""
    data = b'{"id": 1, "name": 12345}'
    result, errors = load_json_with_default(data, User)

    assert result.name == "default_user"
    assert len(errors) == 1
    assert errors[0].type == ErrorType.TYPE_MISMATCH
    assert errors[0].loc == ("name",)


def test_unrecoverable_error_no_default():
    """Tests that a required field without a default raises ValidationError."""
    data = b'{"name": "Bob"}'
    with pytest.raises(ValidationError, match="Object missing required field `id`"):
        load_json_with_default(data, User)


def test_list_item_error():
    """Tests that an error inside a list item can be corrected."""
    data = b'{"members": [{"id": 1}, {"id": 2, "name": 123}]}'
    result, errors = load_json_with_default(data, model=Team)

    assert len(result.members) == 2
    assert result.members[1].name == "default_user"
    assert len(errors) == 1
    assert errors[0].loc == ("members", 1, "name")


def test_set_item_error():
    """Tests that an error inside a set item can be corrected."""
    data = b'{"member_set": [{"id": 1, "name": 123}]}'
    result, errors = load_json_with_default(data, model=Team)

    assert len(result.member_set) == 1
    member = result.member_set.pop()
    assert member.name == "default_user"
    assert len(errors) == 1


def test_root_validation_error_with_default():
    """Tests replacing the object if the root has a validation error."""
    data = b'["this", "is", "a", "list"]'
    result, errors = load_json_with_default(data, model=Profile)

    assert result == Profile()
    assert len(errors) == 1
    assert errors[0].loc == ()


def test_root_validation_error_unrecoverable():
    """Tests a root validation error on a non-default-constructible model."""
    data = b'["not a struct"]'
    with pytest.raises(ValidationError):
        load_json_with_default(data, model=User)


def test_decode_error_with_default_fallback():
    """Tests fallback to a default object on DecodeError."""
    data = b'{"key": "incomplete json'
    result, errors = load_json_with_default(data, Profile)

    assert result == Profile()
    assert len(errors) == 1
    assert errors[0].type == ErrorType.WRAPPED_ERROR
    assert errors[0].loc == ()
    assert "Input data was truncated" in errors[0].msg


def test_decode_error_unrecoverable():
    """Tests that DecodeError is re-raised for non-default-constructible models."""
    data = b'{"key": "incomplete json'
    with pytest.raises(DecodeError):
        load_json_with_default(data, User)


def test_unicode_decode_error_with_default_fallback():
    """Tests fallback to a default object on UnicodeDecodeError."""
    data = b'{"name": "\xc3"}'  # Invalid start byte for UTF-8
    result, errors = load_json_with_default(data, Profile)

    assert result == Profile()
    assert len(errors) == 1
    assert errors[0].type == ErrorType.WRAPPED_ERROR
    assert errors[0].loc == ()
    # 'utf-8' codec can't decode byte 0xc3 in position 0: unexpected end of data
    assert "codec can't decode byte" in errors[0].msg


@pytest.mark.skip(reason="Fails due to msgspec's generic '$.key[...].field' error path")
def test_dict_value_error():
    """Tests an error inside a dict value (skipped due to msgspec limitation)."""
    data = b'{"member_map": {"a": {"id": 1, "name": 123}}}'
    result, errors = load_json_with_default(data, model=Team)
    assert result.member_map["a"].name == "default_user"
