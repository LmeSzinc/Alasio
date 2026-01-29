from msgspec import NODEFAULT, UNSET


def get_annotations(cls):
    """
    Safely get __annotations__ of a class and its parent classes
    A simpler version of `typing.get_type_hints`, that won't do _eval_type() just leave annotation as it is

    Args:
        cls (type): A class

    Returns:
        dict[str, Any]: {name: annotation}, annotation can be typehint or string
    """
    out = {}
    try:
        mro = cls.__mro__
    except AttributeError:
        raise TypeError(f'Input must be a class, got {cls}')

    for base in reversed(mro):
        annotations = getattr(base, '__annotations__', None)
        if annotations:
            out.update(annotations)

    return out


def get_msgspec_annotations(cls):
    """
    Safely get __annotations__ of a class and its parent classes, with special support for msgspec.
    A simpler version of `typing.get_type_hints`, as we don't call _eval_type() just leave annotation as it is

    Args:
        cls (type): A class

    Returns:
        dict[str, Any]: {name: annotation}, annotation can be typehint or string
    """
    out = {}
    try:
        mro = cls.__mro__
    except AttributeError:
        raise TypeError(f'Input must be a class, got {cls}')

    for base in reversed(mro):
        annotations = getattr(base, '__annotations__', None)
        if not annotations:
            continue
        for name, anno in annotations.items():
            # get value
            value = getattr(cls, name, NODEFAULT)
            # ignore UNSET
            if value == UNSET:
                continue
            out[name] = anno

    return out
