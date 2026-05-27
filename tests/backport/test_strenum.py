from enum import auto

import pytest

from alasio.backport.strenum import StrEnum


class TestStrEnum:
    def test_basic(self):
        class Color(StrEnum):
            RED = "red"
            GREEN = "green"

        assert isinstance(Color.RED, str)
        assert isinstance(Color.RED, Color)
        assert Color.RED == "red"
        assert Color.RED.upper() == "RED"
        assert str(Color.RED) == "red"
        assert format(Color.RED) == "red"

    def test_auto(self):
        class Color(StrEnum):
            RED = auto()
            GREEN = auto()

        assert Color.RED == "red"
        assert Color.GREEN == "green"

    def test_contains(self):
        class Color(StrEnum):
            RED = "red"
            GREEN = "green"

        assert "red" in Color
        assert Color.RED in Color
        assert "blue" not in Color
        assert 1 not in Color
        assert None not in Color
        assert [] not in Color
        assert {} not in Color

        class Other(StrEnum):
            BLUE = "blue"

        assert Other.BLUE not in Color

        # Even if values match, it's not a member of Color
        # However, it IS a string, and its value is "blue"
        # Wait, if Other.BLUE is a string, and value is "blue", 
        # and "blue" is not in Color, then it's False.
        # If Other.BLUE value was "red", what then?
        class OtherRed(StrEnum):
            RED = "red"

        # In Python 3.12:
        # "An enumeration is iterable, returning each of its members in return ..."
        # "The membership operator in is used to check for the existence of an enumeration member"
        # However, for StrEnum, members are strings.
        # In 3.12:
        # >>> Color.RED in Color
        # True
        # >>> "red" in Color
        # True
        # >>> OtherRed.RED in Color
        # True (because OtherRed.RED is a string "red")
        assert OtherRed.RED in Color

        class MyStr(str):
            pass

        assert MyStr("red") in Color
        assert MyStr("blue") not in Color

    def test_comparison(self):
        class Color(StrEnum):
            RED = "red"
            GREEN = "green"

        assert Color.RED == "red"
        assert Color.RED != "green"
        assert Color.RED < "yellow"
        assert Color.RED > "blue"

    def test_mixed_types(self):
        # StrEnum members should be strings
        with pytest.raises(TypeError):
            class Invalid(StrEnum):
                RED = 1

    def test_init_validation(self):
        class Color(StrEnum):
            RED = "red"

        # StrEnum(value) should work like Enum(value)
        assert Color("red") is Color.RED

        with pytest.raises(ValueError):
            Color("blue")

    def test_str_arguments(self):
        # Python 3.12 StrEnum handles arguments like str()
        class MyStrEnum(StrEnum):
            UTF8 = b'abc', 'utf-8'
            LATIN1 = b'\xff', 'latin-1'

        assert MyStrEnum.UTF8 == "abc"
        assert MyStrEnum.LATIN1 == "\xff"

        # Validation of arguments count
        with pytest.raises(TypeError):
            class TooManyArgs(StrEnum):
                VAL = "a", "b", "c", "d"

        # Validation of argument types
        with pytest.raises(TypeError):
            class InvalidEncoding(StrEnum):
                VAL = b"abc", 123

    def test_repr(self):
        class Color(StrEnum):
            RED = "red"

        # StrEnum inherits Enum.__repr__
        assert repr(Color.RED) == "<Color.RED: 'red'>"

    def test_values(self):
        class Color(StrEnum):
            RED = "red"
            GREEN = "green"

        # Check values are indeed strings
        assert Color.RED.value == "red"
        assert isinstance(Color.RED.value, str)

    def test_iteration(self):
        class Color(StrEnum):
            RED = "red"
            GREEN = "green"

        assert list(Color) == [Color.RED, Color.GREEN]
        assert [c.value for c in Color] == ["red", "green"]

    def test_subclassing_str_methods(self):
        class Color(StrEnum):
            RED = "red"

        # StrEnum is a str
        assert Color.RED.startswith("r")
        assert Color.RED.split("e") == ["r", "d"]
        assert Color.RED + "dish" == "reddish"
        assert "pure " + Color.RED == "pure red"
