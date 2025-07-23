from typing import Literal


def removeprefix(s, prefix):
    """
    Backport `string.removeprefix(prefix)`, which is on Python>=3.9

    Args:
        s (T):
        suffix (T):

    Returns:
        T:
    """
    if s.startswith(prefix):
        return s[len(prefix):]
    else:
        return s


def removesuffix(s, suffix):
    """
    Backport `string.removesuffix(suffix)`, which is on Python>=3.9

    Args:
        s (T):
        suffix (T):

    Returns:
        T:
    """
    if s.endswith(suffix):
        return s[:-len(suffix)]
    else:
        return s


# Quote LiteralType because typing_extensions.LiteralType is not available on 3.8
def to_literal(items):
    """
    Dynamically create literal object from a list, `Literal[*items]`, which is on Python>=3.11

    Useful when you don't want to write the same things twice like:
        lang = ['zh', 'en', 'ja', 'kr']
        langT = Literal['zh', 'en', 'ja', 'kr']
    With backport you have:
        lang = ['zh', 'en', 'ja', 'kr']
        langT = to_literal(lang)

    Args:
        items (iterable[T]):

    Returns:
        LiteralType[T]:
    """
    return Literal.__getitem__(tuple(items))
