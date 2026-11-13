import ast


def get_version_from_source(source):
    """
    Parse Python source code with ast and extract the version string.

    Tries ``__version__`` first, then ``version``.  Only plain string
    assignments (including concatenated strings) are supported — call
    results, dynamic expressions, and f-strings with interpolations
    are ignored.

    Args:
        source (str): Python source code to parse.

    Returns:
        str | None: The version string if found, otherwise None.

    Raises:
        SyntaxError: source contains invalid Python syntax.
    """
    tree = ast.parse(source)

    found_version = None

    for name, value in _iter_assignments(tree):
        if name == '__version__':
            candidate = _try_extract_string(value)
            if candidate is not None:
                return candidate
        elif name == 'version' and found_version is None:
            candidate = _try_extract_string(value)
            if candidate is not None:
                found_version = candidate

    return found_version


def get_attr_from_source(source, name):
    """
    Read a module attribute assigned to a plain string, without importing
    the module.

    This is the reader of setuptools ``version = {attr = "module.attribute"}``:
    the source of the module is parsed instead of executed, so a module that
    imports a dependency the build does not have still yields its version.
    Only plain string assignments (including concatenated strings) are
    supported, the same subset as ``get_version_from_source()``.

    Args:
        source (str): Python source code to parse
        name (str): Attribute name, e.g. ``__version__``

    Returns:
        str | None: Value of the first plain string assignment of the
            attribute, None if the module assigns it no plain string

    Raises:
        SyntaxError: source contains invalid Python syntax
    """
    tree = ast.parse(source)

    for attr, value in _iter_assignments(tree):
        if attr != name:
            continue
        candidate = _try_extract_string(value)
        if candidate is not None:
            return candidate

    return None


def _iter_assignments(tree):
    """
    Iter the module level assignments of a syntax tree.

    A name assigned more than once is yielded once per assignment, the caller
    keeps the first value it accepts. Both ``name = value`` and
    ``name: type = value`` are read, a bare annotation assigns nothing and is
    skipped.

    Args:
        tree (ast.Module): Parsed module

    Yields:
        tuple[str, ast.AST]: Attribute name, and the assigned value node
    """
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    yield target.id, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Name):
                yield node.target.id, node.value


def _try_extract_string(node):
    """
    Attempt to extract a plain string from an ast node.

    Handles:
    - ``ast.Constant`` (str) — Python 3.8+
    - ``ast.JoinedStr`` with a single ``ast.Constant`` node
    - ``ast.BinOp`` left-to-right string concatenation

    Args:
        node (ast.AST): The value node to inspect.

    Returns:
        str | None: The string if extractable, otherwise None.
    """
    # Direct string constant (Python 3.8+)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value

    # Simple f-string like f"1.0" — only the constant part
    if isinstance(node, ast.JoinedStr):
        parts = node.values
        if len(parts) == 1 and isinstance(parts[0], ast.Constant) and isinstance(parts[0].value, str):
            return parts[0].value
        return None

    # String concatenation: "a" "b" or "a" + "b"
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add,)):
        left = _try_extract_string(node.left)
        right = _try_extract_string(node.right)
        if left is not None and right is not None:
            return left + right
        return None

    return None
