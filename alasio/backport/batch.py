import sys
from typing import Generator


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
