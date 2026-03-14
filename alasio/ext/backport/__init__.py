import sys
from typing import Generator

if sys.version_info >= (3, 9):
    def removeprefix(s, prefix):
        return s.removeprefix(prefix)


    def removesuffix(s, prefix):
        return s.removesuffix(prefix)

else:
    # Backport `string.removeprefix(prefix)`, which is on Python>=3.9
    def removeprefix(s, prefix):
        """
        Args:
            s (T):
            prefix (T):

        Returns:
            T:
        """
        if s.startswith(prefix):
            return s[len(prefix):]
        return s


    # Backport `string.removesuffix(suffix)`, which is on Python>=3.9
    def removesuffix(s, suffix):
        """
        Args:
            s (T):
            suffix (T):

        Returns:
            T:
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


if sys.version_info < (3, 12):
    # no itertools.batched() on Python<3.12
    def batched(iterable, n: int, *, strict: bool = False) -> "Generator[tuple]":
        """
        Batch data from the *iterable* into tuples of length *n*.

        note: The last batch may be shorter than *n* if *strict* is
           True or raise a ValueError otherwise.

        Example:
            >>> i = batched(range(25), 10)
            >>> print(next(i))
            (0, 1, 2, 3, 4, 5, 6, 7, 8, 9)
            >>> print(next(i))
            (10, 11, 12, 13, 14, 15, 16, 17, 18, 19)
            >>> print(next(i))
            (20, 21, 22, 23, 24)
            >>> print(next(i))
            Traceback (most recent call last):
             ...
            StopIteration
            >>> list(batched('ABCD', 2))
            [('A', 'B'), ('C', 'D')]
            >>> list(batched('ABCD', 3, strict=False))
            [('A', 'B', 'C'), ('D',)]
            >>> list(batched('ABCD', 3, strict=True))
            Traceback (most recent call last):
             ...
            ValueError: batched(): incomplete batch

        seealso:: :python:`itertools.batched <library/itertools.html#itertools.batched>`, backported from Python 3.12.
           *strict* option, backported from Python 3.13

        Args:
            iterable:
            n: How many items of the iterable to get in one chunk.
            strict: Raise a ValueError if the final batch is shorter than *n*.

        Raises:
            TypeError: *n* cannot be interpreted as an integer
            ValueError: batched(): incomplete batch
        """
        if not isinstance(n, int):
            raise TypeError(f'{type(n).__name__!r} object cannot be interpreted as an integer')
        group = []
        for item in iterable:
            group.append(item)
            if len(group) == n:
                yield tuple(group)
                group.clear()
        if group:
            if strict:
                raise ValueError('batched(): incomplete batch')
            yield tuple(group)

elif sys.version_info < (3, 13):
    # having itertools.batched() but no `strict` argument on Python==3.12
    from itertools import batched as _batched


    def batched(iterable, n: int, *, strict: bool = False) -> "Generator[tuple]":
        for group in _batched(iterable, n):
            if strict and len(group) < n:
                raise ValueError('batched(): incomplete batch')
            yield group

else:
    from itertools import batched as _batched

    batched = _batched
