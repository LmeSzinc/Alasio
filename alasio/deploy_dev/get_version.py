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

    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id == '__version__':
                candidate = _try_extract_string(node.value)
                if candidate is not None:
                    return candidate
            elif target.id == 'version' and found_version is None:
                candidate = _try_extract_string(node.value)
                if candidate is not None:
                    found_version = candidate

    return found_version


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
