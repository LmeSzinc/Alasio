from typing import get_args, get_origin


def _build_unwrap_origins():
    """
    Build the set of type origins that should be unwrapped to reach the inner type.

    Returns:
        set: Wrapping type origins (ClassVar, Final, Annotated).
    """
    from typing import ClassVar, Final
    origins = {ClassVar, Final}

    # typing.Annotated is available from Python 3.9+
    try:
        from typing import Annotated
        origins.add(Annotated)
    except ImportError:
        pass

    # typing_extensions.Annotated is available on Python 3.8 via pip
    try:
        from typing_extensions import Annotated
        origins.add(Annotated)
    except ImportError:
        pass

    return origins


def _build_literal_origins():
    """
    Build the set of Literal type origins (typing and typing_extensions).

    On Python 3.8, typing.Literal and typing_extensions.Literal are different objects.
    This ensures both are recognized regardless of which module was used to create the type.

    Returns:
        set: Literal type origins.
    """
    origins = set()

    # typing.Literal is available from Python 3.8+
    try:
        from typing import Literal
        origins.add(Literal)
    except ImportError:
        pass

    # typing_extensions.Literal is available on all supported Python versions via pip
    try:
        from typing_extensions import Literal
        origins.add(Literal)
    except ImportError:
        pass

    return origins


_UNWRAP_ORIGINS = _build_unwrap_origins()
_LITERAL_ORIGINS = _build_literal_origins()


def _remove_paired_quotes(arg):
    """
    Remove paired quotes from a literal argument string.

    'normal' -> normal
    "hard"   -> hard
    normal   -> normal

    Args:
        arg (str): A literal argument.

    Returns:
        str: The argument with paired quotes removed.
    """
    if arg.startswith("'") and arg.endswith("'"):
        return arg[1:-1]
    if arg.startswith('"') and arg.endswith('"'):
        return arg[1:-1]
    return arg


def parse_literal_string(anno):
    """
    Parse a literal annotation string into a list of option strings.

    Examples:
        t.Literal[('normal', 'hard')] -> ['normal', 'hard']
        t.Literal['normal', 'hard']   -> ['normal', 'hard']
        Literal['normal', 'hard']     -> ['normal', 'hard']
        typing.Literal['normal', 'hard'] -> ['normal', 'hard']

    Args:
        anno (str): A literal annotation string.

    Returns:
        list[str]: The parsed literal option strings.

    Raises:
        ValueError: If anno is not a literal.
    """
    if not anno.startswith(('Literal', 't.Literal', 'typing.Literal')):
        raise ValueError('Annotation is not a literal')
    args = anno.partition('[')[2].rpartition(']')[0]
    if args.startswith('(') and args.endswith(')'):
        args = args[1:-1]
    return [_remove_paired_quotes(arg.strip()) for arg in args.split(',')]


def get_literal(tp):
    """
    Extract Literal type hint values as a tuple.

    If the input type is (or is wrapped by) Literal, return the literal values as a tuple.
    Handles wrapping types like Annotated, ClassVar, Final, etc.
    If the input is not a Literal type, return None.

    Args:
        tp: A type hint object.

    Returns:
        tuple | None: The literal values if tp is Literal, otherwise None.
    """
    # Fast path: check plain Literal first without entering the unwrap loop
    origin = get_origin(tp)
    if origin in _LITERAL_ORIGINS:
        return get_args(tp)

    # Slow path: unwrap wrapping containers until we reach the core type
    while True:
        # Detect Annotated via __metadata__ — works for both
        # typing.Annotated (3.9+) and typing_extensions._AnnotatedAlias (3.8)
        if hasattr(tp, '__metadata__'):
            args = get_args(tp)
            if args:
                tp = args[0]
                continue

        # Detect other wrappers via get_origin
        origin = get_origin(tp)
        if origin in _UNWRAP_ORIGINS:
            args = get_args(tp)
            if args:
                tp = args[0]
                continue
        break

    # Check if the inner type is Literal
    origin = get_origin(tp)
    if origin in _LITERAL_ORIGINS:
        return get_args(tp)

    return None
