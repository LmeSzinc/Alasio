from msgspec import NODEFAULT, UNSET


def get_class_annotation_dict(cls):
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


def get_msgspec_annotation_dict(cls):
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


def get_class_annotation(cls, key):
    """
    Get the annotation for a specific key from a class and its MRO in sequential order.

    Iterates the MRO in sequential order (most-derived class first) and returns the
    annotation for the given key from the first class that defines it.

    Args:
        cls (type): A class
        key (str): The annotation name to look up

    Returns:
        Any: The annotation for the given key

    Raises:
        AttributeError: If the key is not found in any class in the MRO
    """
    try:
        mro = cls.__mro__
    except AttributeError:
        raise TypeError(f'Input must be a class, got {cls}')

    for base in mro:
        annotations = getattr(base, '__annotations__', None)
        if annotations and key in annotations:
            return annotations[key]

    raise AttributeError(f"'{cls.__name__}' has no annotation '{key}'")


def get_msgspec_annotation(cls, key):
    """
    Get the annotation for a specific key from a class and its MRO, with msgspec UNSET support.

    Iterates the MRO in sequential order (most-derived class first) and returns the
    annotation for the given key from the first class that defines it, skipping any
    field whose value is msgspec.UNSET.

    Args:
        cls (type): A class
        key (str): The annotation name to look up

    Returns:
        Any: The annotation for the given key

    Raises:
        AttributeError: If the key is not found (or is UNSET) in any class in the MRO
    """
    try:
        mro = cls.__mro__
    except AttributeError:
        raise TypeError(f'Input must be a class, got {cls}')

    for base in mro:
        annotations = getattr(base, '__annotations__', None)
        if annotations and key in annotations:
            # Check if the field is marked UNSET
            value = getattr(cls, key, NODEFAULT)
            if value == UNSET:
                raise AttributeError(f"'{cls.__name__}' has no annotation '{key}'")
            return annotations[key]

    raise AttributeError(f"'{cls.__name__}' has no annotation '{key}'")
