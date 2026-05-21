from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from typing_extensions import Self

if sys.version_info >= (3, 12):
    from enum import EnumMeta, StrEnum
else:
    from enum import Enum
    from enum import EnumMeta as _EnumMeta


    class EnumMeta(_EnumMeta):
        # __contains__ was updated in 3.12 to no longer raise TypeError
        # https://docs.python.org/3/library/enum.html#enum.EnumType.__contains__
        # https://github.com/python/cpython/blob/main/Lib/enum.py#
        # The implementation isn't identical to stdlib's because we only care about StrEnum(s) so it's a guarantee
        # our values will either be an instance of the StrEnum or a string value.
        # Despite that, it should be functionally the same for StrEnum.
        def __contains__(cls: type[Any], value: object) -> bool:
            if isinstance(value, cls):
                return True
            if isinstance(value, str):
                return value in cls._value2member_map_
            return False


    # https://github.com/python/cpython/blob/main/Lib/enum.py
    class StrEnum(str, Enum, metaclass=EnumMeta):
        """Enum where members are also (and must be) strings."""

        def __new__(cls, *values: str) -> Self:
            """Values must already be of type `str`."""
            if len(values) > 3:
                raise TypeError(f"too many arguments for str(): {values!r}")
            if len(values) == 1:
                # it must be a string
                if not isinstance(values[0], str):
                    raise TypeError(f"{values[0]!r} is not a string")
            if len(values) >= 2:
                # check that encoding argument is a string
                if not isinstance(values[1], str):
                    raise TypeError(f"encoding must be a string, not {values[1]!r}")
            if len(values) == 3:
                # check that errors argument is a string
                if not isinstance(values[2], str):
                    raise TypeError(f"errors must be a string, not {values[2]!r}")
            value = str(*values)
            member = str.__new__(cls, value)
            member._value_ = value
            return member

        def __str__(self) -> str:
            return self.value  # type: ignore[no-any-return]

        @staticmethod
        def _generate_next_value_(name: str, start: int, count: int, last_values: list[str]) -> str:
            """
            Return the lower-cased version of the member name.
            """
            return name.lower()
