from msgspec import NODEFAULT, UNSET


def iter_class_field(cls):
    """
    A generator that iterates over all type-annotated class variables
    of a class and its parents, with special support for msgspec.
    A simpler version of `typing.get_type_hints`, that won't do _eval_type() just leave annotation as it is

    Args:
        cls (type): A class

    Yields:
        tuple[str, Any, Any]: field name, annotation, value
            if field has no default, default=msgspec.NODEFAULT
    """
    for base in reversed(cls.__mro__):
        annotations = base.__dict__.get('__annotations__', {})
        for name, anno in annotations.items():
            # get value
            value = getattr(cls, name, NODEFAULT)
            # ignore UNSET
            if value == UNSET:
                continue
            yield name, anno, value
