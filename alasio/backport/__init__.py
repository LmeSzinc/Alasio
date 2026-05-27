import sys
from typing import overload


@overload
def removeprefix(s: str, prefix: str) -> str: ...


@overload
def removeprefix(s: bytes, prefix: bytes) -> bytes: ...


@overload
def removesuffix(s: str, suffix: str) -> str: ...


@overload
def removesuffix(s: bytes, suffix: bytes) -> bytes: ...


if sys.version_info >= (3, 9):
    def removeprefix(s, prefix):
        """
        Args:
            s (str | bytes):
            prefix (str | bytes):

        Returns:
            str | bytes:
        """
        return s.removeprefix(prefix)


    def removesuffix(s, suffix):
        """
        Args:
            s (str | bytes):
            suffix (str | bytes):

        Returns:
            str | bytes:
        """
        return s.removesuffix(suffix)

else:
    def removeprefix(s, prefix):
        """
        Backport `string.removeprefix(prefix)`, which is on Python>=3.9

        Args:
            s (str | bytes):
            prefix (str | bytes):

        Returns:
            str | bytes:
        """
        if s.startswith(prefix):
            return s[len(prefix):]
        return s


    def removesuffix(s, suffix):
        """
        Backport `string.removesuffix(suffix)`, which is on Python>=3.9

        Args:
            s (str | bytes):
            suffix (str | bytes):

        Returns:
            str | bytes:
        """
        # s[:-0] is empty string, so we need to check if suffix is empty
        if suffix and s.endswith(suffix):
            return s[:-len(suffix)]
        return s


# Quote LiteralType because typing_extensions.LiteralType is not available on 3.8
def to_literal(items):
    """
    Dynamically create literal object from a list, `Literal[*items]`, which is on Python>=3.11

    Useful when you don't want to write the same things twice like:
        lang = ['zh', 'en', 'ja', 'kr']
        langT = Literal['zh', 'en', 'ja', 'kr']
    With backport you can do:
        lang = ['zh', 'en', 'ja', 'kr']
        langT = to_literal(lang)

    Args:
        items (iterable[T]):

    Returns:
        LiteralType[T]:
    """
    from typing import Literal
    return Literal.__getitem__(tuple(items))


def process_cpu_count():
    """
    Backport os.process_cpu_count() on python >= 3.13

    If the current environment lacks this function (3.7-3.12), fall back to os.cpu_count.
    os.cpu_count returns the number of physical/logical cores without considering process affinity,
    but this is the closest behavior achievable in older Python versions.

    Returns:
        int | None:
    """
    import os
    get_cpu_count = getattr(os, "process_cpu_count", os.cpu_count)
    try:
        # cpu_count may return None (e.g., on systems where it cannot be detected)
        return get_cpu_count()
    except Exception:
        return None
