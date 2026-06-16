import ast


def remove_indent(line, indent=1):
    """
    Remove a specific amount of indent from a line.

    Each indent level is one tab or 4 spaces, with tabs taking priority.
    A line starting with a tab has that tab removed as one indent level;
    otherwise up to 4 leading spaces are removed.
    Empty lines and blank lines are returned unchanged.

    Args:
        line (str): A single line of source code.
        indent (int): Number of indent levels to remove. Defaults to 1.

    Returns:
        str: The dedented line.
    """
    if indent <= 0:
        return line
    # Blank lines should remain blank
    if not line or line.isspace():
        return ""
    for _ in range(indent):
        if line.startswith('\t'):
            line = line[1:]
        elif line.startswith('    '):
            line = line[4:]
        elif line.startswith('   '):
            line = line[3:]
        elif line.startswith('  '):
            line = line[2:]
        elif line.startswith(' '):
            line = line[1:]
        else:
            break
    return line


def extract_method(source, keep_indent=True):
    """
    Extract class methods from source code using ast.

    Walk the AST to find all class definitions, then extract each method's
    source lines by cropping the original source by line number range.
    The extracted source includes method decorators if present.

    Args:
        source (str): Python source code.
        keep_indent (bool): If True, preserve original indentation.
            If False, remove one indent level (4 spaces) from each
            output line. Defaults to True.

    Returns:
        dict: Nested dict with (class_name, method_name) keys,
              values are list of source code lines preserving exact content.
              e.g. {"ClassName": {"method_name": ["def method(self):", "    pass"]}}
    """
    tree = ast.parse(source)
    lines = source.splitlines()
    result = {}

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        class_name = node.name
        methods = {}
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            method_name = item.name
            # Start from the first decorator if the method has any
            if item.decorator_list:
                start_line = item.decorator_list[0].lineno
            else:
                start_line = item.lineno
            end_line = item.end_lineno
            # lineno is 1-based, list index is 0-based
            method_lines = lines[start_line - 1:end_line]
            if not keep_indent:
                method_lines = [remove_indent(ln) for ln in method_lines]
            methods[method_name] = method_lines
        if methods:
            result[class_name] = methods

    return result
